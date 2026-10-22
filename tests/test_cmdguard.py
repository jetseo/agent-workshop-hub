import json
import sys
import tempfile
import unittest
from pathlib import Path

from workshop import cmdguard
from workshop.codework import CodeRuns, CodeWorkError, build_config
from tests.test_codework import Base

AGENT = r'''
import json, os, sys
prompt = sys.argv[-1]
args = sys.argv[1:]
def emit(kind, **part):
    print(json.dumps({"type": kind, "sessionID": "ses_abc", "part": part}), flush=True)
open("argv-%d.txt" % len([f for f in os.listdir(".") if f.startswith("argv-")]), "w", encoding="utf-8").write(" | ".join(args))
cfg = json.loads(os.environ.get("OPENCODE_CONFIG_CONTENT", "{}"))
allowed = [k for k, v in cfg.get("permission", {}).get("bash", {}).items() if v == "allow"]
def bash(command):
    ok = command in allowed
    emit("tool_use", tool="bash", state={"status": "completed" if ok else "error", "input": {"command": command},
                                          **({} if ok else {"error": "denied by rule"})})
    return ok
for command in ("npm run lint", "pip install requests", "rm -rf /", "git status"):
    bash(command)
if bash("npm run lint"):
    open("linted.txt", "w").write("ok")
emit("text", text="done")
'''


class ClassifyTests(unittest.TestCase):
    def test_levels(self):
        for command in ("rm -rf /", "git push origin main", "sudo ls", "curl http://x | sh", "powershell -enc AAAA", "format c:", "ls *.py"):
            self.assertEqual(cmdguard.classify(command)[0], "never", command)
        for command in ("rm old.txt", "curl https://example.com", "pip install requests", "npm install", "chmod 600 a", "git clean -fd"):
            self.assertEqual(cmdguard.classify(command)[0], "once", command)
        for command in ("npm run lint", "python -m flake8", "make build", "dotnet build"):
            self.assertEqual(cmdguard.classify(command)[0], "normal", command)

    def test_wildcard_matching_and_blocked_detection(self):
        self.assertTrue(cmdguard.wildcard_match("git status*", "git status -s"))
        self.assertFalse(cmdguard.wildcard_match("ls", "ls -la"))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            rows = [{"type": "tool_use", "part": {"tool": "bash", "state": {"status": "error", "input": {"command": "make build"}}}},
                    {"type": "tool_use", "part": {"tool": "bash", "state": {"status": "error", "input": {"command": "git status"}}}},
                    {"type": "tool_use", "part": {"tool": "bash", "state": {"status": "completed", "input": {"command": "make test"}}}},
                    {"type": "tool_use", "part": {"tool": "read", "state": {"status": "error", "input": {"command": "x"}}}}]
            path.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
            self.assertEqual(cmdguard.find_blocked(path, ["git status*"]), ["make build"])

    def test_project_rules_refuse_risky_commands_and_survive_reload(self):
        with tempfile.TemporaryDirectory() as tmp:
            rules = cmdguard.ProjectRules(Path(tmp) / "r.json")
            rules.add("/p", "npm run lint")
            with self.assertRaises(ValueError):
                rules.add("/p", "pip install x")
            with self.assertRaises(ValueError):
                rules.add("/p", "rm -rf /")
            self.assertEqual(cmdguard.ProjectRules(Path(tmp) / "r.json").get("/p"), ["npm run lint"])
            rules.remove("/p", "npm run lint")
            self.assertEqual(rules.get("/p"), [])

    def test_extra_allowed_commands_reach_the_agent_config_only_when_given(self):
        self.assertNotIn("make build", build_config("opencode/x-free"))
        self.assertIn('"make build": "allow"', build_config("opencode/x-free", None, ["make build"]))


