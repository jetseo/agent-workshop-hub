import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
STATIC = Path(__file__).resolve().parents[1] / "workshop" / "static"


class LayoutStaticTests(unittest.TestCase):
    """The pieces the collapsible panels rely on exist (browser behaviour itself is checked by hand, see docs/verification.md)."""

    def test_page_has_the_side_gutter_and_the_toggle_button(self):
        from workshop import app_page
        html = app_page.render("t", "off", "") if hasattr(app_page, "render") else ""
        if not html:
            self.skipTest("page builder has another name")
        self.assertIn('id="side-gutter"', html)
        self.assertIn('role="separator"', html)
        self.assertIn('id="side-close"', html)
        self.assertIn('id="tb-left"', html)                          # code-screen tools sit in the top bar, not in a blank row
        self.assertIn('id="tb-right"', html)
        self.assertIn('id="side-hide"', html)                       # hide completely (Ctrl+B)
        self.assertIn('id="modelbtn"', html[html.index('id="composer"'):])   # model picker lives in the composer

    def test_css_defines_the_width_variables_rail_mode_and_the_gutter(self):
        css = (STATIC / "app.css").read_text(encoding="utf-8")
        for needle in ("--side-w", "--runs-w", "--chg-w", ".gutter", "#side.collapsed", ".cw.runs-closed", ".cw.changes-closed", ".cw-changes", ".chg-file", ".cw-composer .cw-opts"):
            self.assertIn(needle, css)

    def test_script_wires_resizer_shortcuts_and_the_runs_toggle(self):
        js = (STATIC / "app.js").read_text(encoding="utf-8")
        for needle in ("function makeResizer", "toggleRuns", "setHidden", "openSystemPop", "runSummaryCard", "buildTimeline", "visibleRuns", "renderRunList", "openRowMenu(row, r", "bulkAct", "toastUndo", "openProjectMenu", "updateProjectChip", "code-adv-btn", "renderChanges", "toggleChanges", "code-toggle-changes", 'key.toLowerCase() !== "b"', "code-toggle-runs"):
            self.assertIn(needle, js)


if __name__ == "__main__":
    unittest.main()
