#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import asyncio.subprocess as aio_subprocess
import contextlib
import json
import shlex
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
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
    ReadTextFileResponse,
    RequestPermissionResponse,
    WriteTextFileResponse,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from call.lib.logging import configure_logging, debug_print, get_logger


DEFAULT_DB_PATH = Path(__file__).resolve().parent / "acp_sessions.db"
DEFAULT_SOCKET_PATH = Path(__file__).resolve().parent / "acp.sock"
CLIENT_INFO = Implementation(name="call-acp-cli", version="0.1.0")
LOGGER = get_logger("acp.client")


@dataclass
class SessionMapping:
    chat_id: str
    session_id: str
    updated_at: str


class SessionStore:
    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._db_path, timeout=5.0)

    def _ensure_schema(self) -> None:
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS acp_sessions (
                        chat_id TEXT PRIMARY KEY,
                        session_id TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )
                    """
                )
                conn.commit()
        except Exception as exc:
            LOGGER.error("Failed to initialize session store: %s", exc, exc_info=True)

    def get(self, chat_id: str) -> SessionMapping | None:
        try:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT chat_id, session_id, updated_at FROM acp_sessions WHERE chat_id = ?",
                    (chat_id,),
                ).fetchone()
                if not row:
                    return None
                return SessionMapping(chat_id=row[0], session_id=row[1], updated_at=row[2])
        except Exception as exc:
            LOGGER.error("Failed to read session mapping: %s", exc, exc_info=True)
            return None

    def upsert(self, chat_id: str, session_id: str) -> None:
        timestamp = datetime.now(timezone.utc).isoformat()
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO acp_sessions (chat_id, session_id, updated_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(chat_id) DO UPDATE SET
                        session_id = excluded.session_id,
                        updated_at = excluded.updated_at
                    """,
                    (chat_id, session_id, timestamp),
                )
                conn.commit()
        except Exception as exc:
            LOGGER.error("Failed to store session mapping: %s", exc, exc_info=True)


class AcpConsoleClient(Client):
    def __init__(
        self,
        *,
        auto_approve: bool,
        allow_always: bool,
        strip_leading_newlines: bool,
        stream_chunk_delimeter: bool,
        out_stream: Any = sys.stdout,
        err_stream: Any = sys.stderr,
    ) -> None:
        self._auto_approve = auto_approve
        self._allow_always = allow_always
        self._strip_leading_newlines = strip_leading_newlines
        self._stream_chunk_delimeter = stream_chunk_delimeter
        self._out = out_stream
        self._err = err_stream
        self._needs_newline = False
        self._started_output = False
        self._chunk_delimeter_active = False

    async def session_update(self, session_id: str, update: Any, **kwargs: Any) -> None:
        update_type = _get_update_type(update)
        if update_type == "agent_message_chunk":
            text = _get_update_text(update)
            if text:
                if self._strip_leading_newlines and not self._started_output:
                    text = text.lstrip("\n")
                    if not text:
                        return
                if not self._started_output:
                    self._started_output = True
                self._out.write(text)
                self._out.flush()
                self._needs_newline = not text.endswith("\n")
                if self._stream_chunk_delimeter:
                    self._err.write(".")
                    self._err.flush()
                    self._chunk_delimeter_active = True
            return
        if update_type in ("tool_call_start", "tool_call_update"):
            self._flush_chunk_delimeter()
            title = getattr(update, "title", None) or _safe_get(update, "title")
            debug_print("[acp]", f"Tool update: {title}")
            return
        if update_type:
            self._flush_chunk_delimeter()
            debug_print("[acp]", f"Session update: {update_type}")

    async def request_permission(
        self,
        options: list[PermissionOption],
        session_id: str,
        tool_call: Any,
        **kwargs: Any,
    ) -> RequestPermissionResponse:
        self._flush_chunk_delimeter()
        if self._needs_newline:
            self._out.write("\n")
            self._out.flush()
            self._needs_newline = False

        if self._auto_approve:
            option_id = "allow_always" if self._allow_always else "allow_once"
            return RequestPermissionResponse(
                outcome=AllowedOutcome(option_id=option_id, outcome="selected")
            )

        if not sys.stdin.isatty():
            debug_print("[acp]", "Permission requested without TTY; rejecting once.")
            return RequestPermissionResponse(
                outcome=AllowedOutcome(option_id="reject_once", outcome="selected")
            )

        title = getattr(tool_call, "title", None) or "tool execution"
        self._err.write(f"\nPermission required: {title}\n")
        for idx, option in enumerate(options, start=1):
            self._err.write(f"  {idx}) {option.name} ({option.option_id})\n")
        self._err.flush()

        option_id = await _select_permission_option(options, self._err)
        if option_id is None:
            return RequestPermissionResponse(outcome=DeniedOutcome(outcome="cancelled"))
        return RequestPermissionResponse(
            outcome=AllowedOutcome(option_id=option_id, outcome="selected")
        )

    def emit_error(self, message: str) -> None:
        self._flush_chunk_delimeter()
        line = f"[acp][error] {message}"
        LOGGER.error(line)
        if self._needs_newline:
            try:
                self._out.write("\n")
                self._out.flush()
            except Exception:
                pass
            self._needs_newline = False

    def _flush_chunk_delimeter(self) -> None:
        if self._chunk_delimeter_active:
            self._err.write("\n")
            self._err.flush()
            self._chunk_delimeter_active = False

    async def write_text_file(
        self,
        content: str,
        path: str,
        session_id: str,
        **kwargs: Any,
    ) -> WriteTextFileResponse | None:
        raise RequestError.method_not_found("fs/write_text_file")

    async def read_text_file(
        self,
        path: str,
        session_id: str,
        limit: int | None = None,
        line: int | None = None,
        **kwargs: Any,
    ) -> ReadTextFileResponse:
        raise RequestError.method_not_found("fs/read_text_file")


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


