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
      if(!summary) summary='Codex is inspecting the target in its isolated task workspace.';
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
  if(!data.audit.length)timeline.append(el('div','empty','Waiting for the first event. Run the demo to watch the loop.'));
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
  if(type)$('event-type').value=type;
}
$('event-target').addEventListener('change',suggestEventFields);
$('event-form').addEventListener('submit',async(event)=>{event.preventDefault();try{const result=await request('/api/events',{type:$('event-type').value,target_id:$('event-target').value,priority:Number($('event-priority').value),source:$('event-source').value,payload:{title:$('event-title').value,repo:$('event-repo').value}});$('error').hidden=true;$('event-result').textContent=result.duplicate?'Event already received.':result.queued?`Queued ${result.queued} work item(s).`:'Event recorded, but no work was queued. Check the target subscription, repository, source and priority.';await refresh();}catch(e){showError(e);}});
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
refresh();setInterval(refresh,1000);
