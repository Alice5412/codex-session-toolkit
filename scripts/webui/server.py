#!/usr/bin/env python3
from __future__ import annotations

import json
import mimetypes
import threading
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from .api import ToolkitWebService, WebUiError


ASSETS_DIR = Path(__file__).resolve().parent / "assets"


class ToolkitWebUiServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, server_address: tuple[str, int], service: ToolkitWebService) -> None:
        super().__init__(server_address, ToolkitWebUiHandler)
        self.service = service
        self.assets_dir = ASSETS_DIR.resolve()


class ToolkitWebUiHandler(BaseHTTPRequestHandler):
    server_version = "CodexSessionToolkitWebUI/1.0"

    @property
    def toolkit_server(self) -> ToolkitWebUiServer:
        return self.server  # type: ignore[return-value]

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/"):
            self._handle_api_get(parsed)
            return
        self._serve_static(parsed.path)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if not parsed.path.startswith("/api/"):
            self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "Unknown endpoint."})
            return
        self._handle_api_post(parsed)

    def log_message(self, format: str, *args: object) -> None:
        return

    def _handle_api_get(self, parsed: Any) -> None:
        params = parse_qs(parsed.query)
        workdir = self._first_query_value(params, "workdir")
        try:
            if parsed.path == "/api/health":
                payload = {"status": "ok"}
            elif parsed.path == "/api/bootstrap":
                payload = self.toolkit_server.service.get_bootstrap(workdir)
            elif parsed.path == "/api/sessions":
                payload = self.toolkit_server.service.list_sessions(workdir)
            elif parsed.path == "/api/session-turns":
                thread_id = self._required_query_value(params, "thread_id")
                payload = self.toolkit_server.service.get_session_turns(
                    requested_workdir=workdir,
                    thread_id=thread_id,
                )
            elif parsed.path == "/api/transfer-view":
                payload = self.toolkit_server.service.get_transfer_view(workdir)
            else:
                self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "Unknown endpoint."})
                return
        except WebUiError as exc:
            self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)})
            return
        except Exception as exc:  # noqa: BLE001
            self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "error": str(exc)})
            return
        self._send_json(HTTPStatus.OK, {"ok": True, "data": payload})

    def _handle_api_post(self, parsed: Any) -> None:
        try:
            body = self._read_json_body()
            if parsed.path == "/api/accounts/switch":
                payload = self.toolkit_server.service.switch_account(
                    self._required_body_value(body, "account_name"),
                    restart_codex=bool(body.get("restart_codex", True)),
                )
            elif parsed.path == "/api/fork":
                payload = self.toolkit_server.service.fork_session(
                    requested_workdir=self._optional_body_value(body, "workdir"),
                    thread_id=self._required_body_value(body, "thread_id"),
                    turn_id=self._required_body_value(body, "turn_id"),
                    restart_codex=bool(body.get("restart_codex", True)),
                )
            elif parsed.path == "/api/transfer/assign":
                payload = self.toolkit_server.service.assign_transfer_conversations(
                    requested_workdir=self._optional_body_value(body, "workdir"),
                    account_name=self._required_body_value(body, "account_name"),
                    thread_ids=self._required_body_list(body, "thread_ids"),
                )
            elif parsed.path == "/api/transfer/copy":
                payload = self.toolkit_server.service.copy_transfer_conversations(
                    requested_workdir=self._optional_body_value(body, "workdir"),
                    target_account=self._required_body_value(body, "target_account"),
                    thread_ids=self._required_body_list(body, "thread_ids"),
                    restart_codex=bool(body.get("restart_codex", True)),
                )
            else:
                self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "Unknown endpoint."})
                return
        except WebUiError as exc:
            self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)})
            return
        except Exception as exc:  # noqa: BLE001
            self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "error": str(exc)})
            return
        self._send_json(HTTPStatus.OK, {"ok": True, "data": payload})

    @staticmethod
    def _first_query_value(params: dict[str, list[str]], key: str) -> str | None:
        values = params.get(key) or []
        if not values:
            return None
        value = values[0].strip()
        return value or None

    def _required_query_value(self, params: dict[str, list[str]], key: str) -> str:
        value = self._first_query_value(params, key)
        if value is None:
            raise WebUiError(f"Missing query parameter: {key}")
        return value

    def _read_json_body(self) -> dict[str, object]:
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise WebUiError("Invalid Content-Length header.") from exc
        if content_length <= 0:
            return {}
        raw_body = self.rfile.read(content_length)
        if not raw_body:
            return {}
        try:
            payload = json.loads(raw_body.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise WebUiError("Request body is not valid JSON.") from exc
        if not isinstance(payload, dict):
            raise WebUiError("JSON body must be an object.")
        return payload

    @staticmethod
    def _optional_body_value(body: dict[str, object], key: str) -> str | None:
        value = body.get(key)
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    def _required_body_value(self, body: dict[str, object], key: str) -> str:
        value = self._optional_body_value(body, key)
        if value is None:
            raise WebUiError(f"Missing field: {key}")
        return value

    def _required_body_list(self, body: dict[str, object], key: str) -> list[str]:
        value = body.get(key)
        if not isinstance(value, list):
            raise WebUiError(f"Field '{key}' must be a list.")
        items = [str(item).strip() for item in value if str(item).strip()]
        if not items:
            raise WebUiError(f"Field '{key}' cannot be empty.")
        return items

    def _serve_static(self, request_path: str) -> None:
        relative_path = request_path.lstrip("/") or "index.html"
        requested = (self.toolkit_server.assets_dir / relative_path).resolve()
        if not str(requested).startswith(str(self.toolkit_server.assets_dir)) or not requested.is_file():
            requested = self.toolkit_server.assets_dir / "index.html"
        content_type, _ = mimetypes.guess_type(str(requested))
        data = requested.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type or "application/octet-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_json(self, status: HTTPStatus, payload: dict[str, object]) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def run_webui_server(
    *,
    codex_home: Path,
    initial_workdir: Path | None,
    accounts_root: Path | None,
    host: str,
    port: int,
    open_browser: bool,
) -> int:
    service = ToolkitWebService(
        codex_home=codex_home,
        accounts_root=accounts_root,
        initial_workdir=initial_workdir,
    )
    server = ToolkitWebUiServer((host, port), service)
    actual_host, actual_port = server.server_address[:2]
    browser_host = "127.0.0.1" if actual_host in {"0.0.0.0", "::", ""} else actual_host
    url = f"http://{browser_host}:{actual_port}"
    print(f"Web UI listening on {url}")
    print("Press Ctrl+C to stop.")
    if open_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping Web UI server.")
    finally:
        server.server_close()
    return 0
