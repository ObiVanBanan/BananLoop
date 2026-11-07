import test from 'node:test';
import assert from 'node:assert/strict';
import {
  defaultPersonalCanvas,decodePersonalCanvas,addPersonalArrow,removePersonalNote,
  saveCanvas,loadCanvas,storageKey
} from '../src/state/personalCanvas.ts';
const graph=['dense','cross','gate-or'];
test('invalid and future versions do not break the canvas',()=>{
  assert.deepEqual(decodePersonalCanvas('{bad',graph),defaultPersonalCanvas());
  assert.deepEqual(decodePersonalCanvas('{"version":999}',graph),defaultPersonalCanvas());
});
test('state is isolated, bounded, and sanitizes positions, notes and edges',()=>{
  const x=decodePersonalCanvas(JSON.stringify({version:1,positions:{dense:{x:120,y:20},ghost:{x:8,y:9},cross:{x:1e80,y:1}},notes:[{id:'note-a',x:3,y:7,text:'x'.repeat(3000)},{id:'note-a',x:10,y:20,text:'duplicate'},{id:'not-note',x:0,y:0,text:'bad'}],arrows:[{id:'arrow-1',source:'dense',target:'note-a'},{id:'arrow-2',source:'dense',target:'ghost'},{id:'arrow-3',source:'dense',target:'dense'}],viewport:{x:5,y:5,zoom:9},railWidth:99999,railCollapsed:true}),graph);
  assert.deepEqual(Object.keys(x.positions),['dense']);
  assert.equal(x.notes.length,1);assert.equal(x.notes[0].text.length,2000);
  assert.equal(x.arrows.length,1);assert.equal(x.viewport,null);
  assert.equal(x.railWidth,360);assert.equal(x.railCollapsed,true);
});
test('personal arrows never create engine dependencies',()=>{
  const state=defaultPersonalCanvas();
  const updated=addPersonalArrow(state,'dense','cross',graph);
  assert.equal(state.arrows.length,0);assert.equal(updated.arrows.length,1);
  assert.equal(addPersonalArrow(updated,'dense','cross',graph),updated);
  assert.equal(addPersonalArrow(updated,'dense','fake',graph),updated);
  assert.equal(addPersonalArrow(updated,'dense','dense',graph),updated);
});
test('removing a note also removes user arrows pointing to it',()=>{
  const canvas={...defaultPersonalCanvas(),notes:[{id:'note-1',x:4,y:9,text:'mine'}],arrows:[{id:'arrow-1',source:'cross',target:'note-1'},{id:'arrow-2',source:'cross',target:'dense'}]};
  const next=removePersonalNote(canvas,'note-1');
  assert.equal(next.notes.length,0);assert.deepEqual(next.arrows.map(a=>a.id),['arrow-2']);
  assert.equal(removePersonalNote(canvas,'dense'),canvas);
});
test('campaign-level browser storage reads/writes round trip',()=>{
  const memory=new Map();const storage={getItem:k=>memory.get(k)??null,setItem:(k,v)=>memory.set(k,v)};
  const initial=defaultPersonalCanvas();initial.positions.dense={x:44,y:55};initial.viewport={x:5,y:9,zoom:.83};
  assert.equal(saveCanvas(storage,'c-01',initial),true);
  assert.equal(memory.has(storageKey('c-01')),true);
  assert.deepEqual(loadCanvas(storage,'c-01',graph),initial);
  assert.deepEqual(loadCanvas(storage,'c-02',graph),defaultPersonalCanvas());
  assert.deepEqual(loadCanvas({getItem(){throw Error('blocked')}},'c-01',graph),defaultPersonalCanvas());
  assert.equal(saveCanvas({setItem(){throw Error('blocked')}},'c-01',initial),false);
});
test('reject giant inputs and unknown graph node references',()=>{
  assert.deepEqual(decodePersonalCanvas(' '.repeat(240001),graph),defaultPersonalCanvas());
  const x=decodePersonalCanvas('{"version":1,"positions":{"unknown":{"x":1,"y":2}}}',graph);
  assert.equal(Object.keys(x.positions).length,0);
});