# Browser Fetch — JavaScript-Heavy & Anti-Bot Web Page MCP Service

Renders web pages in a real Chrome browser and returns clean Markdown / HTML / text. Solves the gap left by plain `webfetch` for sites that:

- Render content with JavaScript (SPAs, lazy-loaded data)
- Show a Cloudflare "Just a moment..." challenge to non-browser clients
- Run aggressive bot detection (Cloudflare Bot Management, basic DataDome, etc.)

Two engines are available: **nodriver** (primary, best stealth) with **Playwright** as automatic fallback.

---

## MCP Tools

| Tool | Purpose |
|---|---|
| `fetch_page` | Default tool. Auto-selects nodriver → Playwright. Returns Markdown by default. |
| `fetch_page_with_engine` | Force a specific engine (`nodriver` / `playwright`) for debugging. |
| `screenshot` | PNG screenshot of fully-rendered page (Playwright). |
| `browser_status` | Health check: which engines are installed, which is the default. |

### `fetch_page(url, mode="markdown", **opts) -> dict`

Main tool. Renders the page, bypasses common Cloudflare challenges, returns clean text.

**Output modes:**

| Mode | Library | Behaviour |
|---|---|---|
| `markdown` (default) | trafilatura | Main-content extraction, strips nav/ads/sidebars (F1 ≈ 0.96) |
| `markdown_full` | markdownify | Full-page Markdown, keeps tables and sidebars |
| `html` | — | Raw rendered HTML (post-JS) |
| `text` | trafilatura | Plain text only, no Markdown formatting |

**Common parameters:**

| Param | Default | Notes |
|---|---|---|
| `timeout` | 30 | Positive whole-page timeout in seconds |
| `wait_until` | `"networkidle"` | Playwright only. One of: `load` / `domcontentloaded` / `networkidle` / `commit` |
| `wait_seconds` | 1.5 | Non-negative extra sleep after page load (SPA hydration / Cloudflare challenge wait) |
| `headless` | true | Set to false + run under Xvfb if you need maximum stealth on a server |
| `cookies_path` | `""` | Absolute path to a JSON cookie file (see "Login walls" below) |
| `proxy_url` | `""` | e.g. `http://user:pass@host:port` (see "Datacenter IP blocking") |
| `user_agent` | (Chrome 131) | Override the default UA string |

**Returns:**

```python
{
    "content": "<the page in the requested mode>",
    "mode": "markdown",
    "title": "Page title",
    "final_url": "https://...",   # after redirects
    "status_code": 200,           # Playwright only
    "engine": "nodriver",         # which engine actually ran
    "html_size": 12345,           # raw HTML chars
    "content_size": 6789,         # converted output chars
    "elapsed_seconds": 4.21,
}
# or on failure:
{"error": "explanation of what failed"}
```

---

## Engine Strategy

```
                       fetch_page (auto)
                              │
                ┌─────────────┴─────────────┐
                ▼                           │
            nodriver                        │
       (CDP, no Playwright shim)            │ on failure
       best stealth, AGPL-3.0               │
                │                           ▼
        ┌───────┴───────┐               Playwright
        ▼               │               (chromium + stealth args)
     success            ▼               Apache-2.0
        │           failure ─────────────► success / hard error
        ▼
     return
```

- **nodriver** drives Chrome over a raw CDP WebSocket. It does *not* issue the `Runtime.enable` / `Target.setAutoAttach` calls that modern Cloudflare detection looks for, and it does not set `navigator.webdriver`. That's why it bypasses Cloudflare Bot Management challenges that Playwright alone trips on.
- **Playwright** is the fallback for sites that nodriver can't or won't drive (rare), and the engine for `screenshot`.

In `fetch_page_with_engine` you can force one or the other.

---

## Site-Specific Guidance

### Cloudflare-protected sites (Upwork, etc.)

Sites like Upwork combine three layers:

| Layer | What blocks you | How to defeat |
|---|---|---|
| 1. Cloudflare Bot Management challenge | Plain HTTP / Playwright with default args | **nodriver handles this** — bypassed automatically |
| 2. Datacenter IP blocking | Datacenter / cloud IPs blocked outright | Pass `proxy_url` pointing at a residential proxy |
| 3. Login wall on protected pages | Returns "Sign in to view this profile" | Pass `cookies_path` (cookies exported from a logged-in browser session) |

