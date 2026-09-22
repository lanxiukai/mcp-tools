# Upstream provenance

This directory is a vendored local fork of
[tomoki1207/vscode-pdfviewer](https://github.com/tomoki1207/vscode-pdfviewer),
imported from commit `d5f1ea28d1826dad60a3f4b6f025109caea9b4c2` on 2026-09-22.
It is tracked by the parent repository, without a nested Git repository or a
separate GitHub fork. Wrapper code retains its [MIT license](LICENSE).

The bundled viewer is upstream PDF.js 3.1.81, covered by
[Mozilla's Apache-2.0 license](lib/LICENSE), with font and CMap notices retained.
This fork preserves the upstream viewer interface. Its own link bridge lives
in `lib/local-links.js`; the PDF.js distribution is unchanged. PDF scripting
and eval-based rendering are disabled. Updating the engine is separate work
and requires rebuilding/testing the HTML template and viewer integration.

Local changes cover VS Code/WSL URI resolution, local link click handling,
reviewed conversion targets, editor routing, navigation messages, and focused
tests. Package identity and editor view type differ from upstream so installing
this build does not overwrite the original extension.
