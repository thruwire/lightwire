---
name: lightwire-workspace-creator
description: Use when creating or expanding a LightWire workspace, including agent folders, skill files, templates, routes, and connector configs.
---

Use this skill when creating or expanding a LightWire workspace for Claude-backed managed agents.

Requirements:
- Create agents under `workspace/agents/<agent_id>/` with `config.yaml` and `AGENT.md`.
- Create reusable skills under `workspace/skills/<skill_id>/` with `SKILL.md` frontmatter and Markdown instructions.
- Put route-specific prompts under `workspace/prompt_templates/*.md`.
- Keep `routes.yaml`, `heartbeats.yaml`, and `slack.yaml` at the workspace root.
- Use direct routed outputs between agents instead of provider-specific filesystem paths.
- Keep templates small, task-specific, and reusable.