Upwork freelancer profile pages (e.g. `https://www.upwork.com/freelancers/~01253f14599071aeb2`) require **all three** for full access. Without proxy + cookies, you'll get the public preview only — which is still better than the empty page that plain `webfetch` returns.

### Exporting cookies from a browser session

1. Log into the target site in real Chrome / Firefox
2. Install a cookie-export extension (e.g. **Get cookies.txt LOCALLY**, **Cookie-Editor**, or **EditThisCookie**)
3. Export cookies as **JSON**
4. Save the JSON file somewhere (e.g. `~/.config/upwork-cookies.json`)
5. Pass the expanded absolute path, such as `cookies_path="/home/your-user/.config/upwork-cookies.json"`, to `fetch_page`

The tool does not expand `~` in cookie paths. Cookie lifetimes depend on the
site; an expired-session response requires exporting a fresh authorized session.

Supported JSON shapes (the loader auto-detects):

```jsonc
// Shape A: bare list of cookie objects
[
  {
    "name": "session_id",
    "value": "abc123",
    "domain": ".upwork.com",
    "path": "/",
    "expires": 1735689600,
    "httpOnly": true,
    "secure": true,
    "sameSite": "Lax"
  }
]

// Shape B: wrapped in an object
{ "cookies": [ /* same as above */ ] }
```

Keep cookie files outside the repository and out of logs and shared examples.

---

## Shared CPU Runtime Setup

Browser Fetch shares **`mcp-local`** with Format Conversion, SVG Diagram, and
the Vision Local MCP frontend. The installer provisions the shared CPU runtime:

```bash
# From the repository root
bash install.sh --cpu-only
```

For a manual setup, restore the same locked shared project:

```bash
uv sync --project environments/mcp-local --locked

# Install Chromium binary for Playwright (~280 MB)
environments/mcp-local/.venv/bin/playwright install chromium
environments/mcp-local/.venv/bin/playwright install-deps chromium  # system libs (sudo prompts)
```

> **About the `playwright install-deps` step**: it apt-installs Chromium runtime libraries (`libnss3`, `libatk-bridge2.0-0`, etc.). It needs sudo. If you don't want to run sudo, manually install the libs once via your distro's package manager.

nodriver uses `BROWSER_FETCH_CHROME_PATH` when set, then a detected system
Chrome/Chromium executable, then the currently installed Playwright Chromium
revision. It drives that browser directly through CDP. The CPU installer
already downloads Playwright Chromium; a separate system Chrome install is
optional. Set `BROWSER_FETCH_CHROME_PATH` when selecting another executable.

---

## opencode.jsonc Configuration

```jsonc
"browser_fetch": {
  "type": "local",
  "command": ["<REPO-DIR>/bin/mcp-tools", "browser-fetch"],
  "enabled": true,
  "timeout": 30000
}
```

Replace `<REPO-DIR>` with the absolute repository path. OpenCode prefixes tool
IDs with the configured server name. For the `browser_fetch` entry above, add
these keys to the applicable `permission` block:

```jsonc
"browser_fetch_fetch_page": "allow",
"browser_fetch_fetch_page_with_engine": "allow",
"browser_fetch_screenshot": "allow",
"browser_fetch_browser_status": "allow"
```

---

## Environment Variables

| Variable | Default | Purpose |
|---|---|---|
| `BROWSER_FETCH_TIMEOUT` | `30` | Timeout reported by `browser_status`; requests still default to `timeout=30` |
| `BROWSER_FETCH_HEADLESS` | `true` | Mode reported by `browser_status`; requests still default to `headless=true` |
| `BROWSER_FETCH_USER_AGENT` | Chrome 131 UA | Override default UA |
| `BROWSER_FETCH_CHROME_PATH` | Auto-detected | Explicit Chrome/Chromium executable for nodriver |
| `BROWSER_FETCH_SCREENSHOT_DIR` | `/tmp/browser-fetch` | Default screenshot output directory |
| `BROWSER_FETCH_LOG_LEVEL` | `INFO` | `INFO` or `DEBUG` |

Set `timeout` and `headless` explicitly on each request to change its behavior.
In the current implementation, the two corresponding environment values affect
the status report only.

---

## Usage Examples

