import { spawn, spawnSync } from 'node:child_process';
import { tmpdir } from 'node:os';
import { mkdtempSync, rmSync } from 'node:fs';
import { dirname, join, resolve, sep } from 'node:path';
import { safeText } from './layout.mjs';

const MAX_LINE = 1024 * 1024;
const MAX_BUFFER = 2 * MAX_LINE;
const MAX_INPUT = 16_000;
const MAX_OUTPUT = 100_000;
const PASSIVE_ITEMS = new Set(['agentMessage', 'userMessage', 'reasoning', 'plan', 'contextCompaction']);
const OPERATOR_CHAT_OUTPUT_SCHEMA = {
  type: 'object',
  properties: {
    intent: { anyOf: [{ const: 'pause' }, { type: 'null' }] },
    message: { type: 'string', maxLength: 1000 },
  },
  required: ['intent', 'message'],
  additionalProperties: false,
};

function childEnvironment(source = process.env) {
  // Codex reads its normal on-disk login. Do not forward arbitrary API keys,
  // tokens, campaign variables, or the host application's complete env.
  const names = [
    'PATH', 'HOME', 'USERPROFILE', 'APPDATA', 'LOCALAPPDATA', 'CODEX_HOME',
    'TEMP', 'TMP', 'SYSTEMROOT', 'WINDIR', 'COMSPEC', 'PATHEXT',
    'LANG', 'LC_ALL', 'LC_CTYPE', 'TERM',
  ];
  return Object.fromEntries(names.filter(name => source[name] !== undefined).map(name => [name, source[name]]));
}

function listConfiguredMcpServers(command, env, cwd) {
  const result = spawnSync(command, ['mcp', 'list', '--json'], {
    cwd,
    env: childEnvironment(env),
    encoding: 'utf8',
    shell: false,
    windowsHide: true,
    timeout: 5000,
    maxBuffer: 1024 * 1024,
  });
  if (result.error || result.status !== 0 || typeof result.stdout !== 'string') {
    throw new Error('Codex MCP configuration could not be verified');
  }
  let servers;
  try { servers = JSON.parse(result.stdout); }
  catch { throw new Error('Codex MCP configuration could not be verified'); }
  return servers;
}

function assertNoEnabledMcpServers(servers) {
  if (!Array.isArray(servers) || servers.some(server => !server || typeof server.name !== 'string'
    || !server.name || typeof server.enabled !== 'boolean')) {
    throw new Error('Codex MCP configuration could not be verified');
  }
  if (servers.some(server => server.enabled)) {
    throw new Error('Codex operator chat requires all configured MCP servers to be disabled');
  }
}

function boundedText(value, limit) {
  const clean = safeText(value);
  if (clean.length > limit) throw new Error('Chat message exceeds the configured limit');
  return clean;
}

