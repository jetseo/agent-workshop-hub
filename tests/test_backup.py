import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from workshop import backup


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "data"
        (self.root / "chats").mkdir(parents=True)
        (self.root / "logs").mkdir()
        (self.root / "worktrees" / "b1").mkdir(parents=True)
        (self.root / "providers.json").write_text('{"providers": {}}')
        (self.root / "secrets.json").write_text('{"x": "sk-secret-0000"}')
        (self.root / "terminal.key").write_text("startupkey")
        (self.root / "chats" / "a.json").write_text('{"id": "a"}')
        (self.root / "logs" / "hub.log").write_text("log")
        (self.root / "worktrees" / "b1" / "f.py").write_text("x")
        (self.root / "usage.jsonl.tmp").write_text("half-written")
        self.out = Path(self.tmp.name) / "b.zip"

    def names(self, path=None):
        with zipfile.ZipFile(path or self.out) as z:
            return set(z.namelist())

    def test_default_export_leaves_out_keys_logs_worktrees_and_temp_files(self):
        result = backup.export(self.root, self.out)
        self.assertEqual(self.names(), {"providers.json", "chats/a.json", "BACKUP.json"})
        self.assertFalse(result["includes_secrets"])
        self.assertNotIn(b"sk-secret", self.out.read_bytes())

    def test_secrets_only_when_asked(self):
        backup.export(self.root, self.out, include_secrets=True)
        self.assertIn("secrets.json", self.names())
        self.assertIn("terminal.key", self.names())

    def test_export_never_overwrites(self):
        backup.export(self.root, self.out)
        with self.assertRaises(FileExistsError):
            backup.export(self.root, self.out)

    def test_missing_data_folder_is_a_clear_error(self):
        with self.assertRaises(FileNotFoundError):
            backup.export(Path(self.tmp.name) / "nope", self.out)

    def test_restore_does_not_overwrite_unless_forced(self):
        backup.export(self.root, self.out)
        target = Path(self.tmp.name) / "restored"
        (target / "chats").mkdir(parents=True)
        (target / "chats" / "a.json").write_text("MINE")
        result = backup.restore(self.out, target)
        self.assertEqual((target / "chats" / "a.json").read_text(), "MINE")
        self.assertIn("chats/a.json", result["skipped_existing_or_secret"])
        self.assertTrue((target / "providers.json").exists())
        backup.restore(self.out, target, force=True)
        self.assertEqual((target / "chats" / "a.json").read_text(), '{"id": "a"}')

    def test_restore_skips_secrets_unless_asked(self):
        backup.export(self.root, self.out, include_secrets=True)
        a, b = Path(self.tmp.name) / "r1", Path(self.tmp.name) / "r2"
        backup.restore(self.out, a)
        self.assertFalse((a / "secrets.json").exists())
        backup.restore(self.out, b, include_secrets=True)
        self.assertTrue((b / "secrets.json").exists())

    def test_path_traversal_in_a_backup_is_rejected_before_anything_is_written(self):
        evil = Path(self.tmp.name) / "evil.zip"
        with zipfile.ZipFile(evil, "w") as z:
            z.writestr("ok.json", "{}")
            z.writestr("../escaped.txt", "pwned")
        target = Path(self.tmp.name) / "t"
        with self.assertRaises(ValueError):
            backup.restore(evil, target)
        self.assertFalse((Path(self.tmp.name) / "escaped.txt").exists())
        for bad in ("/abs.txt", "C:/win.txt", "a/../../b.txt"):
            with zipfile.ZipFile(evil, "w") as z:
                z.writestr(bad, "x")
            with self.assertRaises(ValueError):
                backup.restore(evil, target)

    def test_cli_reports_errors_without_a_traceback(self):
        code = backup.main(["--data", str(Path(self.tmp.name) / "nope"), "export", "--out", str(self.out)])
        self.assertEqual(code, 1)
        self.assertEqual(backup.main(["--data", str(self.root), "export", "--out", str(self.out)]), 0)


if __name__ == "__main__":
    unittest.main()
