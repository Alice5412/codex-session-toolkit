#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

from conversation_transfer import (
    UNASSIGNED_ACCOUNT,
    AccountProfile,
    ConversationTransferError,
    assign_threads_to_account,
    build_account_counts,
    copy_conversations_to_account,
    get_active_account_name,
    load_transfer_view,
    require_single_source_account,
    resolve_transfer_conversations,
)
from desktop_app import restart_codex_desktop_app


class TransferCliError(RuntimeError):
    pass


def resolve_transfer_profile(profiles: list[AccountProfile], account_name: str) -> AccountProfile:
    requested = account_name.strip()
    if not requested:
        raise TransferCliError("Target account name cannot be empty.")

    for profile in profiles:
        if profile.name == requested:
            return profile

    lowered = [profile for profile in profiles if profile.name.lower() == requested.lower()]
    if len(lowered) == 1:
        return lowered[0]

    available = ", ".join(profile.name for profile in profiles) or "(none)"
    raise TransferCliError(f"Account '{account_name}' was not found. Available: {available}")


def list_transfer_view_cli(codex_home: Path, workdir: Path, accounts_root: Path | None) -> int:
    try:
        profiles, conversations = load_transfer_view(codex_home, workdir, accounts_root)
        active_account = get_active_account_name(codex_home, accounts_root)
    except ConversationTransferError as exc:
        raise TransferCliError(str(exc)) from exc

    counts = build_account_counts(profiles, conversations)
    profile_by_name = {profile.name: profile for profile in profiles}

    print(f"Workdir: {workdir}")
    print(f"Codex home: {codex_home}")
    print(f"Active account: {active_account or 'no exact source match'}")
    print()

    ordered_groups = [profile.name for profile in profiles] + [UNASSIGNED_ACCOUNT]
    for group in ordered_groups:
        profile = profile_by_name.get(group)
        suffix = ""
        if profile is not None:
            suffix = f" | provider={profile.provider or '(unknown)'}"
        if group == active_account:
            suffix += " | active"
        print(f"[{group}] {counts.get(group, 0)} conversation(s){suffix}")
        group_items = [conversation for conversation in conversations if conversation.assigned_account == group]
        if not group_items:
            print("  (none)")
            print()
            continue
        for conversation in group_items:
            print(
                f"  {conversation.thread_id}	{conversation.updated_label}	{conversation.model_provider}	{conversation.assignment_source}	{conversation.title}"
            )
        print()
    return 0


def assign_transfer_conversations_cli(
    codex_home: Path,
    workdir: Path,
    accounts_root: Path | None,
    target_account: str,
    sources: list[str],
) -> int:
    try:
        profiles, conversations = load_transfer_view(codex_home, workdir, accounts_root)
        profile = resolve_transfer_profile(profiles, target_account)
        selected = resolve_transfer_conversations(conversations, sources)
    except ConversationTransferError as exc:
        raise TransferCliError(str(exc)) from exc

    assign_threads_to_account([conversation.thread_id for conversation in selected], profile.name, source="manual")

    print(f"Assigned {len(selected)} conversation(s) to {profile.name}.")
    for conversation in selected:
        print(f"  - {conversation.thread_id}	{conversation.title}")
    return 0


def copy_transfer_conversations_cli(
    codex_home: Path,
    workdir: Path,
    accounts_root: Path | None,
    target_account: str,
    sources: list[str],
    *,
    restart_codex: bool,
) -> int:
    try:
        profiles, conversations = load_transfer_view(codex_home, workdir, accounts_root)
        profile = resolve_transfer_profile(profiles, target_account)
        selected = resolve_transfer_conversations(conversations, sources)
        source_group = require_single_source_account(selected)
        if source_group == profile.name:
            raise ConversationTransferError("Source and target account cannot be the same.")
        result = copy_conversations_to_account(codex_home, selected, profile)
    except ConversationTransferError as exc:
        raise TransferCliError(str(exc)) from exc

    active_account = get_active_account_name(codex_home, accounts_root)
    restart_status = "skipped"
    restart_error = ""
    if restart_codex and active_account == profile.name:
        restart_status, restart_error = restart_codex_desktop_app()

    print(f"Copied {result.imported_count} conversation(s) from {source_group} to {result.target_account}.")
    for thread_id in result.imported_thread_ids:
        print(f"  - {thread_id}")

    if restart_status == "restarted":
        print("Desktop app:      Codex App was restarted.")
    elif restart_status == "not_running":
        print("Desktop app:      Codex App was not running.")
    elif restart_status == "failed":
        print("Desktop app:      Automatic restart failed.")
        if restart_error:
            print(f"Restart warning:  {restart_error}")
    elif not restart_codex:
        print("Desktop app:      restart skipped by flag.")
    else:
        print("Desktop app:      restart not required for the current account.")

    return 0
