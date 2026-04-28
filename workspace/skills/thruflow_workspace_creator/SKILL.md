Use this skill when creating or expanding a ThruFlow workspace for Claude-backed managed agents.

Requirements:
- Create agents under `workspace/agents/<agent_id>/` with `config.yaml` and `AGENT.md`.
- Create reusable skills under `workspace/skills/<skill_id>/` with `config.yaml` and `SKILL.md`.
- Put route-specific prompts under `workspace/prompt_templates/*.md`.
- Keep `routes.yaml`, `heartbeats.yaml`, and `slack.yaml` at the workspace root.
- Use provider-managed memory paths under `/mnt/memory`.
- Keep templates small, task-specific, and reusable.
