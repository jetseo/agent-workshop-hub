"""Classify OpenCode model ids by cost so paid usage is never automatic.

free   : provider/name-free (as listed by `opencode models`)
local  : ollama/... or local/... (runs on this PC)
paid   : everything else, including subscription-backed OAuth models (openai/...) and
         pay-as-you-go gateway models (opencode/gpt-..., opencode/claude-...)
"""


def classify(model):
    provider, _, name = (model or "").partition("/")
    if provider in ("ollama", "local"):
        return "local"
    if name.endswith("-free"):
        return "free"
    return "paid"


def parse_models_output(text):
    """Group `opencode models` output lines into free / local / paid lists."""
    groups = {"free": [], "local": [], "paid": []}
    for line in text.splitlines():
        line = line.strip()
        if "/" in line and " " not in line:
            groups[classify(line)].append(line)
    return groups
