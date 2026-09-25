const { test } = require('node:test');
const assert = require('node:assert/strict');
const Module = require('node:module');
const { URI } = require('vscode-uri');

function event() {
  const listeners = new Set();
  return {
    subscribe(fn) { listeners.add(fn); return { dispose: () => listeners.delete(fn) }; },
    fire(value) { for (const fn of [...listeners]) fn(value); },
  };
}

let current;
const originalLoad = Module._load;
Module._load = function (id, ...args) {
  if (id !== 'vscode') return originalLoad.call(this, id, ...args);
  return {
    Uri: URI,
    FileType: { File: 1 },
    RelativePattern: class { constructor(baseUri, pattern) { Object.assign(this, { baseUri, pattern }); } },
    workspace: {
      fs: { async stat(uri) {
        current.stats.push(uri);
        if (!current.exists) throw new Error('File not found');
        return { type: 1, size: current.size };
      } },
      getConfiguration: () => ({ get: () => undefined }),
      createFileSystemWatcher(pattern) {
        current.pattern = pattern;
        return {
          onDidChange: current.change.subscribe,
          onDidCreate: current.create.subscribe,
          onDidDelete: current.remove.subscribe,
          dispose() { current.watcherDisposed = true; },
        };
      },
    },
    window: { showWarningMessage: message => current.warnings.push(message) },
  };
};
const { PdfPreview } = require('../out/src/pdfPreview');
Module._load = originalLoad;

function fixture(t, ready = true) {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  current = {
    change: event(), create: event(), remove: event(), message: event(), state: event(), closed: event(),
    exists: true, size: 100, delivered: true, stats: [], posts: [], warnings: [], disposed: false,
  };
  const state = current;
  state.resource = URI.from({ scheme: 'vscode-remote', authority: 'wsl+Ubuntu', path: '/workspace/docs/guide [draft].pdf' });
  state.panel = {
    visible: true, active: true,
    webview: {
      onDidReceiveMessage: state.message.subscribe,
      asWebviewUri: uri => uri,
      cspSource: 'test:',
      async postMessage(message) { state.posts.push(message); return state.delivered; },
    },
    onDidChangeViewState: state.state.subscribe,
    onDidDispose: state.closed.subscribe,
    dispose() { state.disposed = true; state.closed.fire(); },
  };
  state.preview = new PdfPreview(URI.file('/extension'), state.resource.with({ fragment: 'page=2' }), state.panel, async () => {});
  if (ready) state.message.fire({ type: 'ready' });
  t.after(() => state.preview.dispose());
  return state;
}

async function tick(t, ms) {
  t.mock.timers.tick(ms);
  await new Promise(resolve => setImmediate(resolve));
}

test('file events are debounced and only one reload is in flight until ready', async t => {
  const s = fixture(t);
  assert.equal(s.pattern.baseUri.authority, 'wsl+Ubuntu');
  assert.equal(s.pattern.baseUri.path, '/workspace/docs');
  s.change.fire(s.resource.with({ path: '/workspace/docs/other.pdf' }));
  await tick(t, 200);
  assert.equal(s.posts.length, 0);
  for (let i = 0; i < 5; i++) s.change.fire(s.resource);
  await tick(t, 199);
  assert.equal(s.posts.length, 0);
  await tick(t, 1);
  assert.deepEqual(s.posts, [{ type: 'reload' }]);
  s.change.fire(s.resource);
  await tick(t, 200);
  assert.equal(s.posts.length, 1);
  s.message.fire({ type: 'ready' });
  await tick(t, 0);
  assert.equal(s.posts.length, 2);
  assert.equal(s.stats[0].fragment, '');
});

test('updates during initial loading or in a hidden tab are delivered when ready and visible', async t => {
  const s = fixture(t, false);
  s.change.fire(s.resource);
  await tick(t, 200);
  assert.equal(s.posts.length, 0);
  s.message.fire({ type: 'ready' });
  await tick(t, 0);
  assert.equal(s.posts.length, 1);
  s.message.fire({ type: 'ready' });
  s.panel.visible = false;
  s.change.fire(s.resource);
  await tick(t, 200);
  assert.equal(s.posts.length, 1);
  s.panel.visible = true;
  s.state.fire();
  await tick(t, 0);
  assert.equal(s.posts.length, 2);
});

test('delete/create replacement keeps the editor open and reloads the replacement', async t => {
  const s = fixture(t);
  s.exists = false;
  s.remove.fire(s.resource);
  await tick(t, 200);
  assert.equal(s.posts.length, 0);
  assert.equal(s.disposed, false);
  s.exists = true;
  s.create.fire(s.resource);
  await tick(t, 200);
  assert.deepEqual(s.posts, [{ type: 'reload' }]);
  assert.deepEqual(s.warnings, []);
});

test('incomplete saves and failed loads have bounded retries and recover on another update', async t => {
  const s = fixture(t);
  s.size = 0;
  s.change.fire(s.resource);
  await tick(t, 200);
  for (let i = 0; i < 3; i++) await tick(t, 500);
  assert.equal(s.stats.length, 4);
  assert.equal(s.posts.length, 0);
  assert.equal(s.warnings.length, 1);
  await tick(t, 5000);
  assert.equal(s.stats.length, 4);
  s.size = 100;
  s.change.fire(s.resource);
  await tick(t, 200);
  assert.equal(s.posts.length, 1);
  s.message.fire({ type: 'reload-failed' });
  await tick(t, 500);
  assert.equal(s.posts.length, 2);
  s.message.fire({ type: 'ready' });
  await tick(t, 1000);
  assert.equal(s.posts.length, 2);
});

test('an undeliverable message is retried and closing a tab cancels watchers and pending reloads', async t => {
  const s = fixture(t);
  s.delivered = false;
  s.change.fire(s.resource);
  await tick(t, 200);
  s.delivered = true;
  await tick(t, 500);
  assert.equal(s.posts.length, 2);
  s.message.fire({ type: 'ready' });
  s.change.fire(s.resource);
  s.panel.dispose();
  await tick(t, 1000);
  assert.equal(s.posts.length, 2);
  assert.equal(s.watcherDisposed, true);
});
