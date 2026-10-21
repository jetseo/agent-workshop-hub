"""Arbiter: look at a question (words and size) and decide what kind of work it is and how demanding.

type  : "code"     programming: write/fix/explain code, errors, files, commands
        "research" deep, many-part, evidence-style work: compare, analyse, survey, report, long documents
        "chat"     everyday questions, short explanations, translation, writing help
level : 1 light, 2 standard, 3 heavy (how capable a model the answer deserves)
The router then matches this need to model capability (modelpick.capability). Heuristics on words and size
only: no model is called, so deciding costs nothing. A short follow-up ("why?", "고쳐줘") inherits the kind of
the conversation instead of being judged alone.
"""
import re

CODE_WORDS = re.compile(
    r"\b(def|class|import|function|const|let|var|return|npm|pip|git|docker|sql|regex|api|json|html|css|python|java(?:script)?|"
    r"typescript|rust|golang|c\+\+|c#|bash|powershell|compile|debug|bug|stack ?trace|traceback|exception|error|unit test|refactor|"
    r"endpoint|database|server|script|library|package)\b|코드|함수|클래스|버그|에러|오류|예외|컴파일|디버그|리팩터|스크립트|프로그램|"
    r"파이썬|자바|테스트 코드|쿼리|명령어|라이브러리|패키지|깃|도커|구현해|짜줘|작성해줘.*(코드|함수|스크립트)", re.I)
CODE_SHAPES = re.compile(r"```|^\s*(def |class |import |from \w+ import|function |const |#include|public |SELECT |<\w+>)|"
                         r"\.(py|js|ts|tsx|java|cs|cpp|go|rs|sh|ps1|sql|json|yml|yaml)\b|Traceback|\bat \w+\.\w+\(|\w+\(\)\s*[{:]", re.I | re.M)
RESEARCH_WORDS = re.compile(
    r"\b(research|compare|comparison|analy[sz]e|analysis|survey|literature|review|report|pros and cons|trade-?offs?|in[- ]depth|"
    r"comprehensive|evidence|sources?|citations?|market|strategy|whitepaper|deep dive|state of the art|benchmark)\b|"
    r"조사|리서치|비교|분석|보고서|논문|근거|출처|심층|장단점|시장|전략|동향|현황|종합|검토|타당성|백서", re.I)
HARD_WORDS = re.compile(r"\b(architecture|concurrency|race condition|distributed|security|migrate|optimi[sz]e|scal(?:e|ability)|"
                        r"algorithm|proof|derive)\b|아키텍처|동시성|분산|보안|마이그레이션|최적화|확장성|알고리즘|증명", re.I)
LIGHT_WORDS = re.compile(r"\b(translate|spelling|synonym|what is|who is|define|meaning of|hello|thanks)\b|번역|맞춤법|뜻이|의미가|"
                         r"무엇이야|뭐야|누구|안녕|고마워|추천해|한 줄|한줄|간단히|짧게", re.I)
FOLLOW_UP = re.compile(r"^\s*(그럼|그러면|그리고|그래서|그거|이거|그건|이건|계속|더 |다시|아니|왜|어떻게|그 다음|and |then |why|how come|continue|more|again|what about|also)", re.I)
PARTS = re.compile(r"(?:^|\n)\s*(?:\d+[.)]|[-*•]|[①-⑩])\s+")

CHAT, CODE, RESEARCH = "chat", "code", "research"
LABELS = {CHAT: "일상 질문", CODE: "코드", RESEARCH: "심층 조사·분석"}


def classify(text, previous_type=None):
    """-> {"type", "level", "confidence", "reasons"}. `previous_type` lets a vague follow-up keep its context."""
    text = text if isinstance(text, str) else ""
    n = len(text)
    reasons, score = [], {CODE: 0.0, RESEARCH: 0.0, CHAT: 0.3}

    code_hits = len(CODE_WORDS.findall(text))
    shape_hits = len(CODE_SHAPES.findall(text))
    if shape_hits:
        score[CODE] += 1.5 + min(shape_hits, 4) * 0.3
        reasons.append("코드 모양 " + str(shape_hits) + "곳")
    if code_hits:
        score[CODE] += min(code_hits, 5) * 0.5
        reasons.append("코드 관련 단어 " + str(code_hits) + "개")

    research_hits = len(RESEARCH_WORDS.findall(text))
    parts = len(PARTS.findall(text))
    if research_hits:
        score[RESEARCH] += min(research_hits, 4) * 0.7
        reasons.append("조사·분석 단어 " + str(research_hits) + "개")
    if parts >= 3:
        score[RESEARCH] += 0.8
        reasons.append("여러 항목 요청(" + str(parts) + "개)")
    if n > 1500 and not shape_hits:
        score[RESEARCH] += 0.8 if n < 6000 else 1.4
        reasons.append("긴 입력(" + str(n) + "자)")

    if LIGHT_WORDS.search(text) and n < 300:
        score[CHAT] += 0.8
        reasons.append("가벼운 질문")

    kind = max(score, key=score.get)
    top = score[kind]
    rest = sorted(score.values(), reverse=True)[1]
    confidence = round(min(1.0, 0.4 + (top - rest) / 3.0), 2)
    no_signal = max(score[CODE], score[RESEARCH]) < 0.5 and score[CHAT] <= 0.3        # nothing of its own to judge by
    if (no_signal and previous_type in (CHAT, CODE, RESEARCH) and n < 200
            and (n <= 12 or FOLLOW_UP.search(text))):                                  # and it reads like a follow-up
        kind, confidence = previous_type, 0.5
        reasons.append("짧은 후속 질문: 앞선 대화(" + LABELS[previous_type] + ")를 이어감")

    level = 1
    if n >= 400 or parts >= 2 or code_hits + shape_hits >= 2:
        level = 2
    if n >= 2500 or parts >= 4 or HARD_WORDS.search(text) or (kind == RESEARCH and research_hits >= 2):
        level = 3
    if kind == RESEARCH:
        level = max(level, 2)                          # research is never "light"
    if kind == CHAT and LIGHT_WORDS.search(text) and n < 300:
        level = 1
    if HARD_WORDS.search(text):
        reasons.append("어려운 주제 단어")
    return {"type": kind, "level": level, "confidence": confidence, "reasons": reasons}


def explain(decision, model=None):
    """One short Korean sentence for the screen."""
    text = LABELS[decision["type"]] + " · 난이도 " + ["", "가벼움", "보통", "높음"][decision["level"]]
    return text + (" → " + model if model else "")
