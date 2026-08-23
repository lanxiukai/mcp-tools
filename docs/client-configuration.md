# MCP Client Configuration

All repository servers use MCP stdio. Configure only the services whose
installation profiles you restored; most clients try to start every enabled
server during discovery.

The examples below use `/absolute/path/to/mcp-tools`. Replace it with the output
of `pwd` from the repository root. The `bin/mcp-tools` launcher resolves the
matching repository-local uv interpreter and server script. Update the absolute
launcher path in client configuration if the repository moves.

## Server commands

| Service | Launcher argument | Install or setup first | Suggested tool timeout |
|---|---|---|---:|
| Format Conversion | `format-conversion` | `uv sync --project environments/mcp-local --locked` | 120 seconds |
| Browser Fetch | `browser-fetch` | `bash install.sh --cpu-only` | 120 seconds |
| ASR | `asr` | `bash install.sh --asr-only` | 1,800 seconds |
| OCR | `ocr` | `bash install.sh --ocr-only` | 1,800 seconds |
| Vision Local | `vision-local` | CPU frontend, CUDA llama.cpp build, and model files | 600 seconds |
| Brave Websearch | `brave-websearch` | Node.js 22+, `npx`, and `BRAVE_API_KEY` | 30 seconds |

Use a longer client timeout than the expected tool call. For OCR work that may
exceed one transport window, use `ocr_submit`, `ocr_status`, and repeated bounded
`ocr_wait` calls.

## Generic stdio configuration

Clients that use the common `mcpServers` JSON shape can start Format Conversion
with:

```json
{
  "mcpServers": {
    "mcp-tools-format": {
      "command": "/absolute/path/to/mcp-tools/bin/mcp-tools",
      "args": ["format-conversion"]
    }
  }
}
```

To add another service, duplicate the entry, give it a unique name, and replace
the launcher argument with a value from the table above.

## Codex CLI, IDE extension, and ChatGPT desktop app

Codex local clients support command-based stdio servers and share
`~/.codex/config.toml`. The quickest registration is:

```bash
codex mcp add mcp-tools-format -- \
  /absolute/path/to/mcp-tools/bin/mcp-tools format-conversion
codex mcp list
```

For timeouts and environment forwarding, edit `~/.codex/config.toml`:

```toml
[mcp_servers.mcp_tools_asr]
command = "/absolute/path/to/mcp-tools/bin/mcp-tools"
args = ["asr"]
startup_timeout_sec = 30
tool_timeout_sec = 1800
env_vars = ["HF_TOKEN"]

[mcp_servers.mcp_tools_ocr]
command = "/absolute/path/to/mcp-tools/bin/mcp-tools"
args = ["ocr"]
startup_timeout_sec = 30
tool_timeout_sec = 1800
```

Set `HF_TOKEN` in the shell that launches Codex. Do not store the token value in
the repository. Restart the desktop app or IDE extension after changing the
shared configuration. See the
[official Codex MCP documentation](https://learn.chatgpt.com/docs/extend/mcp).

## Claude Desktop on Windows with WSL2

The repository's locked Python profiles target Linux x86-64. Claude Desktop can
launch them from Windows through `wsl.exe`; a direct macOS configuration is not
supported by the current locks.

Open Claude Desktop **Settings > Developer > Edit Config** and add an entry to
`%APPDATA%\Claude\claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "mcp-tools-format": {
      "command": "wsl.exe",
      "args": [
        "--distribution",
        "Ubuntu",
        "--exec",
        "/home/your-user/project/mcp-tools/bin/mcp-tools",
        "format-conversion"
      ]
    }
  }
}
```

Replace `Ubuntu` with the name shown by `wsl.exe --list --quiet`, replace the
Linux path, save the file, and fully restart Claude Desktop. Keep input files in
a location visible to the selected WSL distribution and pass Linux paths to the
tools. Microsoft documents the command bridge in its
[WSL interoperability guide](https://learn.microsoft.com/en-us/windows/dev-environment/wsl-interop).
See also the
[official local MCP server guide](https://modelcontextprotocol.io/docs/2026-07-28/develop/connect-local-servers).

## Cursor

When Cursor is attached to Linux or WSL, create `.cursor/mcp.json` in the project
that should use the tools, or use `~/.cursor/mcp.json` for a global entry:

```json
{
  "mcpServers": {
    "mcp-tools-format": {
      "command": "/absolute/path/to/mcp-tools/bin/mcp-tools",
      "args": ["format-conversion"]
    }
  }
}
```

For a Windows-local Cursor process, use the `wsl.exe` command and arguments from
the Claude Desktop example. Enable only the tools you want in Cursor's MCP
settings. See the
[official Cursor MCP documentation](https://docs.cursor.com/context/model-context-protocol).

## Visual Studio Code

When the repository itself is the VS Code workspace root, create
`.vscode/mcp.json`:

```json
{
  "servers": {
    "mcpToolsFormat": {
      "type": "stdio",
      "command": "${workspaceFolder}/bin/mcp-tools",
      "args": ["format-conversion"]
    }
  }
}
```

For a user-level entry or a different workspace root, replace
`${workspaceFolder}` with the launcher's absolute path. Use **MCP: List Servers**
to start the server and inspect its output. See the
[official VS Code MCP configuration reference](https://code.visualstudio.com/docs/agents/reference/mcp-configuration).

## OpenCode

The installer prints entries for every selected profile. A minimal
`opencode.jsonc` entry is:

```jsonc
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "mcp_tools_format": {
      "type": "local",
      "command": [
        "/absolute/path/to/mcp-tools/bin/mcp-tools",
        "format-conversion"
      ],
      "enabled": true,
      "timeout": 30000
    }
  }
}
```

Restart OpenCode after changing the configuration. See the
[official OpenCode MCP documentation](https://opencode.ai/docs/mcp-servers/).
The shown `timeout` is the server discovery timeout in current OpenCode
configuration; it does not set the execution ceiling for later tool calls.

## Services with environment variables

Environment-variable syntax differs by client. These are the only credentials
used by repository launchers:

| Service | Variable | Handling |
|---|---|---|
| ASR diarization | `HF_TOKEN` | Forward an existing environment variable or use the client's secret input mechanism |
| Brave Websearch | `BRAVE_API_KEY` | Required; forward it to the launcher without committing the value |

Non-secret runtime overrides are listed in the root
[`README.md`](../README.md#configuration) and component documentation. Avoid
putting cookies, proxy credentials, tokens, or private paths in tracked client
configuration.

## Verify a connection

Before debugging a client, verify the launcher and the CPU MCP round trip from
the repository root:

```bash
bin/mcp-tools --help
bin/mcp-tools doctor
environments/mcp-local/.venv/bin/python examples/pdf_to_text_demo.py
```

If that succeeds but the client shows no tools:

1. Confirm the client is running in Linux/WSL or is using the `wsl.exe` bridge.
2. Confirm every configured path is absolute and executable from the client's
   environment.
3. Inspect the client's MCP stderr/output log.
4. Restart or reload the client so it repeats MCP discovery.
