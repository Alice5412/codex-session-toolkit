#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from account_switcher import (
    find_matching_target_account,
    list_account_sources,
    resolve_accounts_root,
)
from app_state import load_transfer_state, save_transfer_state
from session_tool import (
    SessionToolError,
    package_sessions_archive,
    parse_rollout,
    unpack_sessions_archive,
)


SESSION_GLOB = "rollout-*.jsonl"
UNASSIGNED_ACCOUNT = "Unassigned"
PROVIDER_PATTERN = re.compile(r'^\s*model_provider\s*=\s*"([^"]+)"', re.MULTILINE)


class ConversationTransferError(RuntimeError):
    pass


@dataclass(slots=True)
class AccountProfile:
    name: str
    directory: Path
    description: str
    provider: str | None


@dataclass(slots=True)
class TransferConversation:
    thread_id: str
    rollout_path: Path
    cwd: str
    updated_at: float
    title: str
    preview: str
    model_provider: str
    assigned_account: str
    assignment_source: str

    @property
    def updated_label(self) -> str:
        return datetime.fromtimestamp(self.updated_at).strftime("%Y-%m-%d %H:%M:%S")


@dataclass(slots=True)
class ConversationCopyResult:
    target_account: str
    imported_thread_ids: list[str]
    imported_count: int


def normalize_workdir(path_value: str | Path) -> str:
    return str(Path(path_value).expanduser().resolve())


def strip_windows_extended_path_prefix(path_value: str) -> str:
    if path_value.startswith('\\?\\UNC\\'):
        return '\\' + path_value[7:]
    if path_value.startswith('\\?\\'):
        return path_value[4:]
    return path_value


def _read_text_if_exists(path: Path) -> str:
    if not path.exists():
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def infer_provider_from_text(config_text: str, auth_text: str) -> str | None:
    provider_match = PROVIDER_PATTERN.search(config_text)
    if provider_match:
        provider = provider_match.group(1).strip()
        if provider:
            return provider

    try:
        auth_data = json.loads(auth_text) if auth_text.strip() else {}
    except json.JSONDecodeError:
        auth_data = {}

    if isinstance(auth_data, dict):
        auth_mode = str(auth_data.get("auth_mode") or "").strip().lower()
        if auth_mode == "chatgpt":
            return "openai"
    return None


def load_account_profiles(accounts_root: Path | None) -> list[AccountProfile]:
    try:
        resolved_root = resolve_accounts_root(accounts_root)
        sources = list_account_sources(resolved_root)
    except Exception as exc:  # noqa: BLE001
        raise ConversationTransferError(str(exc)) from exc

    profiles: list[AccountProfile] = []
    for source in sources:
        config_text = _read_text_if_exists(source.config_path)
        auth_text = _read_text_if_exists(source.auth_path)
        profiles.append(
            AccountProfile(
                name=source.name,
                directory=source.directory,
                description=source.description,
                provider=infer_provider_from_text(config_text, auth_text),
            )
        )
    return profiles


def get_active_account_name(codex_home: Path, accounts_root: Path | None) -> str | None:
    try:
        resolved_root = resolve_accounts_root(accounts_root)
        sources = list_account_sources(resolved_root)
    except Exception:  # noqa: BLE001
        return None
    return find_matching_target_account(sources, codex_home)


def assign_threads_to_account(
    thread_ids: list[str],
    account_name: str,
    *,
    state_path: Path | None = None,
    source: str = "manual",
    copied_from_thread_ids: dict[str, str] | None = None,
) -> None:
    state = load_transfer_state(state_path)
    assignments = state.setdefault("thread_assignments", {})
    if not isinstance(assignments, dict):
        assignments = {}
        state["thread_assignments"] = assignments

    for thread_id in thread_ids:
        record: dict[str, object] = {
            "account_name": account_name,
            "source": source,
        }
        if copied_from_thread_ids and thread_id in copied_from_thread_ids:
            record["copied_from_thread_id"] = copied_from_thread_ids[thread_id]
        assignments[thread_id] = record
    save_transfer_state(state, state_path)


def scan_local_workdir_conversations(
    codex_home: Path,
    workdir: str | Path,
    *,
    default_provider: str = "openai",
) -> list[dict[str, object]]:
    sessions_root = codex_home / "sessions"
    if not sessions_root.exists():
        raise ConversationTransferError(f"Codex sessions directory not found: {sessions_root}")

    normalized_workdir = normalize_workdir(workdir)
    latest_by_thread: dict[str, dict[str, object]] = {}

    for rollout_path in sessions_root.rglob(SESSION_GLOB):
        try:
            metadata = parse_rollout(rollout_path, codex_home, default_provider)
        except (OSError, SessionToolError):
            continue
        if metadata.cwd == Path():
            continue
        try:
            session_cwd = normalize_workdir(metadata.cwd)
        except OSError:
            continue
        if session_cwd != normalized_workdir:
            continue
        record = {
            "thread_id": metadata.thread_id,
            "rollout_path": metadata.rollout_path,
            "cwd": session_cwd,
            "updated_at": metadata.updated_at.timestamp(),
            "title": metadata.title or metadata.thread_id,
            "preview": metadata.first_user_message or metadata.title or metadata.thread_id,
            "model_provider": metadata.model_provider,
        }
        previous = latest_by_thread.get(metadata.thread_id)
        if previous is None or float(record["updated_at"]) > float(previous["updated_at"]):
            latest_by_thread[metadata.thread_id] = record

    return sorted(
        latest_by_thread.values(),
        key=lambda item: float(item["updated_at"]),
        reverse=True,
    )