```python
# Simplest — fetch a public page as Markdown
fetch_page("https://example.com")
# → {"content": "# Example Domain\n\nThis domain is for...", "mode": "markdown",
#    "title": "Example Domain", "engine": "nodriver", ...}

# Cloudflare-protected page (no login needed)
fetch_page("https://www.somesitewithcloudflare.com/article/123",
           wait_seconds=4)  # extra wait for challenge to resolve

# Upwork freelancer profile — requires cookies + residential proxy
fetch_page("https://www.upwork.com/freelancers/~01253f14599071aeb2",
           cookies_path="/home/me/.config/upwork-cookies.json",
           proxy_url="http://user:pass@residential-proxy.example.com:7777",
           wait_seconds=4,
           timeout=60)

# Get the rendered HTML (no Markdown conversion) for parsing
fetch_page("https://news.ycombinator.com", mode="html")

# Force a specific engine for debugging
fetch_page_with_engine("https://example.com", engine="playwright")

# Screenshot
screenshot("https://example.com",
           output_path="/tmp/example.png",
           full_page=True)

# Health check
browser_status()
# → {"nodriver": {"available": true, ...},
#    "playwright": {"available": true, "chromium": "import-ok; ..."},
#    "trafilatura": {"available": true, ...},
#    "markdownify": {"available": true, ...},
#    "default_engine": "nodriver", ...}
```

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `nodriver not installed` | Shared CPU profile is missing or incomplete | `uv sync --project environments/mcp-local --locked` from the repository root |
| `playwright fetch failed: Executable doesn't exist` | Chromium binary not downloaded | `environments/mcp-local/.venv/bin/playwright install chromium` from the repository root |
| Cloudflare challenge never resolves | Page needs more time | Increase `wait_seconds` to 4–8 |
| Page loads but content is empty | SPA hydration not complete | Increase `wait_seconds` or use `wait_until="networkidle"` |
| `403 Forbidden` from Upwork-class site | Datacenter IP detected | Supply `proxy_url` (residential) |
| Profile data missing on Upwork | Login wall | Supply `cookies_path` from a logged-in session |
| Headless detected | Some sites detect headless Chrome | Set `headless=False` and run under Xvfb (`xvfb-run -a python ...`) |

---

## Design Rationale

**Why a custom MCP instead of `webfetch`?** `webfetch` does plain HTTP — it cannot execute JavaScript, set custom headers, hold cookies, or pass anti-bot challenges.

**Why nodriver as primary?** [2026 anti-detect benchmarks](https://ianlpaterson.com/blog/anti-detect-browser-benchmark-patchright-nodriver-curl-cffi/) show nodriver is the only Python-native option that bypasses modern Cloudflare Bot Management consistently. It drives Chrome via raw CDP WebSocket, skipping the Playwright protocol artifacts (`Runtime.enable`, `Target.setAutoAttach`) that detection systems specifically watch for.

**Why Playwright as fallback?** It's permissively licensed (Apache-2.0), has a much larger ecosystem, and provides `networkidle` waits + screenshot APIs that nodriver doesn't. It's also necessary for the `screenshot` tool.

**Why trafilatura for Markdown?** Independent benchmarks consistently put trafilatura at the top of HTML→Markdown content extraction (F1 ≈ 0.96). It strips boilerplate (nav, ads, sidebars) automatically — exactly what an agent wants when reading an article or profile.

---

## Files

| File | Purpose |
|---|---|
| `browser_fetch_mcp_server.py` | MCP stdio frontend (single file, no external backend) |
| `../test/browser_fetch/smoke_test.py` | End-to-end smoke test covering all 4 tools and edge cases |
| `README.md` | This document |

---

## Limitations

- **AGPL-3.0 license** for nodriver — if you redistribute this MCP as a linked binary or service, the AGPL applies to the combined work. For internal use it's fine.
- **No CAPTCHAs solved** — neither engine breaks reCAPTCHA, hCaptcha, or Cloudflare Turnstile interactive challenges. Use `cookies_path` for sites that gate on solved CAPTCHAs.
- **Single browser instance per call** — no session reuse across calls. Each `fetch_page` launches and tears down a fresh browser, which is slower than persistent sessions but simpler and avoids state leakage.
- **No JavaScript execution from agent** — the agent can't `evaluate()` arbitrary JS in the page. Only Markdown / HTML / text outputs are exposed. (Add a tool if needed.)
