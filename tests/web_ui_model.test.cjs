const test=require('node:test');
const assert=require('node:assert/strict');
const M=require('../src/bananloop/web_ui_static/model.js');
test('projects only DEV-visible allowed fields',()=>{
 const s=M.projected({schema_version:1,campaign_id:'c-123',goal:'Build <better>',status:'RUNNING',engine_stage:'EXECUTE',metric:{name:'quality',direction:'maximize',baseline:.5,champion:.6,target:.8},budget:{experiments:2,experiment_limit:10},dev_points:[{score:.6,experiment_id:2},{score:'nan',experiment_id:3}],hypotheses:[{title:'<script>alert(1)</script>',ordinal:2,status:'ACCEPTED',dev_score:.6}],final_holdout:{secret:'MUST NEVER RENDER'}});
 assert.equal(s.stage,'IMPLEMENT');assert.equal(s.metric.champion,.6);assert.equal(s.points.length,1);assert.ok(!('final_holdout' in s));assert.ok(!JSON.stringify(s).includes('MUST NEVER RENDER'));
 assert.equal(M.safeHtml(s.hypotheses[0].title),'&lt;script&gt;alert(1)&lt;/script&gt;');
});
test('minimize improvements are positive when score gets lower',()=>{assert.equal(M.improvement(100,80,'minimize'),20);assert.equal(M.improvement(100,120,'maximize'),20);assert.equal(M.improvement(0,0,'maximize'),null)});
test('unknown values are not treated as zero',()=>{assert.equal(M.numeric('0.5'),null);assert.equal(M.formatScore(null),'—');assert.equal(M.formatImprovement(null),'—')});
test('stage projection and status classifications',()=>{assert.equal(M.stageMap.ANALYZE,'LEARN');assert.equal(M.statusClass('REJECTED'),'rejected');assert.equal(M.statusClass('ACCEPTED'),'accepted')});
test('invalid snapshot schema rejected',()=>assert.throws(()=>M.projected({schema_version:2,campaign_id:'x'})));
test('event projection ignores invalid cursor rows',()=>{const e=M.eventProjection({events:[{seq:2,type:'STAGE_ENTERED',payload:{stage:'PROPOSE'}},{seq:-1,type:'bad'}]});assert.equal(e.length,1)});