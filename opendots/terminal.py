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
/listeners              Configured sources, schedules and subscriptions
/providers              Models, provider profiles and target assignments
/plugins                Loaded plugins, versions and capabilities
/notifications          Destinations and notification delivery status
/events [TEXT]          Browse received events, including ignored ones
/events-next            Older events for the same search
/event ID               Full event payload and goal-relevance decisions
/emit JSON              Create an event with an explicit JSON envelope
/connect                How to configure HTTP, JSONL, GitHub and timers
/status                 Connector health, queue age and planning usage
/history [TEXT]         Search task history
/next                   Show the next history page
/work ID                Inspect task evidence
/proposal ID            Review a completed proposal and its patch
/accept ID COMMIT       Use that exact proposal as the next task base
/retry ID inspected     Replan after inspecting prior effects
/cancel ID              Cancel queued work or request a running task stop
/pause or /resume       Pause or resume the selected agent
/send TYPE MESSAGE      Send a particular event type
/help                   Show this help
/quit                   Disconnect; background agents keep running

Type a request to send owner.request to the selected agent.
Up/Down: input history. Left/Right: edit. Tab: commands. PageUp/PageDown: transcript.'''


def task_line(work):
    status=work['status'].replace('_',' ')
    message=work.get('error') or work.get('summary') or 'Waiting to start'
    return f"#{work['id']} · {work['target_id']} · {status}\n  {message}"


def activity_line(entry):
    detail=entry['detail'];kind=entry['kind'].replace('_',' ')
    result=detail.get('result',{}) if isinstance(detail,dict) else {}
    text=detail.get('summary') or detail.get('error') or detail.get('tool') or detail.get('reason') or ''
    if isinstance(result,dict):
        if 'exit_code' in result:
            text=f"{result.get('name','Check')}: {'passed' if result['exit_code']==0 else 'failed'}"
        elif 'note' in result:text=result['note']
    return f"#{entry.get('work_id') or '—'} · {kind}"+(f" · {str(text)[:240]}" if text else '')


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
        self.history_query=''
        self.history_before=None
        self.events_before=None
        self.events_query=''

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
        if command == '/connect':
            return ("Create a message: /send input.changed Your actual information\n"
                "Or /emit {\"type\":\"input.changed\",\"source\":\"local\",\"payload\":{\"title\":\"Your information\"}}\n"
                "HTTP: POST /api/events with JSON and X-OpenDots-Request: dashboard.\n"
                "JSONL: configure sources [{id,kind:jsonl,path,interval_seconds}], then append one JSON event per line.\n"
                "GitHub: configure a github_poll source with repo owner/name and matching github subscriptions.\n"
                "Webhooks: /api/webhooks/github needs a signing secret and an external receiver for localhost.\n"
                "Timers: configure schedules and a timer.heartbeat subscription. Restart after config edits.\n"
                "Use /listeners to see exact rules. Guide: https://github.com/Shashankss1205/OpenDots/blob/main/docs/EVENTS.md")
        if command == '/notifications':
            data = self.client.request('/api/notifications')
            lines = ['NOTIFICATIONS', 'Delivery totals: ' + json.dumps(data['counts'])]
            if data['error']: lines.append(data['error'])
            for route in data['destinations']:
                lines.append(f"{route['id']} | {route['kind']} | {'enabled' if route['enabled'] else 'disabled'} | {', '.join(route['events'])}")
            if not data['destinations']: lines.append('No destinations configured. See docs/NOTIFICATIONS.md.')
            for delivery in data['deliveries'][:20]:
                lines.append(f"{delivery['id']} | {delivery['route_id']} | {delivery['status']} | attempts {delivery['attempts']}")
            return '\n'.join(lines)
        if command == '/providers':
            data = self.client.request('/api/providers')
            lines = ['MODEL PROVIDERS | default: ' + data['default']]
            for provider in data['providers']:
                lines.append(f"{provider['id']} | {provider['kind']} | {provider['model'] or 'provider default'} | {provider['status']} | targets: {', '.join(provider['targets']) or 'none'}")
            lines.append('Available kinds: ' + ', '.join(data['available_kinds']))
            return '\n'.join(lines)
        if command == '/plugins':
            data = self.client.request('/api/plugins')
            lines = ['PLUGINS (loaded capabilities; connections appear in /listeners)']
            for plugin in data['plugins']:
                capabilities = '; '.join(kind + ': ' + ', '.join(names) for kind, names in plugin['capabilities'].items() if names)
                lines.append(f"{plugin['id']} | {plugin['version']} | API {plugin['api_version']} | {plugin['origin']} | {capabilities or 'no capabilities'}")
            return '\n'.join(lines)
        if command == '/listeners':
            data=self.client.request('/api/listeners')
            lines=['LISTENERS (configuration in effect; restart after edits)']
            for source in data['sources']:
                health=source.get('health',{})
                status=health.get('last_error') or health.get('status') or ('polled successfully' if health.get('last_success') else 'not polled yet')
                mode = 'persistent listener' if source.get('mode') == 'listener' else f"poll every {source.get('interval_seconds',5)}s"
                lines.append(f"{source['id']} | {source['kind']} | {source.get('plugin','application')} | {mode} | {source.get('path') or source.get('repo','')} | {status}")
            if not data['sources']: lines.append('No event sources configured.')
            for schedule in data['schedules']:
                lines.append(f"Timer {schedule['id']}: {schedule['type']} every {schedule['interval_seconds']}s -> {schedule['target_id']}")
            for target in data['targets']:
                lines.append(f"{target['id']} | Goal: {target['goal']} | Relevance: {json.dumps(target['relevance'])}")
                for rule in target['subscriptions']:
                    lines.append(f"  types={rule['types']} sources={rule.get('sources') or 'any'} repos={rule.get('repos') or 'any'}")
            lines.append('Local HTTP input: /api/events. GitHub webhook: '+('secret configured' if data['inputs'][1]['configured'] else 'secret not configured'))
            return '\n'.join(lines)
        if command in {'/events','/events-next'}:
            if command == '/events': self.events_query=argument;self.events_before=None
            elif self.events_before is None: return 'No more events. Use /events to refresh.'
            path='/api/events?q='+quote(self.events_query,safe='')
            if self.events_before is not None:path+='&before='+str(self.events_before)
            data=self.client.request(path);self.events_before=data['next_before']
            lines=[]
            for event in data['events']:
                lines.append(f"{event['id']} | {event['type']} | source={event['source']} | {event['title']}")
                for decision in event['decisions']:
                    score=decision.get('confidence')
                    confidence=f"{score:.0%} estimated confidence in {decision.get('decision', decision['status'])}" if score is not None else 'confidence: not assessed'
                    lines.append(f"  {decision['target_id']}: {decision['status']} | {confidence} | {decision.get('work_status') or 'no work'} | {decision['reason']}")
                if not event['decisions']:lines.append('  No recorded target decision (historical event).')
            if self.events_before:lines.append('Use /events-next for older events.')
            return '\n'.join(lines) or 'No events received. /send TYPE MESSAGE or /connect to configure an input.'
        if command == '/event':
            if not argument:raise ValueError('Usage: /event ID')
            return json.dumps(self.client.request('/api/events/'+quote(argument,safe='')),indent=2)
        if command == '/emit':
            try: event=json.loads(argument)
            except ValueError: raise ValueError('Usage: /emit {"type":"input.changed","source":"local","payload":{"title":"Your information"}}') from None
            return json.dumps(self.client.request('/api/events',event),indent=2)+'\nUse /events to see routing and relevance.'

        self.refresh()
        if command=='/agents':
            return '\n'.join(f"{'>' if t['id']==self.selected else ' '} {t['id']} | {'Paused' if t['state'].get('paused') else 'Active'} | {t.get('objective','')}" for t in self.state['targets']) or 'No agents configured.'
        if command=='/use':
            if argument not in {t['id'] for t in self.state['targets']}: raise ValueError('Unknown agent. Use /agents.')
            self.selected=argument;return f'Selected {argument}.'
        if command=='/retry':
            work_id,_,confirmation=argument.partition(' ')
            if confirmation!='inspected':raise ValueError('Inspect /work ID first, then /retry ID inspected. Prior effects are not undone.')
            return json.dumps(self.client.request('/api/work/'+str(int(work_id))+'/retry',{'inspected':True}))
        if command=='/cancel':
            work_id=int(argument)
            self.client.request(f'/api/work/{work_id}/cancel',{})
            return f'Stop requested for #{work_id}. A running action finishes before cancellation.'
        if command in {'/history','/next'}:
            if command=='/history':self.history_query=argument;self.history_before=None
            elif self.history_before is None:return 'No more history. Use /history to start a new search.'
            query='/api/work?limit=20&q='+quote(self.history_query)
            if self.history_before is not None:query+='&before='+str(self.history_before)
            page=self.client.request(query);self.history_before=page.get('next_before')
            return '\n'.join(task_line(work) for work in page['work'])+ ('\n/next for older work.' if self.history_before else '\nEnd of history.')
        if command=='/work':
            detail=self.client.request('/api/work/'+str(int(argument)))
            return task_line(detail['work'])+'\n'+'\n'.join(activity_line(a) for a in reversed(detail['audit'][:20]))+'\nFor retained changes: /proposal '+argument
        if command=='/proposal':
            path='/api/work/'+str(int(argument))
            proposal=self.client.request(path+'/proposal')
            patch=self.client.request(path+'/patch')['patch']
            return json.dumps(proposal,indent=2)+'\n'+patch+f"\nTo accept: /accept {proposal['work_id']} {proposal['commit']}"
        if command=='/accept':
            work_id,_,commit=argument.partition(' ')
            if not commit: raise ValueError('Review /proposal ID, then /accept ID COMMIT')
            return json.dumps(self.client.request('/api/work/'+str(int(work_id))+'/accept',{'commit':commit}))
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
            metrics=self.state.get('metrics',{})
            calls=sum(row['calls'] for row in metrics.get('planning_calls_today',[]))
            lines=[f"Planner: {self.state.get('backend','unknown')} · Checks: {self.state.get('sandbox','unknown')}",
                f"Planning calls today: {calls} · Oldest queued task: {int(metrics.get('oldest_queued_seconds',0))}s"]
            for source in self.state.get('sources',[]):
                lines.append(f"{source['id']}: "+('Needs attention — '+str(source['last_error']) if source.get('last_error') else 'No recorded error'))
            return '\n'.join(lines)
        if command=='/activity':
            return '\n'.join(activity_line(a) for a in reversed(self.state['audit'][:30]) if a['target_id'] in {None,self.selected}) or 'No recent activity.'
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
        transcript=['OpenDots  •  Persistent agents, at your prompt.','/agents  /listeners  /events  /reviews  /help','Closing this view leaves the runtime running.']
        draft='';cursor=0;history=[];history_index=0;scroll=0;last_refresh=0;connection='Connecting';seen={};initialized=False
        accent=curses.A_BOLD
        if curses.has_colors():
            curses.start_color()
            try:
                curses.use_default_colors();curses.init_pair(1,curses.COLOR_CYAN,-1)
                accent|=curses.color_pair(1)
            except curses.error:pass
        def append(text):
            transcript.extend(safe_text(text).splitlines());del transcript[:-2000]
        while True:
            if time.monotonic()-last_refresh>2:
                try:
                    state=session.refresh();connection='Connected'
                    recent=state['work'] if initialized else state['work'][:8]
                    for work in reversed(recent):
                        if seen.get(work['id'])!=work['status'] and work['status'] in {'waiting_approval','completed','failed','blocked','interrupted','ignored'}:
                            append(task_line(work))
                    for entry in reversed(state['audit']):
                        if initialized and entry['id']>session.last_audit and entry['target_id']==session.selected and entry['kind'] in {'work_started','action_completed','action_failed','proposal_accepted','event_relevance_assessed'}:
                            append(activity_line(entry))
                    session.last_audit=max((a['id'] for a in state['audit']),default=0)
                    seen={w['id']:w['status'] for w in state['work']};initialized=True
                except (ValueError,OSError) as exc: connection=str(exc)
                last_refresh=time.monotonic()
            height,width=window.getmaxyx();window.erase()
            def put(y,text,attr=0):
                if 0<=y<height:
                    try: window.addnstr(y,0,safe_text(text),max(0,width-1),attr)
                    except curses.error: pass
            counts=session.state.get('counts',{})
            put(0,f"OpenDots  |  {session.selected or 'No agent'}  |  {counts.get('running',0)} working  |  {counts.get('waiting_approval',0)} reviews",accent)
            rows=[]
            for line in transcript: rows.extend(textwrap.wrap(line,width=max(10,width-2),replace_whitespace=False) or [''])
            room=max(1,height-5);end=max(0,len(rows)-scroll);start=max(0,end-room)
            for index,line in enumerate(rows[start:end]):put(index+2,line)
            put(height-3,connection,curses.A_DIM)
            offset=max(0,cursor-max(1,width-4))
            put(height-2,'> '+draft[offset:offset+max(1,width-3)])
            put(height-1,'Enter send  Tab commands  /help  PgUp/PgDn scroll  Ctrl+D disconnect',curses.A_DIM)
            try:window.move(max(0,height-2),min(max(0,width-2),2+cursor-offset))
            except curses.error:pass
            window.refresh()
            try:key=window.get_wch()
            except curses.error:continue
            if key=='\x04':break
            if key=='\x03' or key=='\x15':draft='';cursor=0;session.confirmation=None;continue
            if key in (curses.KEY_HOME,'\x01'):cursor=0;continue
            if key in (curses.KEY_END,'\x05'):cursor=len(draft);continue
            if key==curses.KEY_LEFT:cursor=max(0,cursor-1);continue
            if key==curses.KEY_RIGHT:cursor=min(len(draft),cursor+1);continue
            if key==curses.KEY_DC:draft=draft[:cursor]+draft[cursor+1:];continue
            if key=='\t':
                choices=[line.split()[0] for line in HELP.splitlines() if line.startswith('/') and line.split()[0].startswith(draft)]
                if len(choices)==1:draft=choices[0]+' ';cursor=len(draft)
                elif choices:append('  '.join(choices));scroll=0
                continue
            if key==curses.KEY_PPAGE:scroll=min(len(rows),scroll+room);continue
            if key==curses.KEY_NPAGE:scroll=max(0,scroll-room);continue
            if key==curses.KEY_UP:
                history_index=max(0,history_index-1);draft=history[history_index] if history else '';cursor=len(draft);continue
            if key==curses.KEY_DOWN:
                history_index=min(len(history),history_index+1);draft=history[history_index] if history_index<len(history) else '';cursor=len(draft);continue
            if key in ('\n','\r',curses.KEY_ENTER):
                text=draft;draft='';cursor=0;scroll=0
                if not text:continue
                history.append(text);history_index=len(history);append('> '+text)
                try:
                    answer=session.submit(text)
                    if answer is None:break
                    append(answer)
                except (ValueError,OSError) as exc:append('Needs attention: '+str(exc))
                last_refresh=0
            elif key in ('\b','\x7f',curses.KEY_BACKSPACE):
                if cursor:draft=draft[:cursor-1]+draft[cursor:];cursor-=1
            elif isinstance(key,str) and key.isprintable() and len(draft)<8000:
                draft=draft[:cursor]+key+draft[cursor:];cursor+=1
    try:curses.wrapper(screen)
    except KeyboardInterrupt:pass
