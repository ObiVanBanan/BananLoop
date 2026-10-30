// Synthetic data and rendering shared by both offline prototypes.
export function initial() {
  return { paused: false, input: '', last: '', goal: 'Improve search quality by 15%', epoch: 3,
    stage: 'IMPLEMENT', events: ['Proposed adaptive chunking', 'Selected hypothesis #3', 'Implementing candidate'],
    hypotheses: [['Hybrid retrieval', '0.612', 'done'], ['Reranking', '0.648', 'done'], ['Adaptive chunking', '--', 'running']],
    scores: [0.56, 0.61, 0.648] };
}
// Text may be displayed by a terminal: drop escape sequences, control bytes and
// directional override/isolate characters before it enters the mock state.
export function safeText(value) {
  return String(value)
    .replace(/\x1b\][^\x07\x1b]*(?:\x07|\x1b\\|$)/g, '')
    .replace(/\x1b\[[0-?]*[ -/]*[@-~]/g, '')
    .replace(/[\x00-\x1f\x7f-\x9f\u061c\u200e\u200f\u202a-\u202e\u2066-\u2069]/g, '');
}
export function scoreGraph(scores) {
  const valid = scores.filter(Number.isFinite);
  if (!valid.length) return 'unavailable';
  const low = Math.min(...valid), high = Math.max(...valid);
  const levels = '._:-=+*#%@';
  return scores.map(x => !Number.isFinite(x) ? '?' : levels[high === low ? 0 : Math.round((x - low) / (high - low) * (levels.length - 1))]).join('');
}
export function command(s, input) {
  const text = safeText(input).trim().slice(0, 120);
  if (text === '/pause' || text === 'stop experiment') { s.paused = true; s.last = 'Mock pause accepted. No campaign was changed.'; }
  else if (text === '/resume') { s.paused = false; s.last = 'Mock resume accepted. No campaign was changed.'; }
  else if (text) s.last = `Mock input: ${text}`;
  s.input = '';
}
export function render(s, width) {
  const w = Math.max(20, Math.min(Number(width) || 80, 150));
  const trim = x => safeText(x).slice(0, w);
  const stage = `STAGES: PROPOSE > SELECT > [${s.stage}] > EVALUATE > LEARN > REPEAT`;
  const head = ['BANANLOOP  [offline synthetic prototype]', `GOAL: ${s.goal}`, ...Array.from({length: Math.ceil(stage.length / w)}, (_, i) => stage.slice(i * w, (i + 1) * w)), `STATUS: ${s.paused ? 'PAUSED' : 'RUNNING'} | EPOCH ${s.epoch} | BUDGET 3/10`];
  const left = ['LIVE EXPERIMENT', ...s.events.map(x => `  > ${x}`), `  ${s.last || 'Awaiting mock input'}`];
  const right = ['EXPERIMENT PROGRESS', `  DEV graph: ${scoreGraph(s.scores)}`, `  DEV: ${s.scores.join(' -> ')}`, 'HYPOTHESES', ...s.hypotheses.map(x => `  ${x.join(' | ')}`)];
  let body;
  if (w >= 100) {
    const col = Math.floor((w - 3) / 2);
    body = Array.from({length: Math.max(left.length, right.length)}, (_, i) => (left[i] || '').slice(0, col).padEnd(col) + ' | ' + (right[i] || '').slice(0, w - col - 3));
  } else body = [...left, '', ...right];
  return [...head, '-'.repeat(w), ...body, '-'.repeat(w), 'AGENT INPUT (mock): /pause, /resume, /quit', `> ${s.input}`].map(trim);
}
