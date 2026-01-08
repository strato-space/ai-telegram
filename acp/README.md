# ACP bridge spec

This document specifies the ACP bridge used by ai-telegram. It is a spec only.
Implementation follows later.

## Purpose

- Keep fast-agent running as a long-lived ACP server.
- Expose a Unix socket per ACP instance for Telegram clients.
- Stream assistant output and tool events to the client.

## Process model

- One ACP service process per instance.
- Each service listens on `./<InstanceName>.sock` (repo root).
- The Telegram bot process connects as a client and owns chat_id mapping.

## Service responsibilities

- Start fast-agent in ACP mode using the instance entrypoint.
- Accept client requests over a Unix socket.
- Stream ACP updates back to the client.
- Do not store chat_id or any bot state.

## Client responsibilities

- Maintain chat_id or chat_id+thread_id to session_id mapping (sqlite or other).
- Provide the session_id on requests; update mapping from the service response.
- Surface tool events and logs to Telegram using sendMessageDraft.

## Entry point handling

The service receives a single entrypoint path per instance:

- If the entrypoint is a Python file, it must support ACP server mode.
- If the entrypoint is an AgentCard path (file or directory), launch via:
  `fast-agent serve --transport acp --card <path>`.

No intermediate configs are allowed.

## Socket protocol (line-delimited JSON)

### Client -> service

```
{"type": "prompt", "session_id": "..." | null, "prompt": "...", "mode_id": "..." | null,
 "auto_approve": false, "allow_always": false, "show_tools": false,
 "strip_leading_newlines": false}
```

### Service -> client

- `{"type": "chunk", "text": "..."}`
- `{"type": "tool", "event": "tool_call_start|tool_call_update", "title": "..."}`
- `{"type": "permission_request", "title": "...", "options": [{"option_id": "...", "name": "..."}]}`
- `{"type": "error", "message": "..."}`
- `{"type": "done", "session_id": "..."}`

### Client -> service (permissions)

```
{"type": "permission_response", "option_id": "allow_once|allow_always|reject_once|reject_always|..."}
```

## Session behavior

- If `session_id` is missing or invalid, the service creates a new session.
- If the server refuses the prompt (stop_reason=refusal), the service retries once
  with a new session and returns the new session_id.

## Concurrency

- One request at a time per service process (single ACP connection).
- To scale, run multiple services (one per instance).

## Logging

- Use `CALL_DEBUG=1` for debug logs.
- Client tool events are streamed via the socket when `show_tools=true`.