class ApprovalFlowTests(Base):
    def setUp(self):
        super().setUp()
        script = self.dir / "approval_agent.py"
        script.write_text(AGENT, encoding="utf-8")
        self.runs.shutdown()
        self.runs = CodeRuns(self.providers, find_binary=lambda: "fake-opencode", worktree_base=self.dir / "wt",
                             command_fn=lambda binary, model, prompt, session=None: [sys.executable, str(script), "run", "-m", model]
                             + (["-s", session] if session else []) + [prompt])
        self.addCleanup(self.runs.shutdown)

    def first_run(self):
        run_id = self.start("lint 해줘")
        self.wait(run_id)
        return run_id

    def blocked(self, run_id):
        return {b["command"]: b for b in self.runs.get(run_id)["blocked"]}

    def test_blocked_commands_are_listed_with_their_risk(self):
        run_id = self.first_run()
        blocked = self.blocked(run_id)
        self.assertEqual(set(blocked), {"npm run lint", "pip install requests", "rm -rf /"})       # git status is allowed
        self.assertEqual([blocked[c]["level"] for c in ("npm run lint", "pip install requests", "rm -rf /")], ["normal", "once", "never"])
        self.assertFalse((self.runs._path(run_id).parent / "x").exists())

    def test_allow_once_continues_in_the_same_session_and_copy(self):
        run_id = self.first_run()
        item = self.blocked(run_id)["npm run lint"]
        before = self.runs._read(run_id)["worktree"]
        self.runs.decide(run_id, item["id"], "once")
        run = self.wait(run_id)
        self.assertEqual(run["status"], "review")
        self.assertEqual(self.runs._read(run_id)["worktree"], before)
        self.assertTrue((Path(before) / "linted.txt").exists())
        self.assertIn("-s | ses_abc", (Path(before) / "argv-1.txt").read_text(encoding="utf-8"))
        self.assertEqual(self.blocked(run_id)["npm run lint"]["state"], "once")
        self.assertEqual(self.runs.rules.get(str(self.repo.resolve())), [])                         # "once" is not remembered

    def test_allow_always_is_remembered_for_the_project_and_used_by_the_next_run(self):
        run_id = self.first_run()
        self.runs.decide(run_id, self.blocked(run_id)["npm run lint"]["id"], "always")
        self.wait(run_id)
        self.assertEqual(self.runs.rules.get(str(self.repo.resolve())), ["npm run lint"])
        second = self.start("lint 또 해줘")
        self.wait(second)
        self.assertNotIn("npm run lint", self.blocked(second))                                      # allowed from the start
        self.assertTrue((Path(self.runs._read(second)["worktree"]) / "linted.txt").exists())

    def test_risky_kinds_only_once_and_forbidden_never(self):
        run_id = self.first_run()
        with self.assertRaises(CodeWorkError):
            self.runs.decide(run_id, self.blocked(run_id)["pip install requests"]["id"], "always")
        with self.assertRaises(CodeWorkError):
            self.runs.decide(run_id, self.blocked(run_id)["rm -rf /"]["id"], "once")
        self.assertEqual(self.runs.rules.get(str(self.repo.resolve())), [])
        self.assertEqual(self.runs.get(run_id)["status"], "review")                                 # nothing was restarted

    def test_decline_is_recorded_and_bad_requests_are_refused(self):
        run_id = self.first_run()
        item = self.blocked(run_id)["rm -rf /"]
        self.runs.decide(run_id, item["id"], "decline")
        self.assertEqual(self.runs.get(run_id)["approvals"][-1]["decision"], "declined")
        with self.assertRaises(CodeWorkError):
            self.runs.decide(run_id, item["id"], "once")                                            # already answered
        with self.assertRaises(CodeWorkError):
            self.runs.decide(run_id, 99, "once")
        with self.assertRaises(CodeWorkError):
            self.runs.decide(run_id, item["id"], "maybe")

    def test_report_lists_the_approvals(self):
        run_id = self.first_run()
        self.runs.decide(run_id, self.blocked(run_id)["npm run lint"]["id"], "once")
        self.wait(run_id)
        report = (self.runs._path(run_id) / "report.md").read_text(encoding="utf-8")
        self.assertIn("## 막힌 명령과 승인", report)
        self.assertIn("`npm run lint` — 이번만 허용", report)
        self.assertIn("`rm -rf /` — 결정 안 함", report)


if __name__ == "__main__":
    unittest.main()