async def _select_permission_option(
    options: list[PermissionOption],
    err_stream: Any,
) -> str | None:
    option_map = {opt.option_id: opt for opt in options}
    index_map = {str(idx): opt.option_id for idx, opt in enumerate(options, start=1)}

    for _ in range(3):
        choice = await asyncio.to_thread(_read_choice, err_stream)
        if not choice:
            continue
        if choice in option_map:
            return choice
        if choice in index_map:
            return index_map[choice]
        err_stream.write("Invalid option. Enter a number or option id.\n")
        err_stream.flush()

    debug_print("[acp]", "Permission prompt exceeded retries; cancelling.")
    return None


def _read_choice(err_stream: Any) -> str:
    err_stream.write("Select option: ")
    err_stream.flush()
    return sys.stdin.readline().strip()


async def _send_socket_message(writer: asyncio.StreamWriter, payload: dict[str, Any]) -> None:
    data = json.dumps(payload, ensure_ascii=False)
    writer.write(data.encode("utf-8") + b"\n")
    await writer.drain()


async def _read_socket_message(reader: asyncio.StreamReader) -> dict[str, Any] | None:
    line = await reader.readline()
    if not line:
        return None
    try:
        return json.loads(line)
    except json.JSONDecodeError:
        return None


async def _select_permission_option_id(
    options: list[dict[str, Any]],
    err_stream: Any,
) -> str | None:
    option_map = {str(opt.get("option_id")): opt for opt in options if opt.get("option_id")}
    index_map = {
        str(idx): opt.get("option_id")
        for idx, opt in enumerate(options, start=1)
        if opt.get("option_id")
    }

    for _ in range(3):
        choice = await asyncio.to_thread(_read_choice, err_stream)
        if not choice:
            continue
        if choice in option_map:
            return choice
        if choice in index_map:
            return index_map[choice]
        err_stream.write("Invalid option. Enter a number or option id.\n")
        err_stream.flush()

    debug_print("[acp]", "Permission prompt exceeded retries; cancelling.")
    return None


def _resolve_prompt(args: argparse.Namespace) -> str:
    if args.prompt:
        text = " ".join(args.prompt).strip()
        if text:
            return text
    if not sys.stdin.isatty():
        text = sys.stdin.read().strip()
        if text:
            return text
    raise ValueError("Prompt text is required (argument or stdin).")


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
    store: SessionStore,
    chat_id: str,
    cwd: Path,
) -> str:
    session = await connection.new_session(cwd=str(cwd), mcp_servers=[])
    session_id = getattr(session, "session_id", None) or getattr(session, "sessionId", None)
    if not session_id:
        raise RuntimeError("ACP new_session did not return a session id.")
    store.upsert(chat_id, session_id)
    return session_id


