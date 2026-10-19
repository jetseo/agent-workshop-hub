# Local-first task planner

This optional component generates a **proposal only**. It does not enqueue jobs, run
OpenCode, call paid APIs, create worktrees, or merge changes.

Start Ollama locally and install a suitable instruction-following model.

```bash
ollama pull qwen2.5-coder:7b
python -m workshop.plan_cli "Inspect tests and propose improvements" --model qwen2.5-coder:7b
python -m unittest discover -s tests -p 'test_planner.py'
```

Review the generated JSON before manually creating queue jobs. Dependencies are
zero-based and must point to earlier tasks. Never execute AI-generated text as shell
commands. Planning output is untrusted; human review is required.

Cloud/free model routing, parallel worktree execution, automatic merge and budget
enforcement are **not** included. Existing worker approval requirements remain intact.
