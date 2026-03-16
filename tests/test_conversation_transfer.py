import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from conversation_transfer import (  # noqa: E402
    AccountProfile,
    assign_threads_to_account,
    classify_conversations,
    copy_conversations_to_account,
    infer_provider_from_text,
    load_transfer_state,
    merge_conversation_records,
    require_single_source_account,
    resolve_transfer_conversations,
    scan_local_workdir_conversations,
)
from session_tool import parse_rollout  # noqa: E402


def write_rollout(
    codex_home: Path,
    *,
    thread_id: str,
    provider: str,
    cwd: Path,
    title: str,
) -> Path:
    timestamp = datetime(2026, 3, 16, 1, 2, 3, tzinfo=timezone.utc)
    rollout_dir = codex_home / "sessions" / "2026" / "03" / "16"
    rollout_dir.mkdir(parents=True, exist_ok=True)
    rollout_path = rollout_dir / f"rollout-2026-03-16T01-02-03-{thread_id}.jsonl"
    records = [
        {
            "timestamp": "2026-03-16T01:02:03Z",
            "type": "session_meta",
            "payload": {
                "id": thread_id,
                "timestamp": timestamp.isoformat().replace("+00:00", "Z"),
                "cwd": str(cwd),
                "originator": "Codex Desktop",
                "cli_version": "0.108.0-alpha.8",
                "source": "vscode",
                "model_provider": provider,
            },
        },
        {
            "timestamp": "2026-03-16T01:02:04Z",
            "type": "event_msg",
            "payload": {"type": "user_message", "message": title},
        },
    ]
    rollout_path.write_text(
        "\n".join(json.dumps(record, ensure_ascii=False) for record in records) + "\n",
        encoding="utf-8",
    )
    return rollout_path


