#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
from typing import Any

from account_switcher import (
    AccountSwitchError,
    describe_target_codex_home,
    find_matching_target_account,
    list_account_sources,
    resolve_account_source,
    resolve_accounts_root,
    switch_account,
)
from app_state import load_workspace_state, save_workspace_state
from conversation_transfer import (
    UNASSIGNED_ACCOUNT,
    AccountProfile,
    ConversationTransferError,
    assign_threads_to_account,
    build_account_counts,
    copy_conversations_to_account,
    get_active_account_name,
    load_account_profiles,
    load_transfer_view,
    require_single_source_account,
    resolve_transfer_conversations,
)
from desktop_app import restart_codex_desktop_app
from fork_cli import (
    DEFAULT_CODEX_HOME,
    CodexAppServerClient,
    ForkToolError,
    find_sessions,
    load_user_turns_for_session,
    perform_fork,
)


MAX_REMEMBERED_WORKDIRS = 15


class WebUiError(RuntimeError):
    pass


def normalize_workdir(path_value: str | Path) -> str:
    return str(Path(path_value).expanduser().resolve())


def coerce_existing_workdirs(
    values: list[object],
    *,
    max_count: int = MAX_REMEMBERED_WORKDIRS,
) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if not isinstance(value, str):
            continue
        text = value.strip()
        if not text:
            continue
        try:
            normalized = normalize_workdir(text)
        except OSError:
            continue
        candidate = Path(normalized)
        if not candidate.exists() or not candidate.is_dir():
            continue
        if normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
        if len(result) >= max_count:
            break
    return result


def merge_workdir_history(
    current_workdir: str,
    state: dict[str, object],
    *,
    initial_workdir: Path | None,
    max_count: int = MAX_REMEMBERED_WORKDIRS,
) -> list[str]:
    values: list[object] = [current_workdir]
    if initial_workdir is not None:
        values.append(str(initial_workdir))
    values.append(state.get("last_workdir"))
    recent = state.get("recent_workdirs")
    if isinstance(recent, list):
        values.extend(recent)
    return coerce_existing_workdirs(values, max_count=max_count)


def serialize_desktop_restart(status: str, error: str) -> dict[str, str]:
    return {
        "status": status,
        "error": error,
    }


