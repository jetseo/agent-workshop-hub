import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from workshop.codework import CodeRuns, CodeWorkError
from tests.test_codework import Base


class TaskManagementTests(Base):
    def finished(self, prompt="README에 한 줄 추가해줘"):
        run_id = self.start(prompt)
        self.wait(run_id)
        return run_id

    def ids(self, view="active", q=""):
        return [r["id"] for r in self.runs.list(view, q)]

    def test_same_title_runs_are_independent_and_trash_hides_only_the_chosen_one(self):
        a, b = self.finished("같은 제목"), self.finished("같은 제목")
        self.assertNotEqual(a, b)
        self.runs.trash(a)
        self.assertEqual(self.ids(), [b])
        self.assertEqual(self.ids("trash"), [a])
        self.assertEqual(self.runs.counts(), {"active": 1, "archived": 0, "trash": 1})
        self.assertTrue(self.repo.exists())                                              # the project folder is untouched

    def test_trash_survives_a_new_process_and_restore_brings_it_back(self):
        a = self.finished()
        self.runs.trash(a)
        again = CodeRuns(self.providers, find_binary=lambda: None, worktree_base=self.dir / "wt")   # "after restarting the app"
        self.addCleanup(again.shutdown)
        self.assertEqual([r["id"] for r in again.list("trash")], [a])
        self.assertEqual(again.list("active"), [])
        again.restore(a)
        self.assertEqual([r["id"] for r in again.list("active")], [a])

    def test_archive_keeps_the_record_and_unarchive_returns_it(self):
        a = self.finished()
        self.runs.archive(a)
        self.assertEqual((self.ids(), self.ids("archived")), ([], [a]))
        self.runs.unarchive(a)
        self.assertEqual(self.ids(), [a])
        self.runs.trash(a)
        with self.assertRaises(CodeWorkError):
            self.runs.archive(a)                                                         # restore it first

    def test_rename_and_search(self):
        a, b = self.finished("첫째 일"), self.finished("둘째 일")
        self.runs.rename(a, "  내가 붙인   이름 ")
        self.assertEqual(self.runs.get(a)["title"], "내가 붙인 이름")
        self.assertEqual(self.ids(q="붙인"), [a])
        self.assertEqual(self.ids(q="둘째"), [b])
        self.runs.rename(a, "")
        self.assertIsNone(self.runs.get(a)["title"])

    def test_purge_needs_the_trash_and_confirmation_and_removes_only_that_run(self):
        a, b = self.finished(), self.finished()
        with self.assertRaises(CodeWorkError):
            self.runs.purge(a, True)                                                     # not in the trash
        self.runs.trash(a)
        with self.assertRaises(CodeWorkError):
            self.runs.purge(a, False)                                                    # no confirmation
        self.runs.purge(a, True)
        self.assertFalse((self.runs.runs_dir / a).exists())
        self.assertTrue((self.runs.runs_dir / b).exists())
        self.assertTrue(self.repo.exists())
        with self.assertRaises(CodeWorkError):
            self.runs.get(a)

    def test_a_stale_running_record_can_be_cleaned_up(self):
        run_id = self.finished()
        path = self.runs.runs_dir / run_id / "run.json"                                  # a crash leaves "running" behind
        data = json.loads(path.read_text(encoding="utf-8"))
        data["status"] = "running"
        path.write_text(json.dumps(data), encoding="utf-8")
        self.runs.trash(run_id)
        self.assertEqual(self.ids("trash"), [run_id])

    def test_a_live_run_is_refused(self):
        run_id = self.finished()
        run = self.runs._read(run_id)
        class Alive:
            def is_alive(self): return True
        run["status"] = "running"
        self.runs._save(run)
        self.runs._threads[run_id] = Alive()
        with self.assertRaises(CodeWorkError):
            self.runs.trash(run_id)
        with self.assertRaises(CodeWorkError):
            self.runs.rename(run_id, "x")
        del self.runs._threads[run_id]

    def test_bulk_reports_each_id_and_a_stale_save_does_not_wipe_the_flags(self):
        a, b = self.finished(), self.finished()
        results = self.runs.bulk([a, "000000000000", b], "trash")
        self.assertEqual([r["ok"] for r in results], [True, False, True])
        stale = self.runs._read(a)
        stale.pop("deleted_at")                                                          # a job that began earlier holds an old copy
        self.runs._save(stale)
        self.assertEqual(sorted(self.ids("trash")), sorted([a, b]))
        self.assertFalse(self.runs.bulk([a], "purge", False)[0]["ok"])

    def test_rerun_starts_a_new_run_and_leaves_the_old_one(self):
        a = self.finished("README에 한 줄 추가해줘")
        new = self.runs.rerun(a)
        self.assertNotEqual(new["id"], a)
        self.assertEqual(new["prompt"], self.runs.get(a)["prompt"])
        self.wait(new["id"])
        self.assertIn(a, self.ids())

    def test_rerun_with_another_model_uses_the_chosen_model_and_keeps_the_old_run(self):
        a = self.finished("README에 한 줄 추가해줘")
        new = self.runs.rerun(a, "loc/tiny-3b")
        self.assertEqual(new["model"], "loc/tiny-3b")
        self.wait(new["id"])
        self.assertEqual(self.runs.get(a)["model"], "loc/qwen2.5-coder:32b")                       # the old run is untouched

    def test_rerun_with_a_bad_unconnected_or_paid_model_is_refused_like_a_fresh_run(self):
        a = self.finished()
        for bad in ("../x", "nope/unknown-model", "pay/gpt-x"):
            with self.assertRaises(CodeWorkError, msg=bad):
                self.runs.rerun(a, bad)
        self.assertEqual(len(self.ids()), 1)                                                         # nothing was started


if __name__ == "__main__":
    unittest.main()
