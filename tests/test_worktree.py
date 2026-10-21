import subprocess
import tempfile
import unittest
from pathlib import Path
from workshop import worktree
from workshop.orchestrator import create_board, run_board
from workshop.providers import Providers


def git(*args, cwd):
    return subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=cwd,
                          capture_output=True, text=True, check=True).stdout


class WorktreeCodeStepTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.repo = self.dir / "repo"
        self.repo.mkdir()
        git("init", "-q", cwd=self.repo)
        (self.repo / "README.md").write_text("hello\n", encoding="utf-8")
        git("add", "-A", cwd=self.repo)
        git("commit", "-qm", "init", cwd=self.repo)
        self.providers = Providers(self.dir / "p.json")
        self.flags = dict(allow_opencode=True, allow_cloud=True, allow_may_train=True,
                          approved_code=["edit"], worktree_base=self.dir / "wt")

    def board(self, model="opencode/glm-5.3-flash-free"):
        return create_board(self.dir / "boards", {"title": "T", "goal": "G", "steps": [
            {"id": "edit", "kind": "code", "model": model, "repo": str(self.repo), "prompt": "append a line"}]})

    @staticmethod
    def editing_agent(prompt, workspace, **kw):
        with open(Path(workspace) / "README.md", "a", encoding="utf-8") as handle:
            handle.write("<!-- e2e -->\n")
        (Path(workspace) / "new.txt").write_text("new file\n", encoding="utf-8")
        return {"exit_code": 0, "stdout": ""}

    def test_edit_happens_in_a_disposable_worktree_and_yields_a_diff(self):
        board = self.board()
        summary = run_board(board, self.providers, code_fn=self.editing_agent, **self.flags)
        self.assertEqual(summary["done"], ["edit"])
        output = (board.folder / "steps" / "edit.md").read_text(encoding="utf-8")
        self.assertIn("+<!-- e2e -->", output)
        self.assertIn("new.txt", output)
        self.assertIn("Nothing was committed", output)
        # the real checkout is untouched and nothing was committed
        self.assertEqual((self.repo / "README.md").read_text(encoding="utf-8"), "hello\n")
        self.assertFalse((self.repo / "new.txt").exists())
        self.assertEqual(git("status", "--porcelain", cwd=self.repo), "")
        self.assertEqual(len(git("log", "--oneline", cwd=self.repo).splitlines()), 1)
        branches = git("branch", "--list", "workshop/*", cwd=self.repo)
        self.assertIn("workshop/" + board.folder.name[:8] + "-edit", branches)

    def test_gates_still_apply_before_any_worktree_is_created(self):
        board = self.board()
        cases = [dict(self.flags, approved_code=[]), dict(self.flags, allow_opencode=False),
                 dict(self.flags, allow_cloud=False)]
        for flags in cases:
            run_board(self.board(), self.providers, code_fn=self.editing_agent, **flags)
        self.assertFalse((self.dir / "wt").exists())
        paid = self.board(model="openai/gpt-5.4")
        summary = run_board(paid, self.providers, code_fn=self.editing_agent, **self.flags)
        self.assertIn("--allow-paid-models", summary["failed"]["edit"])
        self.assertFalse((self.dir / "wt").exists())

    def test_failed_agent_keeps_worktree_and_reports_its_path(self):
        board = self.board()
        summary = run_board(board, self.providers, code_fn=lambda *a, **k: {"exit_code": 2, "stderr": "boom"},
                            **self.flags)
        message = summary["failed"]["edit"]
        self.assertIn("boom", message)
        self.assertIn("worktree kept", message)

    def test_no_change_is_reported_and_remove_cleans_up(self):
        board = self.board()
        run_board(board, self.providers, code_fn=lambda *a, **k: {"exit_code": 0}, **self.flags)
        self.assertIn("No files changed.", (board.folder / "steps" / "edit.md").read_text(encoding="utf-8"))
        path = self.dir / "wt" / board.folder.name / "edit"
        self.assertTrue(path.is_dir())
        worktree.remove(self.repo, path)
        self.assertFalse(path.exists())
        self.assertEqual(git("branch", "--list", "workshop/*", cwd=self.repo).strip(), "")

    def test_main_checkout_workspace_is_refused_by_default(self):
        board = create_board(self.dir / "b2", {"title": "T", "goal": "G", "steps": [
            {"id": "edit", "kind": "code", "model": "ollama/q", "workspace": str(self.repo)}]})
        calls = []
        summary = run_board(board, self.providers, code_fn=lambda *a, **k: calls.append(1) or {"exit_code": 0},
                            **self.flags)
        self.assertIn("--allow-main-checkout", summary["failed"]["edit"])
        self.assertEqual(calls, [])

    def test_spec_repo_must_be_a_git_repository(self):
        with self.assertRaises(ValueError):
            create_board(self.dir / "b3", {"title": "T", "goal": "G", "steps": [
                {"id": "edit", "kind": "code", "model": "ollama/q", "repo": str(self.dir)}]})


if __name__ == "__main__":
    unittest.main()
