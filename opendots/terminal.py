"""A prompt-first terminal client. Closing it never stops the runtime."""
import json
import sys
import textwrap
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse, quote
from urllib.request import Request, urlopen

HELP = '''/agents                 List agents and their status
/use ID                 Select an agent
/reviews                List actions waiting for you
/review ID              Inspect an exact action, diff and checks
/approve ID             Request approval confirmation after review
/reject ID              Reject a reviewed action
/activity               Recent activity for the selected agent
/status                 Connector health, queue age and planning usage
/history [TEXT]         Search task history
/work ID                Inspect task evidence
/pause or /resume       Pause or resume the selected agent
/send TYPE MESSAGE      Send a particular event type
/help                   Show this help
/quit                   Disconnect; background agents keep running

Type a request to send owner.request to the selected agent.
Up/Down: input history. PageUp/PageDown: transcript. Ctrl+U: clear input.'''


def safe_text(value):
    return ''.join(c if c in '\n\t' or c.isprintable() else '?' for c in str(value))


class Client:
    def __init__(self, url='http://127.0.0.1:8765'):
        parsed=urlparse(url)
        if parsed.scheme != 'http' or parsed.hostname not in {'127.0.0.1','localhost'} or parsed.username or parsed.password:
            raise ValueError('Terminal UI requires a local HTTP runtime URL')
        self.url=url.rstrip('/')

    def request(self, path, body=None):
        request=Request(self.url+path,data=None if body is None else json.dumps(body).encode(),
            headers={'Content-Type':'application/json','X-OpenDots-Request':'dashboard'})
        try:
            with urlopen(request, timeout=2) as response:
                return json.load(response)
        except HTTPError as exc:
            try: detail=json.load(exc).get('error',str(exc))
            except ValueError: detail=str(exc)
            raise ValueError(detail) from None
        except URLError:
            raise ValueError('Runtime is offline. Start opendots serve or its background service, then reconnect.') from None


class Session:
    def __init__(self, client):
        self.client=client
        self.state={'targets':[],'work':[],'audit':[],'counts':{}}
        self.selected=None
        self.reviewed={}
        self.confirmation=None
        self.last_audit=0

    def refresh(self):
        state=self.client.request('/api/state')
        self.state=state
        ids=[t['id'] for t in state['targets']]
        if self.selected not in ids: self.selected=ids[0] if ids else None
        return state

    def target(self):
        target=next((t for t in self.state['targets'] if t['id']==self.selected),None)
        if target is None: raise ValueError('No selected agent. Configure a target with opendots init first.')
        return target

    def submit(self, text):
        text=text.strip()
        if not text: return ''
        if self.confirmation:
            work_id,token=self.confirmation
            self.confirmation=None
            if text != f'approve {work_id}': return 'Approval cancelled.'
            self.client.request(f'/api/work/{work_id}/decision',{'approved':True,'approval_token':token})
            return f'Approved the reviewed action for work #{work_id}.'
        command,_,argument=text.partition(' ')
        if command in {'/quit','/exit'}: return None
        if command in {'/help','?'}: return HELP
        self.refresh()
        if command=='/agents':
            return '\n'.join(f"{'>' if t['id']==self.selected else ' '} {t['id']} | {'Paused' if t['state'].get('paused') else 'Active'} | {t.get('objective','')}" for t in self.state['targets']) or 'No agents configured.'
        if command=='/use':
            if argument not in {t['id'] for t in self.state['targets']}: raise ValueError('Unknown agent. Use /agents.')
            self.selected=argument;return f'Selected {argument}.'
        if command=='/history':
            return json.dumps(self.client.request('/api/work?q='+quote(argument)),indent=2)
        if command=='/work':
            return json.dumps(self.client.request('/api/work/'+str(int(argument))),indent=2)
        if command=='/reviews':
            return '\n'.join(f"#{w['id']} | {w['target_id']} | {w['plan']['summary']}" for w in self.state['work'] if w['status']=='waiting_approval') or 'Nothing needs your review.'
        if command in {'/review','/approve','/reject'}:
            work_id=int(argument)
            work=next((w for w in self.state['work'] if w['id']==work_id and w['status']=='waiting_approval'),None)
            if work is None: raise ValueError('That action is no longer awaiting review.')
            if command=='/review':
                self.reviewed[work_id]=work['approval_token']
                preview=next((a['detail'].get('preview') for a in self.state['audit'] if a['work_id']==work_id and a['kind']=='approval_requested'),None)
                action=work['plan']['actions'][work['approval_index']]
                evidence=[a['detail'] for a in self.state['audit'] if a['work_id']==work_id and a['kind'] in {'action_completed','action_failed'}]
                return f"Review #{work_id} — {work['target_id']}\nWhy: {work['plan']['summary']}\nAction: {action['tool']}\n"+(preview.get('diff') if isinstance(preview,dict) and preview.get('diff') else json.dumps(preview or action['args'],indent=2))+'\nEvidence: '+json.dumps(evidence,indent=2)+f'\n/approve {work_id} or /reject {work_id}'
            if self.reviewed.get(work_id)!=work['approval_token']:
                raise ValueError(f'Review the current action first: /review {work_id}')
            if command=='/approve':
                self.confirmation=(work_id,work['approval_token'])
                return f'Type approve {work_id} to confirm this exact action. Anything else cancels.'
            self.client.request(f'/api/work/{work_id}/decision',{'approved':False,'approval_token':work['approval_token']})
            return f'Rejected work #{work_id}.'
        if command=='/status':
            return json.dumps({'sources':self.state.get('sources',[]),'metrics':self.state.get('metrics',{})},indent=2)
        if command=='/activity':
            return '\n'.join(f"{a['kind'].replace('_',' ')} | work {a['work_id'] or '-'} | {json.dumps(a['detail'])}" for a in reversed(self.state['audit'][:50]) if a['target_id'] in {None,self.selected}) or 'No recent activity.'
        if command in {'/pause','/resume'}:
            target=self.target();self.client.request('/api/targets/'+quote(target['id'],safe='')+'/pause',{'paused':command=='/pause'})
            return f"{target['id']}: {'paused' if command=='/pause' else 'resumed'}."
        if command.startswith('/') and command!='/send': raise ValueError('Unknown command. Type /help.')
        target=self.target();event_type='owner.request';message=text
        if command=='/send':
            event_type,_,message=argument.partition(' ')
            if not message: raise ValueError('Usage: /send TYPE MESSAGE')
        repos=[repo for rule in target.get('subscriptions',[]) for repo in rule.get('repos',[])]
        payload={'title':message,'body':message}
        if len(set(repos))==1: payload['repo']=repos[0]
        result=self.client.request('/api/events',{'type':event_type,'source':'local','target_id':target['id'],'payload':payload})
        return f"Queued {result['queued']} work item(s)." if result['queued'] else 'Event recorded; no work queued. Check subscriptions or use /send TYPE MESSAGE.'


