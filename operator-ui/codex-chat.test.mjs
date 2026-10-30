import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import { existsSync } from 'node:fs';
import { PassThrough } from 'node:stream';
import test from 'node:test';
import { createCodexChat } from './codex-chat.mjs';

class FakeChild extends EventEmitter {
  constructor(mode = 'complete') {
    super();
    this.mode = mode;
    this.killed = false;
    this.stdout = new PassThrough();
    this.stderr = new PassThrough();
    this.stdin = new PassThrough();
    this.requests = [];
    let input = '';
    this.stdin.on('data', chunk => {
      input += chunk.toString();
      let nl;
      while ((nl = input.indexOf('\n')) >= 0) {
        const line = input.slice(0, nl);
        input = input.slice(nl + 1);
        if (line) this.respond(JSON.parse(line));
      }
    });
  }

  emitJson(value) { this.stdout.write(`${JSON.stringify(value)}\n`); }
  respond(message) {
    this.requests.push(message);
    if (message.method === 'initialize') this.emitJson({ id: message.id, result: {} });
    else if (message.method === 'config/read') this.emitJson({ id: message.id, result: { config: {
      web_search: 'disabled', mcp_servers: this.mode === 'unsafe-mcp' ? { configured: { enabled: true } } : { configured: { enabled: false } },
      tools: this.mode === 'legacy-null-tools' ? null : this.mode === 'legacy-tools-config' ? {} : { view_image: this.mode === 'unsafe-image' },
      features: { apps: false, shell_tool: this.mode === 'unsafe' ? true : false, unified_exec: false, multi_agent: false,
        remote_plugin: false, browser_use: false, computer_use: false, shell_snapshot: false,
        memories: false, goals: false, hooks: false },
    } } });
    else if (message.method === 'thread/start') {
      this.emitJson({ id: message.id, result: { thread: { id: 'thread-ephemeral', ephemeral: true }, instructionSources: [] } });
      this.emitJson({ method: 'thread/started', params: { thread: { id: 'thread-ephemeral', ephemeral: true } } });
    }
    else if (message.method === 'turn/start') {
      this.emitJson({ id: message.id, result: { turn: { id: 'turn-fake', status: 'inProgress' } } });
      setTimeout(() => {
        this.emitJson({ method: 'item/started', params: { item: { id: 'user-1', type: 'userMessage' } } });
        if (this.mode === 'tool') this.emitJson({ method: 'item/started', params: { item: { id: 'tool-1', type: 'commandExecution' } } });
        const delta = this.mode === 'oversize' ? 'x'.repeat(100_001)
          : this.mode === 'structured' ? '{"intent":"pause","message":"Proposal only."}'
            : 'Hello\u001b[31m there\u001b[0m';
        this.emitJson({ method: 'item/agentMessage/delta', params: { delta } });
        this.emitJson({ method: 'turn/completed', params: { turn: { id: 'turn-fake', status: ['complete', 'tool', 'oversize', 'structured', 'legacy-tools-config', 'legacy-null-tools', 'unsafe-image'].includes(this.mode) ? 'completed' : this.mode } } });
      }, 15);
    } else if (message.method === 'turn/interrupt') this.emitJson({ id: message.id, result: {} });
  }
  kill() { this.killed = true; return true; }
}

function fixture(mode = 'complete', env, mcpServers = [{ name: 'disabled', enabled: false }]) {
  const launches = [];
  let child;
  const client = createCodexChat({
    command: 'codex-test',
    env: env || { PATH: 'path', USERPROFILE: 'home', SYSTEMROOT: 'windows', OPENAI_API_KEY: 'secret', BANANLOOP_STATE_DIR: 'private' },
    spawnProcess: (...args) => {
      launches.push(args);
      child = new FakeChild(mode);
      return child;
    },
    listMcpServers: () => mcpServers,
    requestTimeoutMs: 500,
    turnTimeoutMs: 1000,
  });
  return { client, launches, get child() { return child; } };
}

