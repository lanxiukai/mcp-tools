#!/usr/bin/env python3
"""MCP server for browser-based web page fetching.

Exposes 4 tools via MCP stdio protocol for fetching JavaScript-heavy or
anti-bot-protected web pages that plain HTTP fetchers cannot read:

- fetch_page:              Default tool. Auto-selects nodriver -> Playwright
                           fallback. Returns clean Markdown by default.
- screenshot:              Take a PNG screenshot of a fully-rendered page.
- fetch_page_with_engine:  Force a specific engine (nodriver/playwright)
                           for debugging or fine-grained control.
- browser_status:          Report which engines are installed and Chrome
                           availability.

Engine strategy
---------------
nodriver (primary, recommended for Cloudflare/Bot-Management sites):
    Drives Chrome directly via raw CDP WebSocket, with no Playwright shim.
    No `Runtime.enable` / `Target.setAutoAttach` calls that Cloudflare's
    detection watches for. No `navigator.webdriver` flag. Best in-class
    stealth in 2026 anti-detect benchmarks, but async-only and AGPL-3.0.

Playwright (fallback, permissive license):
    Standard Microsoft Playwright with stealth-friendly launch args.
    Works for most sites without serious anti-bot protection. Detectable
    by stricter Cloudflare Bot Management / DataDome / Kasada products.

Output modes (HTML -> agent-friendly text)
------------------------------------------
- markdown      (default) trafilatura main-content extraction -> Markdown.
                          Strips nav/ads/sidebars. Best for articles, profiles.
- markdown_full markdownify full-page conversion. Keeps tables/sidebars.
- html          Raw rendered HTML.
- text          Plain text (HTML tags stripped).

Site-specific guidance
----------------------
Sites like Upwork combine three layers of defence:
1. Cloudflare Bot Management challenge page  -> nodriver bypasses this
2. Datacenter IP blocking                     -> requires `proxy_url` (residential)
3. Login wall hiding profile data             -> requires `cookies_path`

Pass `cookies_path` (JSON exported from a real browser session) and
`proxy_url` (residential proxy URL) for full access to such sites.

Environment variables
---------------------
BROWSER_FETCH_TIMEOUT       Default timeout in seconds (default: 30)
BROWSER_FETCH_HEADLESS      "true"/"false" — run headless (default: "true")
BROWSER_FETCH_USER_AGENT    Override the default UA string
BROWSER_FETCH_LOG_LEVEL     "INFO" | "DEBUG" (default: "INFO")
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

from mcp.server.fastmcp import FastMCP

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
DEFAULT_TIMEOUT = int(os.environ.get("BROWSER_FETCH_TIMEOUT", "30"))
DEFAULT_HEADLESS = os.environ.get("BROWSER_FETCH_HEADLESS", "true").lower() in (
    "true",
    "1",
    "yes",
)
DEFAULT_USER_AGENT = os.environ.get(
    "BROWSER_FETCH_USER_AGENT",
    # Modern Chrome on Linux UA. Browsers will override this naturally,
    # but we set it as a fallback for any HTTP libs / outgoing headers.
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
)
LOG_LEVEL = os.environ.get("BROWSER_FETCH_LOG_LEVEL", "INFO").upper()


def _log(level: str, msg: str) -> None:
    """Emit a log line to stderr (MCP stdio uses stdout for protocol)."""
    if LOG_LEVEL == "DEBUG" or level != "DEBUG":
        sys.stderr.write(f"[browser_fetch] [{level}] {msg}\n")
        sys.stderr.flush()


# ---------------------------------------------------------------------------
# MCP Server
# ---------------------------------------------------------------------------
mcp = FastMCP(
    name="Browser Fetch",
    json_response=True,
    instructions=(
        "Browser-based web page fetcher for JavaScript-heavy or "
        "anti-bot-protected pages. Renders pages in a real Chrome browser "
        "(via nodriver / Playwright), bypasses common Cloudflare challenges, "
        "and returns clean Markdown / HTML / text. Use this when the regular "
        "webfetch returns empty content, a Cloudflare challenge page, or "
        "a 'Just a moment...' interstitial. "
        "For sites behind a login wall (e.g. Upwork freelancer profiles), "
        "pass `cookies_path` (a JSON cookie file exported from a real "
        "browser session) and optionally `proxy_url` (residential proxy)."
    ),
)


# ---------------------------------------------------------------------------
# Helpers — engine availability, cookies, conversion
# ---------------------------------------------------------------------------

def _check_nodriver() -> tuple[bool, Optional[str]]:
    """Return (available, error_message_if_not)."""
    try:
        import nodriver  # noqa: F401
        return True, None
    except ImportError as e:
        return False, f"nodriver not installed: {e}"
    except Exception as e:  # pragma: no cover
        return False, f"nodriver import failed: {e}"


def _check_playwright() -> tuple[bool, Optional[str]]:
    """Return (available, error_message_if_not)."""
    try:
        from playwright.async_api import async_playwright  # noqa: F401
        return True, None
    except ImportError as e:
        return False, f"playwright not installed: {e}"
    except Exception as e:  # pragma: no cover
        return False, f"playwright import failed: {e}"


def _check_trafilatura() -> tuple[bool, Optional[str]]:
    try:
        import trafilatura  # noqa: F401
        return True, None
    except ImportError as e:
        return False, f"trafilatura not installed: {e}"


def _check_markdownify() -> tuple[bool, Optional[str]]:
    try:
        import markdownify  # noqa: F401
        return True, None
    except ImportError as e:
        return False, f"markdownify not installed: {e}"


def _validate_url(url: str) -> Optional[str]:
    """Return error string if URL is invalid, else None."""
    if not url or not isinstance(url, str):
        return "URL must be a non-empty string"
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return f"URL scheme must be http or https, got: {parsed.scheme!r}"
    if not parsed.netloc:
        return "URL must include a hostname"
    return None


def _load_cookies(cookies_path: Optional[str]) -> tuple[list[dict], Optional[str]]:
    """Load cookies from a JSON file. Accepts either:

    - List of cookie objects with keys: name, value, domain, path,
      expires/expirationDate, httpOnly, secure, sameSite
      (this is the format exported by browser extensions like
      "Get cookies.txt LOCALLY" or "EditThisCookie").
    - A dict with key "cookies" containing the above list.

    Returns (cookies_list, error_message_or_none).
    """
    if not cookies_path:
        return [], None
    p = Path(cookies_path)
    if not p.exists():
        return [], f"cookies_path does not exist: {cookies_path}"
    try:
        raw = p.read_text(encoding="utf-8")
        data = json.loads(raw)
    except Exception as e:
        return [], f"cookies_path is not valid JSON: {e}"
    if isinstance(data, dict) and "cookies" in data:
        data = data["cookies"]
    if not isinstance(data, list):
        return [], "cookies_path must be a JSON list of cookie objects"
    return data, None


def _normalize_cookie_for_playwright(cookie: dict) -> Optional[dict]:
    """Convert a cookie dict to Playwright's expected shape.

    Playwright requires either `url` or both `domain` and `path`.
    Returns None if the cookie is unusable.
    """
    if "name" not in cookie or "value" not in cookie:
        return None
    out: dict[str, Any] = {
        "name": cookie["name"],
        "value": str(cookie["value"]),
    }
    if "domain" in cookie:
        domain = cookie["domain"]
        # Some exporters prefix with a dot; Playwright accepts both.
        out["domain"] = domain
    if "path" in cookie:
        out["path"] = cookie["path"]
    else:
        out["path"] = "/"
    if "domain" not in out:
        # Need either url or domain
        return None

    # Expiration: prefer `expires` (Unix seconds float) or `expirationDate`.
    for key in ("expires", "expirationDate"):
        if key in cookie and cookie[key] is not None:
            try:
                out["expires"] = float(cookie[key])
                break
            except (TypeError, ValueError):
                pass

    if "httpOnly" in cookie:
        out["httpOnly"] = bool(cookie["httpOnly"])
    if "secure" in cookie:
        out["secure"] = bool(cookie["secure"])
    if "sameSite" in cookie:
        ss = str(cookie["sameSite"]).lower()
        ss_map = {
            "lax": "Lax",
            "strict": "Strict",
            "none": "None",
            "no_restriction": "None",
            "unspecified": "Lax",
        }
        if ss in ss_map:
            out["sameSite"] = ss_map[ss]
    return out


def _html_to_markdown(html: str, mode: str) -> str:
    """Convert rendered HTML to one of: markdown / markdown_full / html / text.

    `markdown` (default): trafilatura main-content extraction with Markdown
        output. Strips boilerplate (nav, ads, sidebars). F1 ~0.96.
    `markdown_full`: markdownify full-page conversion. Keeps everything.
    `html`: returns the input unchanged.
    `text`: trafilatura plain text extraction (no Markdown formatting).
    """
    if mode == "html":
        return html

    if mode in ("markdown", "text"):
        ok, err = _check_trafilatura()
        if not ok:
            # Fallback: degraded plain text via stdlib
            from html.parser import HTMLParser

            class _Stripper(HTMLParser):
                def __init__(self) -> None:
                    super().__init__()
                    self.parts: list[str] = []

                def handle_data(self, data: str) -> None:
                    self.parts.append(data)

            s = _Stripper()
            s.feed(html)
            text = " ".join("".join(s.parts).split())
            _log("WARN", f"trafilatura unavailable ({err}); returned naive plain text")
            return text

        import trafilatura
        if mode == "markdown":
            extracted = trafilatura.extract(
                html,
                output_format="markdown",
                include_links=True,
                include_tables=True,
                include_formatting=True,
                with_metadata=False,
            )
        else:  # text
            extracted = trafilatura.extract(
                html,
                output_format="txt",
                include_links=False,
                include_tables=True,
                include_formatting=False,
                with_metadata=False,
            )
        return extracted or ""

    if mode == "markdown_full":
        ok, err = _check_markdownify()
        if not ok:
            _log("WARN", f"markdownify unavailable ({err}); falling back to trafilatura")
            return _html_to_markdown(html, "markdown")
        import markdownify
        return markdownify.markdownify(html, heading_style="ATX")

    raise ValueError(
        f"Unknown mode: {mode!r}. "
        f"Expected one of: markdown, markdown_full, html, text"
    )


# ---------------------------------------------------------------------------
# Engine: nodriver
# ---------------------------------------------------------------------------

async def _fetch_with_nodriver(
    url: str,
    *,
    timeout: int,
    headless: bool,
    cookies_path: Optional[str],
    proxy_url: Optional[str],
    user_agent: Optional[str],
    wait_seconds: float,
) -> dict:
    """Fetch a URL using nodriver. Returns dict with html/title/final_url
    on success, or {error: ...} on failure.
    """
    import nodriver as uc  # type: ignore

    cookies, cookie_err = _load_cookies(cookies_path)
    if cookie_err:
        return {"error": cookie_err}

    browser = None
    start = time.time()
    try:
        browser_args: list[str] = []
        if proxy_url:
            browser_args.append(f"--proxy-server={proxy_url}")
        if user_agent:
            browser_args.append(f"--user-agent={user_agent}")

        # nodriver>=0.40 supports `headless` kwarg + `browser_args`.
        # API may evolve; we handle both `start()` signatures defensively.
        try:
            browser = await uc.start(
                headless=headless,
                browser_args=browser_args or None,
            )
        except TypeError:
            # Older signature without browser_args
            browser = await uc.start(headless=headless)

        # Inject cookies before navigation if possible.
        # nodriver's cookie API is via the browser instance.
        if cookies:
            try:
                # Format expected by nodriver: list of dicts with name/value/domain
                await browser.cookies.set_all(cookies)  # type: ignore[attr-defined]
                _log("DEBUG", f"injected {len(cookies)} cookies via nodriver")
            except Exception as e:  # pragma: no cover
                _log("WARN", f"nodriver cookie injection failed: {e}")

        page = await browser.get(url)

        # Wait for network to settle. nodriver doesn't have networkidle yet,
        # so we use a fixed sleep + heuristic: poll document.readyState.
        deadline = start + timeout
        while time.time() < deadline:
            try:
                state = await page.evaluate("document.readyState")
                if state == "complete":
                    break
            except Exception:
                pass
            await asyncio.sleep(0.5)

        # Extra wait for SPA hydration / Cloudflare challenge resolution.
        if wait_seconds > 0:
            await asyncio.sleep(wait_seconds)

        html: str = await page.evaluate(
            "document.documentElement.outerHTML"
        )
        title: str = await page.evaluate("document.title") or ""
        final_url: str = await page.evaluate("location.href") or url

        return {
            "html": html,
            "title": title,
            "final_url": final_url,
            "elapsed_seconds": round(time.time() - start, 2),
        }

    except Exception as e:
        return {"error": f"nodriver fetch failed: {type(e).__name__}: {e}"}
    finally:
        if browser is not None:
            try:
                browser.stop()  # type: ignore[union-attr]
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Engine: Playwright (fallback)
# ---------------------------------------------------------------------------

# Lightweight stealth: argument-level evasions that don't require an extra dep.
# This is intentionally conservative; the heavy lifting belongs to nodriver.
_PLAYWRIGHT_STEALTH_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--disable-features=IsolateOrigins,site-per-process",
    "--no-sandbox",
    "--disable-dev-shm-usage",
]

_STEALTH_INIT_SCRIPT = """
// Hide navigator.webdriver
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
// Pretend to have plugins
Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
// Pretend to have languages
Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
// chrome runtime stub
window.chrome = window.chrome || { runtime: {} };
"""


async def _fetch_with_playwright(
    url: str,
    *,
    timeout: int,
    headless: bool,
    cookies_path: Optional[str],
    proxy_url: Optional[str],
    user_agent: Optional[str],
    wait_until: str,
    wait_seconds: float,
) -> dict:
    """Fetch a URL using Playwright. Returns dict with html/title/final_url
    on success, or {error: ...} on failure.
    """
    from playwright.async_api import async_playwright  # type: ignore

    cookies, cookie_err = _load_cookies(cookies_path)
    if cookie_err:
        return {"error": cookie_err}

    start = time.time()
    timeout_ms = timeout * 1000

    valid_wait_until = {"load", "domcontentloaded", "networkidle", "commit"}
    if wait_until not in valid_wait_until:
        return {
            "error": (
                f"wait_until must be one of {sorted(valid_wait_until)}, "
                f"got {wait_until!r}"
            )
        }

    try:
        async with async_playwright() as pw:
            launch_kwargs: dict[str, Any] = {
                "headless": headless,
                "args": _PLAYWRIGHT_STEALTH_ARGS,
            }
            if proxy_url:
                launch_kwargs["proxy"] = {"server": proxy_url}

            browser = await pw.chromium.launch(**launch_kwargs)
            try:
                context_kwargs: dict[str, Any] = {
                    "user_agent": user_agent or DEFAULT_USER_AGENT,
                    "viewport": {"width": 1366, "height": 900},
                    "locale": "en-US",
                }
                context = await browser.new_context(**context_kwargs)
                try:
                    await context.add_init_script(_STEALTH_INIT_SCRIPT)

                    if cookies:
                        normalized: list[dict] = []
                        for c in cookies:
                            n = _normalize_cookie_for_playwright(c)
                            if n is not None:
                                normalized.append(n)
                        if normalized:
                            try:
                                await context.add_cookies(normalized)  # type: ignore[arg-type]
                                _log(
                                    "DEBUG",
                                    f"injected {len(normalized)}/{len(cookies)} "
                                    f"cookies via playwright",
                                )
                            except Exception as e:
                                _log("WARN", f"playwright cookie injection failed: {e}")

                    page = await context.new_page()
                    response = await page.goto(
                        url, wait_until=wait_until, timeout=timeout_ms
                    )
                    status_code = response.status if response else None

                    if wait_seconds > 0:
                        await asyncio.sleep(wait_seconds)

                    html = await page.content()
                    title = await page.title()
                    final_url = page.url

                    return {
                        "html": html,
                        "title": title,
                        "final_url": final_url,
                        "status_code": status_code,
                        "elapsed_seconds": round(time.time() - start, 2),
                    }
                finally:
                    await context.close()
            finally:
                await browser.close()

    except Exception as e:
        return {"error": f"playwright fetch failed: {type(e).__name__}: {e}"}


# ---------------------------------------------------------------------------
# Engine: screenshot helpers
# ---------------------------------------------------------------------------

async def _screenshot_with_playwright(
    url: str,
    output_path: str,
    *,
    timeout: int,
    headless: bool,
    cookies_path: Optional[str],
    proxy_url: Optional[str],
    user_agent: Optional[str],
    wait_until: str,
    wait_seconds: float,
    full_page: bool,
) -> dict:
    from playwright.async_api import async_playwright  # type: ignore

    cookies, cookie_err = _load_cookies(cookies_path)
    if cookie_err:
        return {"error": cookie_err}

    start = time.time()
    timeout_ms = timeout * 1000

    try:
        async with async_playwright() as pw:
            launch_kwargs: dict[str, Any] = {
                "headless": headless,
                "args": _PLAYWRIGHT_STEALTH_ARGS,
            }
            if proxy_url:
                launch_kwargs["proxy"] = {"server": proxy_url}
            browser = await pw.chromium.launch(**launch_kwargs)
            try:
                context = await browser.new_context(
                    user_agent=user_agent or DEFAULT_USER_AGENT,
                    viewport={"width": 1366, "height": 900},
                    locale="en-US",
                )
                try:
                    await context.add_init_script(_STEALTH_INIT_SCRIPT)
                    if cookies:
                        normalized: list[dict] = []
                        for c in cookies:
                            n = _normalize_cookie_for_playwright(c)
                            if n is not None:
                                normalized.append(n)
                        if normalized:
                            try:
                                await context.add_cookies(normalized)  # type: ignore[arg-type]
                            except Exception as e:
                                _log("WARN", f"cookie injection failed: {e}")
                    page = await context.new_page()
                    await page.goto(url, wait_until=wait_until, timeout=timeout_ms)
                    if wait_seconds > 0:
                        await asyncio.sleep(wait_seconds)

                    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
                    await page.screenshot(
                        path=output_path,
                        full_page=full_page,
                    )
                    size = Path(output_path).stat().st_size
                    return {
                        "status": "success",
                        "output_path": output_path,
                        "size_bytes": size,
                        "title": await page.title(),
                        "final_url": page.url,
                        "elapsed_seconds": round(time.time() - start, 2),
                    }
                finally:
                    await context.close()
            finally:
                await browser.close()
    except Exception as e:
        return {"error": f"screenshot failed: {type(e).__name__}: {e}"}


# ---------------------------------------------------------------------------
# Async dispatchers
# ---------------------------------------------------------------------------

async def _dispatch_fetch(
    url: str,
    *,
    engine: str,
    mode: str,
    timeout: int,
    headless: bool,
    cookies_path: Optional[str],
    proxy_url: Optional[str],
    user_agent: Optional[str],
    wait_until: str,
    wait_seconds: float,
) -> dict:
    """Pick an engine, render the page, and convert to the requested mode."""
    err = _validate_url(url)
    if err:
        return {"error": err}

    valid_modes = {"markdown", "markdown_full", "html", "text"}
    if mode not in valid_modes:
        return {
            "error": (
                f"mode must be one of {sorted(valid_modes)}, got {mode!r}"
            )
        }

    nd_ok, nd_err = _check_nodriver()
    pw_ok, pw_err = _check_playwright()

    if engine == "auto":
        chosen = "nodriver" if nd_ok else ("playwright" if pw_ok else None)
        if chosen is None:
            return {
                "error": (
                    f"No engine available. nodriver: {nd_err}; "
                    f"playwright: {pw_err}"
                )
            }
    elif engine == "nodriver":
        if not nd_ok:
            return {"error": f"engine=nodriver requested but {nd_err}"}
        chosen = "nodriver"
    elif engine == "playwright":
        if not pw_ok:
            return {"error": f"engine=playwright requested but {pw_err}"}
        chosen = "playwright"
    else:
        return {
            "error": (
                f"engine must be one of: auto, nodriver, playwright. "
                f"Got {engine!r}"
            )
        }

    _log("INFO", f"fetching {url} via {chosen} (mode={mode})")

    if chosen == "nodriver":
        result = await _fetch_with_nodriver(
            url,
            timeout=timeout,
            headless=headless,
            cookies_path=cookies_path,
            proxy_url=proxy_url,
            user_agent=user_agent,
            wait_seconds=wait_seconds,
        )
        # Auto-fallback only in `auto` mode if nodriver failed and Playwright is available.
        if "error" in result and engine == "auto" and pw_ok:
            _log("WARN", f"nodriver failed ({result['error']}); falling back to playwright")
            result = await _fetch_with_playwright(
                url,
                timeout=timeout,
                headless=headless,
                cookies_path=cookies_path,
                proxy_url=proxy_url,
                user_agent=user_agent,
                wait_until=wait_until,
                wait_seconds=wait_seconds,
            )
            if "error" not in result:
                result["engine"] = "playwright"
                result["fallback_reason"] = "nodriver failed, used playwright fallback"
        else:
            if "error" not in result:
                result["engine"] = "nodriver"
    else:
        result = await _fetch_with_playwright(
            url,
            timeout=timeout,
            headless=headless,
            cookies_path=cookies_path,
            proxy_url=proxy_url,
            user_agent=user_agent,
            wait_until=wait_until,
            wait_seconds=wait_seconds,
        )
        if "error" not in result:
            result["engine"] = "playwright"

    if "error" in result:
        return result

    raw_html = result.pop("html", "")
    try:
        converted = _html_to_markdown(raw_html, mode)
    except Exception as e:
        return {"error": f"HTML->{mode} conversion failed: {e}"}

    result["mode"] = mode
    result["content"] = converted
    result["html_size"] = len(raw_html)
    result["content_size"] = len(converted)
    if mode != "html":
        # Keep raw HTML accessible only when explicitly requested via mode=html.
        # For other modes we drop it to keep the response small.
        pass
    return result


# ---------------------------------------------------------------------------
# MCP Tools
#
# All fetch tools are `async def` — FastMCP awaits them natively inside its
# event loop, so we just `await` the engine coroutines directly.
# ---------------------------------------------------------------------------

@mcp.tool()
async def fetch_page(
    url: str,
    mode: str = "markdown",
    *,
    timeout: int = 30,
    cookies_path: str = "",
    proxy_url: str = "",
    user_agent: str = "",
    wait_until: str = "networkidle",
    wait_seconds: float = 1.5,
    headless: bool = True,
) -> dict:
    """Fetch a web page in a real Chrome browser and return clean Markdown.

    Renders the page (including JavaScript), bypasses common Cloudflare
    challenges, and returns the main content as Markdown by default.
    Use this when plain `webfetch` returns empty content, a "Just a moment..."
    interstitial, or a Cloudflare challenge page.

    Engine selection is automatic: nodriver (best stealth, primary) with
    Playwright as fallback if nodriver fails or is not installed.

    Args:
        url:           Full http(s) URL to fetch.
        mode:          Output format:
                       - "markdown"      (default) Main-content Markdown via
                                          trafilatura. Strips nav/ads/sidebars.
                       - "markdown_full" Full-page Markdown (markdownify).
                       - "html"          Raw rendered HTML.
                       - "text"          Plain text (no Markdown formatting).
        timeout:       Per-page timeout in seconds (default 30).
        cookies_path:  Optional. Absolute path to a JSON cookie file exported
                       from a real browser session (e.g. via "Get cookies.txt
                       LOCALLY"). Required for sites behind a login wall like
                       Upwork freelancer profiles.
        proxy_url:     Optional. Proxy URL (e.g. "http://user:pass@host:port").
                       Required for sites that block datacenter IPs (Upwork,
                       LinkedIn, etc.) — supply a residential proxy.
        user_agent:    Optional. Override the default User-Agent string.
        wait_until:    Playwright-only. One of: load / domcontentloaded /
                       networkidle / commit. Default "networkidle".
        wait_seconds:  Extra sleep after page load, for SPA hydration or
                       Cloudflare challenge resolution. Default 1.5.
        headless:      Run browser headless (default True). On a server
                       without a display, headed mode requires Xvfb.

    Returns:
        Success dict:
          - content:      The page text in the requested mode
          - mode:         Echo of input mode
          - title:        Page <title>
          - final_url:    Final URL after redirects
          - status_code:  HTTP status (Playwright only)
          - engine:       Which engine actually fetched the page
          - html_size:    Size of raw rendered HTML (chars)
          - content_size: Size of converted output (chars)
          - elapsed_seconds: Wall-clock time
        Error dict:
          - error:        Description of what failed
    """
    return await _dispatch_fetch(
        url,
        engine="auto",
        mode=mode,
        timeout=timeout,
        headless=headless,
        cookies_path=cookies_path or None,
        proxy_url=proxy_url or None,
        user_agent=user_agent or None,
        wait_until=wait_until,
        wait_seconds=wait_seconds,
    )


@mcp.tool()
async def fetch_page_with_engine(
    url: str,
    engine: str,
    mode: str = "markdown",
    *,
    timeout: int = 30,
    cookies_path: str = "",
    proxy_url: str = "",
    user_agent: str = "",
    wait_until: str = "networkidle",
    wait_seconds: float = 1.5,
    headless: bool = True,
) -> dict:
    """Fetch a page using a specific engine. Use only for debugging / when
    you know which engine you want.

    Args:
        url:     Full http(s) URL.
        engine:  "nodriver" or "playwright". For automatic selection use
                 the regular `fetch_page` tool instead.
        mode, timeout, cookies_path, proxy_url, user_agent, wait_until,
        wait_seconds, headless:  Same as `fetch_page`.

    Returns:
        Same shape as `fetch_page`.
    """
    return await _dispatch_fetch(
        url,
        engine=engine,
        mode=mode,
        timeout=timeout,
        headless=headless,
        cookies_path=cookies_path or None,
        proxy_url=proxy_url or None,
        user_agent=user_agent or None,
        wait_until=wait_until,
        wait_seconds=wait_seconds,
    )


@mcp.tool()
async def screenshot(
    url: str,
    output_path: str = "",
    *,
    full_page: bool = True,
    timeout: int = 30,
    cookies_path: str = "",
    proxy_url: str = "",
    user_agent: str = "",
    wait_until: str = "networkidle",
    wait_seconds: float = 1.5,
    headless: bool = True,
) -> dict:
    """Take a PNG screenshot of a fully-rendered page (Playwright engine).

    Args:
        url:           Full http(s) URL.
        output_path:   Absolute path for the .png file. If empty, defaults to
                       /tmp/browser-fetch/<domain>-<timestamp>.png.
        full_page:     Capture entire scrollable page (default True). Set
                       False to capture only the viewport.
        timeout, cookies_path, proxy_url, user_agent, wait_until,
        wait_seconds, headless:  Same as `fetch_page`.

    Returns:
        Success: {status, output_path, size_bytes, title, final_url, elapsed_seconds}
        Error:   {error}
    """
    err = _validate_url(url)
    if err:
        return {"error": err}

    pw_ok, pw_err = _check_playwright()
    if not pw_ok:
        return {"error": f"screenshot requires playwright, but {pw_err}"}

    if not output_path:
        ts = int(time.time())
        host = (urlparse(url).netloc or "page").replace(":", "_")
        output_dir = os.environ.get("BROWSER_FETCH_SCREENSHOT_DIR", "/tmp/browser-fetch")
        os.makedirs(output_dir, exist_ok=True)
        output_path = f"{output_dir}/{host}-{ts}.png"

    return await _screenshot_with_playwright(
        url,
        output_path,
        timeout=timeout,
        headless=headless,
        cookies_path=cookies_path or None,
        proxy_url=proxy_url or None,
        user_agent=user_agent or None,
        wait_until=wait_until,
        wait_seconds=wait_seconds,
        full_page=full_page,
    )


@mcp.tool()
def browser_status() -> dict:
    """Report which browser engines and conversion libraries are installed.

    Use this to diagnose setup before fetching, especially after a fresh
    install or env change.

    Returns:
        Dict with availability flags for nodriver, playwright, trafilatura,
        markdownify, and notes about Chromium browser binaries.
    """
    nd_ok, nd_err = _check_nodriver()
    pw_ok, pw_err = _check_playwright()
    tr_ok, tr_err = _check_trafilatura()
    md_ok, md_err = _check_markdownify()

    # Probe whether Playwright Chromium is installed (the binary, not just the
    # Python lib). We do a lightweight import-only check; a deeper check
    # would launch a browser, which is too expensive for status.
    chromium_note = "unknown"
    if pw_ok:
        try:
            import playwright  # noqa: F401
            chromium_note = (
                "import-ok; run `playwright install chromium` if launches fail"
            )
        except Exception as e:
            chromium_note = f"playwright import error: {e}"

    return {
        "nodriver": {"available": nd_ok, "error": nd_err},
        "playwright": {"available": pw_ok, "error": pw_err, "chromium": chromium_note},
        "trafilatura": {"available": tr_ok, "error": tr_err},
        "markdownify": {"available": md_ok, "error": md_err},
        "default_engine": (
            "nodriver" if nd_ok else ("playwright" if pw_ok else "none")
        ),
        "default_timeout": DEFAULT_TIMEOUT,
        "default_headless": DEFAULT_HEADLESS,
        "user_agent": DEFAULT_USER_AGENT,
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    mcp.run(transport="stdio")
