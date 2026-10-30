import assert from 'node:assert/strict';
import { createCampaignClient, normalizeSnapshot, normalizeEventPage, validateCampaignId, matchesPendingPivot } from './campaign.mjs';
import { renderLayout } from './layout.mjs';

const raw = {
  schema_version: 1, campaign_id: 'campaign-1', revision: 7, goal: 'Improve measured retrieval',
  status: 'RUNNING', engine_stage: 'EVALUATE', display_stage: 'EVALUATE',
  completed_epochs: 2, attempt_count: 3, experiment_count: 3,
  metric: { name: 'quality', direction: 'maximize', baseline: 0.5, champion: 0.7 },
  dev_points: [{ evaluation_id: 1, score: 0.5 }, { evaluation_id: 2, score: 0.7 }],
  hypotheses: [{ ordinal: 1, title: 'Better retrieval', dev_score: 0.7, status: 'PROMOTED' }],
  composites: [{ id: 'combo-1', status: 'ACTIVE', current_step: 1, max_steps: 2, steps: [
    { step_order: 1, title: 'First edit', dev_score: 0.6, status: 'INTERMEDIATE' },
  ] }],
  pivot_review: { status: 'PENDING', pause_reason: 'Awaiting operator approval', integrity_valid: true,
    proposal: { proposal_id: 'pivot-012345678901234567890123', digest: 'a'.repeat(64), spec_hash: 'b'.repeat(64),
      trigger_experiment_ordinal: 3, current_architecture: 'Single stage', proposed_architecture: 'Bounded two stage',
      rationale: 'Recall has plateaued.', expected_effect: 'Improve DEV recall.', changed_scope: ['src/retrieval.py'],
      resources: { additional_agent_calls: 2, additional_evaluator_calls: 1, additional_evaluation_units: 3 } }, decision: null },
  budget: { agent_calls: 4, agent_call_limit: 20, evaluator_calls: 3, evaluator_call_limit: 10, experiment_limit: 5 },
  stale: false, missing: {}, updated_at: '2026-10-29T00:00:00Z',
};
const view = normalizeSnapshot(raw);
assert.equal(view.goal, raw.goal);
assert.equal(view.epoch, 2);
assert.equal(view.stage, 'EVALUATE');
assert.equal(view.scores.length, 2);
assert.equal(view.hypotheses[0].name, 'Better retrieval');
assert.equal(view.composites[0].steps[0].status, 'INTERMEDIATE');
assert.equal(view.pivotReview.proposal.digest, 'a'.repeat(64));
assert.deepEqual(view.pivotReview.proposal.changedScope, ['src/retrieval.py']);
const pendingView = { ...view, status: 'PAUSED_NEEDS_INPUT' };
assert.equal(matchesPendingPivot(pendingView, 'campaign-1', 'pivot-012345678901234567890123', 'a'.repeat(64)), true);
assert.equal(matchesPendingPivot(pendingView, 'campaign-1', 'pivot-012345678901234567890123', 'd'.repeat(64)), false);
assert.equal(matchesPendingPivot(view, 'campaign-1', 'pivot-012345678901234567890123', 'a'.repeat(64)), false);
const page = normalizeEventPage({ events: [{ seq: 6, type: 'ACTION_STARTED' }, { seq: 7, type: 'ACTION_COMPLETED' }], cursor: 7, has_more: false }, 5);
assert.equal(page.events.length, 2);
view.events = page.events.map(event => event.text);
const wide = renderLayout(view, 120).join('\n');
assert(wide.includes('Improve measured retrieval'));
assert(wide.includes('ENGINE EVALUATE'));
assert(wide.includes('quality | baseline 0.500 | champion 0.700'));
assert(wide.includes('Better retrieval | 0.700 | PROMOTED'));
assert(wide.includes('COMPOSITE SEQUENCES'));
assert(wide.includes('1. First edit | 0.600 | INTERMEDIATE'));
assert(wide.includes('#7 ACTION_COMPLETED'));
assert(wide.includes('ARCHITECTURE PIVOT'));
assert(wide.includes('Single stage'));
assert(wide.includes('Bounded two stage'));
assert(wide.includes('evaluations +1'));
assert(!wide.includes('SYNTHETIC DEMO'));
const stale = renderLayout({ ...view, stale: true }, 60).join('\n');
assert(stale.includes('DATA STALE'));
const disconnected = renderLayout(view, 60, { connection: 'disconnected', eventLag: true }).join('\n');
assert(disconnected.includes('DATA DISCONNECTED'));
assert.throws(() => normalizeSnapshot({ ...raw, schema_version: 99 }));
assert.throws(() => normalizeEventPage({ events: [{ seq: 4, type: 'OLD' }], cursor: 4 }, 5));
assert.throws(() => normalizeEventPage({ events: [{ seq: 6, type: 'NEW' }], cursor: 7 }, 5));
assert.throws(() => validateCampaignId('x; echo BAD'));

