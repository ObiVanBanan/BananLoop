import test from 'node:test';
import assert from 'node:assert/strict';
import {parseAnswer} from './studio-chat-worker.mjs';

test('normal Russian planning conversation is accepted without fake proposal', () => {
 const result=parseAnswer('Для начала определим метрику качества поиска.');
 assert.deepEqual(result,{reply:'Для начала определим метрику качества поиска.',draft:null});
});

test('structured proposal is accepted, including an optional fenced JSON answer',()=>{
 const raw='\`\`\`json\n{"reply":"Предлагаю три эксперимента.","draft":{"goal":"Optimize retrieval quality","budgets":{"max_experiments":3}}}\n\`\`\`';
 const parsed=parseAnswer(raw);
 assert.equal(parsed.reply,'Предлагаю три эксперимента.');
 assert.equal(parsed.draft.goal,'Optimize retrieval quality');
 assert.equal(parsed.draft.budgets.max_experiments,3);
});

test('empty answer fails rather than inventing a Codex message',()=>{
 assert.throws(()=>parseAnswer('   '),/empty response/);
});
