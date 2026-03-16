#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


REQUIRED_ACCOUNT_FILES = ("config.toml", "auth.json")
PROJECT_ROOT = Path(__file__).resolve().parent.parent


class AccountSwitchError(RuntimeError):
    pass


@dataclass(slots=True)
class AccountSourceSummary:
    name: str
    directory: Path
    config_path: Path
    auth_path: Path
    description: str


@dataclass(slots=True)
class AccountSwitchResult:
    account_name: str
    source_dir: Path
    target_dir: Path
    backup_dir: Path | None
    copied_files: list[Path]


def _candidate_account_roots() -> list[Path]:
    return [
        PROJECT_ROOT / "accounts",
        PROJECT_ROOT.parent / "codex-user-change",
    ]


def _contains_switchable_accounts(root: Path) -> bool:
    for child in root.iterdir():
        if not child.is_dir():
            continue
        if all((child / filename).exists() for filename in REQUIRED_ACCOUNT_FILES):
            return True
    return False


def detect_default_accounts_root() -> Path | None:
    existing_roots: list[Path] = []
    for candidate in _candidate_account_roots():
        if candidate.exists() and candidate.is_dir():
            resolved = candidate.resolve()
            existing_roots.append(resolved)
            if _contains_switchable_accounts(resolved):
                return resolved
    if existing_roots:
        return existing_roots[0]
    return None


def resolve_accounts_root(accounts_root: Path | None) -> Path:
    if accounts_root is None:
        detected = detect_default_accounts_root()
        if detected is None:
            raise AccountSwitchError(
                "No account source directory was found. "
                "Create .\\accounts\\<name>\\config.toml and auth.json, "
                "or pass --accounts-root."
            )
        return detected

    resolved = accounts_root.expanduser().resolve()
    if not resolved.exists():
        raise AccountSwitchError(f"Account source directory does not exist: {resolved}")
    if not resolved.is_dir():
        raise AccountSwitchError(f"Account source path is not a directory: {resolved}")
    return resolved


def _read_text_if_exists(path: Path) -> str:
    if not path.exists():
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _describe_payloads(config_text: str, auth_text: str) -> str:
    tags: list[str] = []

    auth_mode = ""
    try:
        auth_data = json.loads(auth_text) if auth_text.strip() else {}
    except json.JSONDecodeError:
        auth_data = {}
        tags.append("auth-invalid")
    if isinstance(auth_data, dict):
        auth_mode = str(auth_data.get("auth_mode") or "").strip()
        api_key = auth_data.get("OPENAI_API_KEY")
        if auth_mode:
            tags.append(auth_mode)
        elif isinstance(api_key, str) and api_key.strip():
            tags.append("api-key")

    provider_match = re.search(r'^\s*model_provider\s*=\s*"([^"]+)"', config_text, re.MULTILINE)
    provider = provider_match.group(1).strip() if provider_match else ""
    if provider:
        tags.append(provider)

    if "disable_response_storage = true" in config_text:
        tags.append("stateless")

    deduped: list[str] = []
    seen: set[str] = set()
    for tag in tags:
        normalized = tag.strip()
        if normalized and normalized not in seen:
            deduped.append(normalized)
            seen.add(normalized)

    return " | ".join(deduped) if deduped else "config.toml + auth.json"


def describe_account_directory(directory: Path) -> str:
    config_path = directory / REQUIRED_ACCOUNT_FILES[0]
    auth_path = directory / REQUIRED_ACCOUNT_FILES[1]
    if not config_path.exists() or not auth_path.exists():
        return "missing config.toml or auth.json"
    return _describe_payloads(_read_text_if_exists(config_path), _read_text_if_exists(auth_path))


def describe_target_codex_home(target_codex_home: Path) -> str:
    target_dir = target_codex_home.expanduser().resolve()
    return describe_account_directory(target_dir)


def list_account_sources(accounts_root: Path | None) -> list[AccountSourceSummary]:
    root = resolve_accounts_root(accounts_root)
    accounts: list[AccountSourceSummary] = []

    for child in sorted(root.iterdir(), key=lambda item: item.name.lower()):
        if not child.is_dir():
            continue
        config_path = child / REQUIRED_ACCOUNT_FILES[0]
        auth_path = child / REQUIRED_ACCOUNT_FILES[1]
        if not config_path.exists() or not auth_path.exists():
            continue
        accounts.append(
            AccountSourceSummary(
                name=child.name,
                directory=child.resolve(),
                config_path=config_path.resolve(),
                auth_path=auth_path.resolve(),
                description=describe_account_directory(child),
            )
        )

    return accounts


def resolve_account_source(accounts: list[AccountSourceSummary], account_name: str) -> AccountSourceSummary:
    requested = account_name.strip()
    if not requested:
        raise AccountSwitchError("Account name cannot be empty.")

    for account in accounts:
        if account.name == requested:
            return account

    lowered = [account for account in accounts if account.name.lower() == requested.lower()]
    if len(lowered) == 1:
        return lowered[0]

    available = ", ".join(account.name for account in accounts) or "(none)"
    raise AccountSwitchError(f"Account '{account_name}' was not found. Available: {available}")


def _files_match(first: Path, second: Path) -> bool:
    if not first.exists() or not second.exists():
        return False
    try:
        return first.read_bytes() == second.read_bytes()
    except OSError:
        return False


def find_matching_target_account(
    accounts: list[AccountSourceSummary],
    target_codex_home: Path,
) -> str | None:
    target_dir = target_codex_home.expanduser().resolve()
    target_config = target_dir / REQUIRED_ACCOUNT_FILES[0]
    target_auth = target_dir / REQUIRED_ACCOUNT_FILES[1]

    for account in accounts:
        if _files_match(account.config_path, target_config) and _files_match(account.auth_path, target_auth):
            return account.name
    return None


def _safe_backup_suffix(account_name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", account_name.strip())
    return cleaned.strip("-") or "account"


def switch_account(
    source: AccountSourceSummary,
    target_codex_home: Path,
) -> AccountSwitchResult:
    target_dir = target_codex_home.expanduser().resolve()
    target_dir.mkdir(parents=True, exist_ok=True)

    missing_files = [
        path.name for path in (source.config_path, source.auth_path) if not path.exists()
    ]
    if missing_files:
        raise AccountSwitchError(
            f"Source account '{source.name}' is missing required file(s): {', '.join(missing_files)}"
        )

    backup_dir: Path | None = None
    copied_files: list[Path] = []

    for filename in REQUIRED_ACCOUNT_FILES:
        source_file = source.directory / filename
        target_file = target_dir / filename

        if target_file.exists():
            if backup_dir is None:
                timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
                backup_dir = target_dir / "account-switch-backups" / f"{timestamp}-{_safe_backup_suffix(source.name)}"
                backup_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target_file, backup_dir / filename)

        shutil.copy2(source_file, target_file)
        copied_files.append(target_file)

    return AccountSwitchResult(
        account_name=source.name,
        source_dir=source.directory,
        target_dir=target_dir,
        backup_dir=backup_dir,
        copied_files=copied_files,
    )
