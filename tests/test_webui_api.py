import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from webui.api import coerce_existing_workdirs, merge_workdir_history  # noqa: E402


class WebUiApiTests(unittest.TestCase):
    def test_coerce_existing_workdirs_filters_missing_entries_and_deduplicates(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            first = root / "first"
            second = root / "second"
            first.mkdir()
            second.mkdir()

            result = coerce_existing_workdirs(
                [
                    "",
                    str(first),
                    str(root / "missing"),
                    str(second),
                    str(first),
                ],
                max_count=5,
            )

            self.assertEqual(result, [str(first.resolve()), str(second.resolve())])

    def test_merge_workdir_history_prioritizes_current_then_initial_then_saved(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            current = root / "current"
            initial = root / "initial"
            saved = root / "saved"
            current.mkdir()
            initial.mkdir()
            saved.mkdir()

            result = merge_workdir_history(
                str(current),
                {
                    "last_workdir": str(saved),
                    "recent_workdirs": [str(initial), str(current)],
                },
                initial_workdir=initial,
                max_count=5,
            )

            self.assertEqual(
                result,
                [
                    str(current.resolve()),
                    str(initial.resolve()),
                    str(saved.resolve()),
                ],
            )


if __name__ == "__main__":
    unittest.main()
