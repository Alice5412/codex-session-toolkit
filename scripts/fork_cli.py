#!/usr/bin/env python3
from __future__ import annotations

import base64
import hashlib
import json
import msvcrt
import os
import secrets
import shutil
import socket
import struct
import subprocess
import sys
import textwrap
import time
import argparse
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from account_switcher import (
    AccountSourceSummary,
    AccountSwitchError,
    describe_target_codex_home,
    find_matching_target_account,
    list_account_sources,
    resolve_account_source,
    resolve_accounts_root,
    switch_account,
)
from desktop_app import restart_codex_desktop_app
from transfer_cli import (
    TransferCliError,
    assign_transfer_conversations_cli,
    copy_transfer_conversations_cli,
    list_transfer_view_cli,
)


SESSION_GLOB = "rollout-*.jsonl"
DEFAULT_CODEX_HOME = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
WINDOWS_APPDATA_CODEX = Path.home() / "AppData" / "Roaming" / "npm" / "codex.cmd"


class ForkToolError(RuntimeError):
    pass


@dataclass(slots=True)
class SessionSummary:
    thread_id: str
    rollout_path: Path
    cwd: str
    created_at: str
    updated_at: float
    title: str
    first_user_message: str
    forked_from_id: str = ""

    @property
    def updated_label(self) -> str:
        return datetime.fromtimestamp(self.updated_at).strftime("%Y-%m-%d %H:%M:%S")


@dataclass(slots=True)
class TurnSummary:
    turn_id: str
    index: int
    role: str
    text: str
    preview: str
    source: str = "app-server"


@dataclass(slots=True)
class UserTurnSummary:
    turn_id: str
    index: int
    user_text: str
    preview: str
    source: str = "app-server"


def normalize_path(path_value: str | Path) -> str:
    return str(Path(path_value).expanduser().resolve()).rstrip("\\/")


def strip_windows_extended_path_prefix(path_value: str) -> str:
    if path_value.startswith("\\\\?\\UNC\\"):
        return "\\" + path_value[7:]
    if path_value.startswith("\\\\?\\"):
        return path_value[4:]
    return path_value


def normalize_optional_path(path_value: str | Path) -> str:
    text = strip_windows_extended_path_prefix(str(path_value).strip())
    if not text:
        return ""
    try:
        return normalize_path(text)
    except OSError:
        return text.rstrip("\\/")


def shorten(text: str, limit: int = 100) -> str:
    cleaned = " ".join(text.split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 3] + "..."


def safe_json_loads(line: str) -> dict | None:
    try:
        data = json.loads(line)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def get_message_text(content: object) -> str:
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return ""

    parts: list[str] = []
    for item in content:
        if isinstance(item, dict):
            text = item.get("text")
            if isinstance(text, str):
                parts.append(text)
    return "".join(parts).strip()


