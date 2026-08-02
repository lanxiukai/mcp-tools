#!/usr/bin/env python3
"""Smoke test for browser_fetch MCP server.

Run from the repository root with the shared MCP environment:
    conda run -n mcp-local python test/browser_fetch/smoke_test.py

Tests every public tool + every code path that doesn't require the
real internet beyond a known-stable lightweight target.
"""

from __future__ import annotations

import asyncio
import sys
import time
import traceback
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_BROWSER_FETCH_DIR = _REPO_ROOT / "browser-fetch"
sys.path.insert(0, str(_BROWSER_FETCH_DIR))
import browser_fetch_mcp_server as m  # noqa: E402

GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
DIM = "\033[2m"
NC = "\033[0m"

PASS = f"{GREEN}PASS{NC}"
FAIL = f"{RED}FAIL{NC}"
SKIP = f"{YELLOW}SKIP{NC}"


class Results:
    def __init__(self) -> None:
        self.passed = 0
        self.failed = 0
        self.skipped = 0
        self.failures: list[tuple[str, str]] = []

    def ok(self, name: str, detail: str = "") -> None:
        self.passed += 1
        suffix = f" {DIM}({detail}){NC}" if detail else ""
        print(f"  [{PASS}] {name}{suffix}")

    def bad(self, name: str, reason: str) -> None:
        self.failed += 1
        self.failures.append((name, reason))
        print(f"  [{FAIL}] {name}")
        print(f"         {RED}{reason}{NC}")

    def skip(self, name: str, reason: str) -> None:
        self.skipped += 1
        print(f"  [{SKIP}] {name} {DIM}({reason}){NC}")

    def header(self, title: str) -> None:
        print(f"\n{title}")
        print("-" * len(title))


R = Results()


def assert_eq(actual, expected, *, msg: str) -> None:
    if actual != expected:
        raise AssertionError(f"{msg}: expected {expected!r}, got {actual!r}")


