"""Deterministic Browser Fetch lifecycle and error-contract regressions."""

from __future__ import annotations

import asyncio
import json
import os
import socket
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from unittest.mock import AsyncMock


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
BROWSER_DIRECTORY = REPOSITORY_ROOT / "browser-fetch"
sys.path.insert(0, str(BROWSER_DIRECTORY))

import browser_fetch_mcp_server as browser_fetch  # noqa: E402


def _fetch_arguments(**overrides):
    arguments = {
        "engine": "playwright",
        "mode": "html",
        "timeout": 5,
        "headless": True,
        "cookies_path": None,
        "proxy_url": None,
        "user_agent": None,
        "wait_until": "load",
        "wait_seconds": 0.0,
    }
    arguments.update(overrides)
    return arguments


class BrowserFetchReliabilityTests(unittest.IsolatedAsyncioTestCase):
    async def test_fetch_rejects_non_positive_timeout_before_engine_launch(self) -> None:
        with mock.patch.object(
            browser_fetch,
            "_fetch_with_playwright",
            new=AsyncMock(return_value={"html": ""}),
        ) as fetch:
            result = await browser_fetch._dispatch_fetch(
                "http://example.test",
                **_fetch_arguments(timeout=0),
            )

        self.assertIn("timeout", result["error"])
        fetch.assert_not_awaited()

    async def test_fetch_rejects_negative_wait_before_engine_launch(self) -> None:
        with mock.patch.object(
            browser_fetch,
            "_fetch_with_playwright",
            new=AsyncMock(return_value={"html": ""}),
        ) as fetch:
            result = await browser_fetch._dispatch_fetch(
                "http://example.test",
                **_fetch_arguments(wait_seconds=-0.1),
            )

        self.assertIn("wait_seconds", result["error"])
        fetch.assert_not_awaited()

    async def test_screenshot_failure_preserves_existing_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "capture.png"
            output.write_bytes(b"previous-valid-png")

            async def fail(_url: str, destination: str, **_kwargs) -> dict:
                Path(destination).write_bytes(b"partial")
                return {"error": "simulated Chromium crash"}

            with mock.patch.object(
                browser_fetch,
                "_check_playwright",
                return_value=(True, None),
            ), mock.patch.object(
                browser_fetch,
                "_screenshot_with_playwright",
                side_effect=fail,
            ):
                result = await browser_fetch.screenshot(
                    "http://example.test",
                    str(output),
                    wait_seconds=0,
                )

            self.assertIn("simulated Chromium crash", result["error"])
            self.assertEqual(output.read_bytes(), b"previous-valid-png")

    async def test_playwright_navigation_failure_closes_context_and_browser(self) -> None:
        page = SimpleNamespace(
            goto=AsyncMock(side_effect=RuntimeError("connection reset")),
        )
        context = SimpleNamespace(
            add_init_script=AsyncMock(),
            new_page=AsyncMock(return_value=page),
            close=AsyncMock(),
        )
        chromium = SimpleNamespace()
        browser = SimpleNamespace(
            new_context=AsyncMock(return_value=context),
            close=AsyncMock(),
        )
        chromium.launch = AsyncMock(return_value=browser)

        class PlaywrightContext:
            async def __aenter__(self):
                return SimpleNamespace(chromium=chromium)

            async def __aexit__(self, *_args):
                return False

        with mock.patch(
            "playwright.async_api.async_playwright",
            return_value=PlaywrightContext(),
        ):
            result = await browser_fetch._fetch_with_playwright(
                "http://example.test",
                **{
                    key: value
                    for key, value in _fetch_arguments().items()
                    if key not in {"engine", "mode"}
                },
            )

        self.assertIn("connection reset", result["error"])
        context.close.assert_awaited_once()
        browser.close.assert_awaited_once()

    async def test_playwright_cancellation_closes_context_and_browser(self) -> None:
        page = SimpleNamespace(
            goto=AsyncMock(side_effect=asyncio.CancelledError()),
        )
        context = SimpleNamespace(
            add_init_script=AsyncMock(),
            new_page=AsyncMock(return_value=page),
            close=AsyncMock(),
        )
        chromium = SimpleNamespace()
        browser = SimpleNamespace(
            new_context=AsyncMock(return_value=context),
            close=AsyncMock(),
        )
        chromium.launch = AsyncMock(return_value=browser)

        class PlaywrightContext:
            async def __aenter__(self):
                return SimpleNamespace(chromium=chromium)

            async def __aexit__(self, *_args):
                return False

        with mock.patch(
            "playwright.async_api.async_playwright",
            return_value=PlaywrightContext(),
        ):
            with self.assertRaises(asyncio.CancelledError):
                await browser_fetch._fetch_with_playwright(
                    "http://example.test",
                    **{
                        key: value
                        for key, value in _fetch_arguments().items()
                        if key not in {"engine", "mode"}
                    },
                )

        context.close.assert_awaited_once()
        browser.close.assert_awaited_once()

    async def test_nodriver_failure_stops_browser(self) -> None:
        browser = SimpleNamespace(
            get=AsyncMock(side_effect=RuntimeError("connection reset")),
            stop=mock.Mock(),
        )
        fake_nodriver = SimpleNamespace(start=AsyncMock(return_value=browser))

        with mock.patch.dict(sys.modules, {"nodriver": fake_nodriver}):
            result = await browser_fetch._fetch_with_nodriver(
                "http://example.test",
                timeout=5,
                headless=True,
                cookies_path=None,
                proxy_url=None,
                user_agent=None,
                wait_seconds=0,
            )

        self.assertIn("connection reset", result["error"])
        browser.stop.assert_called_once()

    async def test_nodriver_hanging_navigation_respects_total_timeout(self) -> None:
        never_finishes = asyncio.Event()

        async def hang(_url: str):
            await never_finishes.wait()

        browser = SimpleNamespace(
            get=AsyncMock(side_effect=hang),
            stop=mock.Mock(),
        )
        fake_nodriver = SimpleNamespace(start=AsyncMock(return_value=browser))

        with mock.patch.dict(sys.modules, {"nodriver": fake_nodriver}):
            result = await asyncio.wait_for(
                browser_fetch._fetch_with_nodriver(
                    "http://example.test",
                    timeout=1,
                    headless=True,
                    cookies_path=None,
                    proxy_url=None,
                    user_agent=None,
                    wait_seconds=0,
                ),
                timeout=1.5,
            )

        self.assertIn("timed out", result["error"])
        browser.stop.assert_called_once()

    def test_cookie_loader_accepts_wrapped_valid_cookies_and_rejects_bad_json(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            valid = Path(directory) / "valid cookies.json"
            invalid = Path(directory) / "invalid cookies.json"
            cookies = [{"name": "session", "value": "ok", "domain": "example.test"}]
            valid.write_text(json.dumps({"cookies": cookies}), encoding="utf-8")
            invalid.write_text("{broken", encoding="utf-8")

            loaded, error = browser_fetch._load_cookies(str(valid))
            invalid_loaded, invalid_error = browser_fetch._load_cookies(str(invalid))

        self.assertEqual(loaded, cookies)
        self.assertIsNone(error)
        self.assertEqual(invalid_loaded, [])
        self.assertIn("not valid JSON", invalid_error)


class _LocalPageHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, _format: str, *_args) -> None:
        return

    def _respond(
        self,
        status: int,
        body: str,
        *,
        headers: dict[str, str] | None = None,
    ) -> None:
        payload = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Connection", "close")
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
        if self.path == "/immediate":
            self._respond(
                200,
                "<html><head><title>Local Immediate</title></head>"
                "<body><main><h1>Immediate</h1><p>local deterministic body</p>"
                "</main></body></html>",
            )
        elif self.path == "/redirect":
            self._respond(302, "redirect", headers={"Location": "/immediate"})
        elif self.path == "/redirect-a":
            self._respond(302, "loop", headers={"Location": "/redirect-b"})
        elif self.path == "/redirect-b":
            self._respond(302, "loop", headers={"Location": "/redirect-a"})
        elif self.path == "/slow":
            time.sleep(2)
            self._respond(200, "<html><body>slow response</body></html>")
        elif self.path == "/hang":
            time.sleep(10)
            self._respond(200, "<html><body>late response</body></html>")
        elif self.path == "/large":
            self._respond(200, "<html><body>" + ("large-response " * 100_000) + "</body></html>")
        elif self.path == "/delayed-js":
            self._respond(
                200,
                "<html><head><title>Delayed</title></head><body>"
                "<div id='app'>before</div><script>"
                "setTimeout(() => {document.getElementById('app').textContent = "
                "'after-javascript';}, 200);</script></body></html>",
            )
        elif self.path == "/status/404":
            self._respond(404, "<html><body>not found body</body></html>")
        elif self.path == "/status/500":
            self._respond(500, "<html><body>server failure body</body></html>")
        elif self.path == "/malformed":
            self._respond(200, "<html><title>Malformed<body><div>still readable")
        elif self.path == "/cookie":
            cookie = self.headers.get("Cookie", "no-cookie")
            self._respond(200, f"<html><body>cookie={cookie}</body></html>")
        elif self.path == "/reset":
            self.connection.shutdown(socket.SHUT_RDWR)
            self.connection.close()
        else:
            self._respond(404, "<html><body>unknown endpoint</body></html>")


