import assert from 'node:assert/strict';
import test from 'node:test';
import { createChatPauseFlow, PAUSE_EFFECT, parsePauseDecision } from './chat-pause-flow.mjs';
import { observeControl } from './control-observation.mjs';

const snapshot = (status = 'RUNNING', campaignId = 'campaign-1') => ({ campaignId, status, connected: true });

function fixture({ intent = 'pause', status = 'RUNNING', ttlMs = 30_000 } = {}) {
  let clock = 1000;
  const calls = [];
  const chat = { async send(...args) {
    calls.push({ kind: 'chat', args });
    return { status: 'completed', valid: true, intent, message: intent ? 'I propose pausing now.' : 'The campaign is running.' };
  } };
  const controlClient = { async requestPause(requestId) {
    calls.push({ kind: 'control', requestId });
    return { requestId, output: 'accepted' };
  } };
  const flow = createChatPauseFlow({
    campaignId: 'campaign-1', chat, controlClient, ttlMs, now: () => clock,
    tokenFactory: () => 'a1b2c3d4e5f6', requestIdFactory: () => 'chat-pause:stable-123',
  });
  return { flow, calls, snapshot: snapshot(status), setTime(value) { clock = value; } };
}

test('natural-language pause creates an exact proposal and performs no control before confirmation', async () => {
  const f = fixture();
  const proposal = await f.flow.chatMessage('/chat please pause this campaign', f.snapshot);
  assert.equal(proposal.status, 'proposal');
  assert.equal(proposal.effect, PAUSE_EFFECT);
  assert.equal(proposal.token, 'a1b2c3d4e5f6');
  assert.equal(proposal.expiresAt, 31_000);
  assert.equal(f.calls.filter(call => call.kind === 'control').length, 0);
  assert.deepEqual(parsePauseDecision('/confirm-pause a1b2c3d4e5f6'), { decision: 'confirm', token: 'a1b2c3d4e5f6' });
});

test('exact confirmation invokes typed pause once with stable request ID and returns observation track', async () => {
  const f = fixture();
  await f.flow.chatMessage('/chat pause', f.snapshot);
  const result = await f.flow.decide('/confirm-pause a1b2c3d4e5f6', f.snapshot);
  assert.equal(result.status, 'accepted');
  assert.equal(result.requestId, 'chat-pause:stable-123');
  assert.equal(result.track.kind, 'pause');
  assert.equal(result.track.fromStatus, 'RUNNING');
  assert.equal(observeControl(result.track, 'RUNNING', 1000).track.phase, 'pending');
  assert.match(observeControl(result.track, 'PAUSED_USER', 1000).notice, /campaign is PAUSED_USER/);
  assert.deepEqual(f.calls.filter(call => call.kind === 'control').map(call => call.requestId), ['chat-pause:stable-123']);
  assert.equal((await f.flow.decide('/confirm-pause a1b2c3d4e5f6', f.snapshot)).status, 'invalid-token');
  assert.equal(f.calls.filter(call => call.kind === 'control').length, 1);
});

test('reject, invalid token and invalid command never invoke control', async () => {
  const f = fixture();
  await f.flow.chatMessage('/chat pause', f.snapshot);
  assert.equal((await f.flow.decide('/confirm-pause 000000000000', f.snapshot)).status, 'invalid-token');
  assert.equal((await f.flow.decide('/reject-pause a1b2c3d4e5f6', f.snapshot)).status, 'rejected');
  assert.equal((await f.flow.decide('/confirm-pause a1b2c3d4e5f6', f.snapshot)).status, 'invalid-token');
  assert.equal((await f.flow.decide('/confirm-pause token', f.snapshot)).status, 'invalid-command');
  assert.equal(f.calls.filter(call => call.kind === 'control').length, 0);
});

test('expired proposal and changed campaign status do not invoke control', async () => {
  const expired = fixture({ ttlMs: 500 });
  await expired.flow.chatMessage('/chat pause', expired.snapshot);
  expired.setTime(1500);
  assert.equal((await expired.flow.decide('/confirm-pause a1b2c3d4e5f6', expired.snapshot)).status, 'expired');
  assert.equal(expired.calls.filter(call => call.kind === 'control').length, 0);

  const changed = fixture();
  await changed.flow.chatMessage('/chat pause', changed.snapshot);
  assert.equal((await changed.flow.decide('/confirm-pause a1b2c3d4e5f6', snapshot('PAUSED_USER'))).status, 'unavailable');
  assert.equal(changed.calls.filter(call => call.kind === 'control').length, 0);

  const transitioned = fixture();
  await transitioned.flow.chatMessage('/chat pause', transitioned.snapshot);
  assert.equal((await transitioned.flow.decide('/confirm-pause a1b2c3d4e5f6', snapshot('READY'))).status, 'stale-status');
  assert.equal(transitioned.calls.filter(call => call.kind === 'control').length, 0);
});

test('non-pause, unavailable status and invalid model output cannot create controls', async () => {
  const answer = fixture({ intent: null });
  assert.equal((await answer.flow.chatMessage('/chat how is it going', answer.snapshot)).status, 'answer');
  assert.equal(answer.calls.filter(call => call.kind === 'control').length, 0);

  const unavailable = fixture({ status: 'PAUSED_USER' });
  assert.equal((await unavailable.flow.chatMessage('/chat pause', unavailable.snapshot)).status, 'unavailable');
  assert.equal(unavailable.calls.filter(call => call.kind === 'control').length, 0);

  const invalid = fixture();
  invalid.flow.clear();
  const flow = createChatPauseFlow({
    campaignId: 'campaign-1', chat: { async send() { return { status: 'invalid', valid: false }; } },
    controlClient: { async requestPause() { throw new Error('must not be called'); } },
  });
  assert.equal((await flow.chatMessage('/chat pause', invalid.snapshot)).status, 'invalid-output');
  assert.equal(flow.pending, null);

  const disconnected = fixture();
  assert.equal((await disconnected.flow.chatMessage('/chat pause', { ...disconnected.snapshot, connected: undefined })).status, 'unavailable');
  assert.equal(disconnected.calls.filter(call => call.kind === 'control').length, 0);
});

console.log('operator-ui chat pause flow: OK');
