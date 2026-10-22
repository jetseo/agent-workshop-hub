import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from workshop import projectmap


def write(root, name, text=""):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class ProjectMapTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def commands(self):
        info = projectmap.read_project(self.root)
        return {(c["kind"], c["command"]) for c in info["commands"]}

    def test_python_project(self):
        write(self.root, "pyproject.toml", "[tool.pytest.ini_options]\n")
        write(self.root, "app/main.py", "print(1)\n")
        write(self.root, "README.md", "# Demo\n\nHello **world**.\n```\nsecret code block\n```\n")
        info = projectmap.read_project(self.root)
        self.assertIn(("test", "python -m pytest"), self.commands())
        self.assertEqual(info["languages"], ["Python"])
        self.assertIn("Hello", info["docs"][0]["text"])
        self.assertNotIn("secret code block", info["docs"][0]["text"])

    def test_unittest_project(self):
        write(self.root, "tests/test_a.py", "")
        self.assertIn(("test", "python -m unittest discover -s tests"), self.commands())

    def test_node_project_uses_the_lockfile_manager(self):
        write(self.root, "package.json", json.dumps({"scripts": {"build": "tsc", "test": "jest", "start": "node ."}}))
        write(self.root, "pnpm-lock.yaml")
        found = self.commands()
        self.assertIn(("test", "pnpm test"), found)
        self.assertIn(("build", "pnpm run build"), found)
        self.assertNotIn("start", {k for k, _ in found})

    def test_dotnet_project(self):
        write(self.root, "App.sln")
        write(self.root, "src/App.csproj")
        write(self.root, "src/Program.cs", "")
        found = self.commands()
        self.assertIn(("build", "dotnet build"), found)
        self.assertIn(("test", "dotnet test"), found)
        self.assertEqual(projectmap.read_project(self.root)["languages"], ["C#"])

    def test_makefile_targets_and_broken_package_json(self):
        write(self.root, "Makefile", "build:\n\techo\ntest:\n\techo\n")
        write(self.root, "package.json", "{not json")
        self.assertEqual(self.commands(), {("build", "make build"), ("test", "make test")})

    def test_empty_repository(self):
        info = projectmap.read_project(self.root)
        self.assertTrue(info["empty"])
        self.assertEqual(info["commands"], [])
        self.assertIn("빈 저장소", projectmap.render(info))

    def test_secrets_and_noise_are_never_listed_or_read(self):
        write(self.root, ".env", "API_KEY=sk-live-123")
        write(self.root, "config/credentials.json", "{}")
        write(self.root, "node_modules/x/index.js", "")
        write(self.root, "src/app.py", "")
        text = projectmap.render(projectmap.read_project(self.root))
        self.assertNotIn(".env", text)
        self.assertNotIn("credentials", text)
        self.assertNotIn("node_modules", text)
        self.assertNotIn("sk-live", text)
        self.assertIn("src/", text)

    def test_git_repo_lists_tracked_files_and_recent_commits(self):
        write(self.root, "a.py", "")
        write(self.root, "untracked.py", "")
        for args in (["init", "-q"], ["add", "a.py"], ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "first commit"]):
            subprocess.run(["git", *args], cwd=self.root, check=True, capture_output=True)
        info = projectmap.read_project(self.root)
        self.assertEqual(info["file_count"], 1)
        self.assertTrue(info["commits"][0].endswith("first commit"))

    def test_summary_is_bounded(self):
        for i in range(300):
            write(self.root, "d%d/s%d/f.py" % (i, i), "")
        write(self.root, "README.md", "x " * 5000)
        text = projectmap.render(projectmap.read_project(self.root))
        self.assertLessEqual(len(text), projectmap.SUMMARY_CHARS)


if __name__ == "__main__":
    unittest.main()
