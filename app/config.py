from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.models import (
    AgentConfig,
    AgentMemoryConfig,
    HeartbeatsFile,
    RoutesFile,
    SkillConfig,
    SlackConfig,
    TelegramConfig,
    ToolsConfig,
)


_ENV_VAR_PATTERN = re.compile(r"\$\{([^}:]+)(?::-(.*?))?\}")


class Settings(BaseSettings):
    # Connector and provider credentials stay in env so the checked-in workspace can remain reusable.
    anthropic_api_key: str = ""
    slack_bot_token: str = ""
    slack_app_token: str = ""
    telegram_bot_token: str = ""
    sqlite_path: str = "./data/thruflow.db"
    workspace_path: str = "./workspace"
    thruflow_fake_claude: bool = True
    anthropic_base_url: str = "https://api.anthropic.com/v1"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


class RuntimeConfig(BaseModel):
    # RuntimeConfig is the in-memory view of the whole workspace plus process-level settings.
    settings: Settings
    agents: dict[str, AgentConfig]
    skills: dict[str, SkillConfig]
    tools: ToolsConfig
    routes: RoutesFile
    heartbeats: HeartbeatsFile
    slack: SlackConfig
    telegram: TelegramConfig
    workspace_path: Path
    prompt_templates: dict[str, str] = Field(default_factory=dict)
    provider_environment_id: str | None = None
    provider_memory_store_id: str | None = None

    def get_agent(self, agent_id: str) -> AgentConfig:
        return self.agents[agent_id]

    def get_agent_system_prompt(self, agent_id: str) -> str:
        agent = self.get_agent(agent_id)
        parts = [agent.instructions.strip()]
        for skill_id in agent.skills:
            skill = self.skills.get(skill_id)
            if skill and skill.enabled:
                # Skills are appended to the stable AGENT.md instructions so route prompts can stay task-specific.
                parts.append(f"Skill: {skill.skill_id}\n{skill.instructions.strip()}")
        return "\n\n".join(part for part in parts if part)

    def get_prompt_template_text(self, template_ref: str) -> str:
        template_path = self.workspace_path / template_ref
        template_key = str(template_path.relative_to(self.workspace_path))
        if template_key not in self.prompt_templates:
            raise FileNotFoundError(
                f"Prompt template '{template_ref}' was not found under workspace '{self.workspace_path}'."
            )
        return self.prompt_templates[template_key]

    def attach_provider_state(self, environment_id: str, memory_store_id: str) -> None:
        self.provider_environment_id = environment_id
        self.provider_memory_store_id = memory_store_id

    def get_provider_environment_id(self) -> str:
        if not self.provider_environment_id:
            raise RuntimeError("Provider environment ID has not been initialized.")
        return self.provider_environment_id

    def get_provider_memory_store_id(self) -> str:
        if not self.provider_memory_store_id:
            raise RuntimeError("Provider memory store ID has not been initialized.")
        return self.provider_memory_store_id


def _expand_env_vars(value: Any) -> Any:
    if isinstance(value, str):
        return _ENV_VAR_PATTERN.sub(
            lambda match: os.environ.get(match.group(1), match.group(2) or ""),
            value,
        )
    if isinstance(value, list):
        return [_expand_env_vars(item) for item in value]
    if isinstance(value, dict):
        return {key: _expand_env_vars(item) for key, item in value.items()}
    return value


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return _expand_env_vars(data or {})


def _load_agents(settings: Settings, workspace_path: Path) -> dict[str, AgentConfig]:
    agents_dir = workspace_path / "agents"
    agents: dict[str, AgentConfig] = {}
    for config_path in sorted(agents_dir.glob("*/config.yaml")):
        agent_dir = config_path.parent
        data = _load_yaml(config_path)
        # Folder names are stable defaults so moving files around does not force explicit IDs everywhere.
        agent_id = data.get("agent_id") or agent_dir.name
        instruction_path = agent_dir / "AGENT.md"
        instructions = instruction_path.read_text(encoding="utf-8")
        memory_data = data.get("memory") or {}
        agent = AgentConfig.model_validate(
            {
                **data,
                "agent_id": agent_id,
                "memory": AgentMemoryConfig.model_validate(memory_data),
                "instruction_path": instruction_path,
                "instructions": instructions,
                "config_path": config_path,
            }
        )
        agents[agent_id] = agent
    return agents


def _load_skills(workspace_path: Path) -> dict[str, SkillConfig]:
    skills_dir = workspace_path / "skills"
    skills: dict[str, SkillConfig] = {}
    for config_path in sorted(skills_dir.glob("*/config.yaml")):
        skill_dir = config_path.parent
        data = _load_yaml(config_path)
        # Skills follow the same convention as agents: directory name first, config override if needed.
        skill_id = data.get("skill_id") or skill_dir.name
        instruction_path = skill_dir / "SKILL.md"
        instructions = instruction_path.read_text(encoding="utf-8")
        skill = SkillConfig.model_validate(
            {
                **data,
                "skill_id": skill_id,
                "instruction_path": instruction_path,
                "instructions": instructions,
                "config_path": config_path,
            }
        )
        skills[skill_id] = skill
    return skills


def _load_prompt_templates(workspace_path: Path) -> dict[str, str]:
    templates_dir = workspace_path / "prompt_templates"
    templates: dict[str, str] = {}
    for template_path in sorted(templates_dir.glob("*.md")):
        # Templates are keyed by workspace-relative path so routes can refer to them directly.
        template_key = str(template_path.relative_to(workspace_path))
        templates[template_key] = template_path.read_text(encoding="utf-8")
    return templates


def load_runtime_config(settings: Settings | None = None) -> RuntimeConfig:
    settings = settings or Settings()
    workspace_path = Path(settings.workspace_path).resolve()

    agents = _load_agents(settings, workspace_path)
    skills = _load_skills(workspace_path)
    tools = ToolsConfig.model_validate(_load_yaml(workspace_path / "tools.yaml"))
    prompt_templates = _load_prompt_templates(workspace_path)
    routes = RoutesFile.model_validate(_load_yaml(workspace_path / "routes.yaml"))
    heartbeats = HeartbeatsFile.model_validate(_load_yaml(workspace_path / "heartbeats.yaml"))
    slack = SlackConfig.model_validate(_load_yaml(workspace_path / "slack.yaml"))
    telegram = TelegramConfig.model_validate(_load_yaml(workspace_path / "telegram.yaml"))

    return RuntimeConfig(
        settings=settings,
        agents=agents,
        skills=skills,
        tools=tools,
        routes=routes,
        heartbeats=heartbeats,
        slack=slack,
        telegram=telegram,
        workspace_path=workspace_path,
        prompt_templates=prompt_templates,
    )
