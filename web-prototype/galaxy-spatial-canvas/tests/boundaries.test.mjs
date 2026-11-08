import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
const source=readFileSync(new URL('../src/Galaxy.tsx',import.meta.url),'utf8');
const store=readFileSync(new URL('../src/state/personalCanvas.ts',import.meta.url),'utf8');
test('read-only Engine graph has no mutation endpoints and no connection handler',()=>{
  assert.match(source,/nodesConnectable=\{false\}/);
  assert.match(source,/nodesDeletable=\{false\}/);
  assert.doesNotMatch(source,/onConnect=/);
  assert.doesNotMatch(source,/\/graph\/commands|fetch\s*\(/);
});
test('Engine edges are assembled independently of personal arrows',()=>{
  assert.match(source,/const researchEdges=useMemo\(\(\)=>edgesFromFixture\(fixture\)/);
  assert.match(source,/const personalEdges=useMemo/);
  assert.match(source,/\[\.\.\.researchEdges,\.\.\.personalEdges\]/);
  assert.match(store,/storageKey=\(campaignId:string\)/);
});
test('gesture hooks and status semantics remain explicit',()=>{
  assert.match(source,/zoomOnScroll zoomOnDoubleClick=\{false\} panOnDrag/);
  assert.match(source,/event\.detail===2/);
  assert.match(source,/onNodeDragStop=\{onNodeDragStop\}/);
});