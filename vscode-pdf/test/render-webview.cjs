// Render the actual extension HTML for the Playwright integration test.
const path = require('node:path');
const Module = require('node:module');
const { URI } = require('vscode-uri');
const originalLoad = Module._load;
const defaults = { 'default.cursor': 'select', 'default.scale': 'page-fit', 'default.sidebar': false, 'default.scrollMode': 'vertical', 'default.spreadMode': 'none' };
Module._load = function (id, ...args) {
  return id === 'vscode' ? { Uri: URI, workspace: { getConfiguration: () => ({ get: key => defaults[key] }) } } : originalLoad.call(this, id, ...args);
};
const { PdfPreview } = require('../out/src/pdfPreview');
Module._load = originalLoad;
const extensionRoot = path.resolve(__dirname, '..');
const resource = path.resolve(process.argv[2]);
const base = process.argv[3];
const preview = Object.create(PdfPreview.prototype);
preview.extensionRoot = URI.file(extensionRoot);
preview.resource = URI.from({ scheme: 'vscode-remote', authority: 'wsl+test', path: resource });
preview.webviewEditor = { webview: {
  cspSource: base,
  asWebviewUri(uri) {
    return URI.parse(base + (uri.path.startsWith(extensionRoot + '/') ? '/extension/' + uri.path.slice(extensionRoot.length + 1) : '/documents/' + path.basename(uri.path)));
  },
} };
process.stdout.write(preview.getWebviewContents());
