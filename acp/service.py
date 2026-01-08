#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import asyncio.subprocess as aio_subprocess
import contextlib
import json
import shlex
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from acp import (
    Client,
    PROTOCOL_VERSION,
    RequestError,
    connect_to_agent,
    text_block,
)
from acp.schema import (
    AllowedOutcome,
    ClientCapabilities,
    DeniedOutcome,
    FileSystemCapability,
    Implementation,
    PermissionOption,
    RequestPermissionResponse,
)

from call.lib.logging import configure_logging, debug_print, get_logger


DEFAULT_SOCKET_PATH = Path(__file__).resolve().parent / "acp.sock"
CLIENT_INFO = Implementation(name="call-acp-service", version="0.1.0")
LOGGER = get_logger("acp.service")


@dataclass
class ActiveRequest:
    reader: asyncio.StreamReader
    writer: asyncio.StreamWriter
    auto_approve: bool
    allow_always: bool
    show_tools: bool
    strip_leading_newlines: bool
    permission_queue: asyncio.Queue[str | None]
    started_output: bool = False


class AcpServiceClient(Client):
    def __init__(self) -> None:
        self._active: ActiveRequest | None = None

    def attach(self, active: ActiveRequest) -> None:
        self._active = active

    def detach(self) -> None:
        self._active = None

    async def session_update(self, session_id: str, update: Any, **kwargs: Any) -> None:
        active = self._active
        if not active:
            return
        update_type = _get_update_type(update)
        if update_type == "agent_message_chunk":
            text = _get_update_text(update)
            if text:
                if active.strip_leading_newlines and not active.started_output:
                    text = text.lstrip("\n")
                    if not text:
                        return
                active.started_output = True
                await _send_message(active.writer, {"type": "chunk", "text": text})
            return
        if update_type in ("tool_call_start", "tool_call_update"):
            title = getattr(update, "title", None) or _safe_get(update, "title")
            if active.show_tools:
                await _send_message(
                    active.writer,
                    {"type": "tool", "event": update_type, "title": title},
                )
            else:
                debug_print("[acp]", f"Tool update: {title}")
            return
        if update_type:
            debug_print("[acp]", f"Session update: {update_type}")

    async def request_permission(
        self,
        options: list[PermissionOption],
        session_id: str,
        tool_call: Any,
        **kwargs: Any,
    ) -> RequestPermissionResponse:
        active = self._active
        if not active:
            return RequestPermissionResponse(outcome=DeniedOutcome(outcome="cancelled"))

        if active.auto_approve:
            option_id = "allow_always" if active.allow_always else "allow_once"
            return RequestPermissionResponse(
                outcome=AllowedOutcome(option_id=option_id, outcome="selected")
            )

        title = getattr(tool_call, "title", None) or "tool execution"
        await _send_message(
            active.writer,
            {
                "type": "permission_request",
                "title": title,
                "options": _serialize_permission_options(options),
            },
        )
        option_id = await active.permission_queue.get()
        if not option_id:
            return RequestPermissionResponse(outcome=DeniedOutcome(outcome="cancelled"))
        return RequestPermissionResponse(
            outcome=AllowedOutcome(option_id=option_id, outcome="selected")
        )

    async def emit_error(self, message: str) -> None:
        active = self._active
        if not active:
            return
        await _send_message(active.writer, {"type": "error", "message": message})


def _serialize_permission_options(
    options: list[PermissionOption],
) -> list[dict[str, str]]:
    payload: list[dict[str, str]] = []
    for option in options:
        payload.append({"option_id": option.option_id, "name": option.name})
    return payload


def _safe_get(obj: Any, key: str) -> Any:
    if isinstance(obj, dict):
        return obj.get(key)
    return None


def _get_update_type(update: Any) -> str | None:
    if hasattr(update, "session_update"):
        return getattr(update, "session_update")
    if isinstance(update, dict):
        return update.get("sessionUpdate") or update.get("session_update")
    return None


def _get_update_text(update: Any) -> str | None:
    content = getattr(update, "content", None) or _safe_get(update, "content")
    if content is None:
        return None
    if hasattr(content, "text"):
        return getattr(content, "text")
    if isinstance(content, dict):
        return content.get("text")
    return None


