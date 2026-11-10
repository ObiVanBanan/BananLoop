import test from 'node:test';
import assert from 'node:assert/strict';
import {toGalaxyFixture,statusFromEngine} from '../src/domain/live.ts';
const node=(id,status='PENDING',blockers=[])=>({id,status,blockers,phase:'EXPLORATION',ready:status==='READY',revision:1});
test('v2 real graph preserves directional typed edges and AND/OR group semantics',()=>{
 const live={schema_version:1,source_schema:2,campaign_id:'c-abc',goal:'goal',status:'RUNNING',revision:4,
  nodes:[node('a','COMPLETED'),node('b','READY'),node('c','PENDING',['requires:a'])],
  edges:[{source:'a',target:'b',type:'REQUIRES'},{source:'b',target:'c',type:'RELATED_TO'}],
  groups:[{id:'branch',owner:'c',kind:'AND',members:['a','b']}],partial:false,capabilities:{graph:true,reasons:true,engine_writes:false}};
 const f=toGalaxyFixture(live);
 assert.equal(f.campaignId,'c-abc');assert.equal(f.revision,4);
 assert.equal(f.nodes[0].status,'VERIFIED');assert.equal(f.nodes[2].status,'BLOCKED');
 assert.equal(f.gates.length,1);assert.equal(f.gates[0].kind,'AND');
 assert.equal(f.edges.length,5);assert.equal(f.edges[0].source,f.nodes[0].id);assert.equal(f.edges[0].target,f.nodes[1].id);
 assert.equal(f.edges.at(-1).source,f.gates[0].id);assert.equal(f.edges.at(-1).target,f.nodes[2].id);
});
test('v1 does not infer links or claim verification for completed experiment',()=>{
 const f=toGalaxyFixture({schema_version:1,source_schema:1,campaign_id:'legacy',goal:'Old campaign',status:'COMPLETED',revision:5,
  nodes:[node('exp-1','COMPLETED')],edges:[],groups:[],partial:false,
  capabilities:{graph:false,reasons:false,engine_writes:false}});
 assert.equal(f.nodes[0].status,'COMPLETED');assert.equal(f.edges.length,0);assert.equal(f.gates.length,0);
});
test('ready is not running, and paused/failed states are distinct',()=>{
 assert.equal(statusFromEngine(node('a','READY'),2),'READY');
 assert.equal(statusFromEngine(node('a','PAUSED'),2),'PAUSED');
 assert.equal(statusFromEngine(node('a','FAILED'),2),'FAILED');
});
test('unknown references and unknown edge types never become fake graph links',()=>{
 const view={schema_version:1,source_schema:2,campaign_id:'c-id',goal:'x',status:'RUNNING',revision:1,nodes:[node('a'),node('b')],edges:[{source:'a',target:'ghost',type:'REQUIRES'},{source:'a',target:'b',type:'UNKNOWN'}],groups:[],partial:false,capabilities:{graph:true,reasons:true,engine_writes:false}};
 assert.equal(toGalaxyFixture(view).edges.length,0);
});