def run(url='http://127.0.0.1:8765'):
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise ValueError('Interactive terminal required. Use opendots status for scriptable JSON.')
    import curses
    session=Session(Client(url))
    def screen(window):
        curses.curs_set(1);window.timeout(150);window.keypad(True)
        transcript=['OpenDots  •  Persistent agents, at your prompt.','/agents  /reviews  /activity  /help','Closing this view leaves the runtime running.']
        draft='';history=[];history_index=0;scroll=0;last_refresh=0;connection='Connecting';seen={}
        def append(text):
            transcript.extend(safe_text(text).splitlines());del transcript[:-2000]
        while True:
            if time.monotonic()-last_refresh>2:
                try:
                    state=session.refresh();connection='Connected'
                    for work in reversed(state['work']):
                        if seen.get(work['id'])!=work['status'] and work['status'] in {'waiting_approval','completed','failed','blocked','interrupted'}:
                            append(f"• {work['target_id']} · #{work['id']} · {work['status'].replace('_',' ')}: {work.get('summary') or work.get('error') or ''}")
                        seen[work['id']]=work['status']
                except (ValueError,OSError) as exc: connection=str(exc)
                last_refresh=time.monotonic()
            height,width=window.getmaxyx();window.erase()
            def put(y,text,attr=0):
                if 0<=y<height:
                    try: window.addnstr(y,0,safe_text(text),max(0,width-1),attr)
                    except curses.error: pass
            counts=session.state.get('counts',{})
            put(0,f"OpenDots  |  {session.selected or 'No agent'}  |  {counts.get('running',0)} working  |  {counts.get('waiting_approval',0)} reviews",curses.A_BOLD)
            rows=[]
            for line in transcript: rows.extend(textwrap.wrap(line,width=max(10,width-2),replace_whitespace=False) or [''])
            room=max(1,height-5);end=max(0,len(rows)-scroll);start=max(0,end-room)
            for index,line in enumerate(rows[start:end]):put(index+2,line)
            put(height-3,connection,curses.A_DIM)
            put(height-2,'> '+draft[-max(1,width-4):])
            put(height-1,'Enter send  /help commands  PgUp/PgDn history  Ctrl+D disconnect',curses.A_DIM)
            try:window.move(max(0,height-2),min(max(0,width-2),2+len(draft)))
            except curses.error:pass
            window.refresh()
            try:key=window.get_wch()
            except curses.error:continue
            if key=='\x04':break
            if key=='\x03' or key=='\x15':draft='';continue
            if key==curses.KEY_PPAGE:scroll=min(len(rows),scroll+room);continue
            if key==curses.KEY_NPAGE:scroll=max(0,scroll-room);continue
            if key==curses.KEY_UP:
                history_index=max(0,history_index-1);draft=history[history_index] if history else '';continue
            if key==curses.KEY_DOWN:
                history_index=min(len(history),history_index+1);draft=history[history_index] if history_index<len(history) else '';continue
            if key in ('\n','\r',curses.KEY_ENTER):
                text=draft;draft='';scroll=0
                if not text:continue
                history.append(text);history_index=len(history);append('> '+text)
                try:
                    answer=session.submit(text)
                    if answer is None:break
                    append(answer)
                except (ValueError,OSError) as exc:append('Needs attention: '+str(exc))
                last_refresh=0
            elif key in ('\b','\x7f',curses.KEY_BACKSPACE):draft=draft[:-1]
            elif isinstance(key,str) and key.isprintable() and len(draft)<8000:draft+=key
    try:curses.wrapper(screen)
    except KeyboardInterrupt:pass
