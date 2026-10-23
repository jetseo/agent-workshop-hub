"""One-shot "look at this page" for the code screen: open a local page in a throw-away headless browser, read what
a person would see, and report console errors and failed requests with a screenshot.

Observe only: nothing is clicked or typed, only localhost (plus the project's own dev server host) opens, and the
page text is returned as untrusted data. The browser runs through `cdp.Browser` (installed Chrome/Edge, temporary
profile, no download).
"""
import time

from . import cdp

TEXT_LIMIT = 1500
PROBLEM_LIMIT = 20


def run_check(url, shot_path, *, allow_hosts=(), browser_factory=cdp.Browser, settle=1.0, sleep=time.sleep):
    """-> {"ok", "url", "title", "text", "elements", "problems", "error"}; never raises."""
    if not cdp.host_allowed(url, allow_hosts):
        return {"ok": False, "url": url, "error": "허용되지 않은 주소입니다. 이 PC의 개발 서버(localhost)나 프로젝트에 등록한 개발 서버 주소만 열 수 있습니다."}
    browser = browser_factory(mode="observe", allow_hosts=tuple(allow_hosts))
    try:
        browser.start()
        browser.open(url)
        sleep(settle)                                         # let scripts run so their errors show up
        page = browser.observe()
        browser.screenshot(shot_path)
        problems = browser.problems()[:PROBLEM_LIMIT]
        return {"ok": True, "url": page.get("url") or url, "title": str(page.get("title", ""))[:120],
                "text": str(page.get("text", ""))[:TEXT_LIMIT], "elements": len(page.get("elements") or []), "problems": problems, "untrusted": True}
    except Exception as error:                                # no browser, page not reachable, protocol trouble: say so plainly
        return {"ok": False, "url": url, "error": str(error)[:200]}
    finally:
        try:
            browser.stop()
        except Exception:
            pass