def unique_ordered(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value and value not in seen:
            result.append(value)
            seen.add(value)
    return result


def read_first_line(path: Path) -> str:
    with path.open("r", encoding="utf-8") as handle:
        return handle.readline()


def parse_session_summary(rollout_path: Path) -> SessionSummary | None:
    first_line = read_first_line(rollout_path)
    first_obj = safe_json_loads(first_line)
    if not first_obj or first_obj.get("type") != "session_meta":
        return None

    payload = first_obj.get("payload")
    if not isinstance(payload, dict):
        return None

    thread_id = str(payload.get("id") or "").strip()
    if not thread_id:
        return None

    cwd = str(payload.get("cwd") or "").strip()
    created_at = str(payload.get("timestamp") or "").strip()
    forked_from_id = str(payload.get("forked_from_id") or "").strip()
    first_user_message = ""
    title = ""

    with rollout_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            obj = safe_json_loads(line)
            if not obj or obj.get("type") != "event_msg":
                continue
            event_payload = obj.get("payload")
            if not isinstance(event_payload, dict):
                continue
            if event_payload.get("type") != "user_message":
                continue
            message = str(event_payload.get("message") or "").strip()
            if not message:
                continue
            first_user_message = message
            title = shorten(message.splitlines()[0], 80)
            break

    if not title:
        title = shorten(thread_id, 80)

    return SessionSummary(
        thread_id=thread_id,
        rollout_path=rollout_path.resolve(),
        cwd=cwd,
        created_at=created_at,
        updated_at=rollout_path.stat().st_mtime,
        title=title,
        first_user_message=first_user_message,
        forked_from_id=forked_from_id,
    )


def parse_session_summary_from_thread_data(thread_obj: object) -> SessionSummary | None:
    if not isinstance(thread_obj, dict):
        return None

    thread_id = str(thread_obj.get("id") or "").strip()
    if not thread_id:
        return None

    raw_cwd = str(thread_obj.get("cwd") or "").strip()
    raw_path = str(thread_obj.get("path") or "").strip()
    title = str(thread_obj.get("name") or thread_obj.get("preview") or thread_id).strip()
    preview = str(thread_obj.get("preview") or "").strip()
    created_at_raw = thread_obj.get("createdAt")
    updated_at_raw = thread_obj.get("updatedAt")

    try:
        created_at = (
            datetime.fromtimestamp(float(created_at_raw)).isoformat()
            if created_at_raw is not None
            else ""
        )
    except (TypeError, ValueError, OSError):
        created_at = ""

    try:
        updated_at = float(updated_at_raw)
    except (TypeError, ValueError):
        return None

    rollout_path = Path(strip_windows_extended_path_prefix(raw_path)) if raw_path else Path()
    if raw_path:
        try:
            rollout_path = rollout_path.expanduser().resolve()
        except OSError:
            rollout_path = Path(strip_windows_extended_path_prefix(raw_path))

    return SessionSummary(
        thread_id=thread_id,
        rollout_path=rollout_path,
        cwd=normalize_optional_path(raw_cwd),
        created_at=created_at,
        updated_at=updated_at,
        title=shorten(title, 80),
        first_user_message=preview,
    )


def find_sessions_from_rollouts(codex_home: Path, workdir: str) -> list[SessionSummary]:
    sessions_root = codex_home / "sessions"
    if not sessions_root.exists():
        raise ForkToolError(f"Codex sessions directory not found: {sessions_root}")

    normalized_workdir = normalize_path(workdir) if workdir.strip() else ""
    latest_by_thread: dict[str, SessionSummary] = {}

    for rollout_path in sessions_root.rglob(SESSION_GLOB):
        summary = parse_session_summary(rollout_path)
        if not summary:
            continue
        if normalized_workdir:
            if not summary.cwd:
                continue
            if normalize_path(summary.cwd) != normalized_workdir:
                continue
        previous = latest_by_thread.get(summary.thread_id)
        if previous is None or summary.updated_at > previous.updated_at:
            latest_by_thread[summary.thread_id] = summary

    return sorted(latest_by_thread.values(), key=lambda item: item.updated_at, reverse=True)


def find_sessions_from_current_account(workdir: str) -> list[SessionSummary]:
    normalized_workdir = normalize_path(workdir) if workdir.strip() else ""
    latest_by_thread: dict[str, SessionSummary] = {}
    client = CodexAppServerClient()

    try:
        response = client.request("thread/list", {})
    finally:
        client.stop()

    result = response.get("result")
    if not isinstance(result, dict):
        raise ForkToolError("thread/list returned an unexpected payload")

    data = result.get("data")
    if not isinstance(data, list):
        raise ForkToolError("thread/list returned no data array")

    for thread_obj in data:
        summary = parse_session_summary_from_thread_data(thread_obj)
        if summary is None:
            continue
        if normalized_workdir and summary.cwd != normalized_workdir:
            continue
        previous = latest_by_thread.get(summary.thread_id)
        if previous is None or summary.updated_at > previous.updated_at:
            latest_by_thread[summary.thread_id] = summary

    return sorted(latest_by_thread.values(), key=lambda item: item.updated_at, reverse=True)


def find_sessions(codex_home: Path, workdir: str) -> list[SessionSummary]:
    try:
        return find_sessions_from_current_account(workdir)
    except Exception:  # noqa: BLE001
        return find_sessions_from_rollouts(codex_home, workdir)


def parse_turns_from_rollout(rollout_path: Path) -> list[TurnSummary]:
    turn_order: list[str] = []
    turn_messages: dict[str, list[tuple[str, str]]] = {}
    current_turn_id = ""

    with rollout_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            obj = safe_json_loads(line)
            if not obj:
                continue
            entry_type = obj.get("type")
            payload = obj.get("payload")
            if not isinstance(payload, dict):
                continue

            if entry_type == "turn_context":
                current_turn_id = str(payload.get("turn_id") or "").strip()
                if current_turn_id and current_turn_id not in turn_messages:
                    turn_order.append(current_turn_id)
                    turn_messages[current_turn_id] = []
                continue

            if entry_type != "response_item":
                continue
            if payload.get("type") != "message":
                continue
            if not current_turn_id:
                continue

            role = str(payload.get("role") or "").strip()
            if role not in {"user", "assistant"}:
                continue
            text = get_message_text(payload.get("content"))
            if not text:
                continue
            turn_messages[current_turn_id].append((role, text))

    turns: list[TurnSummary] = []
    for index, turn_id in enumerate(turn_order):
        messages = turn_messages.get(turn_id, [])
        texts = [text for _, text in messages]
        roles = unique_ordered([role for role, _ in messages])
        joined_text = "\n\n".join(texts).strip()
        turns.append(
            TurnSummary(
                turn_id=turn_id,
                index=index,
                role="/".join(roles) if roles else "unknown",
                text=joined_text,
                preview=shorten(joined_text or turn_id),
                source="rollout",
            )
        )
    return turns


def iter_message_entries(value: object) -> list[tuple[str, str]]:
    messages: list[tuple[str, str]] = []
    if isinstance(value, dict):
        value_type = value.get("type")
        role = value.get("role")
        if value_type == "message" and role in {"user", "assistant"}:
            text = get_message_text(value.get("content"))
            if text:
                messages.append((str(role), text))
        for child in value.values():
            messages.extend(iter_message_entries(child))
    elif isinstance(value, list):
        for item in value:
            messages.extend(iter_message_entries(item))
    return messages


def parse_turns_from_thread(thread_obj: dict) -> list[TurnSummary]:
    raw_turns = thread_obj.get("turns")
    if not isinstance(raw_turns, list):
        return []

    turns: list[TurnSummary] = []
    for index, raw_turn in enumerate(raw_turns):
        if not isinstance(raw_turn, dict):
            continue
        turn_id = str(raw_turn.get("id") or "").strip()
        messages = iter_message_entries(raw_turn)
        texts = unique_ordered([text for _, text in messages])
        roles = unique_ordered([role for role, _ in messages])
        combined_text = "\n\n".join(texts).strip()
        if not combined_text:
            for key in ("summary", "preview", "title"):
                fallback = raw_turn.get(key)
                if isinstance(fallback, str) and fallback.strip():
                    combined_text = fallback.strip()
                    break
        turns.append(
            TurnSummary(
                turn_id=turn_id or f"turn-{index}",
                index=index,
                role="/".join(roles) if roles else "unknown",
                text=combined_text,
                preview=shorten(combined_text or turn_id or f"turn-{index}"),
                source="app-server",
            )
        )
    return turns


def parse_user_turns_from_rollout(rollout_path: Path) -> list[UserTurnSummary]:
    turn_order: list[str] = []
    turn_user_messages: dict[str, list[str]] = {}
    current_turn_id = ""

    with rollout_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            obj = safe_json_loads(line)
            if not obj:
                continue
            entry_type = obj.get("type")
            payload = obj.get("payload")
            if not isinstance(payload, dict):
                continue

            if entry_type == "turn_context":
                current_turn_id = str(payload.get("turn_id") or "").strip()
                if current_turn_id and current_turn_id not in turn_user_messages:
                    turn_order.append(current_turn_id)
                    turn_user_messages[current_turn_id] = []
                continue

            if entry_type != "response_item":
                continue
            if payload.get("type") != "message":
                continue
            if str(payload.get("role") or "").strip() != "user":
                continue
            if not current_turn_id:
                continue

            text = get_message_text(payload.get("content"))
            if text:
                turn_user_messages[current_turn_id].append(text)

    user_turns: list[UserTurnSummary] = []
    for index, turn_id in enumerate(turn_order):
        user_texts = unique_ordered(turn_user_messages.get(turn_id, []))
        if not user_texts:
            continue
        joined_text = "\n\n".join(user_texts).strip()
        user_turns.append(
            UserTurnSummary(
                turn_id=turn_id,
                index=index,
                user_text=joined_text,
                preview=shorten(joined_text),
                source="rollout",
            )
        )
    return user_turns


def parse_user_turns_from_thread(thread_obj: dict) -> list[UserTurnSummary]:
    raw_turns = thread_obj.get("turns")
    if not isinstance(raw_turns, list):
        return []

    user_turns: list[UserTurnSummary] = []
    for index, raw_turn in enumerate(raw_turns):
        if not isinstance(raw_turn, dict):
            continue
        turn_id = str(raw_turn.get("id") or "").strip()
        user_texts = unique_ordered([text for role, text in iter_message_entries(raw_turn) if role == "user"])
        if not user_texts:
            continue
        joined_text = "\n\n".join(user_texts).strip()
        user_turns.append(
            UserTurnSummary(
                turn_id=turn_id or f"turn-{index}",
                index=index,
                user_text=joined_text,
                preview=shorten(joined_text),
                source="app-server",
            )
        )
    return user_turns


class SimpleWebSocket:
    def __init__(self, sock: socket.socket, extra_data: bytes = b"") -> None:
        self.sock = sock
        self.buffer = bytearray(extra_data)

    @classmethod
    def connect(cls, url: str, timeout: float = 10.0) -> "SimpleWebSocket":
        parsed = urlparse(url)
        if parsed.scheme != "ws":
            raise ForkToolError(f"Only ws:// URLs are supported, got: {url}")

        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or 80
        path = parsed.path or "/"
        if parsed.query:
            path = f"{path}?{parsed.query}"

        sock = socket.create_connection((host, port), timeout=timeout)
        sock.settimeout(timeout)

        key = base64.b64encode(secrets.token_bytes(16)).decode("ascii")
        request = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "\r\n"
        )
        sock.sendall(request.encode("ascii"))

        response = bytearray()
        while b"\r\n\r\n" not in response:
            chunk = sock.recv(4096)
            if not chunk:
                raise ForkToolError("WebSocket handshake failed: empty response")
            response.extend(chunk)

        header_bytes, extra = response.split(b"\r\n\r\n", 1)
        header_text = header_bytes.decode("latin1")
        header_lines = header_text.split("\r\n")
        status_line = header_lines[0]
        if " 101 " not in status_line:
            raise ForkToolError(f"WebSocket handshake failed: {status_line}")

        headers: dict[str, str] = {}
        for line in header_lines[1:]:
            if ":" not in line:
                continue
            name, value = line.split(":", 1)
            headers[name.strip().lower()] = value.strip()

        expected_accept = base64.b64encode(
            hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode("ascii")).digest()
        ).decode("ascii")
        if headers.get("sec-websocket-accept") != expected_accept:
            raise ForkToolError("WebSocket handshake failed: bad Sec-WebSocket-Accept")

        sock.settimeout(30.0)
        return cls(sock, extra)

    def close(self) -> None:
        try:
            self._send_frame(0x8, b"")
        except OSError:
            pass
        self.sock.close()

    def send_text(self, text: str) -> None:
        self._send_frame(0x1, text.encode("utf-8"))

    def receive_text(self) -> str:
        while True:
            opcode, payload = self._read_frame()
            if opcode == 0x1:
                return payload.decode("utf-8")
            if opcode == 0x8:
                raise ForkToolError("App server closed the WebSocket connection")
            if opcode == 0x9:
                self._send_frame(0xA, payload)

    def _read_exact(self, size: int) -> bytes:
        while len(self.buffer) < size:
            chunk = self.sock.recv(max(4096, size - len(self.buffer)))
            if not chunk:
                raise ForkToolError("WebSocket connection closed unexpectedly")
            self.buffer.extend(chunk)
        data = bytes(self.buffer[:size])
        del self.buffer[:size]
        return data

    def _read_frame(self) -> tuple[int, bytes]:
        header = self._read_exact(2)
        first, second = header[0], header[1]
        opcode = first & 0x0F
        masked = bool(second & 0x80)
        payload_len = second & 0x7F

        if payload_len == 126:
            payload_len = struct.unpack("!H", self._read_exact(2))[0]
        elif payload_len == 127:
            payload_len = struct.unpack("!Q", self._read_exact(8))[0]

        mask_key = self._read_exact(4) if masked else b""
        payload = self._read_exact(payload_len)

        if masked:
            payload = bytes(byte ^ mask_key[index % 4] for index, byte in enumerate(payload))
        return opcode, payload

    def _send_frame(self, opcode: int, payload: bytes) -> None:
        mask_key = secrets.token_bytes(4)
        first_byte = 0x80 | (opcode & 0x0F)
        payload_len = len(payload)

        if payload_len < 126:
            header = bytes([first_byte, 0x80 | payload_len])
        elif payload_len < 65536:
            header = bytes([first_byte, 0x80 | 126]) + struct.pack("!H", payload_len)
        else:
            header = bytes([first_byte, 0x80 | 127]) + struct.pack("!Q", payload_len)

        masked_payload = bytes(byte ^ mask_key[index % 4] for index, byte in enumerate(payload))
        self.sock.sendall(header + mask_key + masked_payload)


