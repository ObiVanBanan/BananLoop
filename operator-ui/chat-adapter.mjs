import { safeText } from './layout.mjs';

const MAX_USER_MESSAGE = 12_000;
const MAX_CONTEXT = 2_000;

function value(value, limit) {
  const clean = safeText(value ?? '').trim();
  return clean.slice(0, limit);
}

export function parseChatCommand(raw) {
  const match = /^\/chat\s+([\s\S]+)$/i.exec(String(raw ?? '').trim());
  if (!match) throw new Error('Use /chat <message>');
  const message = safeText(match[1]).trim();
  if (!message) throw new Error('Use /chat <message>');
  if (message.length > MAX_USER_MESSAGE) throw new Error('Chat message exceeds the configured limit');
  return message;
}

export function buildChatPrompt(message, snapshot) {
  const goal = value(snapshot?.goal, 500) || 'Unavailable';
  const stage = value(snapshot?.stage, 80) || 'Unavailable';
  const status = value(snapshot?.status, 80) || 'Unavailable';
  const metric = value(snapshot?.metricName, 100) || 'Unavailable';
  const baseline = Number.isFinite(snapshot?.baseline) ? snapshot.baseline.toFixed(5) : 'Unavailable';
  const champion = Number.isFinite(snapshot?.champion) ? snapshot.champion.toFixed(5) : 'Unavailable';
  const budgetAgent = value(snapshot?.budgetAgent, 80) || 'Unavailable';
  const budgetEval = value(snapshot?.budgetEval, 80) || 'Unavailable';
  const context = [
    `Goal: ${goal}`,
    `Stage: ${stage}`,
    `Status: ${status}`,
    `DEV metric ${metric}: baseline ${baseline}; champion ${champion}`,
    `Budget: agent ${budgetAgent}; evaluator ${budgetEval}`,
  ].join('\n');
  if (context.length > MAX_CONTEXT) throw new Error('Campaign context exceeds the configured limit');
  return [
    'You are a read-only operator chat. Do not claim to perform any action.',
    'Return only the required structured object. intent must be exactly "pause" only when the operator clearly asks to pause this campaign; otherwise it must be null.',
    'Never produce another intent. A pause intent is only a proposal and will not change campaign state without a separate operator confirmation command.',
    'Keep message brief and explain the proposal or answer. Do not ask for or invoke tools.',
    `Operator message:\n${message}`,
    `Limited read-only campaign snapshot:\n${context}`,
  ].join('\n\n');
}

export function parseOperatorChatOutput(text) {
  let value;
  try { value = JSON.parse(String(text ?? '')); }
  catch { return { valid: false, reason: 'Codex returned non-JSON output.' }; }
  if (!value || typeof value !== 'object' || Array.isArray(value)) return { valid: false, reason: 'Codex output must be an object.' };
  const keys = Object.keys(value).sort();
  if (keys.length !== 2 || keys[0] !== 'intent' || keys[1] !== 'message') return { valid: false, reason: 'Codex output has unsupported fields.' };
  if (value.intent !== null && value.intent !== 'pause') return { valid: false, reason: 'Codex output has an unsupported intent.' };
  if (typeof value.message !== 'string' || value.message.length > 1000) return { valid: false, reason: 'Codex output message is invalid.' };
  const message = safeText(value.message).trim();
  if (!message) return { valid: false, reason: 'Codex output message is empty.' };
  return { valid: true, intent: value.intent, message };
}

export function createChatAdapter({ clientFactory, onEvent = () => {} }) {
  let client;
  let connecting;
  let closed = false;
  return {
    async send(rawCommand, snapshot) {
      if (closed) throw new Error('Chat is closed');
      const message = parseChatCommand(rawCommand);
      if (!client) {
        client = clientFactory({ onEvent });
        connecting = client.connect();
      }
      await connecting;
      const result = await client.sendMessage(buildChatPrompt(message, snapshot), { structured: true });
      if (result.status !== 'completed') return result;
      const parsed = parseOperatorChatOutput(result.text);
      return parsed.valid ? { status: 'completed', ...parsed } : { status: 'invalid', ...parsed };
    },
    close() {
      if (closed) return;
      closed = true;
      client?.close();
    },
  };
}
