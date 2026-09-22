"""Exercise the actual fork's webview and PDF.js annotation layer in Chromium.

Opt in with MCP_TOOLS_CHROMIUM_TESTS=1 after npm ci and npm run compile in
vscode-pdf. VS Code host routing is separately tested by the extension suite.
"""

import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

import fitz
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "format-conversion"))
import converter  # noqa: E402


def wait_for_state(page, expression):
    # Playwright's wait_for_function uses eval inside the page, which the real
    # extension CSP correctly forbids. CDP evaluation itself needs no relaxation.
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if page.evaluate("() => (" + expression + ")"):
            return
        page.wait_for_timeout(50)
    raise AssertionError(f"Viewer did not reach {expression}: {page.locator('body').inner_text()[:500]}")


@pytest.mark.skipif(os.environ.get("MCP_TOOLS_CHROMIUM_TESTS") != "1", reason="Chromium integration is opt-in")
def test_reader_clicks_local_links_web_links_and_navigation(tmp_path):
    from playwright.sync_api import sync_playwright

    extension = ROOT / "vscode-pdf"
    assert (extension / "out/src/pdfPreview.js").is_file(), "Compile vscode-pdf first"
    (tmp_path / "README.md").write_text("# Source only")
    (tmp_path / "example.py").write_text("# Open this in VS Code; never execute it")
    with fitz.open() as target:
        for text in ("First page", "Second page"):
            target.new_page().insert_text((30, 30), text)
        target.save(tmp_path / "target.pdf")
    source = tmp_path / "links.md"
    source.write_text("# Local document links\n\n[PDF](target.pdf#page=2)\n\n[Source](README.md)\n\n[Script](example.py)\n\n[Web](https://example.com)")
    converter.convert_markdown_to_pdf(str(source), str(tmp_path / "links.pdf"))

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            url = urlsplit(self.path)
            request_path = unquote(url.path)
            if request_path == "/viewer":
                document = "target.pdf" if url.query == "target" else "links.pdf"
                data = subprocess.check_output([
                    "node", str(extension / "test/render-webview.cjs"),
                    str(tmp_path / document), f"http://127.0.0.1:{self.server.server_port}",
                ])
                content_type = "text/html"
            else:
                roots = {"extension": extension, "documents": tmp_path}
                parts = request_path.strip("/").split("/", 1)
                if len(parts) != 2 or parts[0] not in roots:
                    self.send_error(404)
                    return
                path = (roots[parts[0]] / parts[1]).resolve()
                if not path.is_relative_to(roots[parts[0]]) or not path.is_file():
                    self.send_error(404)
                    return
                data = path.read_bytes()
                content_type = {".js": "text/javascript", ".css": "text/css", ".pdf": "application/pdf"}.get(path.suffix, "application/octet-stream")
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page()
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.add_init_script("window.messages=[]; window.acquireVsCodeApi=()=>({postMessage:message=>window.messages.push(message)});")
            base = f"http://127.0.0.1:{server.server_port}/viewer"
            page.goto(base)
            wait_for_state(page, "messages.some(m=>m.type==='ready')")
            for suffix in ("target.pdf#page=2", "README.md", "example.py"):
                page.locator(f'.linkAnnotation a[href$="{suffix}"]').click()
                message = page.evaluate("messages.at(-1)")
                assert message["type"] == "open-document-link"
                assert message["href"].endswith(suffix)
                assert message["resolvedByConverter"] is True
            page.locator('.linkAnnotation a[href="https://example.com/"]').or_(page.locator('.linkAnnotation a[href="https://example.com"]')).click()
            assert page.evaluate("messages.at(-1).href").startswith("https://example.com")
            page.goto(base + "?target")
            wait_for_state(page, "messages.some(m=>m.type==='ready')")
            page.evaluate("window.postMessage({type:'navigate',fragment:'page=2'}, '*')")
            wait_for_state(page, "PDFViewerApplication.page===2")
            page.evaluate("window.postMessage({type:'navigate',fragment:'nonexistent-anchor'}, '*')")
            wait_for_state(page, "messages.some(m=>m.type==='missing-destination')")
            page.evaluate("window.messages=[]; window.postMessage({type:'reload'}, '*')")
            wait_for_state(page, "messages.some(m=>m.type==='ready')")
            wait_for_state(page, "PDFViewerApplication.page===2")
            assert errors == []
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
