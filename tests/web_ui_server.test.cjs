const test = require('node:test');
const assert = require('node:assert/strict');
const {spawn} = require('node:child_process');
const {createServer} = require('node:net');
const {mkdtemp, mkdir, writeFile, rm} = require('node:fs/promises');
const {tmpdir} = require('node:os');
const {join, resolve} = require('node:path');

const serverPath = resolve(__dirname, '../src/bananloop/web_ui_static/server.mjs');
async function getPort() {
  return new Promise((resolvePort, reject) => {
    const s = createServer();
    s.on('error', reject);
    s.listen(0, '127.0.0.1', () => {const port = s.address().port; s.close(()=>resolvePort(port));});
  });
}
async function ready(url) {
  for (let i=0; i<80; i++) {
    try { const r=await fetch(url); if(r.ok) return; } catch (_) {}
    await new Promise(r=>setTimeout(r,50));
  }
  throw new Error('server did not start');
}

test('local web UI uses read-side BananLoop CLI, denies writes and labels live data', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'bananloop-web-'));
  const pkg = join(dir,'bananloop');
  await mkdir(pkg);
  await writeFile(join(pkg,'__init__.py'),'');
  await writeFile(join(pkg,'cli.py'),`import json, sys\ncmd=sys.argv[1]\nif cmd=='operator-snapshot':\n print(json.dumps({'schema_version':1,'campaign_id':'c-test','goal':'Fixture only','status':'RUNNING','engine_stage':'PROPOSE','display_stage':'PROPOSE','experiment_count':1,'completed_epochs':1,'metric':{'name':'quality','direction':'maximize','baseline':0.5,'champion':0.6,'target':0.8},'budget':{'experiments':1,'experiment_limit':3},'hypotheses':[],'dev_points':[]}))\nelif cmd=='operator-events':\n print(json.dumps({'events':[{'seq':1,'event_type':'STAGE_ENTERED','type':'STAGE_ENTERED','created_at':'2026-01-01T00:00:00+00:00','payload':{'stage':'PROPOSE'}}],'cursor':1,'has_more':False}))\nelse: sys.exit(42)\n`);
  const port = await getPort();
  const python=process.env.BANANLOOP_TEST_PYTHON || (process.platform==='win32'?'python':'python3');
  const child=spawn(process.execPath,[serverPath,'--port',String(port),'--repo',dir,'--campaign-id','c-test','--python',python],{env:{...process.env,PYTHONPATH:[dir,process.env.PYTHONPATH].filter(Boolean).join(process.platform==='win32'?';':':')},stdio:'ignore'});
  try {
    const base=`http://127.0.0.1:${port}`;
    await ready(base+'/api/config');
    const conf=await (await fetch(base+'/api/config')).json();
    assert.deepEqual(conf,{connected:true,mode:'read-only'});
    const snapshot=await (await fetch(base+'/api/snapshot')).json();
    assert.equal(snapshot.campaign_id,'c-test');
    assert.equal(snapshot.metric.champion,0.6);
    const events=await (await fetch(base+'/api/events?after=0')).json();
    assert.equal(events.cursor,1);
    assert.equal((await fetch(base+'/api/events?after=-1')).status,400);
    assert.equal((await fetch(base+'/api/snapshot',{method:'POST'})).status,405);
    assert.equal((await fetch(base+'/not-allowlisted')).status,404);
    const ui=await (await fetch(base+'/')).text();
    assert.match(ui,/HYPOTHESES/);
    assert.match(ui,/LIVE EXPERIMENT/);
    assert.match(ui,/EXPERIMENT STAGES/);
  } finally {
    child.kill();
    await new Promise(resolve => child.once('exit',resolve));
    await rm(dir,{recursive:true,force:true});
  }
});