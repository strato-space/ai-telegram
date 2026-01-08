# ACP CLI client (fast-agent)

This folder contains a small ACP client that launches fast-agent over stdio
(`fast-agent serve --transport acp --card <path>`) and sends a single prompt per run.
It maps a stable chat_id to an ACP session_id and keeps that mapping in a local
SQLite database.

For stable sessions across multiple invocations, run the local service (below) and
connect the client via the Unix socket. This keeps the ACP server process alive and
preserves session state between calls.

## Files

- client.py: ACP console client (spawns fast-agent, sends a prompt, prints output).
- service.py: Local ACP service (keeps fast-agent running, accepts socket clients).
- acp_sessions.db: SQLite mapping chat_id -> session_id (auto-created).
- fastagent.secrets.yaml: optional local secrets file (gitignored). Note: the server
  reads secrets from its working directory or ~/.config/fast-agent/fastagent.secrets.yaml,
  not from this folder.

## Features

- ACP over stdio using fast-agent CLI (SDK example style under the hood).
- chat_id -> session_id persistence in SQLite (acp_sessions.db) on the client side.
- Auto session recovery when the server returns stop_reason=refusal (session not found).
- Streams agent_message_chunk updates to stdout.
- Optional local service + socket client for long-lived ACP sessions.
- Optional mode switch via --mode (agent name).
- Tool permission handling: interactive prompt or auto-approve flags.
- Large output support via --stream-limit (default 64MB per JSON line).

## Usage

Basic (spawns fast-agent for the request):

  uv run ./client.py <chat_id> "your prompt text" --card /home/strato-space/fast-agent-vertex-rag/agents

Long-lived service (keeps ACP sessions alive):

  uv run ./service.py --socket ./acp.sock --card /home/strato-space/fast-agent-vertex-rag/agents

Then connect from another shell:

  uv run ./client.py --connect --socket ./acp.sock <chat_id> "your prompt text"

Note: `--card` is required only when the client/service spawns the ACP server.

From stdin:

  echo "your prompt" | uv run ./client.py <chat_id> --card /home/strato-space/fast-agent-vertex-rag/agents

Auto-approve tool permissions:

  uv run ./client.py <chat_id> "..." --auto-approve --card /home/strato-space/fast-agent-vertex-rag/agents

Always allow (stores allow_always):

  uv run ./client.py <chat_id> "..." --allow-always --card /home/strato-space/fast-agent-vertex-rag/agents

Bigger output limit:

  uv run ./client.py <chat_id> "..." --stream-limit 134217728 --card /home/strato-space/fast-agent-vertex-rag/agents

Strip leading newlines (optional):

  uv run ./client.py <chat_id> "..." --strip-leading-newlines --card /home/strato-space/fast-agent-vertex-rag/agents

Custom server command or cwd:

  uv run ./client.py <chat_id> "..." --card /path/to/agents --server-cmd "uv run fast-agent"

The server working directory defaults to the parent directory of `--card`.
You can override it with `--server-cwd /path/to/repo`.

## Logs and troubleshooting

Server logs:
- `<server cwd>/fastagent.jsonl` (for the example above:
  `/home/strato-space/fast-agent-vertex-rag/fastagent.jsonl`)

Client logs:
- CALL_DEBUG=1 enables debug output.
- CALL_LOG_FILE=/path/to/client.log writes client logs to a file.

Service logs:
- Same CALL_DEBUG/CALL_LOG_FILE env vars (run them on the service process).
- Session mappings live on the client (sqlite) even when using --connect.

If you see "session not found":
- The server lost its session (restart or instance scope). The client will log an
  error and create a new session automatically.

If you see LimitOverrunError:
- Increase --stream-limit.

Optional LLM stream capture:

  FAST_AGENT_LLM_TRACE=1 uv run ./client.py <chat_id> "..." --card /home/strato-space/fast-agent-vertex-rag/agents

This writes JSONL stream chunks under `<server cwd>/stream-debug/` (for the example above:
`/home/strato-space/fast-agent-vertex-rag/stream-debug/`).

## FAQ (English)

Q: Why launch the server with `uv run fast-agent`?
A: This matches the common local setup where fast-agent is installed in a uv-managed venv.
   If you have `fast-agent` on PATH, set `--server-cmd "fast-agent"`.

Q: Why not `fast-agent go` like in the documentation?
A: `go` is interactive mode. For ACP we need a protocol server, so we use
   `fast-agent serve --transport acp --card <path>` (or `fast-agent acp`).

Q: Why pass `--card` instead of `--server-root`?
A: `--card` is what fast-agent expects; it can be a file or directory with AgentCards.
   The server cwd is derived from the card path so config/secrets resolve naturally.
   If needed, override it with `--server-cwd`.

Q: Where does the chat_id mapping live when using the service?
A: Only in the client. The service accepts an optional session_id, creates a new one if
   needed, and returns the session_id back to the client.

Q: Can a Unix socket replace stdin/stdout for ACP?
A: At the protocol level, yes—ACP is JSON over a byte stream. But fast-agent implements ACP over
   stdio only, so we run a wrapper service that bridges a Unix socket to the stdio process.
   A "dumb" wrapper can just pipe bytes for a single client, but for multiple clients and
   permission prompts, the wrapper must understand ACP (like `service.py`).
