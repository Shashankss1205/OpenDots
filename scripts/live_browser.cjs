// Real Codex service/UI acceptance. Config must point to a fresh database,
// an actual broken upstream workspace and its protected full-suite check.
const { chromium } = require((process.env.OPENDOTS_PLAYWRIGHT_MODULE || process.env.SPOTS_PLAYWRIGHT_MODULE) || 'playwright');
const { spawn } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '..');
const config = process.argv[2];
const output = path.resolve(process.argv[3] || path.join(root, 'docs/live-testing'));
const port = 8895;
const base = `http://127.0.0.1:${port}`;
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
async function waitForState(predicate) {
  const deadline = Date.now() + 600000;
  while (Date.now() < deadline) {
    const state = await (await fetch(base + '/api/state')).json();
    if (state.counts.failed || state.counts.blocked) throw new Error('Live task failed: ' + JSON.stringify(state.work.map(w => ({status:w.status,error:w.error}))));
    if (predicate(state)) return state;
    await pause(200);
  }
  throw new Error('Live UI state deadline exceeded');
}
(async () => {
  fs.mkdirSync(output, { recursive: true });
  const server = spawn('python3', ['-m', 'opendots', '--config', config, 'serve', '--port', String(port)], {cwd:root,stdio:['ignore','ignore','ignore']});
  let browser, context;
  try {
    let healthy = false;
    for (let n=0;n<100;n++) {
      if (server.exitCode !== null) throw new Error('Server stopped');
      try { healthy=(await fetch(base+'/api/health')).ok; } catch {}
      if (healthy) break;
      await pause(100);
    }
    if (!healthy) throw new Error('Server did not start');
    const waiting = await waitForState(s => s.counts.waiting_approval === 1);
    if (waiting.backend !== 'codex') throw new Error('Live test must use Codex');
    const proposed = waiting.work[0].plan.actions[waiting.work[0].approval_index];
    if (!['replace_text','write_file'].includes(proposed.tool)) throw new Error('Expected a real file proposal');
    browser = await chromium.launch({headless:true,args:['--no-sandbox']});
    context = await browser.newContext({viewport:{width:1440,height:1120},recordVideo:{dir:path.join(output,'video-private'),size:{width:1440,height:1120}}});
    const page = await context.newPage();
    const errors=[];
    page.on('pageerror',error=>errors.push(error.message));
    await page.goto(base);
    const review = page.locator('.review-card');
    await review.waitFor();
    if (await page.locator('#sample').isVisible()) throw new Error('Fixture sample control must be hidden for real Codex');
    const diff = await review.locator('pre').textContent();
    if (!diff.includes('_by_sender') || !diff.includes('+')) throw new Error('Actual production diff missing');
    await page.screenshot({path:path.join(output,'browser-review.png'),fullPage:true});
    await pause(1500);
    await review.getByRole('button',{name:'Approve action'}).click();
    const final = await waitForState(s => s.counts.completed === 1);
    await page.locator('#completed').filter({hasText:'1'}).waitFor();
    await page.screenshot({path:path.join(output,'browser-completed.png'),fullPage:true});
    if (await page.locator('html').evaluate(element=>element.scrollWidth>window.innerWidth)) throw new Error('Desktop overflow');
    await pause(1500);
    const video = page.video();
    await context.close();context=null;
    fs.copyFileSync(await video.path(),path.join(output,'live-codex.webm'));
    const mobile = await browser.newContext({viewport:{width:390,height:950}});
    const mobilePage = await mobile.newPage();
    mobilePage.on('pageerror',error=>errors.push(error.message));
    await mobilePage.goto(base);
    await mobilePage.locator('#completed').filter({hasText:'1'}).waitFor();
    if (await mobilePage.locator('html').evaluate(element=>element.scrollWidth>window.innerWidth)) throw new Error('Mobile overflow');
    await mobilePage.screenshot({path:path.join(output,'browser-mobile.png'),fullPage:true});
    await mobile.close();
    const checks = final.audit.filter(a=>a.kind==='action_completed'&&a.detail.result.name==='upstream').map(a=>a.detail.result);
    if (!checks.length || checks.some(c=>c.exit_code!==0) || errors.length) throw new Error('Actual checks or browser errors failed');
    const report={backend:final.backend,sandbox:final.sandbox,counts:final.counts,action_tool:proposed.tool,checks,javascript_errors:errors,work:final.work,
      truth:'Actual service-generated timer, real Codex proposal, production-source diff, browser approval, sandboxed full upstream suite and retained Git patch. Video starts after the model proposal arrives; no scripted planner or fabricated screenshots.'};
    fs.writeFileSync(path.join(output,'browser-report.json'),JSON.stringify(report,null,2)+'\n');
    console.log(JSON.stringify({success:true,counts:final.counts,checks:checks.map(c=>c.output.trim().split('\n').pop()),javascript_errors:errors.length}));
  } finally {
    if (context) await context.close();
    if (browser) await browser.close();
    server.kill('SIGINT');
    await new Promise(resolve=>{if(server.exitCode!==null)return resolve();server.once('exit',resolve);setTimeout(()=>{server.kill('SIGKILL');resolve();},5000).unref();});
  }
})().catch(error=>{console.error(error);process.exit(1);});