class ConversationTransferTests(unittest.TestCase):
    def test_infer_provider_from_text_prefers_config_then_chatgpt_auth(self) -> None:
        self.assertEqual(
            infer_provider_from_text('model_provider = "teamplus"', "{}"),
            "teamplus",
        )
        self.assertEqual(
            infer_provider_from_text(
                "",
                json.dumps({"auth_mode": "chatgpt", "tokens": {"account_id": "abc"}}),
            ),
            "openai",
        )
        self.assertIsNone(infer_provider_from_text("", json.dumps({"OPENAI_API_KEY": "key"})))

    def test_classify_conversations_uses_auto_assignment_and_manual_override(self) -> None:
        profiles = [
            AccountProfile(name="api", directory=Path("api"), description="", provider="teamplus"),
            AccountProfile(name="user1", directory=Path("user1"), description="", provider="openai"),
        ]
        raw_conversations = [
            {
                "thread_id": "thread-teamplus",
                "rollout_path": Path("teamplus.jsonl"),
                "cwd": "D:\\workspace",
                "updated_at": 10.0,
                "title": "Teamplus thread",
                "preview": "Teamplus preview",
                "model_provider": "teamplus",
            },
            {
                "thread_id": "thread-ambiguous",
                "rollout_path": Path("ambiguous.jsonl"),
                "cwd": "D:\\workspace",
                "updated_at": 20.0,
                "title": "Unknown thread",
                "preview": "Unknown preview",
                "model_provider": "unknown",
            },
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = Path(tmpdir) / "state.json"
            assign_threads_to_account(
                ["thread-ambiguous"],
                "user1",
                state_path=state_path,
                source="manual",
            )
            conversations = classify_conversations(
                raw_conversations,
                profiles,
                state_path=state_path,
            )

        by_id = {conversation.thread_id: conversation for conversation in conversations}
        self.assertEqual(by_id["thread-teamplus"].assigned_account, "api")
        self.assertEqual(by_id["thread-teamplus"].assignment_source, "auto")
        self.assertEqual(by_id["thread-ambiguous"].assigned_account, "user1")
        self.assertEqual(by_id["thread-ambiguous"].assignment_source, "manual")

    def test_copy_conversations_to_account_creates_new_thread_and_overrides_provider(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            codex_home = root / ".codex"
            workdir = root / "workspace"
            workdir.mkdir(parents=True, exist_ok=True)
            state_path = root / "transfer-state.json"

            source_rollout = write_rollout(
                codex_home,
                thread_id="thread-source",
                provider="teamplus",
                cwd=workdir,
                title="Source conversation",
            )

            raw_conversations = scan_local_workdir_conversations(codex_home, workdir)
            conversations = classify_conversations(
                raw_conversations,
                [AccountProfile(name="api", directory=Path("api"), description="", provider="teamplus")],
                state_path=state_path,
            )
            source_conversation = next(
                conversation for conversation in conversations if conversation.thread_id == "thread-source"
            )
            target_profile = AccountProfile(
                name="user1",
                directory=Path("user1"),
                description="",
                provider="openai",
            )

            result = copy_conversations_to_account(
                codex_home,
                [source_conversation],
                target_profile,
                state_path=state_path,
            )

            self.assertEqual(result.imported_count, 1)
            self.assertEqual(len(result.imported_thread_ids), 1)
            imported_thread_id = result.imported_thread_ids[0]
            self.assertNotEqual(imported_thread_id, "thread-source")

            state = load_transfer_state(state_path)
            assignments = state["thread_assignments"]
            self.assertEqual(assignments[imported_thread_id]["account_name"], "user1")
            self.assertEqual(assignments[imported_thread_id]["source"], "copied")
            self.assertEqual(
                assignments[imported_thread_id]["copied_from_thread_id"],
                "thread-source",
            )

            imported_rollouts = sorted(codex_home.glob(f"sessions/**/*.jsonl"))
            self.assertEqual(len(imported_rollouts), 2)

            source_metadata = parse_rollout(source_rollout, codex_home, "openai")
            self.assertEqual(source_metadata.thread_id, "thread-source")
            self.assertEqual(source_metadata.model_provider, "teamplus")

            imported_metadata = None
            for rollout_path in imported_rollouts:
                metadata = parse_rollout(rollout_path, codex_home, "openai")
                if metadata.thread_id == imported_thread_id:
                    imported_metadata = metadata
                    break
            self.assertIsNotNone(imported_metadata)
            self.assertEqual(imported_metadata.model_provider, "openai")

    def test_current_account_visible_threads_override_ambiguous_assignment(self) -> None:
        profiles = [
            AccountProfile(name="user1", directory=Path("user1"), description="", provider="openai"),
            AccountProfile(name="user2", directory=Path("user2"), description="", provider="openai"),
        ]
        raw_conversations = merge_conversation_records(
            [],
            [
                {
                    "thread_id": "thread-current",
                    "rollout_path": Path("current.jsonl"),
                    "cwd": "D:\\workspace",
                    "updated_at": 30.0,
                    "title": "Current visible thread",
                    "preview": "Current visible preview",
                    "model_provider": "openai",
                }
            ],
        )
        conversations = classify_conversations(
            raw_conversations,
            profiles,
            current_account_name="user2",
            current_visible_thread_ids={"thread-current"},
        )
        self.assertEqual(len(conversations), 1)
        self.assertEqual(conversations[0].assigned_account, "user2")
        self.assertEqual(conversations[0].assignment_source, "active")

    def test_resolve_transfer_conversations_accepts_thread_id_and_rollout_path(self) -> None:
        conversations = [
            type(
                "Conversation",
                (),
                {
                    "thread_id": "thread-a",
                    "rollout_path": Path(r"C:\tmp\a.jsonl"),
                },
            )(),
            type(
                "Conversation",
                (),
                {
                    "thread_id": "thread-b",
                    "rollout_path": Path(r"C:\tmp\b.jsonl"),
                },
            )(),
        ]
        selected = resolve_transfer_conversations(
            conversations,
            ["thread-a", r"C:\tmp\b.jsonl"],
        )
        self.assertEqual([item.thread_id for item in selected], ["thread-a", "thread-b"])

    def test_require_single_source_account_rejects_mixed_groups(self) -> None:
        conversations = [
            type("Conversation", (), {"assigned_account": "user1"})(),
            type("Conversation", (), {"assigned_account": "api"})(),
        ]
        with self.assertRaisesRegex(Exception, "same source account or group"):
            require_single_source_account(conversations)


if __name__ == "__main__":
    unittest.main()