async def _send_prompt(
    connection: Any,
    client: AcpConsoleClient,
    store: SessionStore,
    chat_id: str,
    session_id: str,
    prompt: str,
    cwd: Path,
    mode_id: str | None,
) -> str:
    try:
        if mode_id:
            await connection.set_session_mode(mode_id=mode_id, session_id=session_id)
        response = await connection.prompt(
            session_id=session_id,
            prompt=[text_block(prompt)],
        )
        stop_reason = getattr(response, "stop_reason", None) or getattr(response, "stopReason", None)
        if stop_reason == "refusal":
            client.emit_error(f"ACP prompt refused for session {session_id}")
            new_session_id = await _create_session(connection, store, chat_id, cwd)
            if mode_id:
                await connection.set_session_mode(mode_id=mode_id, session_id=new_session_id)
            await connection.prompt(
                session_id=new_session_id,
                prompt=[text_block(prompt)],
            )
            return new_session_id
        return session_id
    except RequestError as exc:
        client.emit_error(f"ACP request failed: {exc}")
        if not _is_session_not_found(exc):
            raise
        LOGGER.warning(
            "ACP session not found for chat_id %s; creating a new session.",
            chat_id,
        )
        client.emit_error(f"ACP session not found: {session_id}")
        new_session_id = await _create_session(connection, store, chat_id, cwd)
        if mode_id:
            await connection.set_session_mode(mode_id=mode_id, session_id=new_session_id)
        await connection.prompt(
            session_id=new_session_id,
            prompt=[text_block(prompt)],
        )
        return new_session_id


async def run_via_socket(args: argparse.Namespace) -> int:
    prompt_text = _resolve_prompt(args)
    store = SessionStore(Path(args.db).expanduser().resolve())
    mapping = store.get(args.chat_id)
    session_id = mapping.session_id if mapping else None
    socket_path = Path(args.socket).expanduser().resolve()
    try:
        reader, writer = await asyncio.open_unix_connection(str(socket_path))
    except FileNotFoundError as exc:
        raise RuntimeError(f"ACP service socket not found: {socket_path}") from exc
    except Exception as exc:
        raise RuntimeError(f"Failed to connect to ACP service: {exc}") from exc

    request = {
        "type": "prompt",
        "session_id": session_id,
        "prompt": prompt_text,
        "mode_id": args.mode_id,
        "auto_approve": args.auto_approve or args.allow_always,
        "allow_always": args.allow_always,
        "strip_leading_newlines": args.strip_leading_newlines,
    }
    await _send_socket_message(writer, request)

    stream_chunk_delimeter = args.stream_chunk_delimeter
    chunk_delimeter_active = False
    try:
        while True:
            message = await _read_socket_message(reader)
            if message is None:
                break
            msg_type = message.get("type")
            if msg_type == "chunk":
                text = message.get("text") or ""
                if text:
                    sys.stdout.write(text)
                    sys.stdout.flush()
                    if stream_chunk_delimeter:
                        sys.stderr.write(".")
                        sys.stderr.flush()
                        chunk_delimeter_active = True
                continue
            if chunk_delimeter_active:
                sys.stderr.write("\n")
                sys.stderr.flush()
                chunk_delimeter_active = False
            if msg_type == "error":
                text = message.get("message") or "Unknown ACP error."
                sys.stderr.write(f"[acp][error] {text}\n")
                sys.stderr.flush()
                continue
            if msg_type == "permission_request":
                options = message.get("options") or []
                title = message.get("title") or "tool execution"
                if args.auto_approve or args.allow_always:
                    option_id = "allow_always" if args.allow_always else "allow_once"
                    await _send_socket_message(
                        writer,
                        {"type": "permission_response", "option_id": option_id},
                    )
                    continue
                if not sys.stdin.isatty():
                    reject_id = "reject_once"
                    if isinstance(options, list) and any(
                        opt.get("option_id") == reject_id for opt in options
                    ):
                        await _send_socket_message(
                            writer,
                            {"type": "permission_response", "option_id": reject_id},
                        )
                        continue
                    await _send_socket_message(
                        writer,
                        {"type": "permission_response", "option_id": None},
                    )
                    continue
                sys.stderr.write(f"\nPermission required: {title}\n")
                for idx, opt in enumerate(options, start=1):
                    name = opt.get("name") or opt.get("option_id") or "option"
                    option_id = opt.get("option_id") or ""
                    sys.stderr.write(f"  {idx}) {name} ({option_id})\n")
                sys.stderr.flush()
                option_id = await _select_permission_option_id(options, sys.stderr)
                await _send_socket_message(
                    writer,
                    {"type": "permission_response", "option_id": option_id},
                )
                continue
            if msg_type == "done":
                new_session_id = message.get("session_id")
                if isinstance(new_session_id, str) and new_session_id:
                    store.upsert(args.chat_id, new_session_id)
                break
            debug_print("[acp]", f"Unknown service message: {msg_type}")
    finally:
        writer.close()
        with contextlib.suppress(Exception):
            await writer.wait_closed()

    return 0