class CodexAppServerClient:
    def __init__(self, codex_command: str | None = None) -> None:
        self.codex_command = codex_command or self._find_codex_command()
        self.process: subprocess.Popen[str] | None = None
        self.socket: SimpleWebSocket | None = None
        self.request_id = 0
        self.port: int | None = None

    def _find_codex_command(self) -> str:
        for name in ("codex", "codex.cmd"):
            found = shutil.which(name)
            if found:
                return found
        if WINDOWS_APPDATA_CODEX.exists():
            return str(WINDOWS_APPDATA_CODEX)
        raise ForkToolError("Cannot locate codex command. Install Codex CLI or add it to PATH.")

    def ensure_started(self) -> None:
        if self.socket is not None:
            return

        self.port = self._get_free_port()
        app_server_url = f"ws://127.0.0.1:{self.port}"
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self.process = subprocess.Popen(
            [self.codex_command, "app-server", "--listen", app_server_url],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
        )

        deadline = time.time() + 20
        last_error: Exception | None = None
        while time.time() < deadline:
            if self.process.poll() is not None:
                raise ForkToolError("Codex app-server exited before it became ready")
            try:
                self.socket = SimpleWebSocket.connect(app_server_url, timeout=2.0)
                break
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                time.sleep(0.25)

        if self.socket is None:
            self.stop()
            raise ForkToolError(f"Failed to connect to Codex app-server: {last_error}")

        self.request(
            "initialize",
            {
                "clientInfo": {"name": "codex-any-node-fork-cli", "version": "1.0"},
                "capabilities": {"experimentalApi": True},
            },
        )

    def stop(self) -> None:
        port = self.port
        if self.socket is not None:
            try:
                self.socket.close()
            except OSError:
                pass
            self.socket = None

        if self.process is not None:
            if self.process.poll() is None:
                self.process.kill()
                try:
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass
            self.process = None
        self.port = None

        if port is not None:
            self._stop_app_server_processes(port)

    def request(self, method: str, params: dict) -> dict:
        self.ensure_started()
        if self.socket is None:
            raise ForkToolError("Codex app-server socket is not connected")

        self.request_id += 1
        payload = {"id": self.request_id, "method": method, "params": params}
        self.socket.send_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))

        while True:
            raw_message = self.socket.receive_text()
            message = safe_json_loads(raw_message)
            if not message:
                continue
            if "id" not in message:
                continue
            if str(message["id"]) != str(self.request_id):
                continue
            if "error" in message and message["error"] is not None:
                raise ForkToolError(f"App server error for {method}: {json.dumps(message['error'], ensure_ascii=False)}")
            return message

    def read_thread(self, thread_id: str) -> dict:
        response = self.request("thread/read", {"threadId": thread_id, "includeTurns": True})
        result = response.get("result")
        if not isinstance(result, dict) or not isinstance(result.get("thread"), dict):
            raise ForkToolError("thread/read returned an unexpected payload")
        return result["thread"]

    def fork_thread(self, thread_id: str) -> dict:
        response = self.request("thread/fork", {"threadId": thread_id})
        result = response.get("result")
        if not isinstance(result, dict) or not isinstance(result.get("thread"), dict):
            raise ForkToolError("thread/fork returned an unexpected payload")
        return result["thread"]

    def resume_thread(self, thread_id: str, persist_extended_history: bool = False) -> dict:
        response = self.request(
            "thread/resume",
            {
                "threadId": thread_id,
                "persistExtendedHistory": persist_extended_history,
            },
        )
        result = response.get("result")
        if not isinstance(result, dict) or not isinstance(result.get("thread"), dict):
            raise ForkToolError("thread/resume returned an unexpected payload")
        return result["thread"]

    def rollback_thread(self, thread_id: str, num_turns: int) -> None:
        self.request("thread/rollback", {"threadId": thread_id, "numTurns": num_turns})

    def list_loaded_threads(self) -> list[str]:
        response = self.request("thread/loaded/list", {})
        result = response.get("result")
        if not isinstance(result, dict):
            raise ForkToolError("thread/loaded/list returned an unexpected payload")
        data = result.get("data")
        if not isinstance(data, list):
            raise ForkToolError("thread/loaded/list returned no data array")
        return [str(item) for item in data]

    @staticmethod
    def _get_free_port() -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            sock.listen(1)
            return int(sock.getsockname()[1])

    @staticmethod
    def _stop_app_server_processes(port: int) -> None:
        command = (
            f"$pattern = '*app-server --listen ws://127.0.0.1:{port}*'; "
            "Get-CimInstance Win32_Process | "
            "Where-Object { $_.Name -in @('cmd.exe','node.exe','codex.exe') -and $_.CommandLine -like $pattern } | "
            "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
        )
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        subprocess.run(
            ["powershell", "-NoProfile", "-Command", command],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
            check=False,
        )


