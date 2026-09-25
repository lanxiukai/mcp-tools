const { test } = require('node:test');
const assert = require('node:assert/strict');
const { navigate } = require('../lib/local-links');
const { resolveLink } = require('../out/src/linkTarget');

function app(names = {}) {
  const calls = [];
  return {
    calls,
    pdfDocument: { numPages: 4, getDestination: async name => names[name] || null },
    pdfLinkService: {
      setHash: value => calls.push(['hash', value]),
      goToDestination: async value => calls.push(['destination', value]),
    },
  };
}

test('cross-file named destinations are decoded once, preserving reserved characters', async () => {
  const name = '章节 & 100% + details=a%20b';
  const destination = [2, { name: 'XYZ' }, 30, 600, null];
  const application = app({ [name]: destination });
  const { uri } = resolveLink('target.pdf#nameddest=' + encodeURIComponent(name),
    { scheme: 'vscode-remote', authority: 'wsl+Ubuntu', path: '/docs/source.pdf' });
  assert.equal(await navigate(application, uri.fragment), true);
  assert.deepEqual(application.calls, [['destination', destination]]);
});

test('encoded and raw explicit coordinates work on an already open PDF', async () => {
  const application = app();
  for (const value of [[1, { name: 'XYZ' }, 60.25, 612.875, null], [3, { name: 'FitH' }, 700]]) {
    const fragment = JSON.stringify(value);
    assert.equal(await navigate(application, encodeURIComponent(fragment)), true);
    assert.equal(await navigate(application, fragment), true);
    assert.deepEqual(application.calls.at(-1), ['destination', value]);
  }
});

test('viewing parameters can appear in any order and named destinations take priority', async () => {
  const destination = [3, { name: 'Fit' }];
  const application = app({ chapter: destination });
  assert.equal(await navigate(application, 'pagemode=bookmarks&page=3'), true);
  assert.deepEqual(application.calls.at(-1), ['hash', 'pagemode=bookmarks&page=3']);
  assert.equal(await navigate(application, 'page=2&nameddest=chapter'), true);
  assert.deepEqual(application.calls.at(-1), ['destination', destination]);
});

test('unavailable or malformed destinations report failure without navigating', async () => {
  const application = app();
  for (const fragment of ['absent', 'nameddest=absent&page=2', 'page=5', 'page=0',
    'page=2&page=3', 'page=2x', '%zz', '[9,{"name":"Fit"}]',
    '[0,{"name":"XYZ"}]', '[0,{"name":"XYZ"},"bad",0,null]',
    '[0,{"name":"constructor"}]']) {
    assert.equal(await navigate(application, fragment), false, fragment);
  }
  assert.deepEqual(application.calls, []);
});
