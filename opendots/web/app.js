const $ = (id) => document.getElementById(id);
let current = null;
const el = (tag, className, text) => {const node = document.createElement(tag); if (className) node.className = className; if (text !== undefined) node.textContent = text; return node;};
async function request(path, body) {
  const response = await fetch(path, body === undefined ? {} : {method: 'POST', headers: {'Content-Type': 'application/json', 'X-OpenDots-Request': 'dashboard'}, body: JSON.stringify(body)});
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || `Request failed (${response.status})`);
  return data;
}
function showError(error) {$('error').textContent = error.message; $('error').hidden = false;}
function render(data) {
  current = data;
  renderListeners(data.listeners);renderNotifications(data.notifications);renderProviders(data.providers);
  if (eventLive) renderEventStream(data.event_stream);
  $('connection').textContent = 'Connected to local workers';
  $('backend').textContent = data.backend === 'demo' ? 'DEMO · deterministic recipes' :
    data.backend === 'codex' ? 'CODEX · CLI planning backend' : `${data.backend} · registered planning provider`;
  $('sample').hidden = data.backend !== 'demo' || !data.targets.some(t=>t.id==='kubernetes') || !data.targets.some(t=>t.id==='react');
  $('events').textContent = data.event_count; $('target-count').textContent = data.targets.length;
  $('completed').textContent = data.counts.completed || 0; $('waiting').textContent = data.counts.waiting_approval || 0;
  $('worker-count').textContent = `${data.workers} configurable worker slots`;
  const grid = $('target-grid'); grid.replaceChildren();
  for (const target of data.targets) {
    const jobs = data.work.filter(w => w.target_id === target.id);
    const waiting = jobs.some(w => w.status === 'waiting_approval');
    const running = jobs.some(w => w.status === 'running');
    const card = el('article','target-card'), top = el('div','target-top');
    top.append(el('div','target-icon','◎')); const label = el('div'); label.append(el('div','target-title',target.name || target.id),el('div','target-id',target.id)); top.append(label,el('span',`badge ${waiting ? 'review' : ''}`,waiting ? 'NEEDS REVIEW' : running ? 'WORKING' : 'WATCHING'));
    card.append(top,el('p',null,target.objective || 'Target removed from configuration'));
    if(target.state.last_summary)card.append(el('p','target-latest',target.state.last_summary));
    const bottom = el('div','target-bottom');
    bottom.append(el('span',null,`${target.state.completed || 0} completed`),el('span',null,`${jobs.filter(w=>w.status==='queued').length} queued`),el('span',null,`${target.state.artifacts?.filter(a=>a.changed).length||0} change proposals`),el('span',null,`${target.state.notes?.length||0} memory entries`));
    card.append(bottom); grid.append(card);
  }
  const workflows = $('workflow-list');
  const workflowKey = JSON.stringify([data.work.map(w=>[w.id,w.status,w.summary,w.planning_round,w.error]),data.audit[0]?.id]);
  if (workflows.dataset.key !== workflowKey) {
    workflows.dataset.key = workflowKey; workflows.replaceChildren();
    if (!data.work.length) workflows.append(el('div','empty','An event starts the loop. Each job keeps its own branch, decisions and test evidence.'));
    for (const work of data.work.slice(0,8)) {
      const target = data.targets.find(t=>t.id===work.target_id);
      const audit = data.audit.filter(a=>a.work_id===work.id);
      const checks = audit.filter(a=>a.kind==='action_completed' && a.detail.result?.name && typeof a.detail.result.exit_code==='number').reverse();
      const approved = audit.some(a=>a.kind==='approval_granted');
      const reviewed = audit.some(a=>a.kind==='approval_requested');
      const artifact = target?.state.artifacts?.find(a=>a.branch===work.branch);
      const card=el('article',`workflow-card ${work.status}`); card.dataset.workId=work.id;
      const top=el('div','workflow-top'), label=el('div');
      label.append(el('div','work-eyebrow',`WORK ${String(work.id).padStart(2,'0')} · ${work.event_id.startsWith('schedule:')?'SCHEDULED SCOUT':'INCOMING EVENT'}`),el('h3',null,target?.name||work.target_id));
      top.append(label,el('span',`queue-status ${work.status}`,work.status.replaceAll('_',' ')));card.append(top);
      const stage = work.status==='queued'?0:work.status==='waiting_approval'?2:work.status==='completed'?4:checks.length?3:approved?3:1;
      const steps=el('ol','workflow-steps');
      ['Signal','Diagnose','Policy','Verify','Retain'].forEach((name,index)=>{const unchecked=index===3 && work.status==='completed' && !checks.length;const li=el('li',unchecked?'':index<stage?'done':index===stage?'active':'');li.append(el('span','step-number',unchecked?'—':index<stage?'✓':String(index+1)),el('span',null,unchecked?'No check':name));steps.append(li);});card.append(steps);
      let summary=work.error||work.plan?.summary||work.summary;
      if(work.status==='queued') summary='Queued behind earlier work for this target. Other targets can continue.';
      if(!summary) summary='Assessing the event or planning the next step with the configured provider.';
      card.append(el('p','workflow-summary',summary));
      if (checks.length) {
        const evidence=el('div','check-evidence');
        for (const check of checks) {
          const result=check.detail.result, row=el('div',`check-result ${result.exit_code===0?'passed':'failed'}`);
          row.append(el('span','check-symbol',result.exit_code===0?'✓':'!'),el('span','check-name',result.name),el('span','check-output',(result.output||'').trim().split('\n').filter(Boolean).slice(-1)[0]||`Exit code ${result.exit_code}`));evidence.append(row);
        }
        card.append(evidence);
      }
      const meta=el('div','workflow-meta');
      const policy=reviewed?(approved?'Reviewed & approved':'Exact change awaiting review'):(target?.policy.write_file==='auto'?'Automatic scoped actions':'Target policy enforced');
      meta.append(el('span',null,policy),el('span',null,`${work.planning_round||0} planning rounds`));
      if (artifact) {
        meta.append(el('span','commit-label',`Git ${artifact.commit.slice(0,8)}`));
        if(artifact.changed){const button=el('button','patch-button','View retained patch ↗');button.addEventListener('click',async()=>{try{const result=await request(`/api/work/${work.id}/patch`);$('patch-title').textContent=`${target?.name||work.target_id} · work #${work.id}`;$('patch-branch').textContent=result.branch;$('patch-content').textContent=result.patch||'No changes in this work item.';$('evidence-dialog').showModal();}catch(error){showError(error);}});meta.append(button);}
      }
      card.append(meta);workflows.append(card);
    }
  }
  const picker = $('event-target'), previous = picker.value;
  if (Array.from(picker.options).map(o=>o.value).join('|') !== data.targets.map(t=>t.id).join('|')) {
    picker.replaceChildren(...data.targets.map(t=>{const option=el('option',null,t.name||t.id);option.value=t.id;return option;}));
    if (data.targets.some(t=>t.id===previous)) picker.value=previous;
    suggestEventFields();
  }
  const approvals = data.work.filter(w=>w.status==='waiting_approval');
  // Keep diff scroll position and focused decision buttons stable between polling cycles.
  const reviewKey = approvals.map(w=>w.approval_token).join('|');
  if ($('review-list').dataset.key !== reviewKey) {
    const reviews = $('review-list'); reviews.dataset.key = reviewKey; reviews.replaceChildren();
    if (!approvals.length) reviews.append(el('div','empty','No actions awaiting review. New proposals will appear here with the exact change.'));
    for (const work of approvals) {
      const action = work.plan.actions[work.approval_index];
      const audit = data.audit.find(a=>a.work_id===work.id && a.kind==='approval_requested' && a.detail.index===work.approval_index);
      const card=el('article','review-card'),top=el('div','review-top'),label=el('div');
      label.append(el('h3',null,`${work.target_id} · ${action.tool}`),el('p',null,work.plan.summary));
      const buttons=el('div');
      for (const [approved,name] of [[true,'Approve action'],[false,'Reject']]) {
        const button=el('button',approved?'approve':'reject',name);
        button.addEventListener('click',async()=>{button.disabled=true;try{await request(`/api/work/${work.id}/decision`,{approved,approval_token:work.approval_token});await refresh();}catch(e){showError(e);button.disabled=false;}});buttons.append(button);
      }
      top.append(label,buttons);card.append(top);
      const preview = audit?.detail.preview;
      if(work.branch)card.append(el('p',null,`Task branch: ${work.branch}`));
      card.append(el('pre',null,preview?.diff || JSON.stringify(preview || action.args,null,2)));reviews.append(card);
    }
  }
  const timeline=$('audit');
  const opened = new Set(Array.from(timeline.querySelectorAll('details[open]')).map(d=>d.dataset.id));
  timeline.replaceChildren();
  for (const audit of data.audit.slice(0,60)) {
    const row=el('div','audit-row'),content=el('div'),title=el('div','audit-title',audit.kind.replaceAll('_',' '));
    title.append(el('span','audit-time',new Date(audit.at*1000).toLocaleTimeString([], {hour:'2-digit',minute:'2-digit',second:'2-digit'})));
    content.append(title,el('div','audit-meta',[audit.target_id,audit.work_id?`work #${audit.work_id}`:null].filter(Boolean).join(' · ')||'Global event stream'));
    const details=el('details');details.dataset.id=String(audit.id);details.open=opened.has(String(audit.id));details.append(el('summary',null,'View evidence'),el('pre',null,JSON.stringify(audit.detail,null,2)));content.append(details);
    row.append(el('div','audit-icon',audit.kind.includes('failed')?'!':'·'),content);timeline.append(row);
  }
  if(!data.audit.length)timeline.append(el('div','empty','Waiting for an event. Send a request or connect an event source.'));
  const queue=$('queue');queue.replaceChildren();
  for(const work of data.work.slice(0,8)){const row=el('div','queue-row');row.append(el('span',null,`#${work.id} · ${work.target_id}`),el('span',`queue-status ${work.status}`,work.status.replaceAll('_',' ')));queue.append(row);}
}
let loading=false;
async function refresh(){if(loading)return;loading=true;try{render(await request('/api/state'));}catch(e){$('connection').textContent='Connection lost';showError(e);}finally{loading=false;}}
function suggestEventFields(){
  const target=current?.targets.find(t=>t.id===$('event-target').value);
  const rules=target?.subscriptions||[];
  $('event-repo').value=rules.flatMap(r=>r.repos||[])[0]||'';
  $('event-source').value=rules.flatMap(r=>r.sources||[])[0]||'local';
  const type=rules.flatMap(r=>r.types||[]).find(t=>!t.includes('*')&&!t.includes('?'));
  $('event-type').value=type||'owner.request';
}
$('event-target').addEventListener('change',suggestEventFields);
function eventFromForm(){
  const payload=JSON.parse($('event-payload').value||'{}');
  if(!payload || Array.isArray(payload) || typeof payload!=='object')throw new Error('Payload must be a JSON object.');
  payload.title=$('event-title').value;
  if($('event-repo').value)payload.repo=$('event-repo').value;
  const event={type:$('event-type').value,target_id:$('event-target').value,priority:Number($('event-priority').value),source:$('event-source').value,payload};
  if($('event-id').value)event.id=$('event-id').value;
  return event;
}
$('preview-event').addEventListener('click',()=>{try{$('event-preview').textContent=JSON.stringify(eventFromForm(),null,2);$('event-preview').hidden=false;}catch(e){showError(e);}});
$('event-form').addEventListener('submit',async(event)=>{event.preventDefault();const button=event.submitter;button.disabled=true;try{const result=await request('/api/events',eventFromForm());$('error').hidden=true;$('event-result').textContent=`Event ${result.event_id}: `+(result.duplicate?'already received.':result.queued?`${result.queued} candidate task(s) queued. Relevance may still be pending.`:'recorded with no work queued. See its routing reasons in Received events.');eventLive=true;await refresh();}catch(e){showError(e);}finally{button.disabled=false;}});
$('close-evidence').addEventListener('click',()=>$('evidence-dialog').close());
$('sample').addEventListener('click',async()=>{
  const button=$('sample');button.disabled=true;
  try {
    // Demo target IDs live only in the sample fixture, never in routing/scheduling logic.
    if(!current?.targets.some(t=>t.id==='kubernetes')||!current?.targets.some(t=>t.id==='react'))throw new Error('This sample requires the provided example config. Use the event form for custom targets.');
    const examples=[
      {type:'github.issue.opened',target_id:'kubernetes',priority:95,payload:{title:'Deployment has zero replicas',repo:'kubernetes/kubernetes'}},
      {type:'timer.scout',target_id:'react',priority:60,payload:{title:'Inspect local accessibility fixture',repo:'facebook/react'}},
      {type:'github.discussion.created',target_id:'kubernetes',priority:40,payload:{title:'Follow up after the deployment check',repo:'kubernetes/kubernetes'}},
      {type:'github.star',target_id:'react',priority:5,payload:{title:'Low attention signal',repo:'facebook/react'}},
      {type:'unrelated.news',priority:50,payload:{title:'No target subscribes to this event'}}
    ];
    for(const example of examples)await request('/api/events',{...example,id:crypto.randomUUID(),source:'demo'});
    $('error').hidden=true;await refresh();
  }catch(e){showError(e);}finally{button.disabled=false;}
});
let eventLive=true, eventBefore=null, eventQuery='';
function renderListeners(data){
  if(!data)return;
  const root=$('listener-list'),key=JSON.stringify(data);
  if(root.dataset.key===key)return;
  const opened=new Set(Array.from(root.querySelectorAll('details[open]')).map(n=>n.dataset.id));
  root.dataset.key=key;root.replaceChildren();
  renderPlugins(data.plugins||[]);
  for(const input of data.inputs){root.append(el('p','input-summary',input.kind==='http'?`Local HTTP / terminal / web: ${input.endpoint}`:`GitHub webhook: ${input.configured?'signing secret configured':'not configured'} (${input.endpoint})`));}
  if(!data.sources.length)root.append(el('p','muted','No polling sources configured.'));
  for(const source of data.sources){const card=el('article','listener-card');const mode=source.mode==='listener'?'Persistent listener':`Poll every ${source.interval_seconds||5}s`;card.append(el('h3',null,`${source.id} · ${source.kind}`),el('p',null,source.path||source.repo||'Custom adapter'),el('p','muted',`${source.plugin||'application'} · ${mode} · ${source.health.last_error||source.health.status|| (source.health.last_success?'Last success '+new Date(source.health.last_success*1000).toLocaleString():'Not polled yet')}`));if(source.health.gap_message)card.append(el('p',null,source.health.gap_message));root.append(card);}
  for(const timer of data.schedules)root.append(el('p','input-summary',`Timer ${timer.id}: ${timer.type} every ${timer.interval_seconds}s for ${timer.target_id}`));
  if(!data.schedules.length)root.append(el('p','muted','No heartbeat schedules configured.'));
  for(const target of data.targets){const card=el('details','listener-card');card.dataset.id=target.id;card.open=opened.has(target.id);card.append(el('summary',null,`${target.id} · ${target.subscriptions.length} subscription rule(s) · relevance ${target.relevance.mode||'off'}`),el('p',null,target.goal));const list=el('ul');for(const rule of target.subscriptions)list.append(el('li',null,`Types: ${rule.types.join(', ')} | Sources: ${(rule.sources||[]).join(', ')||'any'} | Repositories: ${(rule.repos||[]).join(', ')||'any'}`));card.append(list,el('p','muted',`Minimum priority: ${target.minimum_priority}. Model confidence threshold: ${target.relevance.mode==='model'?(target.relevance.minimum_confidence??0.7):'disabled'}.`));root.append(card);}
}
function renderPlugins(plugins){
  const root=$('plugin-list');root.replaceChildren();
  for(const plugin of plugins){const card=el('article','listener-card');card.append(el('h3',null,`${plugin.id} · ${plugin.version}`),el('p',null,plugin.description),el('p','muted',`${plugin.origin} · API ${plugin.api_version}${plugin.legacy?' · legacy compatibility':''}`));for(const [kind,names] of Object.entries(plugin.capabilities)){if(names.length)card.append(el('p',null,`${kind}: ${names.join(', ')}`));}root.append(card);}
}
function renderEventStream(data){
  if(!data)return;
  eventBefore=data.next_before;$('events-older').hidden=!eventBefore;
  const root=$('received-list'),key=JSON.stringify(data);
  if(root.dataset.key===key)return;root.dataset.key=key;root.replaceChildren();
  if(!data.events.length)root.append(el('p','empty','No matching events received. Create one below or connect a source.'));
  for(const event of data.events){const card=el('article','event-card');card.append(el('h3',null,`${event.type} · ${event.source}`),el('p','muted',`${new Date(event.created*1000).toLocaleString()} · ${event.id}`),el('p',null,event.title));for(const d of event.decisions){const score=d.confidence==null?'not assessed':`${Math.round(d.confidence*100)}% estimated confidence in “${d.decision||d.status}”`;const row=el('div','event-decision');row.append(el('strong',null,`${d.target_id}: ${d.status} · ${score}`),el('p',null,d.reason),el('small','muted',`${d.method||'historical'}${d.work_id?' · work #'+d.work_id+' '+d.work_status:' · no task'}`));card.append(row);}if(!event.decisions.length)card.append(el('p','muted','No historical decision recorded.'));const inspect=el('button','reject','View payload and decisions');inspect.addEventListener('click',async()=>{try{$('event-detail').textContent=JSON.stringify(await request('/api/events/'+encodeURIComponent(event.id)),null,2);$('event-dialog').showModal();}catch(e){showError(e);}});card.append(inspect);root.append(card);}
}
async function loadEvents(before){const data=await request('/api/events?q='+encodeURIComponent(eventQuery)+(before?'&before='+before:''));renderEventStream(data);}
$('event-search-form').addEventListener('submit',async event=>{event.preventDefault();eventLive=false;eventQuery=$('event-search').value;try{await loadEvents(null);}catch(e){showError(e);}});
$('events-older').addEventListener('click',async()=>{eventLive=false;try{await loadEvents(eventBefore);}catch(e){showError(e);}});
$('event-live').addEventListener('click',()=>{eventLive=true;eventQuery='';$('event-search').value='';refresh();});
$('close-event').addEventListener('click',()=>$('event-dialog').close());
refresh();setInterval(refresh,1000);