def _browser_process_ids() -> set[int]:
    process_ids: set[int] = set()
    for executable_link in Path("/proc").glob("[0-9]*/exe"):
        try:
            executable = executable_link.resolve(strict=True).name.lower()
        except OSError:
            continue
        if "chrome" in executable or "chromium" in executable:
            process_ids.add(int(executable_link.parent.name))
    return process_ids


def _current_rss_mib() -> float:
    for line in Path("/proc/self/status").read_text(encoding="utf-8").splitlines():
        if line.startswith("VmRSS:"):
            return int(line.split()[1]) / 1024
    return 0.0


def _wait_for_browser_cleanup(baseline: set[int], timeout: float = 8.0) -> set[int]:
    deadline = time.monotonic() + timeout
    remaining: set[int] = set()
    while time.monotonic() < deadline:
        remaining = _browser_process_ids() - baseline
        if not remaining:
            return set()
        time.sleep(0.2)
    return remaining


@unittest.skipUnless(
    os.environ.get("MCP_TOOLS_BROWSER_INTEGRATION") == "1",
    "set MCP_TOOLS_BROWSER_INTEGRATION=1 for real local-browser tests",
)
class LocalBrowserIntegrationTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _LocalPageHandler)
        cls.server.daemon_threads = True
        cls.server_thread = threading.Thread(
            target=cls.server.serve_forever,
            daemon=True,
        )
        cls.server_thread.start()
        cls.base_url = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.server_thread.join(timeout=2)

    async def _playwright_fetch(self, path: str, **overrides) -> dict:
        return await browser_fetch._dispatch_fetch(
            self.base_url + path,
            **_fetch_arguments(**overrides),
        )

    async def test_playwright_endpoint_and_output_mode_matrix(self) -> None:
        immediate = await self._playwright_fetch("/immediate")
        self.assertNotIn("error", immediate)
        self.assertEqual(immediate["status_code"], 200)
        self.assertEqual(immediate["title"], "Local Immediate")

        redirected = await self._playwright_fetch("/redirect")
        self.assertNotIn("error", redirected)
        self.assertTrue(redirected["final_url"].endswith("/immediate"))

        for path, status in (("/status/404", 404), ("/status/500", 500)):
            with self.subTest(path=path):
                result = await self._playwright_fetch(path)
                self.assertNotIn("error", result)
                self.assertEqual(result["status_code"], status)

        malformed = await self._playwright_fetch("/malformed")
        self.assertNotIn("error", malformed)
        self.assertIn("still readable", malformed["content"])

        large = await self._playwright_fetch("/large")
        self.assertNotIn("error", large)
        self.assertGreater(large["html_size"], 1_000_000)

        delayed = await self._playwright_fetch(
            "/delayed-js",
            wait_seconds=0.4,
        )
        self.assertIn("after-javascript", delayed["content"])

        for mode in ("markdown", "markdown_full", "html", "text"):
            with self.subTest(mode=mode):
                result = await self._playwright_fetch("/immediate", mode=mode)
                self.assertNotIn("error", result)
                self.assertEqual(result["mode"], mode)
                self.assertTrue(result["content"])

    async def test_failures_cookies_screenshot_and_cancellation_cleanup(self) -> None:
        baseline = _browser_process_ids()

        slow = await self._playwright_fetch("/slow", timeout=1)
        self.assertIn("Timeout", slow["error"])

        redirect_loop = await self._playwright_fetch("/redirect-a", timeout=3)
        self.assertIn("error", redirect_loop)

        reset = await self._playwright_fetch("/reset")
        self.assertIn("error", reset)

        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            unused_port = probe.getsockname()[1]
        unreachable = await browser_fetch._dispatch_fetch(
            f"http://127.0.0.1:{unused_port}/",
            **_fetch_arguments(timeout=2),
        )
        self.assertIn("error", unreachable)

        bad_proxy = await self._playwright_fetch(
            "/immediate",
            proxy_url="http://127.0.0.1:1",
            timeout=2,
        )
        self.assertIn("error", bad_proxy)

        with tempfile.TemporaryDirectory() as directory:
            cookie_path = Path(directory) / "cookies.json"
            cookie_path.write_text(
                json.dumps(
                    [
                        {
                            "name": "session",
                            "value": "valid-cookie",
                            "domain": "127.0.0.1",
                            "path": "/",
                        }
                    ]
                ),
                encoding="utf-8",
            )
            cookie_result = await self._playwright_fetch(
                "/cookie",
                cookies_path=str(cookie_path),
            )
            self.assertIn("session=valid-cookie", cookie_result["content"])

            screenshot_path = Path(directory) / "page.png"
            screenshot = await browser_fetch.screenshot(
                self.base_url + "/immediate",
                str(screenshot_path),
                wait_until="load",
                wait_seconds=0,
            )
            self.assertNotIn("error", screenshot)
            self.assertEqual(screenshot_path.read_bytes()[:8], b"\x89PNG\r\n\x1a\n")

        task = asyncio.create_task(
            self._playwright_fetch("/hang", timeout=20)
        )
        await asyncio.sleep(0.3)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task

        self.assertEqual(_wait_for_browser_cleanup(baseline), set())

    async def test_nodriver_and_auto_fallback(self) -> None:
        explicit = await browser_fetch._dispatch_fetch(
            self.base_url + "/immediate",
            **_fetch_arguments(engine="nodriver", timeout=15),
        )
        self.assertNotIn("error", explicit)
        self.assertEqual(explicit["engine"], "nodriver")

        with mock.patch.object(
            browser_fetch,
            "_fetch_with_nodriver",
            new=AsyncMock(return_value={"error": "simulated failure"}),
        ):
            fallback = await browser_fetch._dispatch_fetch(
                self.base_url + "/immediate",
                **_fetch_arguments(engine="auto"),
            )
        self.assertNotIn("error", fallback)
        self.assertEqual(fallback["engine"], "playwright")
        self.assertIn("fallback", fallback["fallback_reason"])