def scan_current_account_conversations(workdir: str | Path) -> list[dict[str, object]]:
    from fork_cli import CodexAppServerClient

    normalized_workdir = normalize_workdir(workdir)
    client = CodexAppServerClient()
    try:
        response = client.request("thread/list", {})
    finally:
        client.stop()

    result = response.get("result")
    if not isinstance(result, dict):
        raise ConversationTransferError("thread/list returned an unexpected payload")
    data = result.get("data")
    if not isinstance(data, list):
        raise ConversationTransferError("thread/list returned no data array")

    rows: list[dict[str, object]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        raw_cwd = strip_windows_extended_path_prefix(str(item.get("cwd") or "").strip())
        if not raw_cwd:
            continue
        try:
            item_cwd = normalize_workdir(raw_cwd)
        except OSError:
            continue
        if item_cwd != normalized_workdir:
            continue
        raw_path = strip_windows_extended_path_prefix(str(item.get("path") or "").strip())
        rollout_path = Path(raw_path) if raw_path else Path()
        if raw_path:
            try:
                rollout_path = rollout_path.expanduser().resolve()
            except OSError:
                rollout_path = Path(raw_path)
        rows.append(
            {
                "thread_id": str(item.get("id") or ""),
                "rollout_path": rollout_path,
                "cwd": item_cwd,
                "updated_at": float(item.get("updatedAt") or 0),
                "title": str(item.get("name") or item.get("preview") or item.get("id") or ""),
                "preview": str(item.get("preview") or ""),
                "model_provider": str(item.get("modelProvider") or ""),
            }
        )
    return rows


def merge_conversation_records(
    local_conversations: list[dict[str, object]],
    current_account_conversations: list[dict[str, object]],
) -> list[dict[str, object]]:
    merged = {str(item["thread_id"]): dict(item) for item in local_conversations}
    for item in current_account_conversations:
        thread_id = str(item["thread_id"])
        existing = merged.get(thread_id)
        if existing is None:
            merged[thread_id] = dict(item)
            continue
        if float(item.get("updated_at") or 0) >= float(existing.get("updated_at") or 0):
            updated = dict(existing)
            updated.update(item)
            merged[thread_id] = updated
    return sorted(
        merged.values(),
        key=lambda row: float(row.get("updated_at") or 0),
        reverse=True,
    )


def classify_conversations(
    raw_conversations: list[dict[str, object]],
    profiles: list[AccountProfile],
    *,
    state_path: Path | None = None,
    current_account_name: str | None = None,
    current_visible_thread_ids: set[str] | None = None,
) -> list[TransferConversation]:
    state = load_transfer_state(state_path)
    assignments = state.get("thread_assignments", {})
    if not isinstance(assignments, dict):
        assignments = {}

    provider_to_accounts: dict[str, list[str]] = {}
    profile_names = {profile.name for profile in profiles}
    for profile in profiles:
        if not profile.provider:
            continue
        provider_to_accounts.setdefault(profile.provider, []).append(profile.name)

    classified: list[TransferConversation] = []
    for item in raw_conversations:
        thread_id = str(item["thread_id"])
        stored = assignments.get(thread_id)
        assigned_account = UNASSIGNED_ACCOUNT
        assignment_source = "auto"

        if isinstance(stored, dict):
            stored_account = str(stored.get("account_name") or "").strip()
            stored_source = str(stored.get("source") or "manual").strip() or "manual"
            if stored_account and stored_account in profile_names:
                assigned_account = stored_account
                assignment_source = stored_source
            else:
                stored = None

        if stored is None:
            if current_account_name and current_visible_thread_ids and thread_id in current_visible_thread_ids:
                assigned_account = current_account_name
                assignment_source = "active"
            else:
                candidates = provider_to_accounts.get(str(item["model_provider"]), [])
                if len(candidates) == 1:
                    assigned_account = candidates[0]

        classified.append(
            TransferConversation(
                thread_id=thread_id,
                rollout_path=Path(str(item["rollout_path"])),
                cwd=str(item["cwd"]),
                updated_at=float(item["updated_at"]),
                title=str(item["title"]),
                preview=str(item["preview"]),
                model_provider=str(item["model_provider"]),
                assigned_account=assigned_account,
                assignment_source=assignment_source,
            )
        )
    return classified


def load_transfer_view(
    codex_home: Path,
    workdir: str | Path,
    accounts_root: Path | None,
    *,
    default_provider: str = "openai",
    state_path: Path | None = None,
) -> tuple[list[AccountProfile], list[TransferConversation]]:
    profiles = load_account_profiles(accounts_root)
    raw_conversations = scan_local_workdir_conversations(
        codex_home,
        workdir,
        default_provider=default_provider,
    )
    active_account_name = get_active_account_name(codex_home, accounts_root)
    current_account_conversations: list[dict[str, object]] = []
    current_visible_thread_ids: set[str] = set()
    try:
        current_account_conversations = scan_current_account_conversations(workdir)
    except Exception:  # noqa: BLE001
        current_account_conversations = []
    current_visible_thread_ids = {
        str(item["thread_id"]) for item in current_account_conversations if item.get("thread_id")
    }
    merged_conversations = merge_conversation_records(
        raw_conversations,
        current_account_conversations,
    )
    conversations = classify_conversations(
        merged_conversations,
        profiles,
        state_path=state_path,
        current_account_name=active_account_name,
        current_visible_thread_ids=current_visible_thread_ids,
    )
    return profiles, conversations


def build_account_counts(
    profiles: list[AccountProfile],
    conversations: list[TransferConversation],
) -> dict[str, int]:
    counts = {UNASSIGNED_ACCOUNT: 0}
    for profile in profiles:
        counts[profile.name] = 0
    for conversation in conversations:
        counts.setdefault(conversation.assigned_account, 0)
        counts[conversation.assigned_account] += 1
    return counts


def resolve_transfer_conversations(
    conversations: list[TransferConversation],
    identifiers: list[str],
) -> list[TransferConversation]:
    if not identifiers:
        raise ConversationTransferError('Provide at least one transfer source.')

    by_thread_id = {conversation.thread_id: conversation for conversation in conversations}
    by_rollout_path = {
        str(conversation.rollout_path).lower(): conversation for conversation in conversations
    }
    selected: list[TransferConversation] = []
    seen_ids: set[str] = set()

    for identifier in identifiers:
        key = identifier.strip()
        if not key:
            continue
        conversation = by_thread_id.get(key)
        if conversation is None:
            try:
                path_key = str(Path(key).expanduser().resolve()).lower()
            except OSError:
                path_key = key.lower()
            conversation = by_rollout_path.get(path_key)
        if conversation is None:
            raise ConversationTransferError(f'Transfer source was not found: {identifier}')
        if conversation.thread_id in seen_ids:
            continue
        selected.append(conversation)
        seen_ids.add(conversation.thread_id)

    if not selected:
        raise ConversationTransferError('No conversations matched the provided transfer sources.')
    return selected


def require_single_source_account(conversations: list[TransferConversation]) -> str:
    source_accounts = {conversation.assigned_account for conversation in conversations}
    if len(source_accounts) != 1:
        raise ConversationTransferError(
            'Selected conversations must belong to the same source account or group.'
        )
    return next(iter(source_accounts))


def copy_conversations_to_account(
    codex_home: Path,
    conversations: list[TransferConversation],
    target_profile: AccountProfile,
    *,
    default_provider: str = "openai",
    state_path: Path | None = None,
) -> ConversationCopyResult:
    if not conversations:
        raise ConversationTransferError("Select at least one conversation to copy.")
    if not target_profile.provider:
        raise ConversationTransferError(
            f"Cannot determine the target provider for account '{target_profile.name}'."
        )

    archive_file = tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False)
    archive_path = Path(archive_file.name)
    archive_file.close()

    try:
        package_sessions_archive(
            codex_home,
            [str(conversation.rollout_path) for conversation in conversations],
            archive_path,
            default_provider=default_provider,
        )
        imported = unpack_sessions_archive(
            codex_home,
            archive_path,
            default_provider=default_provider,
            target_provider=target_profile.provider,
            preserve_ids=False,
        )
    except SessionToolError as exc:
        raise ConversationTransferError(str(exc)) from exc
    finally:
        if archive_path.exists():
            archive_path.unlink()

    copied_from_thread_ids = {
        str(item["thread_id"]): str(item["original_thread_id"]) for item in imported
    }
    assign_threads_to_account(
        list(copied_from_thread_ids.keys()),
        target_profile.name,
        state_path=state_path,
        source="copied",
        copied_from_thread_ids=copied_from_thread_ids,
    )
    return ConversationCopyResult(
        target_account=target_profile.name,
        imported_thread_ids=list(copied_from_thread_ids.keys()),
        imported_count=len(copied_from_thread_ids),
    )