export function createCodexChat({
  command = 'codex',
  spawnProcess = spawn,
  env = process.env,
  model,
  listMcpServers = listConfiguredMcpServers,
  requestTimeoutMs = 15_000,
  turnTimeoutMs = 120_000,
  onEvent = () => {},
} = {}) {
  const tempParent = resolve(tmpdir());
  const cwd = mkdtempSync(join(tempParent, 'bananloop-operator-chat-'));
  let child;
  let buffer = '';
  let nextId = 1;
  let threadId;
  let state = 'new';
  let cleaned = false;
  let activeTurn = null;
  const pending = new Map();
  const listeners = new Set();

  function publish(event) {
    const safe = event.text === undefined ? event : { ...event, text: boundedText(event.text, MAX_OUTPUT) };
    try { onEvent(safe); } catch { /* UI callbacks must not break transport cleanup. */ }
    for (const listener of listeners) {
      try { listener(safe); } catch { /* Same boundary for optional subscribers. */ }
    }
  }

  function failAll(message) {
    for (const entry of pending.values()) {
      clearTimeout(entry.timer);
      entry.reject(new Error(message));
    }
    pending.clear();
    if (activeTurn) {
      clearTimeout(activeTurn.timer);
      activeTurn.resolve({ status: 'unknown', reason: message, text: activeTurn.text });
      activeTurn = null;
    }
  }

  function send(message) {
    if (!child || child.killed || !child.stdin?.writable) throw new Error('Codex app-server is unavailable');
    const line = `${JSON.stringify(message)}\n`;
    if (Buffer.byteLength(line, 'utf8') > MAX_LINE) throw new Error('Codex request exceeds the line limit');
    child.stdin.write(line);
  }

  function request(method, params) {
    const id = nextId++;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        pending.delete(id);
        reject(new Error(`Codex app-server request timed out: ${method}`));
      }, requestTimeoutMs);
      pending.set(id, { resolve, reject, timer, method });
      try { send({ method, id, params }); }
      catch (error) {
        clearTimeout(timer);
        pending.delete(id);
        reject(error);
      }
    });
  }

  function handleMessage(message) {
    if (!message || typeof message !== 'object' || Array.isArray(message)) throw new Error('Invalid Codex app-server message');
    if (Object.hasOwn(message, 'id') && (Object.hasOwn(message, 'result') || Object.hasOwn(message, 'error'))) {
      const entry = pending.get(message.id);
      if (!entry) return;
      pending.delete(message.id);
      clearTimeout(entry.timer);
      if (message.error) entry.reject(new Error(`Codex app-server rejected ${entry.method}`));
      else entry.resolve(message.result ?? {});
      return;
    }
    // App-server server-initiated requests are never approved by this client.
    if (typeof message.method === 'string' && Object.hasOwn(message, 'id')) {
      send({ id: message.id, error: { code: -32601, message: 'Unsupported server request' } });
      publish({ type: 'unsupported-server-request' });
      return;
    }
    if (typeof message.method !== 'string') throw new Error('Unknown Codex app-server message shape');
    if (message.method === 'thread/started' && typeof message.params?.thread?.id === 'string') {
      publish({ type: 'thread-started', ephemeral: message.params.thread.ephemeral === true });
      return;
    }
    if (message.method === 'item/agentMessage/delta') {
      const delta = message.params?.delta;
      if (typeof delta !== 'string' || !activeTurn) return;
      const clean = safeText(delta);
      if (activeTurn.text.length + clean.length > MAX_OUTPUT) {
        activeTurn.reason = 'Codex response exceeded the output limit';
        sendInterrupt();
        return;
      }
      activeTurn.text += clean;
      if (!activeTurn.structured) publish({ type: 'delta', text: clean });
      return;
    }
    if (message.method === 'turn/completed') {
      const turn = message.params?.turn;
      if (!activeTurn || !turn || typeof turn.status !== 'string') return;
      clearTimeout(activeTurn.timer);
      const current = activeTurn;
      activeTurn = null;
      const reported = ['completed', 'failed', 'interrupted'].includes(turn.status) ? turn.status : 'unknown';
      const status = current.reason ? 'unknown' : reported;
      const result = {
        status,
        ...(current.reason ? { serverStatus: reported, reason: current.reason } : {}),
        text: current.text,
      };
      publish({ type: 'completed', status });
      current.resolve(result);
      return;
    }
    // Tool use is outside the supported operator-chat contract. Stop and mark
    // the result unknown because a side effect may have started before notice.
    if (message.method === 'item/started' && !PASSIVE_ITEMS.has(message.params?.item?.type)) {
      if (activeTurn) {
        activeTurn.reason = 'Codex requested an unsupported tool or non-chat action';
        sendInterrupt();
      }
      publish({ type: 'unsupported-item' });
      return;
    }
  }

  function consume(chunk) {
    buffer += chunk.toString('utf8');
    if (Buffer.byteLength(buffer, 'utf8') > MAX_BUFFER) throw new Error('Codex app-server output buffer exceeded the limit');
    let newline;
    while ((newline = buffer.indexOf('\n')) >= 0) {
      const line = buffer.slice(0, newline);
      buffer = buffer.slice(newline + 1);
      if (!line) continue;
      if (Buffer.byteLength(line, 'utf8') > MAX_LINE) throw new Error('Codex app-server line exceeded the limit');
      handleMessage(JSON.parse(line));
    }
    if (Buffer.byteLength(buffer, 'utf8') > MAX_LINE) throw new Error('Codex app-server line exceeded the limit');
  }

  async function connect() {
    if (state === 'ready') return { threadId };
    if (state !== 'new') throw new Error('Codex app-server connection cannot be reused');
    state = 'starting';
    try { assertNoEnabledMcpServers(listMcpServers(command, env, cwd)); }
    catch (error) {
      state = 'failed';
      cleanupWorkDir();
      throw error;
    }
    try {
      child = spawnProcess(command, [
        'app-server', '--listen', 'stdio://', '--strict-config',
        '-c', 'web_search="disabled"', '-c', 'mcp_servers={}',
        '-c', 'features.apps=false', '-c', 'features.shell_tool=false',
        '-c', 'features.unified_exec=false', '-c', 'features.multi_agent=false',
        '-c', 'features.remote_plugin=false', '-c', 'features.browser_use=false',
        '-c', 'features.computer_use=false', '-c', 'features.shell_snapshot=false',
        '-c', 'features.memories=false', '-c', 'features.goals=false',
        '-c', 'features.hooks=false',
      ], {
        cwd,
        env: childEnvironment(env),
        shell: false,
        windowsHide: true,
        stdio: ['pipe', 'pipe', 'ignore'],
      });
    } catch {
      state = 'failed';
      cleanupWorkDir();
      throw new Error('Codex app-server could not start');
    }
    child.stdout.on('data', chunk => {
      try { consume(chunk); }
      catch {
        state = 'failed';
        failAll('Invalid or oversized Codex app-server response');
        child?.kill();
      }
    });
    child.on('error', () => {
      state = 'failed';
      failAll('Codex app-server could not start');
    });
    child.on('exit', () => {
      const hadTurn = Boolean(activeTurn);
      state = 'closed';
      failAll(hadTurn ? 'Codex process exited during a turn; outcome is unknown' : 'Codex app-server exited');
      cleanupWorkDir();
    });
    try {
      await request('initialize', { clientInfo: { name: 'bananloop_operator', title: 'BananLoop Operator', version: '0.1.0' } });
      send({ method: 'initialized', params: {} });
      const configResult = await request('config/read', { includeLayers: false });
      const config = configResult.config;
      const mcpConfig = config?.mcp_servers;
      const mcpServersDisabled = mcpConfig && typeof mcpConfig === 'object' && !Array.isArray(mcpConfig)
        && Object.values(mcpConfig).every(server => server && typeof server === 'object' && server.enabled === false);
      const toolsConfig = config?.tools;
      const toolsDisabled = toolsConfig === null || (toolsConfig && typeof toolsConfig === 'object'
        && !Array.isArray(toolsConfig) && Object.values(toolsConfig).every(value => value === false));
      const safeConfig = config && config.web_search === 'disabled'
        && mcpServersDisabled
        && config.features?.apps === false
        && config.features?.shell_tool === false
        && config.features?.unified_exec === false
        && config.features?.multi_agent === false
        && config.features?.remote_plugin === false
        && config.features?.browser_use === false
        && config.features?.computer_use === false
        && config.features?.shell_snapshot === false
        && config.features?.memories === false
        && config.features?.goals === false
        && config.features?.hooks === false
        && toolsDisabled;
      if (!safeConfig) throw new Error('Codex app-server safety configuration is unavailable');
      const params = { cwd, approvalPolicy: 'never', sandbox: 'read-only', ephemeral: true, ...(model ? { model } : {}) };
      const base = await request('thread/start', params);
      if (typeof base.thread?.id !== 'string' || !base.thread.id || base.thread.ephemeral !== true
        || !Array.isArray(base.instructionSources) || base.instructionSources.length !== 0) {
        throw new Error('Codex app-server returned an invalid or non-isolated thread');
      }
      threadId = base.thread.id;
      state = 'ready';
      return { threadId };
    } catch (error) {
      state = 'failed';
      child?.kill();
      throw error;
    }
  }

  function sendInterrupt() {
    if (!activeTurn || !threadId || state !== 'ready') return;
    const turnId = activeTurn.turnId;
    if (!turnId) return;
    void request('turn/interrupt', { threadId, turnId }).catch(() => {});
  }

  function cleanupWorkDir() {
    if (cleaned) return;
    const resolved = resolve(cwd);
    if (dirname(resolved) === tempParent && resolved.startsWith(`${tempParent}${sep}`)
      && resolved.slice(tempParent.length + sep.length).startsWith('bananloop-operator-chat-')) {
      rmSync(resolved, { recursive: true, force: true });
    }
    cleaned = true;
  }

  async function sendMessage(text, { structured = false } = {}) {
    if (state !== 'ready') throw new Error('Codex app-server is not ready');
    if (activeTurn) throw new Error('A Codex turn is already in flight');
    const clean = boundedText(text, MAX_INPUT).trim();
    if (!clean) throw new Error('Chat message is empty');
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        if (!activeTurn) return;
        activeTurn.reason = 'Codex turn timed out; outcome is unknown';
        sendInterrupt();
        const current = activeTurn;
        activeTurn = null;
        current.resolve({ status: 'unknown', reason: current.reason, text: current.text });
      }, turnTimeoutMs);
      activeTurn = { resolve, reject, timer, text: '', turnId: null, reason: null, structured };
      request('turn/start', {
        threadId,
        input: [{ type: 'text', text: clean }],
        cwd,
        approvalPolicy: 'never',
        sandboxPolicy: {
          type: 'workspaceWrite', writableRoots: [],
          readOnlyAccess: { type: 'restricted', includePlatformDefaults: false, readableRoots: [cwd] },
          networkAccess: false,
        },
        ...(structured ? { outputSchema: OPERATOR_CHAT_OUTPUT_SCHEMA } : {}),
        ...(model ? { model } : {}),
      }).then(result => {
        if (result.turn?.id && activeTurn) activeTurn.turnId = result.turn.id;
        if (result.turn?.status === 'failed' && activeTurn) {
          clearTimeout(activeTurn.timer);
          const current = activeTurn;
          activeTurn = null;
          current.resolve({ status: 'failed', text: current.text, reason: 'Codex turn failed to start' });
        }
      }).catch(() => {
        if (!activeTurn) return;
        clearTimeout(activeTurn.timer);
        const current = activeTurn;
        activeTurn = null;
        current.resolve({ status: 'unknown', text: current.text, reason: 'Codex turn start failed; outcome is unknown' });
      });
    });
  }

  function close() {
    if (state === 'closed') { cleanupWorkDir(); return; }
    state = 'closed';
    if (activeTurn) {
      clearTimeout(activeTurn.timer);
      const current = activeTurn;
      activeTurn = null;
      current.resolve({ status: 'unknown', text: current.text, reason: 'Chat client closed during a turn' });
    }
    failAll('Codex app-server closed');
    child?.kill();
    try { cleanupWorkDir(); } catch { /* Best-effort cleanup; exit handler retries. */ }
  }

  return {
    connect,
    sendMessage,
    close,
    onEvent(listener) { listeners.add(listener); return () => listeners.delete(listener); },
    get state() { return state; },
    get threadId() { return threadId; },
  };
}