class ToolkitWebService:
    def __init__(
        self,
        *,
        codex_home: Path | None = None,
        accounts_root: Path | None = None,
        initial_workdir: Path | None = None,
        max_remembered_workdirs: int = MAX_REMEMBERED_WORKDIRS,
    ) -> None:
        self.codex_home = (codex_home or DEFAULT_CODEX_HOME).expanduser().resolve()
        self.accounts_root = accounts_root.expanduser().resolve() if accounts_root is not None else None
        self.initial_workdir = initial_workdir.expanduser().resolve() if initial_workdir is not None else None
        self.max_remembered_workdirs = max_remembered_workdirs

    def _load_workspace_state(self) -> dict[str, object]:
        return load_workspace_state(
            normalize_workdir=normalize_workdir,
            max_remembered_workdirs=self.max_remembered_workdirs,
        )

    def _resolve_workdir(self, requested_workdir: str | None) -> Path:
        candidate = (requested_workdir or "").strip()
        if candidate:
            resolved = Path(candidate).expanduser().resolve()
        else:
            state = self._load_workspace_state()
            remembered = str(state.get("last_workdir") or "").strip()
            if remembered:
                resolved = Path(remembered).expanduser().resolve()
            elif self.initial_workdir is not None:
                resolved = self.initial_workdir
            else:
                resolved = Path.cwd().resolve()

        if not resolved.exists() or not resolved.is_dir():
            raise WebUiError(f"Workdir does not exist or is not a directory: {resolved}")
        return resolved

    def _remember_workdir(self, workdir: Path) -> list[str]:
        normalized = normalize_workdir(workdir)
        state = self._load_workspace_state()
        recent_workdirs = merge_workdir_history(
            normalized,
            state,
            initial_workdir=self.initial_workdir,
            max_count=self.max_remembered_workdirs,
        )
        save_workspace_state(
            last_workdir=normalized,
            recent_workdirs=recent_workdirs,
            max_remembered_workdirs=self.max_remembered_workdirs,
        )
        return recent_workdirs

    @staticmethod
    def _serialize_account(account: Any, *, active_account: str | None) -> dict[str, Any]:
        provider = getattr(account, "provider", None)
        return {
            "name": account.name,
            "description": account.description,
            "provider": provider,
            "directory": str(account.directory),
            "isActive": account.name == active_account,
        }

    @staticmethod
    def _serialize_session(session: Any) -> dict[str, Any]:
        return {
            "threadId": session.thread_id,
            "title": session.title,
            "preview": session.first_user_message,
            "cwd": session.cwd,
            "createdAt": session.created_at,
            "updatedAt": session.updated_at,
            "updatedLabel": session.updated_label,
            "rolloutPath": str(session.rollout_path),
            "forkedFromId": session.forked_from_id,
        }

    @staticmethod
    def _serialize_turn(turn: Any) -> dict[str, Any]:
        return {
            "turnId": turn.turn_id,
            "index": turn.index,
            "text": turn.user_text,
            "preview": turn.preview,
            "source": turn.source,
        }

    @staticmethod
    def _serialize_transfer_conversation(conversation: Any) -> dict[str, Any]:
        return {
            "threadId": conversation.thread_id,
            "title": conversation.title,
            "preview": conversation.preview,
            "cwd": conversation.cwd,
            "updatedAt": conversation.updated_at,
            "updatedLabel": conversation.updated_label,
            "rolloutPath": str(conversation.rollout_path),
            "modelProvider": conversation.model_provider,
            "assignedAccount": conversation.assigned_account,
            "assignmentSource": conversation.assignment_source,
        }

    def _load_accounts_payload(self) -> dict[str, Any]:
        payload = {
            "accountsRoot": "",
            "accounts": [],
            "activeAccount": None,
            "accountsError": "",
        }
        try:
            resolved_root = resolve_accounts_root(self.accounts_root)
            accounts = list_account_sources(resolved_root)
        except AccountSwitchError as exc:
            payload["accountsError"] = str(exc)
            return payload

        active_account = find_matching_target_account(accounts, self.codex_home)
        provider_by_name = {
            profile.name: profile.provider for profile in load_account_profiles(resolved_root)
        }
        payload["accountsRoot"] = str(resolved_root)
        payload["accounts"] = [
            {
                **self._serialize_account(account, active_account=active_account),
                "provider": provider_by_name.get(account.name),
            }
            for account in accounts
        ]
        payload["activeAccount"] = active_account
        if not accounts:
            payload["accountsError"] = (
                f"No switchable accounts were found under {resolved_root}. "
                "Expected subdirectories containing config.toml and auth.json."
            )
        return payload

    def get_bootstrap(self, requested_workdir: str | None = None) -> dict[str, Any]:
        workdir = self._resolve_workdir(requested_workdir)
        recent_workdirs = self._remember_workdir(workdir)
        accounts_payload = self._load_accounts_payload()
        return {
            "projectName": "Codex Session Toolkit",
            "projectSubtitle": "Dracula-themed local control plane for sessions, account switching, forking, and transfer.",
            "phase": "Dracula Web UI",
            "codexHome": str(self.codex_home),
            "codexHomeDescription": describe_target_codex_home(self.codex_home),
            "workdir": str(workdir),
            "recentWorkdirs": recent_workdirs,
            **accounts_payload,
        }

    def list_sessions(self, requested_workdir: str | None = None) -> dict[str, Any]:
        workdir = self._resolve_workdir(requested_workdir)
        recent_workdirs = self._remember_workdir(workdir)
        try:
            sessions = find_sessions(self.codex_home, str(workdir))
        except ForkToolError as exc:
            raise WebUiError(str(exc)) from exc
        return {
            "workdir": str(workdir),
            "recentWorkdirs": recent_workdirs,
            "count": len(sessions),
            "sessions": [self._serialize_session(session) for session in sessions],
        }

    def _find_session(self, workdir: Path, thread_id: str) -> Any:
        try:
            sessions = find_sessions(self.codex_home, str(workdir))
        except ForkToolError as exc:
            raise WebUiError(str(exc)) from exc
        for session in sessions:
            if session.thread_id == thread_id:
                return session
        raise WebUiError(f"Conversation was not found in the current workdir: {thread_id}")

    def get_session_turns(
        self,
        *,
        requested_workdir: str | None,
        thread_id: str,
    ) -> dict[str, Any]:
        workdir = self._resolve_workdir(requested_workdir)
        session = self._find_session(workdir, thread_id)
        client = CodexAppServerClient()
        try:
            turns, app_server_error = load_user_turns_for_session(session, client)
        except ForkToolError as exc:
            raise WebUiError(str(exc)) from exc
        finally:
            client.stop()
        return {
            "workdir": str(workdir),
            "session": self._serialize_session(session),
            "count": len(turns),
            "appServerError": app_server_error or "",
            "turns": [self._serialize_turn(turn) for turn in turns],
        }

    def fork_session(
        self,
        *,
        requested_workdir: str | None,
        thread_id: str,
        turn_id: str,
        restart_codex: bool,
    ) -> dict[str, Any]:
        workdir = self._resolve_workdir(requested_workdir)
        session = self._find_session(workdir, thread_id)
        client = CodexAppServerClient()
        try:
            turns, _ = load_user_turns_for_session(session, client)
            target_turn = next((turn for turn in turns if turn.turn_id == turn_id), None)
            if target_turn is None:
                raise WebUiError(f"Target turn was not found: {turn_id}")
            result = perform_fork(session, target_turn, client)
        except ForkToolError as exc:
            raise WebUiError(str(exc)) from exc
        finally:
            client.stop()

        restart_status = "skipped"
        restart_error = ""
        if restart_codex:
            restart_status, restart_error = restart_codex_desktop_app()

        result["desktop_app_restart_status"] = restart_status
        result["desktop_app_restart_error"] = restart_error
        return {
            "workdir": str(workdir),
            "session": self._serialize_session(session),
            "result": result,
            "desktopApp": serialize_desktop_restart(restart_status, restart_error),
        }

    def get_transfer_view(self, requested_workdir: str | None = None) -> dict[str, Any]:
        workdir = self._resolve_workdir(requested_workdir)
        recent_workdirs = self._remember_workdir(workdir)
        try:
            profiles, conversations = load_transfer_view(self.codex_home, workdir, self.accounts_root)
            active_account = get_active_account_name(self.codex_home, self.accounts_root)
        except ConversationTransferError as exc:
            raise WebUiError(str(exc)) from exc

        counts = build_account_counts(profiles, conversations)
        groups = [
            {
                "name": profile.name,
                "count": counts.get(profile.name, 0),
                "provider": profile.provider,
                "isActive": profile.name == active_account,
            }
            for profile in profiles
        ]
        groups.append(
            {
                "name": UNASSIGNED_ACCOUNT,
                "count": counts.get(UNASSIGNED_ACCOUNT, 0),
                "provider": None,
                "isActive": False,
            }
        )
        return {
            "workdir": str(workdir),
            "recentWorkdirs": recent_workdirs,
            "activeAccount": active_account,
            "profiles": [self._serialize_account(profile, active_account=active_account) for profile in profiles],
            "groups": groups,
            "count": len(conversations),
            "conversations": [
                self._serialize_transfer_conversation(conversation) for conversation in conversations
            ],
        }

    @staticmethod
    def _resolve_profile(profiles: list[AccountProfile], account_name: str) -> AccountProfile:
        requested = account_name.strip()
        if not requested:
            raise WebUiError("Target account name cannot be empty.")
        for profile in profiles:
            if profile.name == requested:
                return profile
        lowered = [profile for profile in profiles if profile.name.lower() == requested.lower()]
        if len(lowered) == 1:
            return lowered[0]
        available = ", ".join(profile.name for profile in profiles) or "(none)"
        raise WebUiError(f"Account '{account_name}' was not found. Available: {available}")

    def assign_transfer_conversations(
        self,
        *,
        requested_workdir: str | None,
        account_name: str,
        thread_ids: list[str],
    ) -> dict[str, Any]:
        workdir = self._resolve_workdir(requested_workdir)
        try:
            profiles, conversations = load_transfer_view(self.codex_home, workdir, self.accounts_root)
            profile = self._resolve_profile(profiles, account_name)
            selected = resolve_transfer_conversations(conversations, thread_ids)
            assign_threads_to_account(
                [conversation.thread_id for conversation in selected],
                profile.name,
                source="manual",
            )
        except ConversationTransferError as exc:
            raise WebUiError(str(exc)) from exc

        return {
            "assignedCount": len(selected),
            "targetAccount": profile.name,
            "threadIds": [conversation.thread_id for conversation in selected],
            "transferView": self.get_transfer_view(str(workdir)),
        }

    def copy_transfer_conversations(
        self,
        *,
        requested_workdir: str | None,
        target_account: str,
        thread_ids: list[str],
        restart_codex: bool,
    ) -> dict[str, Any]:
        workdir = self._resolve_workdir(requested_workdir)
        try:
            profiles, conversations = load_transfer_view(self.codex_home, workdir, self.accounts_root)
            profile = self._resolve_profile(profiles, target_account)
            selected = resolve_transfer_conversations(conversations, thread_ids)
            source_group = require_single_source_account(selected)
            if source_group == profile.name:
                raise WebUiError("Source and target account cannot be the same.")
            copy_result = copy_conversations_to_account(self.codex_home, selected, profile)
        except ConversationTransferError as exc:
            raise WebUiError(str(exc)) from exc

        active_account = get_active_account_name(self.codex_home, self.accounts_root)
        restart_status = "skipped"
        restart_error = ""
        if restart_codex and active_account == profile.name:
            restart_status, restart_error = restart_codex_desktop_app()

        return {
            "sourceAccount": source_group,
            "targetAccount": copy_result.target_account,
            "importedCount": copy_result.imported_count,
            "importedThreadIds": copy_result.imported_thread_ids,
            "desktopApp": serialize_desktop_restart(restart_status, restart_error),
            "transferView": self.get_transfer_view(str(workdir)),
        }

    def switch_account(self, account_name: str, *, restart_codex: bool) -> dict[str, Any]:
        try:
            resolved_root = resolve_accounts_root(self.accounts_root)
            accounts = list_account_sources(resolved_root)
            selected = resolve_account_source(accounts, account_name)
            result = switch_account(selected, self.codex_home)
        except AccountSwitchError as exc:
            raise WebUiError(str(exc)) from exc

        restart_status = "skipped"
        restart_error = ""
        if restart_codex:
            restart_status, restart_error = restart_codex_desktop_app()

        return {
            "accountName": result.account_name,
            "accountsRoot": str(resolved_root),
            "sourceDir": str(result.source_dir),
            "targetDir": str(result.target_dir),
            "backupDir": str(result.backup_dir) if result.backup_dir is not None else "",
            "copiedFiles": [str(path) for path in result.copied_files],
            "desktopApp": serialize_desktop_restart(restart_status, restart_error),
        }
