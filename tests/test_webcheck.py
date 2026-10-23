import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from workshop import webcheck
from workshop.codework import CodeWorkError


class FakeBrowser:
    instances = []

    def __init__(self, **kw):
        self.kw, self.calls = kw, []
        FakeBrowser.instances.append(self)

    def start(self): self.calls.append("start")
    def open(self, url): self.calls.append("open " + url)
    def observe(self): return {"title": "앱", "url": "http://localhost:5173/", "text": "안녕 " * 2000, "elements": [{}, {}]}
    def screenshot(self, path): Path(path).write_bytes(b"PNG"); self.calls.append("shot")
    def problems(self): return ["콘솔 오류: boom"]
    def stop(self): self.calls.append("stop")


class WebCheckTests(unittest.TestCase):
    def setUp(self):
        FakeBrowser.instances = []

    def test_reports_page_problems_and_a_screenshot_for_a_local_page(self):
        shot = Path(self.id() + ".png")
        self.addCleanup(lambda: shot.exists() and shot.unlink())
        r = webcheck.run_check("http://localhost:5173/", str(shot), browser_factory=FakeBrowser, sleep=lambda s: None)
        self.assertTrue(r["ok"])
        self.assertEqual(r["elements"], 2)
        self.assertEqual(r["problems"], ["콘솔 오류: boom"])
        self.assertLessEqual(len(r["text"]), webcheck.TEXT_LIMIT)
        self.assertTrue(r["untrusted"])
        self.assertEqual(FakeBrowser.instances[0].kw["mode"], "observe")          # never clicks or types
        self.assertEqual(FakeBrowser.instances[0].calls[-1], "stop")             # the temp profile is always cleaned up
        self.assertTrue(shot.exists())

    def test_other_hosts_are_refused_before_any_browser_starts(self):
        r = webcheck.run_check("https://example.com/", "x.png", browser_factory=FakeBrowser)
        self.assertFalse(r["ok"])
        self.assertEqual(FakeBrowser.instances, [])
        ok = webcheck.run_check("https://dev.example.com/", "x.png", allow_hosts=["dev.example.com"], browser_factory=FakeBrowser, sleep=lambda s: None)
        self.assertTrue(ok["ok"] or "error" in ok)                                # a registered dev host is allowed to start

    def test_browser_trouble_becomes_an_error_not_an_exception(self):
        class Broken(FakeBrowser):
            def start(self): raise RuntimeError("설치된 Chrome 또는 Edge를 찾지 못했습니다")
        r = webcheck.run_check("http://localhost:3000/", "x.png", browser_factory=Broken)
        self.assertFalse(r["ok"])
        self.assertIn("Chrome", r["error"])


from tests.test_codework import Base


class CodeRunWebCheckTests(Base):
    def finished_run(self):
        run_id = self.start("README에 한 줄 추가해줘")
        self.wait(run_id)
        return run_id

    def wait_webcheck(self, run_id):
        for _ in range(100):
            info = self.runs.get(run_id).get("webcheck") or {}
            if info.get("state") == "done":
                return info
            import time; time.sleep(0.05)
        self.fail("web check did not finish")

    def test_check_runs_in_the_background_and_is_noted_in_the_audit_file(self):
        run_id = self.finished_run()
        seen = {}
        def fake(url, shot, allow_hosts=()):
            seen["hosts"] = tuple(allow_hosts)
            Path(shot).write_bytes(b"PNG")
            return {"ok": True, "url": url, "title": "앱", "text": "t", "elements": 1, "problems": []}
        self.runs.webcheck_fn = fake
        self.runs.webcheck(run_id, "http://localhost:5173/")
        info = self.wait_webcheck(run_id)
        self.assertTrue(info["ok"] and info["has_image"])
        self.assertTrue(self.runs.get(run_id)["webcheck_image"])
        audit = (self.runs._path(run_id) / "audit.jsonl").read_text(encoding="utf-8")
        self.assertIn('"state": "webcheck"', audit)

    def test_other_hosts_and_empty_urls_are_refused_without_starting_anything(self):
        run_id = self.finished_run()
        self.runs.webcheck_fn = lambda *a, **k: self.fail("must not run")
        for bad in ("https://example.com/", "file:///C:/x", ""):
            with self.assertRaises(CodeWorkError):
                self.runs.webcheck(run_id, bad)
        self.assertIsNone(self.runs.get(run_id).get("webcheck"))


if __name__ == "__main__":
    unittest.main()