async def _send_message(writer: asyncio.StreamWriter, payload: dict[str, Any]) -> None:
    try:
        data = json.dumps(payload, ensure_ascii=False)
        writer.write(data.encode("utf-8") + b"\n")
        await writer.drain()
    except Exception:
        LOGGER.debug("Failed to write to service client", exc_info=True)


async def _read_message(reader: asyncio.StreamReader) -> dict[str, Any] | None:
    line = await reader.readline()
    if not line:
        return None
    try:
        return json.loads(line)
    except json.JSONDecodeError:
        return None


async def _listen_for_client_messages(
    reader: asyncio.StreamReader,
    queue: asyncio.Queue[str | None],
) -> None:
    try:
        while True:
            message = await _read_message(reader)
            if message is None:
                await queue.put(None)
                break
            if message.get("type") == "permission_response":
                await queue.put(message.get("option_id"))
    except asyncio.CancelledError:
        raise
    except Exception:
        await queue.put(None)


def _resolve_server_command(card_path: Path, server_cmd: str) -> list[str]:
    if not server_cmd.strip():
        raise ValueError("Server command is required.")
    cmd = shlex.split(server_cmd)
    if not cmd:
        raise ValueError("Server command is required.")
    if not card_path.exists():
        raise FileNotFoundError(f"Agent card path not found: {card_path}")

    return [
        *cmd,
        "serve",
        "--transport",
        "acp",
        "--instance-scope",
        "connection",
        "--card",
        str(card_path),
    ]


def _is_session_not_found(exc: RequestError) -> bool:
    msg = str(exc)
    if "Session not found" in msg:
        return True
    data = getattr(exc, "data", None)
    if isinstance(data, dict):
        details = data.get("details") or data.get("message")
        if isinstance(details, str) and "Session not found" in details:
            return True
    return False


async def _create_session(
    connection: Any,
    cwd: Path,
) -> str:
    session = await connection.new_session(cwd=str(cwd), mcp_servers=[])
    session_id = getattr(session, "session_id", None) or getattr(session, "sessionId", None)
    if not session_id:
        raise RuntimeError("ACP new_session did not return a session id.")
    return session_id


async def _send_prompt(
    connection: Any,
    client: AcpServiceClient,
    session_id: str | None,
    prompt: str,
    cwd: Path,
    mode_id: str | None,
) -> str:
    try:
        if not session_id:
            session_id = await _create_session(connection, cwd)
        if mode_id:
            await connection.set_session_mode(mode_id=mode_id, session_id=session_id)
        response = await connection.prompt(
            session_id=session_id,
            prompt=[text_block(prompt)],
        )
        stop_reason = getattr(response, "stop_reason", None) or getattr(response, "stopReason", None)
        if stop_reason == "refusal":
            await client.emit_error(f"ACP prompt refused for session {session_id}")
            new_session_id = await _create_session(connection, cwd)
            if mode_id:
                await connection.set_session_mode(mode_id=mode_id, session_id=new_session_id)
            await connection.prompt(
                session_id=new_session_id,
                prompt=[text_block(prompt)],
            )
            return new_session_id
        return session_id
    except RequestError as exc:
        await client.emit_error(f"ACP request failed: {exc}")
        if not _is_session_not_found(exc):
            raise
        await client.emit_error(f"ACP session not found: {session_id}")
        new_session_id = await _create_session(connection, cwd)
        if mode_id:
            await connection.set_session_mode(mode_id=mode_id, session_id=new_session_id)
        await connection.prompt(
            session_id=new_session_id,
            prompt=[text_block(prompt)],
        )
        return new_session_id


