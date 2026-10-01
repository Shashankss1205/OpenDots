// Optional browser QA/recording utility. npm install --prefix tools @playwright/test
// PLAYWRIGHT_BROWSERS_PATH=... OPENDOTS_PLAYWRIGHT_MODULE=... node scripts/record_demos.cjs
const {chromium}=require((process.env.OPENDOTS_PLAYWRIGHT_MODULE || process.env.SPOTS_PLAYWRIGHT_MODULE)||'playwright');
const {spawn,spawnSync}=require('child_process');
const fs=require('fs');
const path=require('path');
const crypto=require('crypto');
const root=path.resolve(__dirname,'..');
const output=path.join(root,'docs','demos');fs.mkdirSync(output,{recursive:true});
const pause=ms=>new Promise(resolve=>setTimeout(resolve,ms));
async function waitText(page,id,value){let deadline=Date.now()+25000;while(Date.now()<deadline){if(await page.locator('#'+id).textContent()===value)return;await pause(100);}throw new Error(`Timed out: ${id}=${value}`);}
async function api(base,route,body,headers={}){let response=await fetch(base+route,{method:'POST',headers:{'Content-Type':'application/json','X-OpenDots-Request':'dashboard',...headers},body:JSON.stringify(body)});let data=await response.json();if(!response.ok)throw new Error(JSON.stringify(data));return data;}

(async()=>{
  const browser=await chromium.launch({headless:true,args:['--no-sandbox']});
  const results=[];
  try {
    for(const [i,mode] of ['kubernetes','react','combined'].entries()){
      const directory=path.join(root,'.opendots','recordings',`${mode}-${Date.now()}`);
      const generated=spawnSync('python3',['scripts/make_demo_config.py',mode,directory],{cwd:root,encoding:'utf8'});
      if(generated.status!==0)throw new Error(generated.stderr);
      const config=generated.stdout.trim(),port=8871+i,base=`http://127.0.0.1:${port}`;
      const secret='public-fixture-webhook-secret';
      const server=spawn('python3',['-m','opendots','--config',config,'serve','--port',String(port)],{cwd:root,env:{...process.env,OPENDOTS_GITHUB_WEBHOOK_SECRET:secret},stdio:['ignore','pipe','pipe']});
      let serverLog='';server.stdout.on('data',chunk=>serverLog+=chunk);server.stderr.on('data',chunk=>serverLog+=chunk);
      let context;
      try {
        let healthy=false;for(let n=0;n<100;n++){if(server.exitCode!==null)throw new Error(serverLog);try{healthy=(await fetch(base+'/api/health')).ok;}catch{}if(healthy)break;await pause(100);}if(!healthy)throw new Error('Server did not start');
        context=await browser.newContext({viewport:{width:1440,height:1120},recordVideo:{dir:path.join(directory,'video'),size:{width:1440,height:1120}}});
        const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
        await page.goto(base);await pause(800);
        if(mode!=='react'){
          // Synthetic GitHub sender; the signature verification/normalizer/runtime are real.
          const body={action:'opened',repository:{full_name:'kubernetes/kubernetes'},issue:{number:1,title:'Deployment fixture requests zero replicas',labels:[]}};
          const signature='sha256='+crypto.createHmac('sha256',secret).update(JSON.stringify(body)).digest('hex');
          await api(base,'/api/webhooks/github',body,{'X-Hub-Signature-256':signature,'X-GitHub-Delivery':`${mode}-fixture`,'X-GitHub-Event':'issues'});
        }
        if(mode==='combined'){
          await api(base,'/api/events',{id:'follow-up',type:'github.discussion.created',priority:40,payload:{repo:'kubernetes/kubernetes',title:'Follow up after the proposal'}});
          await api(base,'/api/events',{id:'star',type:'github.star',payload:{repo:'facebook/react'}});
          await api(base,'/api/events',{id:'noise',type:'unrelated.news',payload:{title:'No subscribed target'}});
        }
        await waitText(page,'waiting',mode==='combined'?'2':'1');
        await page.screenshot({path:path.join(output,`${mode}-review.png`),fullPage:true});
        await pause(1200);
        if(mode==='combined'){
          const reactReview=page.locator('.review-card').filter({hasText:'react · write_file'});
          await reactReview.getByRole('button',{name:'Approve action'}).click();
          await waitText(page,'completed','1');
          await waitText(page,'waiting','1');
          // Explicitly verify one target completes while the other retains its barrier.
          const state=await(await fetch(base+'/api/state')).json();
          if(state.counts.queued!==1||state.counts.waiting_approval!==1)throw new Error('Per-target barrier failed');
          await pause(1000);
        }
        await page.getByRole('button',{name:'Approve action'}).first().click();
        await waitText(page,'completed',mode==='combined'?'3':'1');
        await page.screenshot({path:path.join(output,`${mode}-completed.png`),fullPage:true});
        if(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth))throw new Error('Desktop overflow');
        await pause(1200);
        const video=page.video();await context.close();context=null;
        fs.copyFileSync(await video.path(),path.join(output,`${mode}.webm`));
        const state=await(await fetch(base+'/api/state')).json();
        const report={mode,counts:state.counts,event_count:state.event_count,backend:state.backend,sandbox:state.sandbox,javascript_errors:errors,checks:state.audit.filter(a=>a.kind==='action_completed'&&a.detail.result.exit_code!==undefined).map(a=>a.detail.result),artifacts:state.targets.flatMap(t=>t.state.artifacts||[])};
        if(errors.length)throw new Error(errors.join('\n'));
        fs.writeFileSync(path.join(output,`${mode}-report.json`),JSON.stringify(report,null,2)+'\n');
        results.push({mode,counts:state.counts,event_count:state.event_count,javascript_errors:errors.length});
      } finally {
        if(context)await context.close();
        server.kill('SIGINT');
        await new Promise(resolve=>{if(server.exitCode!==null)return resolve();server.once('exit',resolve);setTimeout(()=>{server.kill('SIGKILL');resolve();},5000).unref();});
      }
    }
    console.log(JSON.stringify(results));
  }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exit(1);});