function renderNotifications(data){
  if(!data)return;
  const root=$('notification-list'),key=JSON.stringify(data);
  if(root.dataset.key===key)return;root.dataset.key=key;root.replaceChildren();
  if(data.error)root.append(el('p','event-help',data.error));
  if(!data.destinations.length)root.append(el('p','empty','No notification destinations configured. Add a webhook or local JSONL destination to your configuration.'));
  for(const route of data.destinations){const card=el('article','listener-card');card.append(el('h3',null,`${route.id} · ${route.kind} · ${route.enabled?'enabled':'disabled'}`),el('p',null,route.events.join(', ')));root.append(card);}
  for(const delivery of data.deliveries.slice(0,10)){const card=el('article','event-card');card.append(el('h3',null,`${delivery.route_id}: ${delivery.status}`),el('p','muted',`${delivery.id} · attempts ${delivery.attempts}`));if(delivery.error)card.append(el('p',null,delivery.error));root.append(card);}
}

function renderProviders(data){
  const root=$('provider-list');root.replaceChildren();if(!data)return;
  for(const provider of data.providers){const card=el('article','listener-card');
    card.append(el('h3',null,`${provider.id} · ${provider.kind}`),el('p',null,`Model: ${provider.model||'provider default'} · ${provider.status}`),el('p','muted',`Targets: ${provider.targets.join(', ')||'none'} · Plugin: ${provider.plugin}`));root.append(card);}
}