const calls = [];
const spawned = [];
const fakeExecute = (program, argv, options, callback) => {
  calls.push({ program, argv, options });
  const isEvents = argv.includes('operator-events');
  callback(null, JSON.stringify(isEvents ? { events: [], cursor: 7, latest_seq: 7, has_more: false } : raw), '');
};
const fakeSpawn = (program, argv, options) => {
  spawned.push({ program, argv, options });
  return { unref() {}, once(event, callback) { if (event === 'spawn') queueMicrotask(callback); return this; } };
};
const client = createCampaignClient({ repo: 'C:/research/project', campaignId: 'campaign-1', python: 'python', execute: fakeExecute, spawnProcess: fakeSpawn });
assert.equal((await client.readSnapshot()).campaign_id, 'campaign-1');
assert.equal((await client.readEvents(7)).cursor, 7);
assert(calls.every(call => call.options.shell === false));
assert(calls.every(call => call.argv.includes('campaign-1')));
assert(calls[1].argv.includes('--after'));
assert.equal(calls[1].argv[calls[1].argv.indexOf('--after') + 1], '7');
const pause = await client.requestPause('pause-123');
assert.equal(pause.requestId, 'pause-123');
assert(calls[2].argv.includes('pause'));
assert(calls[2].argv.includes('--request-id'));
assert.equal(calls[2].argv[calls[2].argv.indexOf('--request-id') + 1], 'pause-123');
assert.equal(calls[2].options.shell, false);
await client.requestPivotDecision({
  decision: 'reject', proposalId: 'pivot-012345678901234567890123', digest: 'a'.repeat(64),
  rejectionAction: 'change_conditions', requestId: 'pivot-decision-1',
});
assert(calls[3].argv.includes('pivot-decision'));
assert.equal(calls[3].argv[calls[3].argv.indexOf('--proposal-id') + 1], 'pivot-012345678901234567890123');
assert.equal(calls[3].argv[calls[3].argv.indexOf('--digest') + 1], 'a'.repeat(64));
assert.equal(calls[3].argv[calls[3].argv.indexOf('--on-reject') + 1], 'change_conditions');
assert.equal(calls[3].options.shell, false);
assert.throws(() => client.requestPivotDecision({
  decision: 'reject', proposalId: 'pivot-012345678901234567890123', digest: 'a'.repeat(64),
}));
const resume = await client.startResume('resume-456');
assert.equal(resume.requestId, 'resume-456');
assert(spawned[0].argv.includes('resume'));
assert(spawned[0].argv.includes('--request-id'));
assert.equal(spawned[0].argv[spawned[0].argv.indexOf('--request-id') + 1], 'resume-456');
assert.equal(spawned[0].options.shell, false);
assert.equal(spawned[0].options.detached, true);

const failingClient = createCampaignClient({
  repo: 'C:/research/project', campaignId: 'campaign-1', python: 'python',
  execute: (_program, _argv, _options, callback) => callback(Object.assign(new Error('failed'), { code: 'EIO' }), '', 'provider-token=must-not-render'),
});
await assert.rejects(failingClient.requestPause('pause-error'), error => {
  assert.equal(error.message, 'BananLoop control failed (EIO)');
  assert(!error.message.includes('provider-token'));
  return true;
});
console.log('operator-ui campaign adapter: OK');