test('uses documented JSONL handshake and streams a sanitized completed turn', async () => {
  const f = fixture();
  const events = [];
  f.client.onEvent(event => events.push(event));
  await f.client.connect();
  const response = await f.client.sendMessage('hello');
  assert.deepEqual(response, { status: 'completed', text: 'Hello there' });
  const [program, argv, options] = f.launches[0];
  assert.equal(program, 'codex-test');
  assert.deepEqual(argv.slice(0, 3), ['app-server', '--listen', 'stdio://']);
  assert.equal(options.shell, false);
  assert.match(options.cwd, /bananloop-operator-chat-/);
  const tempRoot = options.cwd;
  assert.equal(options.env.OPENAI_API_KEY, undefined);
  assert.equal(options.env.BANANLOOP_STATE_DIR, undefined);
  assert.equal(options.env.USERPROFILE, 'home');
  assert(events.some(event => event.type === 'delta' && event.text === 'Hello there'));
  assert.deepEqual(events.filter(event => event.type === 'thread-started').map(event => event.ephemeral), [true]);
  assert.equal(f.client.threadId, 'thread-ephemeral');
  assert.deepEqual(f.child.requests.map(request => request.method), [
    'initialize', 'initialized', 'config/read', 'thread/start', 'turn/start',
  ]);
  const turn = f.child.requests.find(request => request.method === 'turn/start');
  assert.equal(turn.params.threadId, 'thread-ephemeral');
  assert.deepEqual(turn.params.sandboxPolicy, {
    type: 'workspaceWrite', writableRoots: [],
    readOnlyAccess: { type: 'restricted', includePlatformDefaults: false, readableRoots: [options.cwd] },
    networkAccess: false,
  });
  assert.equal(turn.params.input[0].text.includes('campaign'), false);
  const baseThread = f.child.requests.find(request => request.method === 'thread/start');
  assert.equal(Object.hasOwn(baseThread.params, 'input'), false);
  assert.equal(baseThread.params.ephemeral, true);
  assert.equal(baseThread.params.sandbox, 'read-only');
  assert.equal(baseThread.params.approvalPolicy, 'never');
  assert.equal(f.child.requests.some(request => request.method === 'thread/fork'), false);
  assert(argv.includes('--strict-config'));
  assert(argv.includes('mcp_servers={}'));
  assert(argv.includes('web_search="disabled"'));
  assert.equal(argv.includes('tools.view_image=false'), false);
  f.client.close();
  assert.equal(f.child.killed, true);
  assert.equal(existsSync(tempRoot), false);
});

test('rejects overlapping turns and never retries a failed or interrupted outcome', async () => {
  for (const status of ['failed', 'interrupted']) {
    const f = fixture(status);
    await f.client.connect();
    const first = f.client.sendMessage('one');
    await assert.rejects(f.client.sendMessage('two'), /already in flight/);
    assert.equal((await first).status, status);
    f.client.close();
  }
});

test('tool activity is unsupported and the turn is interrupted without replay', async () => {
  const f = fixture('tool');
  await f.client.connect();
  const result = await f.client.sendMessage('hello');
  assert.equal(result.status, 'unknown');
  assert.equal(result.reason, 'Codex requested an unsupported tool or non-chat action');
  f.client.close();
});

test('late server success after the client output limit remains an unknown outcome', async () => {
  const f = fixture('oversize');
  await f.client.connect();
  const result = await f.client.sendMessage('hello');
  assert.equal(result.status, 'unknown');
  assert.equal(result.serverStatus, 'completed');
  assert.match(result.reason, /output limit/);
  assert.equal(result.text, '');
  f.client.close();
});

test('structured turn uses a fixed output schema and suppresses raw JSON deltas', async () => {
  const f = fixture('structured');
  const events = [];
  f.client.onEvent(event => events.push(event));
  await f.client.connect();
  const result = await f.client.sendMessage('classify only', { structured: true });
  const turn = f.child.requests.find(request => request.method === 'turn/start');
  assert.equal(turn.params.outputSchema.additionalProperties, false);
  assert.deepEqual(turn.params.outputSchema.required, ['intent', 'message']);
  assert.equal(turn.params.outputSchema.properties.intent.anyOf[0].const, 'pause');
  assert.equal(events.some(event => event.type === 'delta'), false);
  assert.equal(result.text, '{"intent":"pause","message":"Proposal only."}');
  f.client.close();
});

