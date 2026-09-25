const { test, after } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const Module = require('node:module');
const { URI } = require('vscode-uri');

class FileSystemError extends Error { constructor(code) { super(code); this.code = code; } }
const calls = [];
let choices = [];
// Remote URI paths are POSIX paths even when the test host is Windows.
const remoteRoot = '/workspace';
const localPath = uri => path.join(temporary, path.posix.relative(remoteRoot, uri.path));
const mockVscode = {
  Uri: { from: value => URI.from(value), parse: value => URI.parse(value), joinPath: (uri, part) => uri.with({ path: path.posix.join(uri.path, part) }) },
  FileSystemError, FileType: { File: 1 }, ViewColumn: { Active: -1 },
  workspace: {
    fs: {
      async stat(uri) { try { const s = await fs.stat(localPath(uri)); return { type: s.isFile() ? 1 : 2 }; } catch (error) { throw new FileSystemError(error.code === 'ENOENT' ? 'FileNotFound' : error.code); } },
      async readDirectory(uri) { return (await fs.readdir(localPath(uri), { withFileTypes: true })).map(e => [e.name, e.isFile() ? 1 : 2]); },
    },
    getConfiguration() { return { get: (_key, fallback) => fallback }; },
  },
  commands: { async executeCommand(...args) { calls.push(args); } },
  env: { async openExternal(uri) { calls.push(['external', uri]); } },
  window: { async showQuickPick(items) { choices = items; return items[0]; } },
};
const originalLoad = Module._load;
Module._load = function (id, ...args) { return id === 'vscode' ? mockVscode : originalLoad.call(this, id, ...args); };
const { openDocumentLink } = require('../out/src/openLink');
Module._load = originalLoad;
let temporary;
after(async () => { if (temporary) await fs.rm(temporary, { recursive: true, force: true }); });

test('PDFs use the PDF editor, other formats use VS Code, and explicit source choices are honored', async () => {
  temporary = await fs.mkdtemp(path.join(os.tmpdir(), 'pdf-local-links-'));
  const source = URI.from({ scheme: 'vscode-remote', authority: 'wsl+Ubuntu', path: path.posix.join(remoteRoot, 'index.pdf') });
  for (const name of ['guide.md', 'guide.pdf', 'example.py', 'README.md', 'page.html']) await fs.writeFile(path.join(temporary, name), 'fixture');
  const navigate = async (...args) => calls.push(['pdf', ...args]);
  await openDocumentLink('guide.pdf#page=2', source, true, navigate);
  assert.equal(calls.at(-1)[0], 'pdf');
  assert.equal(calls.at(-1)[1].authority, source.authority);
  assert.equal(calls.at(-1)[2], 'page=2');
  for (const name of ['example.py', 'README.md', 'page.html', 'guide.md']) {
    await openDocumentLink(name, source, true, navigate);
    assert.equal(calls.at(-1)[0], 'vscode.open');
    assert.equal(path.posix.basename(calls.at(-1)[1].path), name);
  }
  const fragment = 'part & notes+100%';
  await openDocumentLink('guide.pdf#nameddest=' + encodeURIComponent(fragment), source, true, navigate);
  assert.equal(calls.at(-1)[2], 'nameddest=' + encodeURIComponent(fragment));
  await openDocumentLink('page.html#' + encodeURIComponent(fragment), source, true, navigate);
  assert.equal(calls.at(-1)[1].fragment, fragment);
  await openDocumentLink('guide.md', source, false, navigate);
  assert.equal(calls.at(-1)[0], 'pdf');
  await fs.writeFile(path.join(temporary, 'guide-dark.pdf'), 'fixture');
  await openDocumentLink('guide.md', source, false, navigate);
  assert.equal(choices.length, 3);
  assert.equal(calls.at(-1)[0], 'vscode.open');
  await assert.rejects(openDocumentLink('missing.pdf', source, true, navigate), /does not exist/);
  await openDocumentLink('https://example.com', source, true, navigate);
  assert.equal(calls.at(-1)[0], 'simpleBrowser.api.open');
});
