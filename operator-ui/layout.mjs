// BananLoop operator screen layout. The view is deliberately data-only:
// callers supply committed campaign state when that integration exists.
const CONTROL = /\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07\x1b]*(?:\x07|\x1b\\))|[\x00-\x1f\x7f-\x9f\u202a-\u202e\u2066-\u2069]/g;

export function safeText(value) {
  return String(value ?? '').replace(CONTROL, '');
}

export function demoSnapshot() {
  return {
    demo: true,
    goal: 'Improve search quality by 15%',
    stage: 'IMPLEMENT',
    status: 'RUNNING',
    epoch: 3,
    experiments: 4,
    experimentLimit: 10,
    budget: '3 / 10 calls',
    events: [
      'Proposed adaptive chunking',
      'Selected hypothesis #3',
      'Implementing candidate',
    ],
    scores: [0.56, 0.61, 0.648],
    hypotheses: [
      { name: 'Hybrid retrieval', score: 0.612, status: 'DONE' },
      { name: 'Reranking', score: 0.648, status: 'DONE' },
      { name: 'Adaptive chunking', score: null, status: 'RUNNING' },
    ],
  };
}

const stages = ['PROPOSE', 'SELECT', 'IMPLEMENT', 'EVALUATE', 'LEARN', 'REPEAT'];
const clampWidth = width => Math.max(24, Math.min(240, Number(width) || 80));
const fit = (value, width) => safeText(value).slice(0, width);
const heading = (label, width) => ` ${label} `.padEnd(width, '-');

function wrapText(value, width) {
  const words = safeText(value).trim().split(/\s+/).filter(Boolean);
  const lines = [];
  let line = '';
  for (const word of words) {
    if (!line) line = word.slice(0, width);
    else if (`${line} ${word}`.length <= width) line += ` ${word}`;
    else {
      lines.push(line);
      line = word.slice(0, width);
    }
  }
  if (line) lines.push(line);
  return lines.length ? lines : [''];
}

function graph(scores, width) {
  const vals = (scores || []).filter(value => Number.isFinite(value));
  if (!vals.length) return ['  No DEV scores yet'];
  const lo = Math.min(...vals);
  const hi = Math.max(...vals);
  const usable = Math.max(4, Math.min(32, width - 13));
  return vals.slice(-8).map((value, i) => {
    const n = hi === lo ? usable : Math.max(1, Math.round(((value - lo) / (hi - lo)) * usable));
    return `  ${String(i + 1).padStart(2)} ${'█'.repeat(n)} ${value.toFixed(3)}`;
  });
}

function liveLines(s, width) {
  return [heading('LIVE EXPERIMENT / AGENT', width),
    ...((s.events || []).length ? s.events.slice(-8).map(x => `  > ${safeText(x)}`) : ['  No committed events yet']),
    '', s.demo ? '  Agent input below. Demo messages stay local.' : '  Agent chat is not connected yet.'];
}

