# mcp

The toolkit as an MCP server. The contract is [`CONTRACT.md`](CONTRACT.md); this is the working notes.

```bash
python3 src/server.py --out-dir ../../out          # stdio, speaks 2025-11-25
python3 src/server.py --list-tools                 # sanity check, no client needed
```

Six tools: `generate_model`, `generate_image`, `rig_model`, `list_models`, `get_preview`, `download_glb`. `generate_model` takes `rig: "humanoid"` and chains both stages in one call.

## Why there is no SDK here

The stdio transport is newline-delimited JSON-RPC 2.0, and the surface a tool server needs is four methods: `initialize`, `tools/list`, `tools/call`, `ping`. The official Python SDK would bring the layer's first dependency into a repo that is otherwise standard library only, and its 1.x line is in maintenance while 2.0 lands with the `2026-07-28` revision, which removes `initialize` and the session header outright. Hand-rolling 300 lines against a spec that is about to simplify is cheaper than tracking a rewrite.

The trade is real: no Tasks, no resources, no elicitation, no OAuth. If any of those become necessary, take the SDK then.

## Why nothing returns bytes

A 20 MB GLB base64-encodes to 27,962,028 characters. Claude Code's default cap on a tool result is 25,000 tokens, and the per-tool escape hatch tops out at 500,000 characters, which is 375 KB of binary. Even a preview PNG is marginal: the client counts the base64 string against the token budget, not the visual tokens.

So every result is a handle: `id`, `path`, `byteSize`, `previewUrl`, plus a `resource_link` the client can fetch on its own terms. The bytes stay on disk, where a glTF loader can read them.

## Testing a server that talks over pipes

`tests/test_server.py` starts the real process and writes real JSON-RPC at it. The two tools that need a GPU are pointed at stand-in CLIs through `$T2M_PIPELINE` and `$T2M_RIG`, so the protocol, the id resolution, the flag mapping and the error translation are all exercised without a GPU in the room.

One test worth keeping: `test_ids_match_the_preview_layers_ids` imports the preview layer's `model_id` and asserts it agrees with this layer's `asset_id` for the same file names. Two layers computing ids independently is fine; two layers computing *different* ids means every `previewUrl` this server hands out is a 404.

## Things that will bite you

- **Nothing but JSON-RPC on stdout.** The banner goes to stderr. A subprocess that prints to stdout would be caught by `capture_output`, but a stray `print` in this file breaks every client.
- A tool that fails should return `isError`, not a JSON-RPC error. A protocol error tells the client the server is broken; `isError` tells the model to try something else.
- Progress notifications are what keep a three-minute generation alive. The heartbeat thread exists for the client's idle timer, not for the human.
- `handle()` catches everything. A server that dies on one bad request takes the session with it.
