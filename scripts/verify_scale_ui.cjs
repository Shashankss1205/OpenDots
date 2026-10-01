// Read-only browser verification of the actual retained scale-run database.
// No worker loop is started, no events/decisions are submitted, no UI data is replaced.
const {chromium} = require((process.env.OPENDOTS_PLAYWRIGHT_MODULE || process.env.SPOTS_PLAYWRIGHT_MODULE) || 'playwright');
const {spawn} = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '..');
const directory = path.resolve(process.argv[2]);
const output = path.resolve(process.argv[3]);
const report = JSON.parse(fs.readFileSync(path.join(directory,'report.json')));
const requireTrue = (condition, message) => {if (!condition) throw new Error(message);};
(async () => {
  requireTrue(report.success && report.goal_fanout, 'Verify a successful, complete scale run');
  fs.mkdirSync(output, {recursive:true});
  const code = `from pathlib import Path
from opendots.config import load_config
from opendots.engine import Engine
from opendots.server import make_server
server=make_server(Engine(load_config(Path(${JSON.stringify(path.join(directory,'config.json'))}))),port=0)
print(server.server_address[1],flush=True)
server.serve_forever()
`;
  const server = spawn('python3',['-c',code],{cwd:root,stdio:['ignore','pipe','pipe']});
  let browser;
  try {
    const port = await new Promise((resolve,reject) => {
      let content='';
      server.stdout.on('data', chunk => {content+=chunk; if(content.includes('\n'))resolve(Number(content.split('\n')[0]));});
      server.once('exit', code=>reject(new Error('Read-only server exited '+code)));
      setTimeout(()=>reject(new Error('Server start timeout')),10000).unref();
    });
    const base='http://127.0.0.1:'+port;
    const state=await (await fetch(base+'/api/state')).json();
    requireTrue(state.backend==='codex','Expected actual Codex configuration');
    requireTrue(JSON.stringify(state.counts)===JSON.stringify(report.counts),'UI database counts differ from report');
    requireTrue(state.targets.length===10 && state.event_count===report.event_count,'Wrong target/event counts');
    browser=await chromium.launch({headless:true,args:['--no-sandbox']});
    const errors=[],views=[];
    for (const [name,width,height] of [['desktop',1440,1120],['mobile',390,950]]) {
      const context=await browser.newContext({viewport:{width,height}});
      const page=await context.newPage();
      page.on('pageerror',error=>errors.push(error.message));
      await page.goto(base);
      await page.locator('.target-card').nth(9).waitFor();
      requireTrue(await page.locator('.target-card').count()===10,'Not all targets render');
      requireTrue(await page.locator('#event-target option').count()===10,'Target picker missing targets');
      requireTrue(Number(await page.locator('#completed').textContent())===report.counts.completed,'Completed display differs');
      requireTrue(!(await page.locator('#sample').isVisible()),'Fixture sample visible in real backend');
      requireTrue(!(await page.locator('html').evaluate(e=>e.scrollWidth>window.innerWidth)),'Horizontal overflow on '+name);
      await page.screenshot({path:path.join(output,'scale-'+name+'.png'),fullPage:true});
      views.push({name,width,height,targets:10,horizontal_overflow:false});
      await context.close();
    }
    requireTrue(errors.length===0,'Browser JavaScript errors: '+errors.join('; '));
    fs.writeFileSync(path.join(output,'browser-report.json'),JSON.stringify({success:true,counts:state.counts,event_count:state.event_count,views,javascript_errors:errors,
      truth:'Actual retained SQLite state served through the production HTTP/UI code. Read-only browser inspection; no active worker or fabricated UI data.'},null,2)+'\n');
    console.log(JSON.stringify({success:true,views:views.length,targets:10,javascript_errors:errors.length}));
  } finally {
    if(browser)await browser.close();
    server.kill('SIGTERM');
    await new Promise(resolve=>{if(server.exitCode!==null)return resolve();server.once('exit',resolve);setTimeout(()=>{server.kill('SIGKILL');resolve();},5000).unref();});
  }
})().catch(error=>{console.error(error);process.exit(1);});