test('rejects oversized input before sending it to app-server', async () => {
  const f = fixture();
  await f.client.connect();
  await assert.rejects(f.client.sendMessage('x'.repeat(16_001)), /exceeds the configured limit/);
  f.client.close();
});

test('process exit during a turn reports unknown and does not replay', async () => {
  const f = fixture();
  await f.client.connect();
  const resultPromise = f.client.sendMessage('hello');
  await new Promise(resolve => setTimeout(resolve, 2));
  f.child.emit('exit', 1, null);
  const result = await resultPromise;
  assert.equal(result.status, 'unknown');
  assert.match(result.reason, /outcome is unknown/);
  f.client.close();
});

test('malformed and oversized server lines kill the transport without exposing payload', async () => {
  for (const payload of ['not-json\n', `${'x'.repeat(1024 * 1024 + 1)}\n`]) {
    const f = fixture();
    await f.client.connect();
    f.child.stdout.write(payload);
    await new Promise(resolve => setTimeout(resolve, 1));
    assert.equal(f.child.killed, true);
    assert.equal(f.client.state, 'failed');
    f.client.close();
  }
});

test('fails closed if the effective Codex configuration did not disable tools', async () => {
  const bad = fixture('unsafe');
  await assert.rejects(bad.client.connect(), /safety configuration/);
  assert.deepEqual(bad.child.requests.map(request => request.method), ['initialize', 'initialized', 'config/read']);
  assert.equal(bad.client.state, 'failed');
  bad.client.close();
});

test('accepts older app-server config with no enabled optional tools', async () => {
  const legacy = fixture('legacy-tools-config');
  await legacy.client.connect();
  assert.equal((await legacy.client.sendMessage('hello')).status, 'completed');
  legacy.client.close();
});

test('accepts app-server config where optional tools are unset', async () => {
  const legacy = fixture('legacy-null-tools');
  await legacy.client.connect();
  assert.equal((await legacy.client.sendMessage('hello')).status, 'completed');
  legacy.client.close();
});

test('fails closed when an optional Codex tool is enabled', async () => {
  const unsafe = fixture('unsafe-image');
  await assert.rejects(unsafe.client.connect(), /safety configuration/);
  assert.deepEqual(unsafe.child.requests.map(request => request.method), ['initialize', 'initialized', 'config/read']);
  unsafe.client.close();
});

test('fails closed when any configured MCP server remains enabled', async () => {
  const unsafe = fixture('complete', undefined, [{ name: 'private', enabled: true }]);
  await assert.rejects(unsafe.client.connect(), /MCP servers to be disabled/);
  assert.equal(unsafe.launches.length, 0);
  assert.equal(unsafe.client.state, 'failed');
});

test('fails closed when MCP inventory cannot be verified', async () => {
  for (const mcpServers of [null, [{ name: 'missing-enabled-field' }]]) {
    const unsafe = fixture('complete', undefined, mcpServers);
    await assert.rejects(unsafe.client.connect(), /MCP configuration could not be verified/);
    assert.equal(unsafe.launches.length, 0);
    assert.equal(unsafe.client.state, 'failed');
    unsafe.client.close();
  }
});

test('cleans its temporary directory if app-server launch throws synchronously', async () => {
  const { readdirSync } = await import('node:fs');
  const { tmpdir } = await import('node:os');
  const before = new Set(readdirSync(tmpdir()));
  const client = createCodexChat({
    command: 'codex-test', env: { PATH: 'path' }, listMcpServers: () => [],
    spawnProcess: () => { throw new Error('missing executable'); },
  });
  await assert.rejects(client.connect(), /could not start/);
  assert.equal(client.state, 'failed');
  assert.deepEqual(readdirSync(tmpdir()).filter(name => name.startsWith('bananloop-operator-chat-') && !before.has(name)), []);
  client.close();
});