function progressLines(s, width) {
  const metric = s.metricName ? safeText(s.metricName) : 'DEV metric unavailable';
  const baseline = Number.isFinite(s.baseline) ? s.baseline.toFixed(3) : 'Unavailable';
  const champion = Number.isFinite(s.champion) ? s.champion.toFixed(3) : 'Unavailable';
  return [heading('EXPERIMENT PROGRESS', width), `  ${metric} | baseline ${baseline} | champion ${champion}`, ...graph(s.scores, width), '',
    heading('HYPOTHESES', width),
    ...((s.hypotheses || []).length ? s.hypotheses.slice(-8).map(h =>
      `  ${safeText(h.name ?? `#${h.ordinal ?? '?'} title unavailable`)} | ${Number.isFinite(h.score) ? h.score.toFixed(3) : '--'} | ${safeText(h.status)}`) : ['  No hypotheses recorded']),
    '', heading('COMPOSITE SEQUENCES', width),
    ...((s.composites || []).length ? s.composites.slice(0, 4).flatMap(sequence => [
      `  ${safeText(sequence.id)} | ${safeText(sequence.status)} | ${sequence.currentStep}/${sequence.maxSteps ?? '?'}`,
      ...sequence.steps.map(step => `    ${step.order}. ${safeText(step.title ?? 'title unavailable')} | ${Number.isFinite(step.score) ? step.score.toFixed(3) : '--'} | ${safeText(step.status)}`),
    ]) : ['  No composite sequences recorded']),
    '', ...pivotLines(s.pivotReview, width)];
}

function pivotLines(review, width) {
  if (!review) return [heading('ARCHITECTURE PIVOT', width), '  No pivot proposal recorded'];
  if (!review.proposal) return [heading('ARCHITECTURE PIVOT', width), `  ${safeText(review.status)} | proposal body unavailable`, '  Do not approve; integrity check failed.'];
  const p = review.proposal;
  const lines = [heading('ARCHITECTURE PIVOT', width),
    `  STATUS ${safeText(review.status)} | ${review.integrityValid ? 'DIGEST VALID' : 'DIGEST INVALID'}`,
    `  PROPOSAL ${safeText(p.proposalId)}`,
    `  SHA256 ${safeText(p.digest)}`,
    `  CURRENT: ${safeText(p.currentArchitecture)}`,
    `  PROPOSED: ${safeText(p.proposedArchitecture)}`,
    `  RATIONALE: ${safeText(p.rationale)}`,
    `  EXPECTED: ${safeText(p.expectedEffect)}`,
    `  SCOPE: ${(p.changedScope || []).map(safeText).join(', ') || 'Unavailable'}`,
    `  RESOURCES: agents +${p.resources?.additional_agent_calls ?? '?'} | evaluations +${p.resources?.additional_evaluator_calls ?? '?'} | units +${p.resources?.additional_evaluation_units ?? '?'}`];
  if (review.pauseReason) lines.push(`  PAUSE: ${safeText(review.pauseReason)}`);
  if (review.decision) lines.push(`  DECISION: ${safeText(review.decision.decision)} / ${safeText(review.decision.status)}`);
  if (review.status === 'PENDING' && review.integrityValid) {
    lines.push('  /pivot approve <proposal-id> <sha256>');
    lines.push('  /pivot reject <proposal-id> <sha256> <continue_old_plan|change_conditions|end_campaign>');
  }
  return lines.flatMap(line => wrapText(line, Math.max(12, width - 2)).map(item => `  ${item}`));
}

export function renderLayout(snapshot, width, { input = '', notice = '', tab = 'live', connection = 'connected', eventLag = false, chatLines = [] } = {}) {
  const s = snapshot;
  const w = clampWidth(width);
  const out = [
    fit('🍌 BANANLOOP | OPERATOR CONSOLE', w),
    ...(s.demo ? ['SYNTHETIC DEMO | NO CAMPAIGN CONNECTED'] : []),
    fit(`GOAL  ${s.goal ?? 'Unavailable'}`, w),
    fit('STAGES  ' + stages.map(x => x === s.stage ? `[${x}]` : x).join(' > '), w),
    fit(`STATUS ${s.status ?? 'UNKNOWN'}   EPOCH ${s.epoch ?? '--'}   EXP ${s.experiments ?? '--'}/${s.experimentLimit ?? '--'}`, w),
    fit(s.budgetAgent ? `BUDGET agent ${s.budgetAgent} | evaluator ${s.budgetEval}` : `BUDGET ${s.budget ?? '--'}`, w),
    ...(s.demo ? [] : [fit(`ENGINE ${s.engineStage ?? 'Unavailable'} | DATA ${connection === 'connected' ? (s.stale ? 'STALE' : 'CURRENT') : 'DISCONNECTED'}${eventLag ? ' | EVENT BACKLOG' : ''}`, w)]),
    '-'.repeat(w),
  ];
  const left = liveLines(s, w);
  const right = progressLines(s, w);
  if (w >= 100) {
    const a = Math.floor((w - 3) / 2);
    const b = w - a - 3;
    for (let i = 0; i < Math.max(left.length, right.length); i++) {
      out.push(fit(left[i] || '', a).padEnd(a) + ' | ' + fit(right[i] || '', b));
    }
  } else {
    out.push(fit(`[Tab] ${tab === 'live' ? 'Live experiment' : 'Progress & hypotheses'}  |  ${tab === 'live' ? 'Progress & hypotheses' : 'Live experiment'}`, w));
    out.push(...(tab === 'live' ? left : right).map(line => fit(line, w)));
  }
  if (chatLines.length) {
    out.push('', fit('OPERATOR CHAT | SENT TO CONFIGURED CODEX ACCOUNT', w));
    out.push(...chatLines.slice(-6).flatMap(line => wrapText(`  ${line}`, w)));
  }
  const hint = w >= 100
    ? 'Wide view shows both panels; resize below 100 columns to switch with Tab.'
    : 'Tab switches panels; Esc exits.';
  out.push('-'.repeat(w), fit(`AGENT > ${input}`, w), fit(notice || hint, w));
  return out;
}