async def run(args: argparse.Namespace) -> int:
    if args.connect:
        return await run_via_socket(args)
    if not args.card_path:
        raise ValueError("--card is required when spawning the ACP server.")
    card_path = Path(args.card_path).expanduser().resolve()
    server_cwd = (
        Path(args.server_cwd).expanduser().resolve()
        if args.server_cwd
        else card_path.parent
    )
    if not server_cwd.exists():
        raise FileNotFoundError(f"Server cwd not found: {server_cwd}")

    store = SessionStore(Path(args.db).expanduser().resolve())
    prompt_text = _resolve_prompt(args)
    command = _resolve_server_command(card_path, args.server_cmd)

    auto_approve = args.auto_approve or args.allow_always
    client = AcpConsoleClient(
        auto_approve=auto_approve,
        allow_always=args.allow_always,
        strip_leading_newlines=args.strip_leading_newlines,
        stream_chunk_delimeter=args.stream_chunk_delimeter,
    )
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
    try:
        await connection.initialize(
            protocol_version=PROTOCOL_VERSION,
            client_capabilities=capabilities,
            client_info=CLIENT_INFO,
        )

        mapping = store.get(args.chat_id)
        session_id = mapping.session_id if mapping else await _create_session(
            connection, store, args.chat_id, server_cwd
        )

        await _send_prompt(
            connection,
            client,
            store,
            args.chat_id,
            session_id,
            prompt_text,
            server_cwd,
            args.mode_id,
        )
    finally:
        await connection.close()
        if proc.returncode is None:
            proc.terminate()
            with contextlib.suppress(ProcessLookupError):
                await proc.wait()

    return 0


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="ACP console client for fast-agent.",
    )
    parser.add_argument("chat_id", help="Chat id to map to an ACP session.")
    parser.add_argument(
        "prompt",
        nargs="*",
        help="Prompt text. If omitted, the client reads from stdin.",
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
        "--connect",
        action="store_true",
        help="Connect to a running ACP service over a local socket.",
    )
    parser.add_argument(
        "--socket",
        default=str(DEFAULT_SOCKET_PATH),
        help="Path to ACP service socket (used with --connect).",
    )
    parser.add_argument(
        "--db",
        default=str(DEFAULT_DB_PATH),
        help="SQLite path for chat id to session id mappings.",
    )
    parser.add_argument(
        "--mode",
        dest="mode_id",
        default=None,
        help="Optional ACP mode id (agent name).",
    )
    parser.add_argument(
        "--stream-limit",
        dest="stream_limit",
        type=int,
        default=64 * 1024 * 1024,
        help="Max bytes per ACP stdio line before raising a limit error.",
    )
    parser.add_argument(
        "--auto-approve",
        action="store_true",
        help="Auto-approve tool permissions (allow once).",
    )
    parser.add_argument(
        "--allow-always",
        action="store_true",
        help="Auto-approve tool permissions and persist allow_always.",
    )
    parser.add_argument(
        "--strip-leading-newlines",
        action="store_true",
        help="Strip leading newlines from the first streamed chunk.",
    )
    parser.add_argument(
        "--stream-chunk-delimeter",
        action="store_true",
        help="Print '.' to stderr for each streamed chunk.",
    )
    return parser.parse_args(argv)


def main() -> int:
    configure_logging()
    args = parse_args(sys.argv[1:])
    try:
        return asyncio.run(run(args))
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        debug_print("[acp]", f"ACP client failed: {exc}")
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
