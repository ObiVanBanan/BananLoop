import { randomBytes, randomUUID } from 'node:crypto';
import { beginControlObservation } from './control-observation.mjs';

const TOKEN = /^[a-f0-9]{12}$/;
export const PAUSE_EFFECT = 'Request PAUSE: ask the supervisor to cancel active managed local work, keep the last committed campaign/champion state, and preserve uncertain work as uncertain. The campaign remains resumable and needs an explicit /resume; this does not terminally stop it.';

export function parsePauseDecision(raw) {
  const match = /^\/(confirm|reject)-pause\s+([a-f0-9]{12})$/i.exec(String(raw ?? '').trim());
  if (!match) return null;
  return { decision: match[1].toLowerCase(), token: match[2].toLowerCase() };
}

export function createChatPauseFlow({
  campaignId,
  chat,
  controlClient,
  now = Date.now,
  tokenFactory = () => randomBytes(6).toString('hex'),
  requestIdFactory = () => `chat-pause:${randomUUID()}`,
  ttlMs = 30_000,
} = {}) {
  if (!campaignId || !chat || !controlClient) throw new Error('Chat pause flow requires a campaign, chat and typed control client');
  let pending = null;

  function expire() {
    if (pending && now() >= pending.expiresAt) {
      pending = null;
      return true;
    }
    return false;
  }

  return {
    get pending() { expire(); return pending ? { token: pending.token, expiresAt: pending.expiresAt } : null; },
    expireProposal() { return expire(); },
    async chatMessage(rawCommand, snapshot) {
      const response = await chat.send(rawCommand, snapshot);
      if (response.status === 'invalid') return { status: 'invalid-output', message: response.reason || 'Codex output was invalid.' };
      if (response.status !== 'completed') return response;
      if (!response.valid) return { status: 'invalid-output', message: response.reason };
      if (response.intent === null) return { status: 'answer', message: response.message };
      if (response.intent !== 'pause') return { status: 'invalid-output', message: 'Codex returned an unsupported intent.' };
      expire();
      if (pending) return { status: 'proposal-pending', token: pending.token, expiresAt: pending.expiresAt };
      if (snapshot?.stale || snapshot?.connected !== true) {
        return { status: 'unavailable', message: response.message, effect: PAUSE_EFFECT, observedStatus: 'STALE/DISCONNECTED' };
      }
      if (!['READY', 'RUNNING'].includes(snapshot?.status)) {
        return { status: 'unavailable', message: response.message, effect: PAUSE_EFFECT, observedStatus: snapshot?.status ?? 'UNKNOWN' };
      }
      const token = tokenFactory();
      if (!TOKEN.test(token)) throw new Error('Pause confirmation token generator returned an invalid token');
      pending = {
        token,
        requestId: requestIdFactory(),
        campaignId,
        fromStatus: snapshot.status,
        expiresAt: now() + ttlMs,
      };
      return { status: 'proposal', message: response.message, effect: PAUSE_EFFECT, token, expiresAt: pending.expiresAt };
    },
    async decide(rawCommand, snapshot) {
      const parsed = parsePauseDecision(rawCommand);
      if (!parsed) return { status: 'invalid-command', message: 'Use /confirm-pause <token> or /reject-pause <token>.' };
      if (expire()) return { status: 'expired', message: 'Pause proposal expired; campaign state was not changed.' };
      if (!pending || pending.campaignId !== campaignId || parsed.token !== pending.token) {
        return { status: 'invalid-token', message: 'No pause action was taken; token is invalid or no proposal is pending.' };
      }
      if (parsed.decision === 'reject') {
        pending = null;
        return { status: 'rejected', message: 'Pause proposal rejected; campaign state was not changed.' };
      }
      if (snapshot?.stale || snapshot?.connected !== true) {
        pending = null;
        return { status: 'unavailable', message: 'Campaign snapshot is stale or disconnected; proposal discarded without action.' };
      }
      if (snapshot?.campaignId !== campaignId) {
        pending = null;
        return { status: 'campaign-mismatch', message: 'Campaign selection changed; proposal discarded without action.' };
      }
      if (!['READY', 'RUNNING'].includes(snapshot?.status)) {
        pending = null;
        return { status: 'unavailable', message: `Pause no longer applies from ${snapshot?.status ?? 'UNKNOWN'}; campaign state was not changed.` };
      }
      if (snapshot.status !== pending.fromStatus) {
        const previousStatus = pending.fromStatus;
        pending = null;
        return { status: 'stale-status', message: `Campaign status changed from ${previousStatus} to ${snapshot.status}; proposal discarded without action.` };
      }
      const action = pending;
      pending = null;
      const receipt = await controlClient.requestPause(action.requestId);
      return {
        status: 'accepted',
        requestId: action.requestId,
        receipt,
        track: beginControlObservation('pause', action.fromStatus, action.requestId, now()),
        effect: PAUSE_EFFECT,
      };
    },
    clear() { pending = null; },
  };
}
