import assert from 'node:assert/strict';
import { demoSnapshot, renderLayout, safeText } from './layout.mjs';

for (const width of [40, 60, 80, 120]) {
  const state = demoSnapshot();
  const lines = renderLayout(state, width);
  assert(lines.every(line => [...line].length <= width), `line exceeds ${width} columns`);
  assert(lines.some(line => line.includes('GOAL')));
  assert(lines.some(line => line.includes('STAGES')));
  assert(lines.some(line => line.includes('BUDGET')));
  assert(lines.some(line => line.includes('SYNTHETIC DEMO')));
}
const state = demoSnapshot();
const wide = renderLayout(state, 120).join('\n');
assert(wide.includes('LIVE EXPERIMENT'));
assert(wide.includes('EXPERIMENT PROGRESS'));
assert(wide.includes('HYPOTHESES'));
assert(wide.includes('0.648'));
assert(wide.includes('Wide view shows both panels; resize below 100 columns to switch with Tab.'));
const narrow = renderLayout(state, 60, { tab: 'progress' }).join('\n');
assert(narrow.includes('EXPERIMENT PROGRESS'));
assert(!narrow.includes('LIVE EXPERIMENT / AGENT'));
assert(narrow.includes('Tab switches panels; Esc exits.'));
const narrowChat = renderLayout(state, 40, { chatLines: [
  'PROPOSED EFFECT: Preserve the last committed campaign/champion state and leave the campaign resumable.',
] }).join('\n');
assert(narrowChat.split('\n').every(line => [...line].length <= 40));
assert(narrowChat.includes('resumable.'));
assert.equal(safeText('\u001b]0;evil\u0007\u001b[31mhello\u202e'), 'hello');
state.scores = [];
assert(renderLayout(state, 120).join('\n').includes('No DEV scores yet'));
console.log('operator-ui layout: OK');
