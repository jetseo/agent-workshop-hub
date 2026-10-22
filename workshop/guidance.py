"""Work guidance appended to a code-work request: how a careful engineer approaches the job.

The ideas come from widely used agent workflows (root cause before fixing, test first and watch it fail, no success
claim without a fresh run, stay in scope), written here in JetAI's own words; see docs/skills-research.md.
Weak models follow long instructions badly, so the text has two sizes. Whether it helps is meant to be measured with
the evaluation harness, not assumed.
"""

SHORT = ("작업 지침:\n"
         "1. 고치기 전에 관련 파일과 오류 내용을 먼저 읽으세요.\n"
         "2. 요청한 것만 바꾸고, 관계없는 파일은 건드리지 마세요.\n"
         "3. 끝나기 전에 {tests}을(를) 실행해 결과를 확인하고, 바꾼 것과 확인한 것을 짧게 보고하세요.")

FULL = ("작업 지침:\n"
        "1. 먼저 읽기: 수정 전에 관련 파일, 오류 메시지, 최근 변경을 읽고 원인이나 들어갈 위치를 파악하세요. 증상만 가리는 수정은 하지 마세요.\n"
        "2. 테스트 먼저: 버그 수정이나 새 기능에는 먼저 실패하는 작은 테스트를 추가하거나 기존 테스트로 실패를 확인한 뒤, "
        "통과할 최소한의 코드를 쓰세요. 이미 통과하는 테스트는 아무것도 증명하지 못합니다.\n"
        "3. 범위 지키기: 요청한 것만 바꾸고, 관계없는 파일·정리·큰 리팩터는 하지 마세요. 기존 코드의 이름과 방식을 따르세요.\n"
        "4. 증거 후 보고: 끝났다고 말하기 전에 {tests}을(를) 실제로 실행하고 출력을 읽으세요. 실행하지 않았다면 실행하지 못했다고 쓰세요. "
        "'아마 될 것'은 보고가 아닙니다.\n"
        "5. 마지막에 바꾼 파일, 실행한 명령, 그 결과를 짧게 정리하세요. 막힌 명령이 있으면 어떤 명령이었는지 적으세요.")


def tests_phrase(commands):
    found = [c["command"] for c in (commands or []) if c.get("kind") == "test"] or [c["command"] for c in (commands or []) if c.get("kind") == "build"]
    return "`%s`" % found[0] if found else "프로젝트의 테스트 명령(찾아서)"


def build(level, commands=None):
    """-> (text, size) where size is "short" or "full"."""
    size = "full" if level >= 2 else "short"
    return (FULL if size == "full" else SHORT).format(tests=tests_phrase(commands)), size
