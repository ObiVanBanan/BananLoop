import test from 'node:test';
import assert from 'node:assert/strict';
import {emptyStudioChats,insertStudioThread,newStudioThread,parseStudioChats,readStudioChats,saveStudioChats,studioChatTitle,updateStudioThread} from '../src/state/studioChats.ts';

test('new chat has no messages, budget draft or previous execution request',()=>{
 const fresh=newStudioThread('chat-123456',42);
 assert.deepEqual(fresh.messages,[]);assert.equal(fresh.draft,null);
 assert.equal(fresh.requestId,null);assert.equal(fresh.launch,null);
 assert.equal(fresh.mode,'full_auto');
});
test('switch, persist and restore independent conversations',()=>{
 let state=emptyStudioChats();
 state=insertStudioThread(state,newStudioThread('chat-123456',12));
 state=updateStudioThread(state,'chat-123456',t=>({...t,title:studioChatTitle('Test goal'),messages:[{role:'user',content:'Test goal'}]}));
 state=insertStudioThread(state,newStudioThread('chat-654321',15));
 const map=new Map();const storage={getItem:key=>map.get(key)??null,setItem:(key,value)=>map.set(key,value)};
 saveStudioChats(storage,state);const restored=readStudioChats(storage);
 assert.equal(restored.activeId,'chat-654321');assert.equal(restored.threads.length,2);
 assert.deepEqual(restored.threads[0].messages,[]);
 assert.deepEqual(restored.threads[1].messages,[{role:'user',content:'Test goal'}]);
});
test('invalid, oversized or future persisted state is safely discarded',()=>{
 assert.equal(parseStudioChats('{').threads.length,0);
 assert.equal(parseStudioChats('x'.repeat(450001)).threads.length,0);
 assert.equal(parseStudioChats('{"version":2,"threads":[]}').threads.length,0);
 assert.equal(parseStudioChats('{"version":1,"activeId":"x","threads":[]}').activeId,'');
});
test('history entries are capped and malformed budget plans cannot be persisted as valid',()=>{
 const messages=Array.from({length:100},(_,i)=>({role:'user',content:`Message ${i}`}));
 const raw=JSON.stringify({version:1,activeId:'chat-123456',threads:[{
  id:'chat-123456',title:'Y',createdAt:1,updatedAt:2,messages,
  draft:{goal:'Fix the quality of things',budgets:{max_experiments:-1},missing:[],ready_for_bootstrap:true},
  mode:'supervised',requestId:'',launch:null
 }]});
 const out=parseStudioChats(raw);
 assert.equal(out.threads[0].messages.length,60);
 assert.equal(out.threads[0].draft,null);
 assert.equal(out.threads[0].mode,'supervised');
});