def clear_screen() -> None:
    os.system("cls")


def read_key() -> str:
    key = msvcrt.getwch()
    if key in ("\x00", "\xe0"):
        code = msvcrt.getwch()
        return {"H": "up", "P": "down"}.get(code, "")
    if key == "\r":
        return "enter"
    if key == "\x08":
        return "back"
    if key == "\x1b":
        return "escape"
    if key in {"q", "Q"}:
        return "quit"
    return key


def wait_for_key(prompt: str = "Press any key to continue...") -> None:
    print()
    print(prompt)
    msvcrt.getwch()


def clear_screen() -> None:
    os.system("cls")


def read_key() -> str:
    key = msvcrt.getwch()
    if key in ("\x00", "\xe0"):
        code = msvcrt.getwch()
        return {"H": "up", "P": "down"}.get(code, "")
    if key == "\r":
        return "enter"
    if key == "\x08":
        return "back"
    if key == "\x1b":
        return "escape"
    if key in {"q", "Q"}:
        return "quit"
    return key


def wait_for_key(prompt: str = "Press any key to continue...") -> None:
    print()
    print(prompt)
    msvcrt.getwch()


def clear_screen() -> None:
    os.system("cls")


def read_key() -> str:
    key = msvcrt.getwch()
    if key in ("\x00", "\xe0"):
        code = msvcrt.getwch()
        return {"H": "up", "P": "down"}.get(code, "")
    if key == "\r":
        return "enter"
    if key == "\x08":
        return "back"
    if key == "\x1b":
        return "escape"
    if key in {"q", "Q"}:
        return "quit"
    return key


