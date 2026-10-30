import { execFile, spawn } from 'node:child_process';
import { randomUUID } from 'node:crypto';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const projectRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const sourceDir = resolve(projectRoot, 'src');
const idPattern = /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/;

export function validateCampaignId(id) {
  if (!idPattern.test(id ?? '')) throw new Error('Invalid campaign ID');
  return id;
}

function parseJSON(stdout, expected) {
  if (stdout.length > 2_000_000) throw new Error('Campaign response too large');
  const value = JSON.parse(stdout);
  if (!value || typeof value !== 'object' || Array.isArray(value) || !expected(value)) {
    throw new Error('Invalid campaign response');
  }
  return value;
}

export function normalizeSnapshot(raw) {
  if (raw.schema_version !== 1 || typeof raw.campaign_id !== 'string' || typeof raw.revision !== 'number') {
    throw new Error('Unsupported operator snapshot');
  }
  const budget = raw.budget || {};
  const metric = raw.metric || {};
  return {
    demo: false,
    campaignId: raw.campaign_id,
    revision: raw.revision,
    goal: raw.goal ?? null,
    stage: raw.display_stage ?? null,
    engineStage: raw.engine_stage ?? null,
    status: raw.status ?? null,
    stopReason: raw.stop_reason ?? null,
    epoch: raw.completed_epochs ?? null,
    attempts: raw.attempt_count ?? null,
    experiments: raw.experiment_count ?? null,
    experimentLimit: budget.experiment_limit ?? null,
    budget: `${budget.agent_calls ?? '--'}/${budget.agent_call_limit ?? '--'} agent calls | ${budget.evaluator_calls ?? '--'}/${budget.evaluator_call_limit ?? '--'} evaluations`,
    budgetAgent: `${budget.agent_calls ?? '--'}/${budget.agent_call_limit ?? '--'}`,
    budgetEval: `${budget.evaluator_calls ?? '--'}/${budget.evaluator_call_limit ?? '--'}`,
    metricName: metric.name ?? null,
    baseline: Number.isFinite(metric.baseline) ? metric.baseline : null,
    champion: Number.isFinite(metric.champion) ? metric.champion : null,
    scores: Array.isArray(raw.dev_points) ? raw.dev_points.map(p => p.score).filter(Number.isFinite) : [],
    hypotheses: Array.isArray(raw.hypotheses) ? raw.hypotheses.map(h => ({
      name: h.title ?? null,
      score: Number.isFinite(h.dev_score) ? h.dev_score : null,
      status: h.status ?? 'UNKNOWN',
      ordinal: h.ordinal ?? null,
    })) : [],
    composites: Array.isArray(raw.composites) ? raw.composites.map(sequence => ({
      id: sequence.id ?? null,
      status: sequence.status ?? 'UNKNOWN',
      currentStep: sequence.current_step ?? 0,
      maxSteps: sequence.max_steps ?? null,
      steps: Array.isArray(sequence.steps) ? sequence.steps.map(step => ({
        order: step.step_order ?? null,
        title: step.title ?? null,
        score: Number.isFinite(step.dev_score) ? step.dev_score : null,
        status: step.status ?? 'UNKNOWN',
      })) : [],
    })) : [],
    pivotReview: normalizePivotReview(raw.pivot_review),
    missing: raw.missing && typeof raw.missing === 'object' ? raw.missing : {},
    stale: raw.stale === true,
    updatedAt: raw.updated_at ?? null,
    events: [],
  };
}

function normalizePivotReview(raw) {
  if (raw == null) return null;
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) throw new Error('Invalid pivot review projection');
  const proposal = raw.proposal;
  if (proposal != null && (!proposal || typeof proposal !== 'object' || Array.isArray(proposal))) {
    throw new Error('Invalid pivot proposal projection');
  }
  return {
    status: typeof raw.status === 'string' ? raw.status : 'UNKNOWN',
    pauseReason: raw.pause_reason ?? null,
    integrityValid: raw.integrity_valid === true,
    proposal: proposal ? {
      proposalId: proposal.proposal_id ?? null,
      digest: proposal.digest ?? null,
      specHash: proposal.spec_hash ?? null,
      triggerExperimentOrdinal: proposal.trigger_experiment_ordinal ?? null,
      currentArchitecture: proposal.current_architecture ?? null,
      proposedArchitecture: proposal.proposed_architecture ?? null,
      rationale: proposal.rationale ?? null,
      expectedEffect: proposal.expected_effect ?? null,
      changedScope: Array.isArray(proposal.changed_scope) ? proposal.changed_scope : [],
      resources: proposal.resources && typeof proposal.resources === 'object' ? proposal.resources : {},
    } : null,
    decision: raw.decision ?? null,
  };
}

