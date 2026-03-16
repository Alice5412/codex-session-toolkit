#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path
import sqlite3
import sys
import tarfile
import tempfile
import uuid


ID_KEYS = {"id", "thread_id", "forked_from_id"}
SESSION_DIRNAME = "sessions"
ARCHIVED_DIRNAME = "archived_sessions"


class SessionToolError(RuntimeError):
    pass


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


def parse_iso_utc(value: str) -> dt.datetime:
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(
        dt.timezone.utc
    ).replace(microsecond=0)


def format_session_timestamp(value: dt.datetime) -> str:
    return value.astimezone(dt.timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def epoch_seconds(value: dt.datetime | None) -> int | None:
    if value is None:
        return None
    return int(value.timestamp())


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def rollout_root(codex_home: Path, archived: bool) -> Path:
    return codex_home / (ARCHIVED_DIRNAME if archived else SESSION_DIRNAME)


def iter_rollout_paths(codex_home: Path) -> list[Path]:
    paths: list[Path] = []
    for dirname in (SESSION_DIRNAME, ARCHIVED_DIRNAME):
        root = codex_home / dirname
        if not root.exists():
            continue
        paths.extend(sorted(root.rglob("rollout-*.jsonl")))
    return paths


def is_archived_path(codex_home: Path, rollout_path: Path) -> bool:
    try:
        rollout_path.relative_to(codex_home / ARCHIVED_DIRNAME)
        return True
    except ValueError:
        return False


def replace_ids(value: object, replacements: dict[str, str], parent_key: str | None = None) -> object:
    if isinstance(value, dict):
        return {
            key: replace_ids(subvalue, replacements, key) for key, subvalue in value.items()
        }
    if isinstance(value, list):
        return [replace_ids(item, replacements, parent_key) for item in value]
    if isinstance(value, str) and parent_key in ID_KEYS:
        return replacements.get(value, value)
    return value


class ThreadMetadata:
    def __init__(
        self,
        *,
        thread_id: str,
        rollout_path: Path,
        created_at: dt.datetime,
        updated_at: dt.datetime,
        source: str,
        model_provider: str,
        cwd: Path,
        cli_version: str,
        title: str,
        sandbox_policy: str,
        approval_mode: str,
        tokens_used: int,
        first_user_message: str,
        archived: bool,
        archived_at: dt.datetime | None,
        git_sha: str | None,
        git_branch: str | None,
        git_origin_url: str | None,
        agent_nickname: str | None,
        agent_role: str | None,
    ) -> None:
        self.thread_id = thread_id
        self.rollout_path = rollout_path
        self.created_at = created_at
        self.updated_at = updated_at
        self.source = source
        self.model_provider = model_provider
        self.cwd = cwd
        self.cli_version = cli_version
        self.title = title
        self.sandbox_policy = sandbox_policy
        self.approval_mode = approval_mode
        self.tokens_used = tokens_used
        self.first_user_message = first_user_message
        self.archived = archived
        self.archived_at = archived_at
        self.git_sha = git_sha
        self.git_branch = git_branch
        self.git_origin_url = git_origin_url
        self.agent_nickname = agent_nickname
        self.agent_role = agent_role

    @property
    def has_user_event(self) -> int:
        return int(bool(self.first_user_message))

    def to_row(self) -> dict[str, object]:
        return {
            "id": self.thread_id,
            "rollout_path": str(self.rollout_path),
            "created_at": epoch_seconds(self.created_at),
            "updated_at": epoch_seconds(self.updated_at),
            "source": self.source,
            "model_provider": self.model_provider,
            "cwd": str(self.cwd),
            "title": self.title,
            "sandbox_policy": self.sandbox_policy,
            "approval_mode": self.approval_mode,
            "tokens_used": self.tokens_used,
            "has_user_event": self.has_user_event,
            "archived": int(self.archived),
            "archived_at": epoch_seconds(self.archived_at),
            "git_sha": self.git_sha,
            "git_branch": self.git_branch,
            "git_origin_url": self.git_origin_url,
            "cli_version": self.cli_version,
            "first_user_message": self.first_user_message,
            "agent_nickname": self.agent_nickname,
            "agent_role": self.agent_role,
            "memory_mode": "enabled",
        }


def parse_rollout(rollout_path: Path, codex_home: Path | None = None, default_provider: str = "openai") -> ThreadMetadata:
    records = rollout_path.read_text(encoding="utf-8").splitlines()
    if not records:
        raise SessionToolError(f"empty rollout file: {rollout_path}")

    meta_payload: dict[str, object] | None = None
    cwd = Path()
    source = "cli"
    provider = default_provider
    cli_version = ""
    sandbox_policy = "read_only"
    approval_mode = "on_request"
    title = ""
    first_user_message = ""
    tokens_used = 0
    git_sha = None
    git_branch = None
    git_origin_url = None
    agent_nickname = None
    agent_role = None

    for line in records:
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        event_type = obj.get("type")
        payload = obj.get("payload")
        if not isinstance(payload, dict):
            continue
        if event_type == "session_meta":
            meta_payload = payload
            source = str(payload.get("source") or source)
            provider = str(payload.get("model_provider") or provider)
            cli_version = str(payload.get("cli_version") or cli_version)
            cwd_value = payload.get("cwd")
            if isinstance(cwd_value, str) and cwd_value:
                cwd = Path(cwd_value)
            if isinstance(payload.get("agent_nickname"), str):
                agent_nickname = payload["agent_nickname"]
            if isinstance(payload.get("agent_role"), str):
                agent_role = payload["agent_role"]
            git = payload.get("git")
            if isinstance(git, dict):
                git_sha = git.get("commit_hash") or git_sha
                git_branch = git.get("branch") or git_branch
                git_origin_url = git.get("repository_url") or git_origin_url
        elif event_type == "turn_context":
            sandbox_policy = str(payload.get("sandbox_policy") or sandbox_policy)
            approval_mode = str(payload.get("approval_policy") or approval_mode)
            cwd_value = payload.get("cwd")
            if isinstance(cwd_value, str) and cwd_value and cwd == Path():
                cwd = Path(cwd_value)
        elif event_type == "event_msg":
            subtype = payload.get("type")
            if subtype == "user_message":
                message = str(payload.get("message") or "").strip()
                if message and not first_user_message:
                    first_user_message = message
                if message and not title:
                    title = message
            elif subtype == "token_count":
                info = payload.get("info")
                if isinstance(info, dict):
                    total = info.get("total_token_usage")
                    if isinstance(total, dict):
                        tokens_used = int(total.get("total_tokens") or tokens_used)

    if meta_payload is None:
        raise SessionToolError(f"missing session_meta in {rollout_path}")

    thread_id = str(meta_payload.get("id") or "")
    if not thread_id:
        raise SessionToolError(f"missing thread id in {rollout_path}")

    created_ts = meta_payload.get("timestamp")
    if not isinstance(created_ts, str):
        raise SessionToolError(f"missing session timestamp in {rollout_path}")
    created_at = parse_iso_utc(created_ts)
    updated_at = dt.datetime.fromtimestamp(rollout_path.stat().st_mtime, tz=dt.timezone.utc).replace(
        microsecond=0
    )

    archived = codex_home is not None and is_archived_path(codex_home, rollout_path)
    archived_at = updated_at if archived else None

    return ThreadMetadata(
        thread_id=thread_id,
        rollout_path=rollout_path.resolve(),
        created_at=created_at,
        updated_at=updated_at,
        source=source,
        model_provider=provider,
        cwd=cwd.resolve() if cwd != Path() else Path(),
        cli_version=cli_version,
        title=title,
        sandbox_policy=sandbox_policy,
        approval_mode=approval_mode,
        tokens_used=tokens_used,
        first_user_message=first_user_message,
        archived=archived,
        archived_at=archived_at,
        git_sha=git_sha,
        git_branch=git_branch,
        git_origin_url=git_origin_url,
        agent_nickname=agent_nickname,
        agent_role=agent_role,
    )


def db_path(codex_home: Path) -> Path:
    return codex_home / "state_5.sqlite"


def ensure_db(codex_home: Path) -> sqlite3.Connection:
    ensure_parent(db_path(codex_home))
    conn = sqlite3.connect(db_path(codex_home))
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS threads (
            id TEXT PRIMARY KEY,
            rollout_path TEXT,
            created_at INTEGER,
            updated_at INTEGER,
            source TEXT,
            model_provider TEXT,
            cwd TEXT,
            title TEXT,
            sandbox_policy TEXT,
            approval_mode TEXT,
            tokens_used INTEGER,
            has_user_event INTEGER DEFAULT 0,
            archived INTEGER DEFAULT 0,
            archived_at INTEGER,
            git_sha TEXT,
            git_branch TEXT,
            git_origin_url TEXT,
            cli_version TEXT,
            first_user_message TEXT,
            agent_nickname TEXT,
            agent_role TEXT,
            memory_mode TEXT DEFAULT 'enabled'
        )
        """
    )
    conn.commit()
    return conn


def upsert_thread(conn: sqlite3.Connection, metadata: ThreadMetadata) -> None:
    row = metadata.to_row()
    existing = conn.execute(
        "SELECT memory_mode FROM threads WHERE id = ?", (metadata.thread_id,)
    ).fetchone()
    if existing and existing["memory_mode"]:
        row["memory_mode"] = existing["memory_mode"]
    columns = list(row.keys())
    insert_columns = ", ".join(columns)
    placeholders = ", ".join("?" for _ in columns)
    updates = ", ".join(f"{column}=excluded.{column}" for column in columns if column != "id")
    conn.execute(
        f"""
        INSERT INTO threads ({insert_columns})
        VALUES ({placeholders})
        ON CONFLICT(id) DO UPDATE SET {updates}
        """,
        [row[column] for column in columns],
    )
    conn.commit()


def delete_thread(conn: sqlite3.Connection, thread_id: str) -> None:
    conn.execute("DELETE FROM threads WHERE id = ?", (thread_id,))
    conn.commit()


def build_destination_path(
    codex_home: Path,
    *,
    archived: bool,
    created_at: dt.datetime,
    thread_id: str,
) -> Path:
    root = rollout_root(codex_home, archived)
    relative_dir = Path(created_at.strftime("%Y/%m/%d"))
    name = f"rollout-{created_at.strftime('%Y-%m-%dT%H-%M-%S')}-{thread_id}.jsonl"
    return root / relative_dir / name


def rewrite_rollout(
    src: Path,
    dst: Path,
    *,
    replacements: dict[str, str] | None = None,
    target_provider: str | None = None,
    timestamp_override: dt.datetime | None = None,
) -> None:
    replacements = replacements or {}
    ensure_parent(dst)
    with src.open("r", encoding="utf-8") as reader, dst.open("w", encoding="utf-8") as writer:
        for line in reader:
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if replacements:
                obj = replace_ids(obj, replacements)
            if obj.get("type") == "session_meta" and isinstance(obj.get("payload"), dict):
                payload = obj["payload"]
                if target_provider is not None:
                    payload["model_provider"] = target_provider
                if timestamp_override is not None:
                    payload["timestamp"] = format_session_timestamp(timestamp_override)
            writer.write(json.dumps(obj, ensure_ascii=False))
            writer.write("\n")


def make_rel_path(path: Path, start: Path) -> Path:
    try:
        return path.resolve().relative_to(start.resolve())
    except ValueError as exc:
        raise SessionToolError(f"path is outside codex_home: {path}") from exc


def manifest_name() -> str:
    return "manifest.json"


def collect_package_entries(
    codex_home: Path,
    identifiers: list[str],
    default_provider: str,
) -> list[dict[str, object]]:
    entries: list[dict[str, object]] = []
    seen_ids: set[str] = set()
    for identifier in identifiers:
        rollout_path = resolve_rollout_path(codex_home, identifier)
        metadata = parse_rollout(rollout_path, codex_home, default_provider)
        if metadata.thread_id in seen_ids:
            continue
        seen_ids.add(metadata.thread_id)
        entries.append(
            {
                "thread_id": metadata.thread_id,
                "model_provider": metadata.model_provider,
                "archived": metadata.archived,
                "created_at": format_session_timestamp(metadata.created_at),
                "rollout_relpath": str(make_rel_path(rollout_path, codex_home)),
                "source": metadata.source,
                "cwd": str(metadata.cwd),
                "cli_version": metadata.cli_version,
                "title": metadata.title,
            }
        )
    return entries


def resolve_rollout_path(codex_home: Path, identifier: str) -> Path:
    candidate = Path(identifier).expanduser()
    if candidate.exists():
        return candidate.resolve()
    conn = ensure_db(codex_home)
    try:
        row = conn.execute(
            "SELECT rollout_path FROM threads WHERE id = ?", (identifier,)
        ).fetchone()
    finally:
        conn.close()
    if row:
        path = Path(row["rollout_path"])
        if path.exists():
            return path.resolve()
    raise SessionToolError(f"could not resolve rollout path from: {identifier}")


def cmd_list(args: argparse.Namespace) -> int:
    conn = ensure_db(args.codex_home)
    try:
        if args.scan:
            rows = []
            for path in iter_rollout_paths(args.codex_home):
                metadata = parse_rollout(path, args.codex_home, args.default_provider)
                if args.provider and metadata.model_provider != args.provider:
                    continue
                rows.append(metadata)
            rows.sort(key=lambda item: item.updated_at, reverse=True)
            for item in rows[: args.limit]:
                print(
                    f"{item.thread_id}\t{item.model_provider}\t{item.updated_at.isoformat()}\t{item.rollout_path}"
                )
            return 0

        query = """
            SELECT id, model_provider, updated_at, rollout_path
            FROM threads
            WHERE 1 = 1
        """
        params: list[object] = []
        if args.provider:
            query += " AND model_provider = ?"
            params.append(args.provider)
        query += " ORDER BY updated_at DESC LIMIT ?"
        params.append(args.limit)
        for row in conn.execute(query, params):
            updated = (
                dt.datetime.fromtimestamp(row["updated_at"], tz=dt.timezone.utc).isoformat()
                if row["updated_at"] is not None
                else ""
            )
            print(f"{row['id']}\t{row['model_provider']}\t{updated}\t{row['rollout_path']}")
        return 0
    finally:
        conn.close()


def cmd_reindex(args: argparse.Namespace) -> int:
    conn = ensure_db(args.codex_home)
    try:
        scanned = 0
        for path in iter_rollout_paths(args.codex_home):
            metadata = parse_rollout(path, args.codex_home, args.default_provider)
            if args.provider and metadata.model_provider != args.provider:
                continue
            upsert_thread(conn, metadata)
            scanned += 1
        if args.prune_missing:
            stale_ids = [
                row["id"]
                for row in conn.execute("SELECT id, rollout_path FROM threads")
                if not Path(row["rollout_path"]).exists()
            ]
            for thread_id in stale_ids:
                delete_thread(conn, thread_id)
        print(f"reindexed {scanned} session(s)")
        return 0
    finally:
        conn.close()


def cmd_copy(args: argparse.Namespace) -> int:
    raise SessionToolError("copy has been replaced by package/unpack")


def cmd_move(args: argparse.Namespace) -> int:
    raise SessionToolError("move has been replaced by package/unpack")


def cmd_delete(args: argparse.Namespace) -> int:
    src = resolve_rollout_path(args.codex_home, args.source)
    metadata = parse_rollout(src, args.codex_home, args.default_provider)
    if src.exists():
        src.unlink()
    conn = ensure_db(args.codex_home)
    try:
        delete_thread(conn, metadata.thread_id)
    finally:
        conn.close()
    print(f"deleted {metadata.thread_id}")
    return 0


def cmd_package(args: argparse.Namespace) -> int:
    entries = package_sessions_archive(
        args.codex_home,
        args.sources,
        args.output,
        default_provider=args.default_provider,
    )
    print(f"packaged {len(entries)} session(s)")
    print(args.output.expanduser().resolve())
    return 0


def choose_import_target(
    codex_home: Path,
    entry: dict[str, object],
    *,
    preserve_ids: bool,
    provider_override: str | None,
) -> tuple[Path, dict[str, str], str, str]:
    original_thread_id = str(entry["thread_id"])
    created_at = parse_iso_utc(str(entry["created_at"]))
    archived = bool(entry["archived"])
    target_provider = provider_override or str(entry["model_provider"])
    target_thread_id = original_thread_id if preserve_ids else str(uuid.uuid4())
    dst = build_destination_path(
        codex_home,
        archived=archived,
        created_at=created_at,
        thread_id=target_thread_id,
    )
    replacements = {original_thread_id: target_thread_id}
    return dst, replacements, target_provider, target_thread_id


def package_sessions_archive(
    codex_home: Path,
    sources: list[str],
    output: Path,
    *,
    default_provider: str = "openai",
) -> list[dict[str, object]]:
    entries = collect_package_entries(codex_home, sources, default_provider)
    if not entries:
        raise SessionToolError("no sessions matched for packaging")

    output = output.expanduser().resolve()
    ensure_parent(output)
    manifest = {
        "format": 1,
        "created_at": format_session_timestamp(utc_now()),
        "source_codex_home": str(codex_home),
        "sessions": entries,
    }
    with tarfile.open(output, "w:gz") as archive:
        payload = json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")
        info = tarfile.TarInfo(name=manifest_name())
        info.size = len(payload)
        with tempfile.SpooledTemporaryFile() as handle:
            handle.write(payload)
            handle.seek(0)
            archive.addfile(info, handle)
        for entry in entries:
            relpath = Path(str(entry["rollout_relpath"]))
            src = codex_home / relpath
            archive.add(src, arcname=relpath.as_posix())
    return entries


def unpack_sessions_archive(
    codex_home: Path,
    archive_path: Path,
    *,
    default_provider: str = "openai",
    target_provider: str | None = None,
    preserve_ids: bool = False,
) -> list[dict[str, object]]:
    archive_path = archive_path.expanduser().resolve()
    if not archive_path.exists():
        raise SessionToolError(f"archive not found: {archive_path}")

    imported: list[dict[str, object]] = []
    with tarfile.open(archive_path, "r:gz") as archive:
        try:
            manifest_member = archive.getmember(manifest_name())
        except KeyError as exc:
            raise SessionToolError("archive is missing manifest.json") from exc
        manifest_file = archive.extractfile(manifest_member)
        if manifest_file is None:
            raise SessionToolError("failed to read manifest.json from archive")
        manifest = json.load(manifest_file)
        sessions = manifest.get("sessions")
        if not isinstance(sessions, list):
            raise SessionToolError("invalid manifest: missing sessions list")

        for entry in sessions:
            if not isinstance(entry, dict):
                raise SessionToolError("invalid manifest: bad session entry")
            relpath = Path(str(entry["rollout_relpath"]))
            member = archive.extractfile(relpath.as_posix())
            if member is None:
                member = archive.extractfile(str(relpath))
            if member is None:
                raise SessionToolError(f"archive member missing: {relpath}")
            dst, replacements, provider, target_thread_id = choose_import_target(
                codex_home,
                entry,
                preserve_ids=preserve_ids,
                provider_override=target_provider,
            )
            ensure_parent(dst)
            with tempfile.NamedTemporaryFile(
                mode="wb", suffix=".jsonl", delete=False, dir=str(dst.parent)
            ) as tmp:
                tmp.write(member.read())
                tmp_path = Path(tmp.name)
            try:
                rewrite_rollout(
                    tmp_path,
                    dst,
                    replacements=replacements if not preserve_ids else None,
                    target_provider=provider,
                )
            finally:
                if tmp_path.exists():
                    tmp_path.unlink()
            imported.append(
                {
                    "original_thread_id": str(entry["thread_id"]),
                    "thread_id": target_thread_id,
                    "rollout_path": str(dst.resolve()),
                    "model_provider": provider,
                    "archived": bool(entry["archived"]),
                }
            )

    reindex_args = argparse.Namespace(
        codex_home=codex_home,
        default_provider=default_provider,
        provider=None,
        prune_missing=False,
    )
    cmd_reindex(reindex_args)
    return imported


def cmd_unpack(args: argparse.Namespace) -> int:
    imported = unpack_sessions_archive(
        args.codex_home,
        args.archive,
        default_provider=args.default_provider,
        target_provider=args.target_provider,
        preserve_ids=args.preserve_ids,
    )
    print(f"unpacked {len(imported)} session(s)")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manipulate Codex session rollouts and thread index.")
    parser.add_argument(
        "--codex-home",
        type=Path,
        default=Path("~/.codex").expanduser(),
        help="Codex home directory containing sessions/ and state_5.sqlite",
    )
    parser.add_argument(
        "--default-provider",
        default="openai",
        help="Fallback provider if a rollout lacks model_provider",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list", help="List indexed sessions")
    list_parser.add_argument("--provider", help="Filter by model_provider")
    list_parser.add_argument("--limit", type=int, default=20)
    list_parser.add_argument(
        "--scan", action="store_true", help="Read rollout files directly instead of SQLite"
    )
    list_parser.set_defaults(func=cmd_list)

    reindex_parser = subparsers.add_parser("reindex", help="Rebuild the threads index from rollout files")
    reindex_parser.add_argument("--provider", help="Only reindex this provider")
    reindex_parser.add_argument(
        "--prune-missing",
        action="store_true",
        help="Delete DB rows whose rollout files no longer exist",
    )
    reindex_parser.set_defaults(func=cmd_reindex)

    delete_parser = subparsers.add_parser("delete", help="Delete a rollout and its thread row")
    delete_parser.add_argument("source", help="Rollout path or thread id")
    delete_parser.set_defaults(func=cmd_delete)

    package_parser = subparsers.add_parser("package", help="Export one or more sessions to a tar.gz archive")
    package_parser.add_argument("output", type=Path, help="Output .tar.gz archive path")
    package_parser.add_argument("sources", nargs="+", help="Session thread ids or rollout paths")
    package_parser.set_defaults(func=cmd_package)

    unpack_parser = subparsers.add_parser("unpack", help="Import sessions from a tar.gz archive")
    unpack_parser.add_argument("archive", type=Path, help="Input .tar.gz archive path")
    unpack_parser.add_argument(
        "--target-provider",
        help="Override model_provider for all imported sessions",
    )
    unpack_parser.add_argument(
        "--preserve-ids",
        action="store_true",
        help="Keep original thread ids instead of generating new ones",
    )
    unpack_parser.set_defaults(func=cmd_unpack)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    args.codex_home = args.codex_home.expanduser().resolve()
    try:
        return int(args.func(args))
    except SessionToolError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