def wait_for_key(prompt: str = "Press any key to continue...") -> None:
    print()
    print(prompt)
    msvcrt.getwch()


def clear_screen() -> None:
    os.system("cls")


def read_key() -> str:
    key = msvcrt.getwch()
    if key in ("\x00", "\xe0"):
        code = msvcrt.getwch()
        return {"H": "up", "P": "down"}.get(code, "")
    if key == "\r":
        return "enter"
    if key == "\x08":
        return "back"
    if key == "\x1b":
        return "escape"
    if key in {"q", "Q"}:
        return "quit"
    return key


def wait_for_key(prompt: str = "Press any key to continue...") -> None:
    print()
    print(prompt)
    msvcrt.getwch()


def format_detail(text: str, width: int, max_lines: int = 14) -> str:
    wrapped: list[str] = []
    inner_width = max(20, width - 4)

    for raw_line in text.splitlines() or [""]:
        stripped = raw_line.strip()
        if not stripped:
            wrapped.append("")
            continue
        wrapped.extend(textwrap.wrap(stripped, width=inner_width))

    if len(wrapped) > max_lines:
        wrapped = wrapped[: max_lines - 1] + ["..."]
    return "\n".join(wrapped)


def choose_item(
    *,
    title: str,
    subtitle: str,
    items: list,
    render_item,
    render_detail,
    allow_back: bool,
) -> object | None:
    if not items:
        clear_screen()
        print(title)
        if subtitle:
            print(subtitle)
        print()
        print("No items available.")
        wait_for_key()
        return None

    selected = 0
    while True:
        width, height = shutil.get_terminal_size((120, 30))
        page_size = max(6, height - 12)
        start = max(0, min(selected - page_size // 2, max(0, len(items) - page_size)))
        end = min(len(items), start + page_size)

        clear_screen()
        print(title)
        if subtitle:
            print(subtitle)
        print()

        for idx in range(start, end):
            marker = ">" if idx == selected else " "
            print(f"{marker} {idx + 1:>3}. {render_item(items[idx], width)}")

        print()
        print("Up/Down: select | Enter: confirm | Backspace: back | q: quit")
        print("-" * width)
        print(render_detail(items[selected], width))

        key = read_key()
        if key == "up":
            selected = (selected - 1) % len(items)
        elif key == "down":
            selected = (selected + 1) % len(items)
        elif key == "enter":
            return items[selected]
        elif key in {"back", "escape"} and allow_back:
            return None
        elif key == "quit":
            raise KeyboardInterrupt


def render_session_item(session: SessionSummary, width: int) -> str:
    base = f"{session.updated_label} | {session.title}"
    return shorten(base, max(30, width - 10))


def render_session_detail(session: SessionSummary, width: int) -> str:
    parts = [
        f"Thread ID: {session.thread_id}",
        f"Workdir:   {session.cwd}",
    ]
    if session.forked_from_id:
        parts.append(f"Forked from: {session.forked_from_id}")
    if session.first_user_message:
        parts.append("")
        parts.append("First user message:")
        parts.append(format_detail(session.first_user_message, width, max_lines=10))
    return "\n".join(parts)


def render_turn_item(turn: UserTurnSummary, width: int) -> str:
    base = f"turn {turn.index:>3} | {turn.preview}"
    return shorten(base, max(30, width - 10))


def render_turn_detail(turn: UserTurnSummary, width: int) -> str:
    return "\n".join(
        [
            f"Turn ID: {turn.turn_id}",
            f"Source:  {turn.source}",
            "",
            "User message:",
            format_detail(turn.user_text, width, max_lines=14),
        ]
    )


def load_user_turns_for_session(
    session: SessionSummary,
    client: CodexAppServerClient,
) -> tuple[list[UserTurnSummary], str | None]:
    app_server_error: str | None = None
    user_turns: list[UserTurnSummary] = []

    try:
        thread = client.read_thread(session.thread_id)
        user_turns = parse_user_turns_from_thread(thread)
    except Exception as exc:  # noqa: BLE001
        app_server_error = str(exc)

    if not user_turns:
        user_turns = parse_user_turns_from_rollout(session.rollout_path)

    return user_turns, app_server_error


def load_switchable_accounts(
    accounts_root: Path | None,
) -> tuple[Path, list[AccountSourceSummary]]:
    try:
        resolved_root = resolve_accounts_root(accounts_root)
        accounts = list_account_sources(resolved_root)
    except AccountSwitchError as exc:
        raise ForkToolError(str(exc)) from exc

    if not accounts:
        raise ForkToolError(
            f"No switchable accounts were found under {resolved_root}. "
            "Expected subdirectories containing config.toml and auth.json."
        )
    return resolved_root, accounts


def list_accounts(codex_home: Path, accounts_root: Path | None) -> int:
    resolved_root, accounts = load_switchable_accounts(accounts_root)
    active_account = find_matching_target_account(accounts, codex_home)
    current_target = describe_target_codex_home(codex_home)

    print(f"Account root: {resolved_root}")
    print(f"Target Codex home: {codex_home}")
    print(f"Current target files: {current_target}")
    print()
    print("Accounts:")
    for account in accounts:
        marker = "*" if account.name == active_account else " "
        print(f"{marker} {account.name}\t{account.description}\t{account.directory}")

    print()
    if active_account:
        print(f"Active account: {active_account}")
    else:
        print("Active account: no exact source match")
    return 0


def run_account_switch(
    codex_home: Path,
    accounts_root: Path | None,
    account_name: str,
    *,
    restart_codex: bool,
) -> int:
    resolved_root, accounts = load_switchable_accounts(accounts_root)
    try:
        selected_account = resolve_account_source(accounts, account_name)
        result = switch_account(selected_account, codex_home)
    except AccountSwitchError as exc:
        raise ForkToolError(str(exc)) from exc

    restart_status = "skipped"
    restart_error = ""
    if restart_codex:
        restart_status, restart_error = restart_codex_desktop_app()

    print(f"Switched account: {result.account_name}")
    print(f"Account root:     {resolved_root}")
    print(f"Source folder:    {result.source_dir}")
    print(f"Target folder:    {result.target_dir}")
    print("Copied files:")
    for copied_file in result.copied_files:
        print(f"  - {copied_file}")

    if result.backup_dir is not None:
        print(f"Backup folder:    {result.backup_dir}")
    else:
        print("Backup folder:    not created (target files did not already exist)")

    if restart_codex:
        if restart_status == "restarted":
            print("Desktop app:      Codex App was restarted.")
        elif restart_status == "not_running":
            print("Desktop app:      Codex App was not running.")
        else:
            print("Desktop app:      Automatic restart failed.")
            if restart_error:
                print(f"Restart warning:  {restart_error}")
    else:
        print("Desktop app:      restart skipped by flag")

    return 0


def confirm_fork(session: SessionSummary, turn: UserTurnSummary) -> bool:
    while True:
        width, _ = shutil.get_terminal_size((120, 30))
        clear_screen()
        print("Confirm fork")
        print(f"Conversation: {session.title}")
        print(f"Thread ID:     {session.thread_id}")
        print(f"Target turn:   {turn.turn_id}")
        print(f"Turn index:    {turn.index}")
        print()
        print(format_detail(turn.user_text, width, max_lines=16))
        print()
        print("Enter: fork here | Backspace/Esc: cancel | q: quit")

        key = read_key()
        if key == "enter":
            return True
        if key in {"back", "escape"}:
            return False
        if key == "quit":
            raise KeyboardInterrupt


def perform_fork(
    session: SessionSummary,
    turn: UserTurnSummary,
    client: CodexAppServerClient,
) -> dict[str, str | int | bool]:
    source_thread = client.read_thread(session.thread_id)
    raw_turns = source_thread.get("turns")
    if not isinstance(raw_turns, list):
        raise ForkToolError("thread/read returned no turns for the source thread")

    target_index = next(
        (
            idx
            for idx, raw_turn in enumerate(raw_turns)
            if isinstance(raw_turn, dict) and str(raw_turn.get("id") or "").strip() == turn.turn_id
        ),
        -1,
    )
    if target_index < 0:
        raise ForkToolError(f"Target turn was not found in source thread: {turn.turn_id}")

    dropped_turns = len(raw_turns) - target_index - 1
    forked_thread = client.fork_thread(session.thread_id)
    forked_thread_id = str(forked_thread.get("id") or "").strip()
    if not forked_thread_id:
        raise ForkToolError("thread/fork did not return a new thread id")

    if dropped_turns > 0:
        client.rollback_thread(forked_thread_id, dropped_turns)

    verify_thread = client.read_thread(forked_thread_id)
    verify_turns = verify_thread.get("turns")
    if not isinstance(verify_turns, list) or not verify_turns:
        raise ForkToolError("Verification failed: forked thread has no turns")

    last_turn = verify_turns[-1]
    if not isinstance(last_turn, dict):
        raise ForkToolError("Verification failed: last turn payload is invalid")
    last_turn_id = str(last_turn.get("id") or "").strip()
    if last_turn_id != turn.turn_id:
        raise ForkToolError(
            f"Fork verification failed: expected last turn {turn.turn_id}, got {last_turn_id}"
        )

    codex_sync_status = "failed"
    codex_sync_error = ""
    try:
        client.resume_thread(forked_thread_id, persist_extended_history=False)
        loaded_threads = client.list_loaded_threads()
        codex_sync_status = "loaded" if forked_thread_id in loaded_threads else "requested"
    except Exception as exc:  # noqa: BLE001
        codex_sync_error = str(exc)

    return {
        "original_thread_id": session.thread_id,
        "forked_thread_id": forked_thread_id,
        "target_turn_id": turn.turn_id,
        "dropped_turns": dropped_turns,
        "forked_path": str(verify_thread.get("path") or "").strip(),
        "codex_sync_status": codex_sync_status,
        "codex_sync_error": codex_sync_error,
    }


def show_result(result: dict[str, str | int | bool]) -> None:
    clear_screen()
    print("Fork complete")
    print()
    print(f"Original thread: {result['original_thread_id']}")
    print(f"Forked thread:   {result['forked_thread_id']}")
    print(f"Target turn:     {result['target_turn_id']}")
    print(f"Dropped turns:   {result['dropped_turns']}")
    print(f"Fork path:       {result['forked_path'] or '(unknown)'}")
    print()
    if result["codex_sync_status"] == "loaded":
        print("Codex sync:      attempted via thread/resume and loaded in the current Codex backend.")
    elif result["codex_sync_status"] == "requested":
        print("Codex sync:      attempted via thread/resume.")
    else:
        print("Codex sync:      automatic load attempt failed.")
        if result["codex_sync_error"]:
            print(f"Load warning:    {result['codex_sync_error']}")
    print()
    if result["desktop_app_restart_status"] == "restarted":
        print("Desktop app:     Codex App was restarted to refresh the thread list.")
    elif result["desktop_app_restart_status"] == "not_running":
        print("Desktop app:     Codex App was not running, so no restart was needed.")
    else:
        print("Desktop app:     Automatic restart failed.")
        if result["desktop_app_restart_error"]:
            print(f"Restart warning: {result['desktop_app_restart_error']}")
    print()
    print("If the new thread still does not appear in Codex App, reopen the app manually.")
    wait_for_key()


def run_gui(codex_home: Path, workdir: Path | None, accounts_root: Path | None) -> int:
    if os.name == "nt":
        pythonw = Path(sys.executable).with_name("pythonw.exe")
        gui_script = Path(__file__).with_name("fork_gui.py")
        if pythonw.exists():
            argv = [
                str(pythonw),
                str(gui_script),
                "--codex-home",
                str(codex_home),
            ]
            if workdir is not None:
                argv.extend(["--workdir", str(workdir)])
            if accounts_root is not None:
                argv.extend(["--accounts-root", str(accounts_root.expanduser().resolve())])
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            try:
                subprocess.Popen(
                    argv,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=creationflags,
                )
                return 0
            except OSError:
                pass

    from fork_gui import main as gui_main

    argv = [
        "--codex-home",
        str(codex_home),
    ]
    if workdir is not None:
        argv.extend(["--workdir", str(workdir)])
    if accounts_root is not None:
        argv.extend(["--accounts-root", str(accounts_root.expanduser().resolve())])
    return gui_main(argv)


def run_interactive(codex_home: Path, workdir: Path) -> int:
    sessions = find_sessions(codex_home, str(workdir))
    if not sessions:
        clear_screen()
        print(f"No conversations found for workdir: {workdir}")
        wait_for_key()
        return 1

    client = CodexAppServerClient()
    try:
        while True:
            session = choose_item(
                title="Select conversation",
                subtitle=f"Workdir: {workdir}",
                items=sessions,
                render_item=render_session_item,
                render_detail=render_session_detail,
                allow_back=False,
            )
            if session is None:
                return 0

            user_turns, app_server_error = load_user_turns_for_session(session, client)
            subtitle = f"Conversation: {session.title}"
            if app_server_error:
                subtitle = subtitle + f" | preview fallback: {app_server_error}"

            turn = choose_item(
                title="Select user message to fork from",
                subtitle=subtitle,
                items=user_turns,
                render_item=render_turn_item,
                render_detail=render_turn_detail,
                allow_back=True,
            )
            if turn is None:
                continue

            if not confirm_fork(session, turn):
                continue

            result = perform_fork(session, turn, client)
            client.stop()
            restart_status, restart_error = restart_codex_desktop_app()
            result["desktop_app_restart_status"] = restart_status
            result["desktop_app_restart_error"] = restart_error
            show_result(result)
            return 0
    except KeyboardInterrupt:
        clear_screen()
        print("Cancelled.")
        return 130
    finally:
        client.stop()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Interactive Codex session toolkit")
    parser.add_argument("-ls", "--list-sessions", action="store_true", help="List conversations for the current working directory")
    parser.add_argument("--gui", action="store_true", help="Launch the graphical interface")
    parser.add_argument("--codex-home", type=Path, default=DEFAULT_CODEX_HOME, help="Codex home directory")
    parser.add_argument("--workdir", type=Path, help="Target working directory; defaults to current directory")
    parser.add_argument("--accounts-root", type=Path, help="Directory containing one subdirectory per switchable account")
    parser.add_argument("--list-accounts", action="store_true", help="List switchable Codex account sources")
    parser.add_argument("--switch-account", metavar="NAME", help="Switch Codex account by copying NAME\\config.toml and auth.json into codex_home")
    parser.add_argument("--list-transfer-view", action="store_true", help="List local transfer conversations grouped by assigned account for the current workdir")
    parser.add_argument("--assign-conversations-to", metavar="NAME", help="Assign selected conversations to NAME in the transfer mapping")
    parser.add_argument("--copy-conversations-to", metavar="NAME", help="Copy selected conversations to NAME")
    parser.add_argument("--transfer-sources", nargs="+", metavar="SOURCE", help="Thread IDs or rollout paths used by transfer assignment/copy commands")
    parser.add_argument("--no-restart-codex", action="store_true", help="Skip automatic Codex Desktop restart after switching accounts")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if len(sys.argv) == 1:
        args.list_sessions = True

    workdir = args.workdir.expanduser().resolve() if args.workdir is not None else Path.cwd().resolve()
    codex_home = args.codex_home.expanduser().resolve()

    if args.transfer_sources and not (args.assign_conversations_to or args.copy_conversations_to):
        parser.error("--transfer-sources requires --assign-conversations-to or --copy-conversations-to.")

    selected_actions = sum(
        int(bool(option))
        for option in (
            args.list_sessions,
            args.gui,
            args.list_accounts,
            args.switch_account,
            args.list_transfer_view,
            args.assign_conversations_to,
            args.copy_conversations_to,
        )
    )
    if selected_actions > 1:
        parser.error("Choose only one primary action at a time.")

    if args.list_accounts:
        try:
            return list_accounts(codex_home, args.accounts_root)
        except ForkToolError as exc:
            clear_screen()
            print(f"Error: {exc}")
            return 2

    if args.switch_account:
        try:
            return run_account_switch(
                codex_home,
                args.accounts_root,
                args.switch_account,
                restart_codex=not args.no_restart_codex,
            )
        except ForkToolError as exc:
            print(f"Error: {exc}")
            return 2

    if args.list_transfer_view:
        try:
            return list_transfer_view_cli(codex_home, workdir, args.accounts_root)
        except (ForkToolError, TransferCliError) as exc:
            print(f"Error: {exc}")
            return 2

    if args.assign_conversations_to:
        if not args.transfer_sources:
            parser.error("--assign-conversations-to requires --transfer-sources.")
        try:
            return assign_transfer_conversations_cli(
                codex_home,
                workdir,
                args.accounts_root,
                args.assign_conversations_to,
                args.transfer_sources,
            )
        except (ForkToolError, TransferCliError) as exc:
            print(f"Error: {exc}")
            return 2

    if args.copy_conversations_to:
        if not args.transfer_sources:
            parser.error("--copy-conversations-to requires --transfer-sources.")
        try:
            return copy_transfer_conversations_cli(
                codex_home,
                workdir,
                args.accounts_root,
                args.copy_conversations_to,
                args.transfer_sources,
                restart_codex=not args.no_restart_codex,
            )
        except (ForkToolError, TransferCliError) as exc:
            print(f"Error: {exc}")
            return 2

    if args.gui:
        explicit_workdir = args.workdir.expanduser().resolve() if args.workdir is not None else None
        return run_gui(codex_home, explicit_workdir, args.accounts_root)

    if not args.list_sessions:
        parser.error("Use `fork -ls` to start interactive selection.")

    try:
        return run_interactive(codex_home, workdir)
    except ForkToolError as exc:
        clear_screen()
        print(f"Error: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