export function matchesPendingPivot(snapshot, campaignId, proposalId, digest) {
  const pivot = snapshot?.pivotReview;
  return snapshot?.campaignId === campaignId
    && snapshot?.status === 'PAUSED_NEEDS_INPUT'
    && pivot?.integrityValid === true
    && pivot?.status === 'PENDING'
    && pivot?.proposal?.proposalId === proposalId
    && pivot?.proposal?.digest === digest;
}

export function normalizeEventPage(raw, previousCursor) {
  if (!raw || !Array.isArray(raw.events) || !Number.isInteger(raw.cursor) || raw.cursor < previousCursor) {
    throw new Error('Invalid event page');
  }
  const events = raw.events.map(event => {
    if (!Number.isInteger(event.seq) || typeof event.type !== 'string') throw new Error('Invalid event');
    return { seq: event.seq, text: `#${event.seq} ${event.type}` };
  });
  if (events.some((event, i) => event.seq <= previousCursor || (i && event.seq <= events[i - 1].seq))) {
    throw new Error('Out-of-order event page');
  }
  if (raw.cursor !== (events.length ? events.at(-1).seq : previousCursor)) {
    throw new Error('Event cursor does not match returned page');
  }
  return { events, cursor: raw.cursor, hasMore: raw.has_more === true };
}

export function createCampaignClient({ repo, campaignId, python = process.env.BANANLOOP_PYTHON || 'python', execute = execFile, spawnProcess = spawn }) {
  validateCampaignId(campaignId);
  if (typeof repo !== 'string' || !repo) throw new Error('Repository path is required');
  const env = { ...process.env, PYTHONPATH: [sourceDir, process.env.PYTHONPATH].filter(Boolean).join(process.platform === 'win32' ? ';' : ':') };
  function run(args, check) {
    return new Promise((resolvePromise, reject) => {
      execute(python, ['-m', 'bananloop.cli', ...args], {
        cwd: repo,
        env,
        timeout: 5000,
        maxBuffer: 2_000_000,
        windowsHide: true,
        shell: false,
      }, (error, stdout) => {
        if (error) return reject(new Error(`BananLoop CLI unavailable (${error.code || 'error'})`));
        try { resolvePromise(parseJSON(stdout, check)); }
        catch (parseError) { reject(parseError); }
      });
    });
  }
  function runControl(args) {
    return new Promise((resolvePromise, reject) => {
      execute(python, ['-m', 'bananloop.cli', ...args], {
        cwd: repo, env, timeout: 15000, maxBuffer: 64_000, windowsHide: true, shell: false,
      }, (error, stdout) => {
        if (error) return reject(new Error(`BananLoop control failed (${error.code || 'error'})`));
        resolvePromise(String(stdout || '').trim());
      });
    });
  }
  const common = ['--repo', repo, '--campaign-id', campaignId];
  return {
    readSnapshot: () => run(['operator-snapshot', ...common], x => x.schema_version === 1),
    readEvents: after => {
      if (!Number.isInteger(after) || after < 0) throw new Error('Invalid event cursor');
      return run(['operator-events', ...common, '--after', String(after), '--limit', '100'], x => Array.isArray(x.events));
    },
    requestPause: (requestId = randomUUID()) => runControl(['pause', ...common, '--request-id', requestId]).then(output => ({ requestId, output })),
    requestPivotDecision: ({ decision, proposalId, digest, rejectionAction, requestId = randomUUID() }) => {
      if (!['approve', 'reject'].includes(decision)) throw new Error('Invalid pivot decision');
      if (decision === 'reject' && !['continue_old_plan', 'change_conditions', 'end_campaign'].includes(rejectionAction)) {
        throw new Error('Invalid pivot rejection choice');
      }
      const args = ['pivot-decision', ...common, '--decision', decision,
        '--proposal-id', validateCampaignId(proposalId), '--digest', digest, '--request-id', requestId];
      if (decision === 'reject') args.push('--on-reject', rejectionAction);
      return runControl(args).then(output => ({ requestId, output }));
    },
    startResume: (requestId = randomUUID()) => new Promise((resolvePromise, reject) => {
      let child;
      try {
        child = spawnProcess(python, ['-m', 'bananloop.cli', 'resume', ...common, '--request-id', requestId], {
          cwd: repo, env, windowsHide: true, shell: false, detached: true, stdio: 'ignore',
        });
      } catch (error) {
        reject(error);
        return;
      }
      child.once?.('error', reject);
      child.once?.('spawn', () => {
        child.unref?.();
        resolvePromise({ requestId });
      });
      // Lightweight injected process doubles may not emit lifecycle events.
      if (!child.once) resolvePromise({ requestId });
    }),
  };
}
