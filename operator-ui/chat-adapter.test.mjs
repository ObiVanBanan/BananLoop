import assert from 'node:assert/strict';
import test from 'node:test';
import { buildChatPrompt, createChatAdapter, parseChatCommand, parseOperatorChatOutput } from './chat-adapter.mjs';

test('accepts only explicit /chat input and bounds message size', () => {
  assert.equal(parseChatCommand('/chat Please explain this status'), 'Please explain this status');
  assert.throws(() => parseChatCommand('hello'), /Use \/chat/);
  assert.throws(() => parseChatCommand('/chat ' + 'x'.repeat(12_001)), /limit/);
});

test('chat prompt contains only goal, stage/status, DEV metrics and budget', () => {
  const prompt = buildChatPrompt('Explain progress', {
    goal: 'Reduce latency', stage: 'EVALUATE', status: 'RUNNING',
    metricName: 'dev_score', baseline: 0.42, champion: 0.51,
    budgetAgent: '2/8', budgetEval: '3/12',
    events: ['private event payload'], validation: { secret: 'validation secret' },
    holdout: { score: 0.99 }, repo: 'C:/secret/project',
  });
  assert.match(prompt, /Goal: Reduce latency/);
  assert.match(prompt, /DEV metric dev_score: baseline 0\.42000; champion 0\.51000/);
  assert.match(prompt, /Budget: agent 2\/8; evaluator 3\/12/);
  assert(!prompt.includes('private event payload'));
  assert(!prompt.includes('validation secret'));
  assert(!prompt.includes('0.99'));
  assert(!prompt.includes('C:/secret/project'));
  assert.match(prompt, /intent must be exactly "pause"/);
  assert.match(prompt, /separate operator confirmation/);
});

test('structured parser accepts only pause or null and rejects unknown output', () => {
  assert.deepEqual(parseOperatorChatOutput('{"intent":"pause","message":"I propose pausing."}'), {
    valid: true, intent: 'pause', message: 'I propose pausing.',
  });
  assert.deepEqual(parseOperatorChatOutput('{"intent":null,"message":"Still running."}'), {
    valid: true, intent: null, message: 'Still running.',
  });
  for (const raw of [
    'not json',
    '{"intent":"resume","message":"resume"}',
    '{"intent":"pause","message":"pause","control":true}',
    '{"intent":"pause","message":""}',
  ]) assert.equal(parseOperatorChatOutput(raw).valid, false);
});

test('UI adapter connects lazily and uses only its injected fake client', async () => {
  const calls = [];
  const fake = {
    async connect() { calls.push('connect'); },
    async sendMessage(prompt, options) { calls.push({ prompt, options }); return { status: 'completed', text: '{"intent":null,"message":"ready"}' }; },
    close() { calls.push('close'); },
  };
  const adapter = createChatAdapter({ clientFactory: () => fake });
  assert.equal(calls.length, 0);
  const result = await adapter.send('/chat Give me a short summary', {
    goal: 'Reduce latency', stage: 'EVALUATE', status: 'RUNNING',
    metricName: 'dev', baseline: 0.4, champion: 0.5,
    budgetAgent: '1/4', budgetEval: '2/8', events: ['must not be sent'],
  });
  assert.equal(result.status, 'completed');
  assert.equal(result.intent, null);
  assert.equal(calls[0], 'connect');
  assert.match(calls[1].prompt, /Give me a short summary/);
  assert(!calls[1].prompt.includes('must not be sent'));
  assert.deepEqual(calls[1].options, { structured: true });
  adapter.close();
  assert.equal(calls.at(-1), 'close');
});

console.log('operator-ui chat adapter: OK');
