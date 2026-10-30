// Explicitly loaded Pi extension. No campaign, model, credentials or network calls.
import { initial, render, command } from '../shared/mock.mjs';

export default function(pi: any) {
  pi.registerCommand('bananloop-demo', {
    description: 'Open offline BananLoop synthetic UI prototype',
    handler: async (_args: string, ctx: any) => {
      if (!ctx.hasUI) { ctx.ui.notify('Interactive terminal required', 'warning'); return; }
      const state = initial();
      await ctx.ui.custom((tui: any, _theme: any, _keys: any, done: any) => ({
        invalidate() {},
        render(width: number) { return render(state, width); },
        handleInput(data: string) {
          if (data === '\u001b' || data === '\u0003') { done(undefined); return; }
          if (data === '\r' || data === '\n') {
            if (state.input.trim() === '/quit') { done(undefined); return; }
            command(state, state.input);
          } else if (data === '\u007f' || data === '\b') state.input = state.input.slice(0, -1);
          else if (data && !/[\x00-\x1f\x7f]/.test(data)) state.input += data.slice(0, 120);
          tui.requestRender();
        },
      }));
    },
  });
}