async def run_service(args: argparse.Namespace) -> int:
    if not args.card_path:
        raise ValueError("--card is required to launch the ACP server.")
    card_path = Path(args.card_path).expanduser().resolve()
    server_cwd = (
        Path(args.server_cwd).expanduser().resolve()
        if args.server_cwd
        else card_path.parent
    )
    if not server_cwd.exists():
        raise FileNotFoundError(f"Server cwd not found: {server_cwd}")

    socket_path = Path(args.socket).expanduser().resolve()
    if socket_path.exists():
        if socket_path.is_socket():
            socket_path.unlink()
        else:
            raise RuntimeError(f"Socket path exists and is not a socket: {socket_path}")

    command = _resolve_server_command(card_path, args.server_cmd)

    client = AcpServiceClient()
    capabilities = ClientCapabilities(
        fs=FileSystemCapability(read_text_file=False, write_text_file=False),
        terminal=False,
    )

    proc = await asyncio.create_subprocess_exec(
        command[0],
        *command[1:],
        stdin=aio_subprocess.PIPE,
        stdout=aio_subprocess.PIPE,
        stderr=None,
        cwd=str(server_cwd),
        limit=args.stream_limit,
    )
    if proc.stdin is None or proc.stdout is None:
        raise RuntimeError("Agent process does not expose stdio pipes.")

    connection = connect_to_agent(client, proc.stdin, proc.stdout)
    await connection.initialize(
        protocol_version=PROTOCOL_VERSION,
        client_capabilities=capabilities,
        client_info=CLIENT_INFO,
    )

    request_lock = asyncio.Lock()

    async def handle_client(
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        if request_lock.locked():
            await _send_message(
                writer,
                {"type": "error", "message": "ACP service busy; try again later."},
            )
            writer.close()
            return
        async with request_lock:
            message = await _read_message(reader)
            if not message:
                writer.close()
                return
            if message.get("type") != "prompt":
                await _send_message(
                    writer,
                    {"type": "error", "message": "Expected prompt request."},
                )
                writer.close()
                return
            prompt = str(message.get("prompt") or "")
            if not prompt:
                await _send_message(
                    writer,
                    {"type": "error", "message": "prompt is required."},
                )
                writer.close()
                return
            session_id = message.get("session_id")
            if session_id is not None and not isinstance(session_id, str):
                await _send_message(
                    writer,
                    {"type": "error", "message": "session_id must be a string or null."},
                )
                writer.close()
                return
            if isinstance(session_id, str) and not session_id.strip():
                session_id = None

            active = ActiveRequest(
                reader=reader,
                writer=writer,
                auto_approve=bool(message.get("auto_approve")),
                allow_always=bool(message.get("allow_always")),
                show_tools=bool(message.get("show_tools")),
                strip_leading_newlines=bool(message.get("strip_leading_newlines")),
                permission_queue=asyncio.Queue(),
            )
            client.attach(active)
            listener_task = asyncio.create_task(
                _listen_for_client_messages(reader, active.permission_queue)
            )

            try:
                session_id = await _send_prompt(
                    connection,
                    client,
                    session_id,
                    prompt,
                    server_cwd,
                    message.get("mode_id") or None,
                )
                await _send_message(writer, {"type": "done", "session_id": session_id})
            except Exception as exc:
                await client.emit_error(f"ACP service error: {exc}")
                if session_id:
                    await _send_message(
                        writer,
                        {"type": "done", "session_id": session_id},
                    )
            finally:
                listener_task.cancel()
                client.detach()
                writer.close()
                with contextlib.suppress(Exception):
                    await writer.wait_closed()

    server = await asyncio.start_unix_server(handle_client, path=str(socket_path))
    LOGGER.info("ACP service listening on %s", socket_path)

    try:
        async with server:
            await server.serve_forever()
    except asyncio.CancelledError:
        raise
    finally:
        await connection.close()
        if proc.returncode is None:
            proc.terminate()
            with contextlib.suppress(ProcessLookupError):
                await proc.wait()
        with contextlib.suppress(FileNotFoundError):
            socket_path.unlink()

    return 0


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="ACP service for fast-agent (keeps sessions alive).",
    )
    parser.add_argument(
        "--card",
        dest="card_path",
        default=None,
        help="Path to an agent card file or directory (fast-agent --card).",
    )
    parser.add_argument(
        "--server-cmd",
        default="uv run fast-agent",
        help="Command used to launch fast-agent (e.g. 'uv run fast-agent' or 'fast-agent').",
    )
    parser.add_argument(
        "--server-cwd",
        default=None,
        help="Working directory for the ACP server (defaults to parent of --card).",
    )
    parser.add_argument(
        "--socket",
        default=str(DEFAULT_SOCKET_PATH),
        help="Unix socket path for ACP clients.",
    )
    parser.add_argument(
        "--stream-limit",
        dest="stream_limit",
        type=int,
        default=64 * 1024 * 1024,
        help="Max bytes per ACP stdio line before raising a limit error.",
    )
    return parser.parse_args(argv)


def main() -> int:
    configure_logging()
    args = parse_args(sys.argv[1:])
    try:
        return asyncio.run(run_service(args))
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        debug_print("[acp]", f"ACP service failed: {exc}")
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
