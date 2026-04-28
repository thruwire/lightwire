from __future__ import annotations

from typing import Any

import httpx

from app.config import RuntimeConfig
from app.memory.path_extractor import extract_memory_paths
from app.models import AgentConfig, ClaudeSessionRequest, ClaudeSessionResult, MCPServerAuthType, SessionStatus
from app.utils.ids import new_id


class ClaudeManagedAgentClient:
    def __init__(self, config: RuntimeConfig) -> None:
        self.config = config

    async def deploy_agent(self, agent: AgentConfig, system_prompt: str) -> dict[str, Any]:
        # Deployment compiles provider-neutral workspace config into Claude-specific agent payloads.
        payload = {
            "name": agent.display_name or agent.agent_id,
            "description": agent.description,
            "model": {"id": agent.model},
            "system": system_prompt,
            "metadata": {"agent_id": agent.agent_id, "skills": agent.skills},
            "mcp_servers": self._build_mcp_servers(agent),
            "tools": self._build_tools(agent),
        }
        if self.config.settings.thruflow_fake_claude:
            return {
                "agent_id": agent.agent_id,
                "provider": agent.provider,
                "status": "mocked",
                "prompt_length": len(system_prompt),
                "tools": payload["tools"],
                "mcp_servers": payload["mcp_servers"],
            }

        return await self._post("/managed-agents/agents", payload)

    async def create_or_update_agent(self, agent_id: str) -> dict[str, Any]:
        agent = self.config.get_agent(agent_id)
        return await self.deploy_agent(agent, self.config.get_agent_system_prompt(agent_id))

    async def create_session(self, request: ClaudeSessionRequest) -> ClaudeSessionResult:
        if self.config.settings.thruflow_fake_claude:
            return ClaudeSessionResult(
                external_session_id=new_id("claude_session"),
                status=SessionStatus.COMPLETED,
                content=self._mock_response(request),
                attached_memory_store_id=request.memory_store_id,
                raw={"mode": "mock", "vault_ids": request.vault_ids},
            )

        payload = {
            "agent_id": request.agent_id,
            "system_prompt": request.system_prompt,
            "input": request.task_prompt,
            "environment_id": self.config.get_provider_environment_id(),
            "vault_ids": request.vault_ids,
            "attachments": [
                {
                    "type": "memory_store",
                    "memory_store_id": request.memory_store_id,
                    "access": request.memory_access,
                }
            ],
            # Session metadata mirrors ThruFlow correlation state so provider logs can be traced back locally.
            "metadata": request.metadata | {"correlation_id": request.correlation_id, "agent_id": request.agent_id},
        }
        data = await self._post("/managed-agents/sessions", payload)
        return ClaudeSessionResult(
            external_session_id=str(data["id"]),
            status=SessionStatus(str(data.get("status", SessionStatus.COMPLETED.value))),
            content=str(data.get("output_text", "")),
            attached_memory_store_id=request.memory_store_id,
            raw=data,
        )

    async def create_environment(self, name: str) -> str:
        if self.config.settings.thruflow_fake_claude:
            return new_id("claude_env")

        data = await self._post(
            "/managed-agents/environments",
            {
                "name": name,
                "metadata": {"workspace": self.config.workspace_path.name},
                "config": {
                    "type": "cloud",
                    "networking": {
                        "type": "limited",
                        "allow_mcp_servers": True,
                        "allow_package_managers": True,
                        "allowed_hosts": ["https://api.anthropic.com"],
                    },
                },
            },
        )
        return str(data["id"])

    async def create_memory_store(self, name: str) -> str:
        if self.config.settings.thruflow_fake_claude:
            return new_id("memory_store")

        data = await self._post(
            "/memory-stores",
            {"name": name, "metadata": {"workspace": self.config.workspace_path.name}},
        )
        return str(data["id"])

    async def create_vault(self, display_name: str, metadata: dict[str, Any]) -> str:
        if self.config.settings.thruflow_fake_claude:
            return new_id("vault")

        data = await self._post("/managed-agents/vaults", {"display_name": display_name, "metadata": metadata})
        return str(data["id"])

    async def create_or_update_vault_credential(
        self,
        vault_id: str,
        display_name: str,
        metadata: dict[str, Any],
        auth: dict[str, Any],
    ) -> str:
        if self.config.settings.thruflow_fake_claude:
            return new_id("credential")

        data = await self._post(
            f"/managed-agents/vaults/{vault_id}/credentials",
            {"display_name": display_name, "metadata": metadata, "auth": auth},
        )
        return str(data["id"])

    async def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        headers = {
            "x-api-key": self.config.settings.anthropic_api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        async with httpx.AsyncClient(base_url=self.config.settings.anthropic_base_url, timeout=60.0) as client:
            response = await client.post(path, json=payload, headers=headers)
            response.raise_for_status()
            return response.json()

    def _build_mcp_servers(self, agent: AgentConfig) -> list[dict[str, Any]]:
        servers: list[dict[str, Any]] = []
        for server_name in sorted(agent.tools.mcp):
            server = self.config.tools.mcp_servers.get(server_name)
            if not server or not server.enabled:
                continue
            # Workspace tool config defines the connection details; agent config only chooses which servers to activate.
            servers.append({"name": server_name, "type": server.type, "url": server.url})
        return servers

    def _build_tools(self, agent: AgentConfig) -> list[dict[str, Any]]:
        tools: list[dict[str, Any]] = []
        built_in_configs = [
            {
                "name": name,
                "enabled": True,
                "permission_policy": {"type": self.config.tools.built_in.get(name).permission_policy.value},
            }
            for name in agent.tools.built_in
            if name in self.config.tools.built_in
        ]
        tools.append(
            {
                "type": "agent_toolset_20260401",
                "default_config": {"enabled": False, "permission_policy": {"type": "always_allow"}},
                "configs": built_in_configs,
            }
        )
        for server_name, activation in sorted(agent.tools.mcp.items()):
            server = self.config.tools.mcp_servers.get(server_name)
            if not server or not server.enabled:
                continue
            # MCP toolsets default closed and then opt specific tools in, which keeps agent permissions narrow.
            tools.append(
                {
                    "type": "mcp_toolset",
                    "mcp_server_name": server_name,
                    "default_config": {"enabled": False, "permission_policy": {"type": server.permission_policy.value}},
                    "configs": [
                        {
                            "name": tool_name,
                            "enabled": True,
                            "permission_policy": {"type": server.permission_policy.value},
                        }
                        for tool_name in activation.allow
                    ],
                }
            )
        return tools

    def _mock_response(self, request: ClaudeSessionRequest) -> str:
        text = request.task_prompt
        mentioned_paths = extract_memory_paths(text)
        artifact_candidates = [path for path in mentioned_paths if "/artifacts/" in path]
        handoff_candidates = [path for path in mentioned_paths if "/handoffs/" in path]
        artifact_path = artifact_candidates[-1] if artifact_candidates else request.memory_store_id
        handoff_path = handoff_candidates[-1] if handoff_candidates else None
        if request.agent_id == "researcher":
            response = (
                "Research complete. I reviewed the topic, noted the main benefits and risks, "
                f"and wrote the research artifact to {artifact_path}."
            )
            if handoff_path:
                response += f" I also wrote a concise handoff note to {handoff_path}."
            return response
        if request.agent_id == "analyst":
            response = (
                "Analysis complete. I evaluated the tradeoffs, uncertainty, and operational risks, "
                f"and wrote the analysis artifact to {artifact_path}."
            )
            if handoff_path:
                response += f" I also wrote a handoff note to {handoff_path}."
            return response
        if request.agent_id == "brief_writer":
            response = (
                "Executive brief complete. I wrote the final brief to "
                f"{artifact_path} with the main recommendation and open questions."
            )
            if handoff_path:
                response += f" I also wrote a handoff note to {handoff_path}."
            return response
        return f"Completed the task and wrote durable output to {artifact_path}."