@unittest.skipUnless(
    os.environ.get("MCP_TOOLS_BROWSER_STRESS") == "1",
    "set MCP_TOOLS_BROWSER_STRESS=1 for repeated/concurrent browser stress",
)
class LocalBrowserStressTests(LocalBrowserIntegrationTests):
    async def test_repeated_and_concurrent_playwright_requests_stabilize(self) -> None:
        baseline = _browser_process_ids()
        rss_start = _current_rss_mib()
        started = time.perf_counter()

        for _ in range(100):
            result = await self._playwright_fetch("/immediate", mode="text")
            self.assertNotIn("error", result)

        timings: dict[str, float] = {}
        for concurrency in (4, 8):
            batch_started = time.perf_counter()
            semaphore = asyncio.Semaphore(concurrency)

            async def fetch_one() -> dict:
                async with semaphore:
                    return await self._playwright_fetch("/immediate", mode="text")

            results = await asyncio.gather(*(fetch_one() for _ in range(16)))
            self.assertTrue(all("error" not in result for result in results))
            timings[str(concurrency)] = round(time.perf_counter() - batch_started, 3)

        orphans = _wait_for_browser_cleanup(baseline)
        metrics = {
            "sequential_requests": 100,
            "total_elapsed_seconds": round(time.perf_counter() - started, 3),
            "concurrent_elapsed_seconds": timings,
            "rss_start_mib": round(rss_start, 1),
            "rss_end_mib": round(_current_rss_mib(), 1),
            "orphan_browser_pids": sorted(orphans),
        }
        print("BROWSER_STRESS_METRICS=" + json.dumps(metrics, sort_keys=True))
        self.assertEqual(orphans, set())


if __name__ == "__main__":
    unittest.main()