def assert_true(cond: bool, *, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


def assert_in(needle: str, haystack: str, *, msg: str) -> None:
    if needle not in haystack:
        raise AssertionError(f"{msg}: {needle!r} not found in {haystack[:200]!r}...")


def assert_has_keys(d: dict, keys: list[str], *, msg: str) -> None:
    missing = [k for k in keys if k not in d]
    if missing:
        raise AssertionError(f"{msg}: missing keys {missing}; got keys {sorted(d.keys())}")


# ============================================================
# Test 1 — browser_status
# ============================================================

def test_browser_status() -> None:
    R.header("Test 1: browser_status()")
    try:
        s = m.browser_status()
        assert_has_keys(
            s,
            ["nodriver", "playwright", "trafilatura", "markdownify",
             "default_engine", "default_timeout", "default_headless", "user_agent"],
            msg="browser_status response shape",
        )
        for component in ["nodriver", "playwright", "trafilatura", "markdownify"]:
            assert_true(s[component]["available"] is True,
                        msg=f"{component}.available should be True, got {s[component]}")
        assert_eq(s["default_engine"], "nodriver", msg="default_engine")
        R.ok("browser_status returns expected shape", f"default_engine={s['default_engine']}")
        R.ok("all 4 components available")
    except Exception as e:
        R.bad("browser_status", f"{type(e).__name__}: {e}")
        traceback.print_exc()


# ============================================================
# Test 2 — fetch_page (auto engine, default markdown)
# ============================================================

async def test_fetch_page_auto() -> None:
    R.header("Test 2: fetch_page(example.com) — auto engine, markdown")
    try:
        r = await m._dispatch_fetch(
            "https://example.com",
            engine="auto", mode="markdown", timeout=30, headless=True,
            cookies_path=None, proxy_url=None, user_agent=None,
            wait_until="networkidle", wait_seconds=1.0,
        )
        assert_true("error" not in r, msg=f"got error: {r.get('error')}")
        assert_has_keys(
            r, ["content", "mode", "title", "final_url", "engine",
                "html_size", "content_size", "elapsed_seconds"],
            msg="fetch_page response shape",
        )
        assert_eq(r["mode"], "markdown", msg="mode echo")
        assert_eq(r["title"], "Example Domain", msg="page title")
        assert_in("documentation examples", r["content"], msg="content extraction")
        assert_true(r["engine"] in ("nodriver", "playwright"),
                    msg=f"engine should be nodriver or playwright, got {r['engine']}")
        R.ok("fetch_page (auto) succeeded",
             f"engine={r['engine']}, {r['elapsed_seconds']}s, {r['content_size']} chars")
    except Exception as e:
        R.bad("fetch_page auto", f"{type(e).__name__}: {e}")
        traceback.print_exc()


# ============================================================
# Test 3 — fetch_page_with_engine (explicit engine)
# ============================================================

async def test_explicit_engines() -> None:
    R.header("Test 3: fetch_page_with_engine — both engines")
    for engine in ("nodriver", "playwright"):
        try:
            r = await m._dispatch_fetch(
                "https://example.com",
                engine=engine, mode="markdown", timeout=30, headless=True,
                cookies_path=None, proxy_url=None, user_agent=None,
                wait_until="networkidle", wait_seconds=0.5,
            )
            assert_true("error" not in r, msg=f"{engine}: {r.get('error')}")
            assert_eq(r["engine"], engine, msg=f"{engine} engine echo")
            assert_in("Example Domain", r["title"], msg=f"{engine} title")
            R.ok(f"engine={engine}", f"{r['elapsed_seconds']}s")
        except Exception as e:
            R.bad(f"engine={engine}", f"{type(e).__name__}: {e}")
            traceback.print_exc()


# ============================================================
# Test 4 — All 4 output modes
# ============================================================

async def test_output_modes() -> None:
    R.header("Test 4: output modes (markdown / markdown_full / html / text)")
    for mode in ("markdown", "markdown_full", "html", "text"):
        try:
            r = await m._dispatch_fetch(
                "https://example.com",
                engine="playwright",  # use playwright for speed (~2.5s vs nodriver 4s)
                mode=mode, timeout=30, headless=True,
                cookies_path=None, proxy_url=None, user_agent=None,
                wait_until="networkidle", wait_seconds=0.5,
            )
            assert_true("error" not in r, msg=f"mode={mode}: {r.get('error')}")
            assert_eq(r["mode"], mode, msg=f"{mode} mode echo")
            content = r["content"]
            assert_true(len(content) > 0, msg=f"{mode}: empty content")

            BODY_PHRASE = "documentation examples"
            TITLE_PHRASE = "Example Domain"
            if mode == "html":
                assert_in("<html", content, msg=f"{mode}: should contain <html")
                assert_in(TITLE_PHRASE, content, msg=f"{mode}: title in HTML")
            elif mode == "text":
                assert_true("<html" not in content,
                            msg=f"{mode}: text mode should NOT contain HTML tags")
                assert_in(BODY_PHRASE, content, msg=f"{mode}: body extracted")
            elif mode == "markdown":
                assert_in(BODY_PHRASE, content, msg=f"{mode}: body extracted")
                assert_true(content.count("<html") == 0,
                            msg=f"{mode}: should not be raw HTML")
            elif mode == "markdown_full":
                assert_in(TITLE_PHRASE, content, msg=f"{mode}: title kept")
                assert_in(BODY_PHRASE, content, msg=f"{mode}: body kept")
                assert_true(content.count("<html") == 0,
                            msg=f"{mode}: should not be raw HTML")
            R.ok(f"mode={mode}", f"{len(content)} chars")
        except Exception as e:
            R.bad(f"mode={mode}", f"{type(e).__name__}: {e}")
            traceback.print_exc()


# ============================================================
# Test 5 — screenshot
# ============================================================

async def test_screenshot() -> None:
    R.header("Test 5: screenshot()")
    try:
        out_path = "/tmp/browser-fetch-smoke-test.png"
        Path(out_path).unlink(missing_ok=True)

        r = await m._screenshot_with_playwright(
            "https://example.com", out_path,
            timeout=30, headless=True, cookies_path=None, proxy_url=None,
            user_agent=None, wait_until="networkidle", wait_seconds=0.5,
            full_page=True,
        )
        assert_true("error" not in r, msg=f"screenshot error: {r.get('error')}")
        assert_eq(r["status"], "success", msg="status field")
        assert_true(Path(out_path).exists(), msg="PNG file should exist")
        size = Path(out_path).stat().st_size
        assert_true(size > 1000, msg=f"PNG suspiciously small: {size} bytes")
        # PNG magic bytes
        with open(out_path, "rb") as f:
            magic = f.read(8)
        assert_eq(magic, b"\x89PNG\r\n\x1a\n", msg="PNG magic bytes")
        R.ok("screenshot saved as valid PNG",
             f"{size} bytes, {r['elapsed_seconds']}s")
        Path(out_path).unlink()
    except Exception as e:
        R.bad("screenshot", f"{type(e).__name__}: {e}")
        traceback.print_exc()


# ============================================================
# Test 6 — Error paths (must return {error: ...}, not raise)
# ============================================================

async def test_error_paths() -> None:
    R.header("Test 6: error paths return {error: ...} dicts")

    cases: list[tuple[str, dict]] = [
        ("invalid URL scheme",
         dict(url="ftp://example.com", engine="auto", mode="markdown")),
        ("missing hostname",
         dict(url="http://", engine="auto", mode="markdown")),
        ("empty URL",
         dict(url="", engine="auto", mode="markdown")),
        ("invalid mode",
         dict(url="https://example.com", engine="auto", mode="latex")),
        ("invalid engine",
         dict(url="https://example.com", engine="puppeteer", mode="markdown")),
        ("missing cookies file",
         dict(url="https://example.com", engine="playwright", mode="markdown",
              cookies_path="/nonexistent/cookies.json")),
        ("invalid wait_until",
         dict(url="https://example.com", engine="playwright", mode="markdown",
              wait_until="immediately")),
    ]

    for name, kwargs in cases:
        try:
            kwargs.setdefault("timeout", 10)
            kwargs.setdefault("headless", True)
            kwargs.setdefault("proxy_url", None)
            kwargs.setdefault("user_agent", None)
            kwargs.setdefault("wait_until", "networkidle")
            kwargs.setdefault("wait_seconds", 0.5)
            kwargs.setdefault("cookies_path", None)
            r = await m._dispatch_fetch(**kwargs)
            assert_true("error" in r, msg=f"{name}: expected error dict, got {r}")
            assert_true(isinstance(r["error"], str) and len(r["error"]) > 0,
                        msg=f"{name}: error message empty")
            R.ok(f"{name}", f"error={r['error'][:60]}")
        except Exception as e:
            R.bad(f"{name}", f"raised {type(e).__name__} instead of returning dict: {e}")
            traceback.print_exc()


# ============================================================
# Test 7 — Auto fallback (force nodriver to fail)
# ============================================================

async def test_auto_fallback() -> None:
    R.header("Test 7: auto fallback (nodriver -> playwright)")
    try:
        # Force nodriver failure by monkey-patching _check_nodriver to "succeed"
        # but _fetch_with_nodriver to return an error. We do this without
        # mutating the real module state outside the test.
        original_fetch_nd = m._fetch_with_nodriver

        async def broken_nodriver(url, **kw):
            return {"error": "simulated nodriver failure for fallback test"}

        m._fetch_with_nodriver = broken_nodriver  # type: ignore[assignment]
        try:
            r = await m._dispatch_fetch(
                "https://example.com",
                engine="auto", mode="markdown", timeout=30, headless=True,
                cookies_path=None, proxy_url=None, user_agent=None,
                wait_until="networkidle", wait_seconds=0.5,
            )
            assert_true("error" not in r, msg=f"fallback failed: {r.get('error')}")
            assert_eq(r["engine"], "playwright", msg="should have fallen back to playwright")
            assert_in("fallback", r.get("fallback_reason", "").lower(),
                      msg="fallback_reason should be set")
            R.ok("auto fallback nodriver -> playwright",
                 f"reason: {r['fallback_reason'][:60]}")
        finally:
            m._fetch_with_nodriver = original_fetch_nd  # type: ignore[assignment]

    except Exception as e:
        R.bad("auto fallback", f"{type(e).__name__}: {e}")
        traceback.print_exc()


# ============================================================
# Driver
# ============================================================

async def main() -> int:
    print(f"\n{'=' * 60}")
    print("browser_fetch MCP server — smoke test")
    print(f"{'=' * 60}")
    t0 = time.time()

    # Test 1 is sync
    test_browser_status()

    # Async tests
    await test_fetch_page_auto()
    await test_explicit_engines()
    await test_output_modes()
    await test_screenshot()
    await test_error_paths()
    await test_auto_fallback()

    elapsed = time.time() - t0
    print(f"\n{'=' * 60}")
    print(f"Summary: {GREEN}{R.passed} passed{NC}, "
          f"{RED}{R.failed} failed{NC}, "
          f"{YELLOW}{R.skipped} skipped{NC}  "
          f"{DIM}({elapsed:.1f}s){NC}")
    print(f"{'=' * 60}\n")

    if R.failures:
        print(f"{RED}Failures:{NC}")
        for name, reason in R.failures:
            print(f"  - {name}: {reason}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
