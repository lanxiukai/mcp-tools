# PDF Local Links

A local fork of `vscode-pdf` that opens linked documents in VS Code, including
files in another repository on the same WSL connection. The upstream PDF.js
toolbar, search, zoom, and reading interface are retained. See
[UPSTREAM.md](UPSTREAM.md) for provenance and licenses.

## Build and install

Use Node.js 24 and npm from this directory:

```bash
npm ci --ignore-scripts
npm test
npm run package
```

The result is `pdf-local-links.vsix`. In the Windows VS Code Extensions view,
choose **Install from VSIX** and select that file through the WSL filesystem.
The extension ID is `lanxiukai.pdf-local-links`. It runs as a UI extension on
the Windows side; workspace files are accessed through VS Code's filesystem
API. Disable `tomoki1207.pdf`, then use **Reopen Editor With → Configure default
editor → PDF Local Links** for PDFs. Alternatively, select this editor through
VS Code's `workbench.editorAssociations` setting:

```json
{
  "workbench.editorAssociations": {
    "*.pdf": "pdfLocalLinks.preview"
  }
}
```

Existing `pdf-preview.default.*` cursor, zoom, sidebar, scroll, and spread
preferences are reused. `pdf-local-links.webLinks` is `vscode` by default;
set it to `external` to use the operating system's browser. VS Code's built-in
browser command selects its integrated browser when available, otherwise the
Simple Browser; some sites cannot be embedded by Simple Browser.

## Link behavior

- Local PDFs open with this PDF editor. Other local formats, including Markdown,
  HTML, Python, and images, open through VS Code's registered editor.
- Relative paths resolve from the current PDF; absolute file URLs retain the
  current remote connection. Cross-repository paths do not require adding every
  repository to the workspace. Links cannot switch remote authorities.
- The [conversion tools](../format-conversion/README.md#clickable-pdf-links)
  inspect Markdown/HTML references and select their actual existing PDFs.
  Their PDF metadata records that those choices have already been made. The
  viewer respects the stored destinations, including explicit source-file
  choices. No linked script or document is executed.
- For older PDFs, a Markdown/HTML link uses its unique same-stem PDF if present.
  Multiple filename variants or competing Markdown/HTML sources produce a
  picker, including an option to open the source. Arbitrarily renamed legacy
  PDFs require regeneration using a reviewed conversion mapping.
- PDF `#page=N`, supported PDF.js viewing parameters, and existing named PDF
  destinations are supported. Markdown/HTML anchors do not automatically
  become PDF destinations. An unavailable destination opens the document and
  displays a notice; this fork does not synthesize chapter coordinates.
- Missing files produce an error identifying the target. HTTP/HTTPS links open
  according to `pdf-local-links.webLinks`. Other URI schemes are not expanded
  into local file or command execution.

## Verification

`npm test` compiles the extension and exercises URL resolution, WSL authority
preservation, source/PDF selection, and VS Code editor routing with a host API
fixture. From the parent repository's CPU environment, the optional Chromium
test renders the actual extension HTML and PDF.js assets and clicks the link
annotations:

```bash
cd ../environments/mcp-local
MCP_TOOLS_CHROMIUM_TESTS=1 .venv/bin/python -m pytest -q \
  ../../test/format_conversion/test_pdf_reader.py
```

The browser test needs loopback sockets and an installed Playwright Chromium.
It checks click messages, destination navigation, and reload position. It does
not replace an interactive acceptance check in Windows VS Code with Remote WSL.
The extension is packaged locally; publication to the Marketplace is separate.
