// Offline, line-oriented standalone feasibility spike. No campaign or model access.
import readline from 'node:readline';
import { initial, render, command } from '../shared/mock.mjs';
const state = initial();
const width = Number(process.argv.find(x => x.startsWith('--width='))?.split('=')[1]) || process.stdout.columns || 80;
if (process.argv.includes('--snapshot')) {
  console.log(render(state, width).join('\n'));
} else {
  const rl = readline.createInterface({input: process.stdin, output: process.stdout, terminal: Boolean(process.stdin.isTTY)});
  function draw() { console.log('\n' + render(state, process.stdout.columns || width).join('\n')); }
  draw();
  rl.on('line', line => { if (line.trim() === '/quit') return rl.close(); command(state, line); draw(); });
  process.stdout.on('resize', draw);
}
