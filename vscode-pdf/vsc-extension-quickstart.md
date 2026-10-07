# PDF Local Links Development Quickstart

This component is a VS Code custom PDF editor. Its main files are:

| Path | Purpose |
|---|---|
| `package.json` | Extension identity, custom editor, settings, and build scripts |
| `src/` | VS Code host integration and viewer HTML |
| `lib/` | Vendored PDF.js assets and the local-link bridge |
| `test/` | Node tests with VS Code host fixtures |
| `tsconfig.json` | TypeScript compilation into `out/` |

## Build and test

From `vscode-pdf/`, use Node.js 24:

```bash
npm ci --ignore-scripts
npm test
npm run package
```

`npm test` compiles the extension and runs the Node tests. Packaging creates
`pdf-local-links.vsix`. During development, `npm run watch` rebuilds changed
TypeScript; it does not automatically launch a VS Code Extension Development Host.

## Check the installed editor

Install the VSIX through **Extensions → Install from VSIX**, disable
`tomoki1207.pdf`, and open a PDF with **PDF Local Links**. Check local links,
verified chapter destinations, external-browser links, and file refresh in the
target VS Code/WSL setup. Repackage and reinstall after changes.

The [component README](README.md) documents editor associations, the optional
actual PDF.js browser test, and the remaining manual Remote WSL acceptance
checks. [UPSTREAM.md](UPSTREAM.md) records the vendored viewer's provenance.
