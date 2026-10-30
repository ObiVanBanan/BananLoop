// Opt-in operator shell. Campaign projection is read-only; typed controls are explicit.
import { demoSnapshot, renderLayout, safeText } from './layout.mjs';
import { createCampaignClient, normalizeSnapshot, normalizeEventPage, matchesPendingPivot } from './campaign.mjs';
import { beginControlObservation, observeControl } from './control-observation.mjs';
import { createChatAdapter } from './chat-adapter.mjs';
import { createChatPauseFlow } from './chat-pause-flow.mjs';
import { createCodexChat } from './codex-chat.mjs';
import { randomUUID } from 'node:crypto';

export default function (pi: any) {
  pi.registerCommand('bananloop', {
    description: 'Open a BananLoop campaign screen, or use "demo" for synthetic data',
    handler: async (args: string, ctx: any) => {
      if (!ctx.hasUI) {
        ctx.ui.notify('BananLoop operator screen requires an interactive terminal', 'warning');
        return;
      }
      const campaignId = args.trim();
      const demo = campaignId === 'demo';
      if (!campaignId) {
        ctx.ui.notify('Usage: /bananloop <campaign-id>  (or /bananloop demo)', 'warning');
        return;
      }
      let client: ReturnType<typeof createCampaignClient> | null = null;
      let state = demoSnapshot();
      let cursor = 0;
      let connection = 'connected';
      let eventLag = false;
      if (!demo) {
        try {
          client = createCampaignClient({ repo: process.cwd(), campaignId });
          state = normalizeSnapshot(await client.readSnapshot()) as typeof state;
          if (state.campaignId !== campaignId) throw new Error('Campaign ID mismatch');
          cursor = Math.max(0, state.revision - 100);
        } catch (error) {
          ctx.ui.notify(`Campaign view unavailable: ${error instanceof Error ? error.message : 'read error'}`, 'error');
          return;
        }
      }
      let input = '';
      let notice = demo ? 'SYNTHETIC DEMO: no campaign or model is connected.' : 'Commands: /pivot approve|reject <id> <digest> [choice], /pause, /resume, /chat <message>, /quit.';
      let tab = 'live';
      let active = true;
      let busy = false;
      let controlBusy = false;
      let chatBusy = false;
      let tuiRef: any;
      const chatLines: string[] = [];
      const chat = !demo ? createChatAdapter({
        clientFactory: ({ onEvent }: any) => createCodexChat({
          model: process.env.BANANLOOP_OPERATOR_MODEL || undefined,
          onEvent: (event: any) => {
            if (event.type === 'delta') {
              const text = safeText(event.text);
              const last = chatLines.at(-1) || '';
              if (last.startsWith('Codex: ')) chatLines[chatLines.length - 1] = safeText(`${last}${text}`).slice(-2000);
              else chatLines.push(`Codex: ${text}`);
              while (chatLines.length > 6) chatLines.shift();
            }
            if (event.type === 'completed' && event.status !== 'completed') chatLines.push(`Codex turn ${event.status}; no automatic retry.`);
            tuiRef?.requestRender();
            onEvent?.(event);
          },
        }),
      }) : null;
      const pauseFlow = !demo && client && chat ? createChatPauseFlow({ campaignId, chat, controlClient: client }) : null;
      let controlTrack: ReturnType<typeof beginControlObservation> | null = null;
      let timer: ReturnType<typeof setInterval> | undefined;
      try {
        await ctx.ui.custom((tui: any, theme: any, _keys: any, done: any) => {
          tuiRef = tui;
          async function refresh() {
            if (pauseFlow?.expireProposal()) notice = 'Pause proposal expired; campaign state was not changed.';
            if (!active || busy || !client) return;
            busy = true;
            try {
              const next = normalizeSnapshot(await client.readSnapshot());
              if (next.campaignId !== campaignId) throw new Error('Campaign ID mismatch');
              if (!active) return;
              next.events = state.events;
              state = next as typeof state;
              connection = 'connected';
              if (controlTrack) {
                const observed = observeControl(controlTrack, next.status);
                controlTrack = observed.track;
                if (observed.notice) notice = observed.notice;
              }
              try {
                const page = normalizeEventPage(await client.readEvents(cursor), cursor);
                if (!active) return;
                cursor = page.cursor;
                state.events = [...state.events, ...page.events.map(event => event.text)].slice(-30);
                eventLag = page.hasMore;
                if (!controlTrack) notice = page.hasMore ? 'Reading committed event backlog...' : 'Commands: /pivot approve|reject <id> <digest> [choice], /pause, /resume, /chat <message>, /quit.';
              } catch {
                eventLag = true;
                if (!controlTrack) notice = 'Event stream unavailable; snapshot is still current.';
              }
            } catch {
              connection = 'disconnected';
              if (controlTrack) {
                const observed = observeControl(controlTrack, state.status);
                controlTrack = observed.track;
                notice = observed.notice || 'Campaign read failed; control status remains pending.';
              } else notice = 'Campaign read failed; showing last committed snapshot.';
            } finally {
              busy = false;
              if (active) tui.requestRender();
            }
          }
          if (client) {
            void refresh();
            timer = setInterval(() => void refresh(), 2000);
          }
          return {
        invalidate() {},
        render(width: number) {
          return renderLayout(state, width, { input, notice, tab, connection, eventLag, chatLines }).map((line, index) => {
            if (index === 0 || line.includes('STAGES') || line.startsWith('LIVE EXPERIMENT') || line.startsWith('EXPERIMENT PROGRESS') || line.startsWith('HYPOTHESES')) {
              return theme.style(line, { fg: 'accent', bold: true });
            }
            if (line.startsWith('STATUS')) return theme.fg('success', line);
            if (line.includes('SYNTHETIC DEMO')) return theme.fg('warning', line);
            return theme.fg('text', line);
          });
        },
        handleInput(data: string) {
          if (data === '\u001b' || data === '\u0003') { done(undefined); return; }
          if (data === '\t') tab = tab === 'live' ? 'progress' : 'live';
          else if (data === '\r' || data === '\n') {
            const command = input.trim();
            if (command === '/quit') { done(undefined); return; }
            if (/^\/chat(?:\s|$)/i.test(command)) {
              if (demo || !chat || !pauseFlow) notice = 'Chat is disabled in synthetic demo mode.';
              else if (chatBusy) notice = 'A Codex chat turn is still in flight.';
              else if (command.toLowerCase() === '/chat') notice = 'Usage: /chat <message>';
              else {
                const userMessage = command.replace(/^\/chat\s+/i, '').trim();
                if (!userMessage) notice = 'Usage: /chat <message>';
                else {
                  chatBusy = true;
                  chatLines.push(`You: ${safeText(userMessage).slice(0, 500)}`);
                  while (chatLines.length > 6) chatLines.shift();
                  notice = 'Sending this message with limited goal/stage/status/DEV metric/budget context to your configured Codex account. No events, repo files, validation or HOLDOUT data are attached; chat has no BananLoop control API.';
                  ctx.ui.notify('This /chat message and limited campaign snapshot are sent to your configured Codex account.', 'warning');
                  void pauseFlow.chatMessage(command, { ...state, connected: connection === 'connected' }).then(result => {
                    if (!result) { notice = 'Codex chat unavailable; no retry was made.'; return; }
                    if (result.status === 'proposal') {
                      chatLines.push(`Codex: ${safeText(result.message).slice(0, 500)}`);
                      chatLines.push(`PROPOSED EFFECT: ${safeText(result.effect)}`);
                      chatLines.push(`Confirm within 30 seconds: /confirm-pause ${result.token}`);
                      chatLines.push(`Or reject: /reject-pause ${result.token}`);
                      notice = 'No campaign action has occurred. Confirm the exact pause effect with the displayed token within 30 seconds.';
                    } else if (result.status === 'proposal-pending') {
                      notice = `A pause proposal is already pending. Confirm or reject with token ${result.token}.`;
                    } else if (result.status === 'answer') {
                      chatLines.push(`Codex: ${safeText(result.message).slice(0, 1000)}`);
                      notice = 'Codex reply received; no campaign action was taken.';
                    } else if (result.status === 'unavailable') {
                      chatLines.push(`Codex: ${safeText(result.message || 'Pause is unavailable.').slice(0, 500)}`);
                      chatLines.push(`NOT AVAILABLE: ${safeText(result.effect || '')}`);
                      notice = `No pause proposal can be confirmed from ${result.observedStatus || state.status || 'UNKNOWN'}. No campaign action was taken.`;
                    } else if (result.status === 'invalid-output') {
                      notice = 'Codex returned an invalid structured response; no campaign action was taken.';
                    } else if (result.status !== 'completed') notice = `Codex chat ${result.status}; no automatic retry.`;
                    while (chatLines.length > 6) chatLines.shift();
                  }).catch(() => {
                    notice = 'Codex chat unavailable or safety configuration was not verified; no retry was made.';
                  }).finally(() => { chatBusy = false; tui.requestRender(); });
                }
              }
              input = '';
              tui.requestRender();
              return;
            }
            if (/^\/(?:confirm|reject)-pause(?:\s|$)/i.test(command)) {
              if (demo || !pauseFlow || !client) notice = 'Pause confirmation is unavailable in synthetic demo mode.';
              else if (controlBusy || chatBusy) notice = 'Wait for the active control or chat request to finish.';
              else {
                controlBusy = true;
                notice = 'Checking the confirmation token; no action is taken unless it matches a live proposal.';
                void pauseFlow.decide(command, { ...state, connected: connection === 'connected' }).then(result => {
                  if (result.status === 'accepted') {
                    controlTrack = result.track;
                    notice = `${result.receipt?.output || 'Pause request accepted.'} Confirmation recorded as request ${result.requestId}; waiting for committed PAUSED_USER status.`;
                  } else notice = result.message || `Pause decision ${result.status}; no campaign action was taken.`;
                }).catch(() => {
                  notice = 'Typed pause command failed or has an unknown receipt; no automatic retry was made.';
                }).finally(() => { controlBusy = false; tui.requestRender(); });
              }
              input = '';
              tui.requestRender();
              return;
            }
            if (/^\/pivot(?:\s|$)/i.test(command)) {
              const parts = command.split(/\s+/);
              const action = parts[1];
              const proposalId = parts[2];
              const digest = parts[3];
              const rejectionAction = parts[4];
              if (demo || !client) notice = 'Pivot decisions are unavailable in synthetic demo mode.';
              else if (controlBusy || chatBusy) notice = 'Wait for the active control or chat request to finish.';
              else if (!['approve', 'reject'].includes(action) || !proposalId || !/^[0-9a-f]{64}$/.test(digest || '')
                || (action === 'approve' && parts.length !== 4)
                || (action === 'reject' && (parts.length !== 5 || !['continue_old_plan', 'change_conditions', 'end_campaign'].includes(rejectionAction)))
                || (action === 'reject' && parts.length !== 5)) {
                notice = 'Usage: /pivot approve <proposal-id> <sha256> | /pivot reject <proposal-id> <sha256> <continue_old_plan|change_conditions|end_campaign>';
              } else {
                const shown = state.pivotReview;
                if (!shown?.integrityValid || shown.status !== 'PENDING'
                  || shown.proposal?.proposalId !== proposalId || shown.proposal?.digest !== digest) {
                  notice = 'Proposal identity or digest changed; review the refreshed proposal and re-enter its exact digest.';
                } else if (state.status !== 'PAUSED_NEEDS_INPUT') {
                  notice = `Pivot decisions require PAUSED_NEEDS_INPUT; current status is ${state.status || 'UNKNOWN'}.`;
                } else {
                  controlBusy = true;
                  notice = 'Rechecking the committed proposal and digest before recording the operator decision.';
                  void client.readSnapshot().then(raw => {
                    const fresh = normalizeSnapshot(raw);
                    if (!matchesPendingPivot(fresh, campaignId, proposalId, digest)) {
                      state = fresh;
                      throw new Error('Proposal changed or became stale; review it again before deciding.');
                    }
                    return client!.requestPivotDecision({
                      decision: action, proposalId, digest,
                      rejectionAction: action === 'reject' ? rejectionAction : undefined,
                    });
                  }).then(({ output }) => {
                    const receipt = JSON.parse(output);
                    notice = `${receipt.decision.toUpperCase()} recorded (${receipt.status}). No campaign work was started; a new explicit operator action is required to continue.`;
                    return client!.readSnapshot();
                  }).then(raw => { state = normalizeSnapshot(raw); })
                    .catch(error => { notice = safeText(error instanceof Error ? error.message : 'Pivot decision failed; no automatic retry.'); })
                    .finally(() => { controlBusy = false; tui.requestRender(); });
                }
              }
              input = '';
              tui.requestRender();
              return;
            }
            if (!demo && client && !controlBusy && command === '/pause') {
              if (!['READY', 'RUNNING'].includes(state.status || '')) notice = `Pause unavailable from ${state.status || 'UNKNOWN'}.`;
              else {
                controlBusy = true;
                notice = 'Submitting pause request; acceptance does not mean cancellation is complete.';
                void client.requestPause().then(({ requestId, output }) => {
                  controlTrack = beginControlObservation('pause', state.status, requestId);
                  notice = `${output || 'Pause request accepted.'} Waiting for the committed campaign status (request ${requestId}).`;
                }).catch(error => { notice = `Pause request failed: ${safeText(error instanceof Error ? error.message : 'CLI error')}`; })
                  .finally(() => { controlBusy = false; tui.requestRender(); });
              }
            } else if (!demo && client && !controlBusy && command === '/resume') {
              if (!['PAUSED_USER', 'PAUSED_INFRA', 'PAUSED_NEEDS_INPUT'].includes(state.status || '')) notice = `Resume unavailable from ${state.status || 'UNKNOWN'}.`;
              else {
                controlBusy = true;
                notice = 'Starting explicit resume. This can invoke configured agents and evaluators.';
                const fromStatus = state.status;
                const requestId = randomUUID();
                controlTrack = beginControlObservation('resume', fromStatus, requestId);
                void Promise.resolve().then(() => client!.startResume(requestId)).then(() => {
                  notice = `Resume launched (request ${requestId}); observing committed status. The campaign may continue after this screen closes.`;
                }).catch(error => { notice = `Resume launch failed: ${safeText(error instanceof Error ? error.message : 'CLI error')}`; })
                  .finally(() => { controlBusy = false; tui.requestRender(); });
              }
            } else {
              notice = demo ? `Demo input only: ${safeText(input).slice(0, 100)}` : 'Input was not sent. Use explicit /pause, /resume or /chat <message>.';
            }
            input = '';
          } else if (data === '\u007f' || data === '\b') input = input.slice(0, -1);
          else input = safeText(input + data).slice(0, 16_000);
          tui.requestRender();
        },
          };
        });
      } finally {
        active = false;
        if (timer) clearInterval(timer);
        chat?.close();
        pauseFlow?.clear();
        tuiRef = undefined;
      }
    },
  });
}
