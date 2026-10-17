# Agent Workshop Hub

A unified local-first AI development workspace for coding agents, image generation, and repeatable automated workflows.

## Goals

- Run OpenCode as the primary coding agent, with optional Codex CLI and Claude Code integrations.
- Connect local models via Ollama and cloud models via configurable providers.
- Queue image-generation tasks through ComfyUI.
- Orchestrate tasks, approvals, retries, and checkpoints through LangGraph.
- Use Git worktrees to isolate concurrent coding agents.
- Provide a Windows + WSL2 bootstrap with non-destructive setup.

## Status

**Scaffolding / planning.** This repository does not yet provide a complete one-click installer. Avoid putting API keys or private code in tracked configuration files.

## Proposed architecture

`Web dashboard -> LangGraph orchestration -> OpenCode / Codex / Claude Code / ComfyUI -> Git worktrees + task state`

Model selection is a separate concern: local Ollama, free-tier providers, and optional paid APIs.

## Milestones

1. WSL2 prerequisites and environment checks
2. OpenCode + Ollama + ComfyUI integration
3. Durable job queue and approval checkpoints
4. Parallel Git worktree agents and automated testing
5. Dashboard, scheduling, recovery, and remote access

## Safety defaults

- No automatic paid-model calls without user-defined policy.
- No automatic merge to the default branch.
- Never overwrite existing Codex or Claude Code credentials.
- Bind local service endpoints to loopback by default.
- Keep secrets in untracked environment files or an OS credential store.
