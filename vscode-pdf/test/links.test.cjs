const { test } = require('node:test');
const assert = require('node:assert/strict');
const { resolveLink } = require('../out/src/linkTarget');
const { isLocalLink, enableLocalAnnotations } = require('../lib/local-links');

const remote = { scheme: 'vscode-remote', authority: 'wsl+Ubuntu', path: '/home/user/repo-a/docs/index.pdf' };

test('absolute and relative links preserve the remote filesystem across repositories', () => {
  for (const raw of ['../../repo-b/guide%20notes.pdf#page=3', 'file:///home/user/repo-b/guide%20notes.pdf#page=3']) {
    const { uri } = resolveLink(raw, remote);
    assert.equal(uri.scheme, remote.scheme);
    assert.equal(uri.authority, remote.authority);
    assert.equal(uri.path, '/home/user/repo-b/guide notes.pdf');
    assert.equal(uri.fragment, 'page=3');
  }
});

test('source directories with literal percent, hash, spaces, and Unicode survive resolution', () => {
  const source = { ...remote, path: '/home/user/%20 # notes/学习/index.pdf' };
  assert.equal(resolveLink('guide%23one.py', source).uri.path, '/home/user/%20 # notes/学习/guide#one.py');
});

test('local Windows file URIs keep their original filesystem', () => {
  const source = { scheme: 'file', authority: '', path: '/C:/notes/index.pdf' };
  assert.equal(resolveLink('../other/code.py', source).uri.path, '/C:/other/code.py');
});

test('foreign authorities and executable URI schemes never become editor commands', () => {
  for (const raw of ['javascript:alert(1)', 'command:workbench.action.closeWindow', 'data:text/html,test', 'vscode-remote://ssh-remote+other/etc/passwd', 'file://other-host/etc/passwd']) {
    assert.throws(() => resolveLink(raw, remote));
    if (!raw.startsWith('file:') && !raw.startsWith('vscode-remote:')) assert.equal(isLocalLink(raw), false);
  }
});

test('web URLs remain web URLs and encoded filename fragments are decoded once', () => {
  assert.deepEqual(resolveLink('https://example.com/guide.md?q=1#x', remote), { kind: 'web', url: 'https://example.com/guide.md?q=1#x' });
  assert.equal(resolveLink('file:///home/user/a%2520b.md', remote).uri.path, '/home/user/a%20b.md');
});

test('only local unsafe annotations gain clickable URLs; internal destinations survive', () => {
  const annotations = [
    { subtype: 'Link', unsafeUrl: 'file:///home/user/guide.pdf' },
    { subtype: 'Link', unsafeUrl: '../code.py' },
    { subtype: 'Link', unsafeUrl: 'javascript:alert(1)' },
    { subtype: 'Link', dest: 'chapter-1' },
    { subtype: 'Link', url: 'https://example.com' },
  ];
  enableLocalAnnotations(annotations);
  assert.equal(annotations[0].url, annotations[0].unsafeUrl);
  assert.equal(annotations[1].url, '../code.py');
  assert.equal(annotations[2].url, undefined);
  assert.equal(annotations[3].dest, 'chapter-1');
  assert.equal(annotations[4].url, 'https://example.com');
});
