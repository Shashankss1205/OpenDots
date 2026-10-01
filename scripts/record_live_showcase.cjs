// Record the actual local service. Only chapter captions are editorial overlays.
const {chromium}=require((process.env.OPENDOTS_PLAYWRIGHT_MODULE || process.env.SPOTS_PLAYWRIGHT_MODULE)||'playwright');
const {spawn}=require('node:child_process');
const fs=require('node:fs');
const path=require('node:path');
const root=path.resolve(__dirname,'..');
const config=path.resolve(process.argv[2]);
const output=path.resolve(process.argv[3]);
const port=Number((process.env.OPENDOTS_DEMO_PORT || process.env.SPOTS_DEMO_PORT)||8896);
const base=`http://127.0.0.1:${port}`;
const pause=ms=>new Promise(resolve=>setTimeout(resolve,ms));
let page,origin,marks=[];
function mark(name){const seconds=(Date.now()-origin)/1000;marks.push({name,seconds});console.log(JSON.stringify({scene:name,seconds}));}
async function state(){return await (await fetch(base+'/api/state')).json();}
async function waitFor(predicate){const deadline=Date.now()+600000;while(Date.now()<deadline){const data=await state();if(data.counts.failed||data.counts.blocked)throw Error(JSON.stringify(data.work.map(w=>({status:w.status,error:w.error}))));if(predicate(data))return data;await pause(250);}throw Error('Live demo state timeout');}
async function caption(number,title,detail){await page.evaluate(({number,title,detail})=>{let panel=document.getElementById('demo-caption');if(!panel){panel=document.createElement('div');panel.id='demo-caption';panel.style.cssText='position:fixed;bottom:20px;left:260px;right:25px;z-index:100;background:#0b1918f2;border:1px solid #548d72;border-radius:12px;padding:18px 25px;display:flex;align-items:center;gap:22px;box-shadow:0 8px 35px #0008;pointer-events:none';document.body.append(panel);}panel.replaceChildren();const count=document.createElement('span');count.textContent=number;count.style.cssText='font:600 13px ui-monospace,monospace;color:#a1edca;background:#234534;border-radius:6px;padding:10px';const words=document.createElement('div');const h=document.createElement('div');h.textContent=title;h.style.cssText='font-size:19px;font-weight:600;color:#f0f8f4';const p=document.createElement('div');p.textContent=detail;p.style.cssText='font-size:12px;color:#9db9ac;margin-top:7px';words.append(h,p);panel.append(count,words);},{number,title,detail});}
async function event(target,type,title){await page.locator('#event-form').scrollIntoViewIfNeeded();await page.selectOption('#event-target',target);await page.fill('#event-type',type);await page.fill('#event-title',title);await page.fill('#event-priority','85');await pause(650);await page.locator('#event-form button').click();await pause(700);}
(async()=>{
 if(fs.existsSync(output))throw Error('Use a fresh recording directory');fs.mkdirSync(output,{recursive:true});
 const server=spawn('python3',['-m','opendots','--config',config,'serve','--port',String(port)],{cwd:root,stdio:['ignore','ignore','ignore']});
 let browser,context;
 try{
  for(let n=0;n<100;n++){if(server.exitCode!==null)throw Error('Service stopped');try{if((await fetch(base+'/api/health')).ok)break;}catch{}await pause(100);}
  browser=await chromium.launch({headless:true,args:['--no-sandbox']});
  context=await browser.newContext({viewport:{width:1440,height:1000},recordVideo:{dir:path.join(output,'raw'),size:{width:1440,height:1000}}});
  page=await context.newPage();origin=Date.now();const errors=[];page.on('pageerror',error=>errors.push(error.message));
  await page.goto(base);await page.locator('.target-card').first().waitFor();
  if((await state()).backend!=='codex'||await page.locator('#sample').isVisible())throw Error('Real Codex backend required');
  await caption('01','Goals that keep working','The React Scout starts from a real scheduled event. Cachetools waits for a report.');mark('overview');await pause(4500);
  await caption('02','A regression becomes an event','Submit a report through the working dashboard. Subscriptions route it to Cachetools.');mark('submit');
  await event('cache','ci.failure','LRU evicts the most recently used item. Investigate the failing upstream tests.');
  await caption('03','One target, one ordered queue','A React follow-up waits behind its earlier repair. Other targets keep moving.');mark('followup');
  await event('react','ci.verify','Verify the retained accessibility repair and reuse persistent target memory.');
  const queued=await waitFor(s=>s.work.some(w=>w.target_id==='react'&&w.status==='queued'));
  await page.locator('#workflows').scrollIntoViewIfNeeded();await pause(2000);
  await page.screenshot({path:path.join(output,'showcase-queue.png')});
  await caption('04','Real Codex investigates','Live source inspection and scoped repair proposals. Planning wait is accelerated in the edited video.');mark('planning-wait');
  const waiting=await waitFor(s=>s.counts.waiting_approval===2);mark('planning-finished');
  for(const work of waiting.work.filter(w=>w.status==='waiting_approval')){const action=work.plan.actions[work.approval_index];if(!['write_file','replace_text'].includes(action.tool))throw Error('Expected production edit');}
  await caption('05','Review the exact change','Each edit waits for its own approval. Tests and validation harnesses stay protected.');
  await page.locator('#approvals').scrollIntoViewIfNeeded();await page.locator('.review-card').first().waitFor();await pause(3500);
  await page.screenshot({path:path.join(output,'showcase-review.png')});mark('approve-react');
  await page.locator('.review-card').filter({has:page.locator('h3',{hasText:'react ·'})}).getByRole('button',{name:'Approve action'}).click();await pause(1800);
  await caption('06','Your approval starts execution','OpenDots applies the local patch and runs the actual configured suite in the sandbox.');mark('approve-cache');
  await page.locator('.review-card').filter({has:page.locator('h3',{hasText:'cache ·'})}).getByRole('button',{name:'Approve action'}).click();
  await page.locator('#workflows').scrollIntoViewIfNeeded();await pause(2000);mark('execution-wait');
  let final;
  while(true){const data=await waitFor(s=>s.counts.completed===3||s.counts.waiting_approval);if(data.counts.completed===3){final=data;break;}await page.locator('.review-card').first().scrollIntoViewIfNeeded();await pause(1500);await page.locator('.review-card').first().getByRole('button',{name:'Approve action'}).click();}
  mark('execution-finished');await caption('07','Verified work, retained evidence','Real React client/server checks and all 220 Cachetools tests pass. The follow-up inherits the repaired branch.');
  await page.locator('#workflows').scrollIntoViewIfNeeded();await page.locator('.workflow-card.completed').first().waitFor();await pause(5500);
  if(await page.locator('html').evaluate(e=>e.scrollWidth>innerWidth))throw Error('Desktop overflow');
  await page.screenshot({path:path.join(output,'showcase-evidence.png')});mark('patch');
  await caption('08','The patch is yours to inspect','A real Git commit and patch remain in the local task workspace. Source workspaces remain untouched.');
  await page.locator('.workflow-card').filter({has:page.locator('h3',{hasText:'React'})}).getByRole('button',{name:'View retained patch'}).first().click();
  await page.locator('#evidence-dialog').waitFor();const patch=await page.locator('#patch-content').textContent();if(!patch.includes('SearchButton')||!patch.includes('+'))throw Error('Retained React patch missing');
  await pause(6000);await page.screenshot({path:path.join(output,'showcase-patch.png')});await page.locator('#close-evidence').click();
  await caption('09','The loop keeps its memory','Three completed jobs. Two retained repair proposals. One verified follow-up.');mark('finale');await page.evaluate(()=>window.scrollTo({top:0,behavior:'smooth'}));await pause(6500);
  await page.locator('#demo-caption').evaluate(e=>e.remove());await page.screenshot({path:path.join(output,'showcase-desktop.png')});mark('end');
  const video=page.video();await context.close();context=null;fs.copyFileSync(await video.path(),path.join(output,'showcase-raw.webm'));
  const mobile=await browser.newContext({viewport:{width:390,height:950}});const mp=await mobile.newPage();mp.on('pageerror',e=>errors.push(e.message));await mp.goto(base);await mp.locator('#completed').filter({hasText:'3'}).waitFor();
  if(await mp.locator('html').evaluate(e=>e.scrollWidth>innerWidth))throw Error('Mobile overflow');await mp.screenshot({path:path.join(output,'showcase-mobile.png'),fullPage:true});await mobile.close();
  const checks=final.audit.filter(a=>a.kind==='action_completed'&&a.detail.result?.name&&typeof a.detail.result.exit_code==='number').map(a=>({target:a.target_id,work_id:a.work_id,...a.detail.result}));
  if(checks.length<3||checks.some(c=>c.exit_code!==0)||errors.length)throw Error('Missing checks or browser errors');
  const react=final.work.filter(w=>w.target_id==='react').sort((a,b)=>a.id-b.id);if(react[1].base_ref!==react[0].branch)throw Error('Follow-up branch continuity lost');
  const report={backend:final.backend,counts:final.counts,checks,javascript_errors:errors,marks,work:final.work,targets:final.targets,
   assertions:['real scheduled Scout','real dashboard-submitted regression event','same-target follow-up queued','two production repairs reviewed in browser','actual configured checks pass','retained patch opened from server','follow-up inherits repaired branch','desktop and mobile without horizontal overflow','no browser JavaScript errors'],
   truth:'Actual Codex-backed service, timer, events, UI approval, sandboxed React/client-server behavior and Cachetools full upstream suite. Captions are editorial overlays. Only waiting intervals are accelerated in the edited video. Controlled faults are local; no upstream publication.'};
  fs.writeFileSync(path.join(output,'showcase-report.json'),JSON.stringify(report,null,2)+'\n');console.log(JSON.stringify({success:true,counts:final.counts,checks:checks.map(c=>c.output.trim().split('\n').pop()),output}));
 }finally{if(context)await context.close();if(browser)await browser.close();server.kill('SIGINT');await new Promise(resolve=>{if(server.exitCode!==null)return resolve();server.once('exit',resolve);setTimeout(()=>{server.kill('SIGKILL');resolve();},5000).unref();});}
})().catch(e=>{console.error(e);process.exit(1);});
