import assert from 'node:assert/strict';
import { initial, render, command, safeText, scoreGraph } from './mock.mjs';

for (const width of [40, 60, 80, 120]) {
  const state = initial();
  const rows = render(state, width);
  assert.ok(rows.every(row => row.length <= width));
  for (const title of ['GOAL:', 'STAGES:', 'LIVE EXPERIMENT', 'EXPERIMENT PROGRESS', 'HYPOTHESES', 'AGENT INPUT']) {
    assert.ok(rows.some(row => row.includes(title)), `${width}: missing ${title}`);
  }
  command(state, '/pause'); assert.equal(state.paused, true);
  command(state, '/resume'); assert.equal(state.paused, false);
}

const attack = '\u001b[2J\u001b]0;unsafe title\u0007hello\u202eworld\u009b31m';
assert.equal(safeText(attack), 'helloworld31m');
const state = initial();
state.input = attack;
command(state, attack);
assert.equal(state.last, 'Mock input: helloworld31m');
state.input = attack;
assert.ok(!/[\x00-\x1f\x7f-\x9f\u202e]/.test(render(state, 120).join('')));

assert.equal(scoreGraph([0, 5, 10]), '.+@');
assert.equal(scoreGraph([10, 5, 0]), '@+.');
assert.equal(scoreGraph([5, 5]), '..');
assert.equal(scoreGraph([]), 'unavailable');
assert.equal(scoreGraph([0, NaN, 10]), '.?@');
state.scores = [10, 5, 0];
assert.ok(render(state, 120).some(row => row.includes('DEV graph: @+.')));
console.log('synthetic layout, controls, terminal sanitization and proportional graph: OK');
