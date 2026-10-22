import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "workshop"
JS = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
API = (ROOT / "chat_api.py").read_text(encoding="utf-8")


class CodeScreenStaticTests(unittest.TestCase):
    def test_page_has_the_single_mount_point_for_the_code_screen(self):
        html = Path(ROOT / "app_page.py").read_text(encoding="utf-8")
        self.assertIn('id="code-root"', html)
        self.assertNotIn('id="code-form"', html)                                  # the old form/list/detail split is gone

    def test_every_action_the_screen_posts_is_handled_by_the_api(self):
        actions = set(re.findall(r'path: "(\w+)"', JS))
        self.assertTrue({"apply", "undo", "check", "review", "cancel", "discard"} <= actions, actions)
        for action in actions:
            self.assertIn('action == "%s"' % action, API, action)
        self.assertIn('"/command"', JS)                                           # blocked-command answers
        self.assertIn('action == "command"', API)

    def test_every_slash_command_has_an_action_or_is_new(self):
        names = re.findall(r'\{ name: "(\w+)", alias:', JS)
        self.assertGreaterEqual(len(names), 7)
        actions = set(re.findall(r'path: "(\w+)"', JS))
        for name in names:
            self.assertTrue(name == "new" or name in actions, name)


if __name__ == "__main__":
    unittest.main()
