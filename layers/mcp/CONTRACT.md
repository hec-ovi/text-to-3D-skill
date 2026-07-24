# CONTRACT - mcp

`contractVersion: 1.0`

## Purpose

Expose the whole toolkit to an MCP client over stdio, handing back handles rather than bytes.

## Inputs

| Param | Schema | Preconditions |
| --- | --- | --- |
| JSON-RPC 2.0 messages on stdin | the MCP schema for revision `2025-11-25` | One message per line. `initialize`, `tools/list`, `tools/call` and `ping` are answered; anything else is `-32601`. |
| `--out-dir <path>` | no schema: a filesystem path | Where assets are written and looked up. Created by the generating layers, read here. |
| `--preview-url <url>` | no schema: a base URL | What `previewUrl` is built from. The viewer does not have to be running for an id to resolve; the URL is a link, not a promise. |

Entry point: `python3 src/server.py --out-dir out`. Python 3.10+, standard library only, no SDK. `--list-tools` prints the tool table and exits, for a sanity check.

The six tools and what each one needs are declared in `tools/list`, with a full JSON Schema per tool: `generate_model`, `generate_image`, `rig_model`, `list_models`, `get_preview`, `download_glb`.

## Outputs

| Param | Schema | Postconditions |
| --- | --- | --- |
| `CallToolResult` | [`schema/tool_result.json`](schema/tool_result.json) | Validated before it leaves. `structuredContent` carries the payload; `content[0]` mirrors it as text, because client support for structured output is uneven and the spec's own compatibility note says to serialise it into a content block. When the payload names a file that exists, `content[1]` is a `resource_link` to it. |
| `notifications/progress` | the MCP notification shape | Emitted every 5 s during a tool call that supplied a `progressToken`, until the call returns. |

Every asset-bearing payload carries `id`, `path`, `byteSize` and `previewUrl`. **No tool ever returns bytes.** A 20 MB GLB base64-encodes to about 28 M characters against a 25,000-token default result cap in Claude Code, and a tool result is model context, not a download channel.

`id` is the file stem folded to `[A-Za-z0-9._-]`, computed by the same rule as the preview layer, so one id addresses an asset in both. That equality is a test, not a convention.

## Events

`notifications/progress` only. There is no subscription, no `list_changed`, and no resources or prompts: the tools are the whole surface.

## Errors

Two kinds, deliberately.

| Kind | Shape | When |
| --- | --- | --- |
| Tool error | `{"content": [...], "isError": true}` | The model can read it and act: an unknown id, an unknown tool, a stage that failed with its own envelope code, a destination directory that does not exist, a timeout. |
| Protocol error | JSON-RPC `error` | The request itself is wrong: unknown method (`-32601`), unparseable line (`-32700`), or an unexpected exception (`-32603`). |

A failing layer CLI is surfaced as a tool error carrying that layer's own code and message (`TEXT2IMAGE_FAILED: ComfyUI is down`), because the model chooses its next move from that code.

## Dependencies

The layer CLIs, run as subprocesses: `pipeline/src/pipeline.py`, `text2image/src/klein.py`, `rig/src/rig.py`. Overridable with `$T2M_PIPELINE`, `$T2M_TEXT2IMAGE` and `$T2M_RIG`, which is how the tests stand in for the two that need a GPU. No layer is imported.

## Invariants

- stdout carries JSON-RPC and nothing else. The banner, and anything a subprocess prints, goes to stderr; a stray line on stdout breaks the transport.
- Results are schema-validated before they are written, so an off-contract payload fails here rather than in a client.
- Tool annotations are honest: `list_models` and `get_preview` are `readOnlyHint`, `download_glb` writes a file the caller named, and every tool is `openWorldHint: false` because nothing here reaches off this machine.
- A long call keeps sending progress. The client's idle timer, not the tool timeout, is what kills a three-minute generation.
- One server process holds no state beyond its arguments: ids are resolved from the directory on every call, so a file written by another process is visible immediately.

## How to modify this blackbox safely

1. Adding a tool: append to `TOOLS` with a full `inputSchema` and honest `annotations`, add the matching method to `Toolkit`, and add a test that calls it over real stdio. The dispatcher only exposes names that appear in `TOOLS`.
2. Do not add a tool that returns file bytes. If a client needs the bytes, it reads the path or fetches the preview server.
3. The protocol revision is a constant at the top of `src/server.py`. The `2026-07-28` revision removes `initialize` and sessions; when adopting it, keep answering the old handshake until clients have moved.
4. Run `uvx pytest tests/ -q` from this folder. The tests drive a real server process over real stdio; nothing about the protocol is mocked.
