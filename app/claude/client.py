from __future__ import annotations

import asyncio
import json
import logging
import shutil
from typing import Any

from app.config import RuntimeConfig
from app.models import AgentConfig, ClaudeSessionRequest, ClaudeSessionResult, MCPServerAuthType, SessionStatus
from app.utils.ids import new_id


logger = logging.getLogger(__name__)


class ClaudeManagedAgentClient:
    def __init__(self, config: RuntimeConfig) -> None:
        self.config = config

    async def deploy_agent(
        self,
        agent: AgentConfig,
        system_prompt: str,
        *,
        existing_agent_id: str | None = None,
    ) -> dict[str, Any]:
        payload = self._build_agent_payload(agent, system_prompt)
        if self.config.settings.thruflow_fake_claude:
            return {
                "id": agent.agent_id,
                "name": agent.display_name or agent.agent_id,
                "provider": agent.provider,
                "status": "mocked",
                "version": 1,
                "tools": payload["tools"],
                "mcp_servers": payload["mcp_servers"],
            }

        await self._require_ant()
        # When deploy already knows the provider agent ID from SQLite, update that
        # exact remote object directly. This avoids list-based rediscovery drift.
        if existing_agent_id and await self.agent_exists(existing_agent_id):
            existing = await self._run_ant_json(
                ["beta:agents", "retrieve", "--agent-id", existing_agent_id, "--format", "json"]
            )
            update_payload = dict(payload)
            if "version" in existing:
                update_payload["version"] = existing["version"]
            return await self._run_ant_json(
                ["beta:agents", "update", "--agent-id", existing_agent_id, "--format", "json"],
                update_payload,
            )
        existing = await self._find_named_resource(
            ["beta:agents", "list", "--limit", "100", "--format", "json"],
            metadata_slug=agent.agent_id,
            name=agent.display_name or agent.agent_id,
            archive_duplicates=("beta:agents", "--agent-id"),
        )
        if existing is None:
            return await self._run_ant_json(["beta:agents", "create", "--format", "json"], payload)
        agent_id = self._extract_id(existing)
        update_payload = dict(payload)
        if "version" in existing:
            update_payload["version"] = existing["version"]
        return await self._run_ant_json(
            ["beta:agents", "update", "--agent-id", agent_id, "--format", "json"],
            update_payload,
        )

    async def create_or_update_agent(self, agent_id: str) -> dict[str, Any]:
        agent = self.config.get_agent(agent_id)
        return await self.deploy_agent(agent, self.config.get_agent_system_prompt(agent_id))

    async def agent_exists(self, agent_id: str) -> bool:
        return await self._resource_exists(["beta:agents", "retrieve", "--agent-id", agent_id, "--format", "json"])

    async def list_managed_agents(self) -> list[dict[str, Any]]:
        return await self._list_managed_resources(["beta:agents", "list", "--limit", "100", "--max-items", "-1", "--format", "json"])

    async def list_managed_vaults(self) -> list[dict[str, Any]]:
        return await self._list_managed_resources(["beta:vaults", "list", "--limit", "100", "--max-items", "-1", "--format", "json"])

    async def list_vault_credentials(self, vault_id: str) -> list[dict[str, Any]]:
        if self.config.settings.thruflow_fake_claude:
            return []
        await self._require_ant()
        payload = await self._run_ant_json(
            ["beta:vaults:credentials", "list", "--vault-id", vault_id, "--limit", "100", "--max-items", "-1", "--format", "json"]
        )
        return self._list_items(payload)

    async def archive_agent(self, agent_id: str) -> None:
        await self._archive_resource(["beta:agents", "archive", "--agent-id", agent_id, "--format", "json"])

    async def archive_vault(self, vault_id: str) -> None:
        await self._archive_resource(["beta:vaults", "archive", "--vault-id", vault_id, "--format", "json"])

    async def archive_vault_credential(self, vault_id: str, credential_id: str) -> None:
        await self._archive_resource(
            ["beta:vaults:credentials", "archive", "--vault-id", vault_id, "--credential-id", credential_id, "--format", "json"]
        )

    async def environment_exists(self, environment_id: str) -> bool:
        return await self._resource_exists(["beta:environments", "retrieve", "--environment-id", environment_id, "--format", "json"])

    async def memory_store_exists(self, memory_store_id: str) -> bool:
        return await self._resource_exists(["beta:memory-stores", "retrieve", "--memory-store-id", memory_store_id, "--format", "json"])

    async def vault_exists(self, vault_id: str) -> bool:
        return await self._resource_exists(["beta:vaults", "retrieve", "--vault-id", vault_id, "--format", "json"])

    async def vault_credential_exists(self, vault_id: str, credential_id: str) -> bool:
        return await self._resource_exists(
            ["beta:vaults:credentials", "retrieve", "--vault-id", vault_id, "--credential-id", credential_id, "--format", "json"]
        )

    async def create_session(self, request: ClaudeSessionRequest) -> ClaudeSessionResult:
        if self.config.settings.thruflow_fake_claude:
            return ClaudeSessionResult(
                external_session_id=new_id("claude_session"),
                status=SessionStatus.COMPLETED,
                content=self._mock_response(request),
                attached_memory_store_id=request.memory_store_id,
                raw={"mode": "mock", "vault_ids": request.vault_ids},
            )

        session_id, content, status, raw = await asyncio.to_thread(self._run_sdk_session, request)
        return ClaudeSessionResult(
            external_session_id=session_id,
            status=status,
            content=content,
            attached_memory_store_id=request.memory_store_id,
            raw=raw,
        )

    async def create_environment(self, name: str) -> str:
        if self.config.settings.thruflow_fake_claude:
            return new_id("claude_env")
        await self._require_ant()
        payload = {
            "name": name,
            "metadata": {
                "managed_agents_repo": "thruflow",
                "managed_agents_kind": "environment",
                "managed_agents_slug": "default",
                "workspace": self.config.workspace_path.name,
            },
            "config": {
                "type": "cloud",
                "networking": {
                    "type": "limited",
                    "allow_mcp_servers": True,
                    "allow_package_managers": True,
                    "allowed_hosts": ["api.anthropic.com"],
                },
            },
        }
        existing = await self._find_named_resource(
            ["beta:environments", "list", "--limit", "100", "--format", "json"],
            metadata_slug="default",
            name=name,
            archive_duplicates=("beta:environments", "--environment-id"),
        )
        if existing is None:
            data = await self._run_ant_json(["beta:environments", "create", "--format", "json"], payload)
        else:
            data = await self._run_ant_json(
                ["beta:environments", "update", "--environment-id", self._extract_id(existing), "--format", "json"],
                payload,
            )
        return str(data["id"])

    async def create_memory_store(self, name: str) -> str:
        if self.config.settings.thruflow_fake_claude:
            return new_id("memory_store")
        await self._require_ant()
        payload = {
            "name": name,
            "description": f"Shared ThruFlow memory for workspace {self.config.workspace_path.name}.",
            "metadata": {
                "managed_agents_repo": "thruflow",
                "managed_agents_kind": "memory_store",
                "managed_agents_slug": "shared",
                "workspace": self.config.workspace_path.name,
            },
        }
        existing = await self._find_named_resource(
            ["beta:memory-stores", "list", "--limit", "100", "--format", "json"],
            metadata_slug="shared",
            name=name,
            archive_duplicates=("beta:memory-stores", "--memory-store-id"),
        )
        if existing is None:
            data = await self._run_ant_json(["beta:memory-stores", "create", "--format", "json"], payload)
        else:
            data = await self._run_ant_json(
                ["beta:memory-stores", "update", "--memory-store-id", self._extract_id(existing), "--format", "json"],
                payload,
            )
        return str(data["id"])

    async def create_vault(self, display_name: str, metadata: dict[str, Any]) -> str:
        if self.config.settings.thruflow_fake_claude:
            return new_id("vault")
        await self._require_ant()
        payload = {
            "display_name": display_name,
            "metadata": metadata
            | {
                "managed_agents_repo": "thruflow",
                "managed_agents_kind": "vault",
                "managed_agents_slug": metadata.get("agent_id", display_name),
            },
        }
        existing = await self._find_named_resource(
            ["beta:vaults", "list", "--limit", "100", "--format", "json"],
            metadata_slug=metadata.get("agent_id", display_name),
            name=display_name,
            archive_duplicates=("beta:vaults", "--vault-id"),
        )
        if existing is None:
            data = await self._run_ant_json(["beta:vaults", "create", "--format", "json"], payload)
        else:
            data = await self._run_ant_json(
                ["beta:vaults", "update", "--vault-id", self._extract_id(existing), "--format", "json"],
                payload,
            )
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
        await self._require_ant()
        existing = await self._find_named_resource(
            ["beta:vaults:credentials", "list", "--vault-id", vault_id, "--limit", "100", "--format", "json"],
            metadata_slug=f"{metadata.get('agent_id', display_name)}:{metadata.get('server_name', display_name)}",
            name=display_name,
            archive_duplicates=("beta:vaults:credentials", "--credential-id", ["--vault-id", vault_id]),
        )
        payload = {
            "display_name": display_name,
            "metadata": metadata
            | {
                "managed_agents_repo": "thruflow",
                "managed_agents_kind": "vault_credential",
                "managed_agents_slug": f"{metadata.get('agent_id', display_name)}:{metadata.get('server_name', display_name)}",
            },
            "auth": auth,
        }
        if existing is None:
            data = await self._run_ant_json(
                ["beta:vaults:credentials", "create", "--vault-id", vault_id, "--format", "json"],
                payload,
            )
        else:
            credential_id = self._extract_id(existing)
            data = await self._run_ant_json(
                [
                    "beta:vaults:credentials",
                    "update",
                    "--vault-id",
                    vault_id,
                    "--credential-id",
                    credential_id,
                    "--format",
                    "json",
                ],
                payload,
            )
        return str(data["id"])

    def _build_agent_payload(self, agent: AgentConfig, system_prompt: str) -> dict[str, Any]:
        return {
            "name": agent.display_name or agent.agent_id,
            "description": agent.description,
            "model": {"id": agent.model},
            "system": system_prompt,
            "metadata": {
                "managed_agents_repo": "thruflow",
                "managed_agents_kind": "agent",
                "managed_agents_slug": agent.agent_id,
                "agent_id": agent.agent_id,
            },
            "mcp_servers": self._build_mcp_servers(agent),
            "tools": self._build_tools(agent),
        }

    def _run_sdk_session(self, request: ClaudeSessionRequest) -> tuple[str, str, SessionStatus, dict[str, Any]]:
        client = self._get_sdk_client()
        session = client.beta.sessions.create(
            agent=request.agent_id,
            environment_id=self.config.get_provider_environment_id(),
            title=f"ThruFlow {request.correlation_id}",
            metadata=request.metadata | {"correlation_id": request.correlation_id, "agent_id": request.agent_id},
            vault_ids=request.vault_ids,
            resources=[
                {
                    "type": "memory_store",
                    "memory_store_id": request.memory_store_id,
                    "access": request.memory_access,
                }
            ],
        )
        session_id = str(session.id)
        events_payload: list[dict[str, Any]] = []
        texts: list[str] = []
        final_status = SessionStatus.RUNNING
        cleanup: dict[str, Any] = {"attempted": False, "deleted": False}

        with client.beta.sessions.events.stream(session_id) as stream:
            client.beta.sessions.events.send(
                session_id,
                events=[
                    {
                        "type": "user.message",
                        "content": [{"type": "text", "text": request.task_prompt}],
                    }
                ],
            )
            for event in stream:
                event_dict = self._sdk_to_plain_data(event)
                events_payload.append(event_dict)
                event_type = getattr(event, "type", None) or event_dict.get("type")
                self._log_sdk_event_anomalies(session_id, event_type, event_dict)
                if event_type == "agent.message":
                    for block in getattr(event, "content", []) or event_dict.get("content", []) or []:
                        block_type = getattr(block, "type", None) if not isinstance(block, dict) else block.get("type")
                        block_text = getattr(block, "text", None) if not isinstance(block, dict) else block.get("text")
                        if block_type == "text" and block_text:
                            texts.append(str(block_text))
                elif event_type == "session.status_idle":
                    final_status = SessionStatus.COMPLETED
                    break
                elif event_type == "session.error":
                    raise RuntimeError(f"Managed-agent session '{session_id}' failed: {json.dumps(event_dict)}")

        if final_status == SessionStatus.COMPLETED and self.config.settings.thruflow_delete_completed_sessions:
            cleanup["attempted"] = True
            try:
                deleted = client.beta.sessions.delete(session_id)
                cleanup["deleted"] = True
                cleanup["result"] = self._sdk_to_plain_data(deleted)
            except Exception as exc:  # pragma: no cover - defensive provider cleanup path
                cleanup["error"] = str(exc)
                logger.warning("Managed-agent session cleanup failed for %s: %s", session_id, exc)

        return (
            session_id,
            "\n".join(texts).strip(),
            final_status,
            {"session": self._sdk_to_plain_data(session), "events": events_payload, "cleanup": cleanup},
        )

    def _log_sdk_event_anomalies(self, session_id: str, event_type: str | None, event_dict: dict[str, Any]) -> None:
        if event_dict.get("error"):
            logger.error(
                "Managed-agent session event error session_id=%s event_type=%s payload=%s",
                session_id,
                event_type,
                json.dumps(event_dict),
            )
            return
        for block in event_dict.get("content", []) or []:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_result" and block.get("is_error"):
                logger.warning(
                    "Managed-agent tool result anomaly session_id=%s event_type=%s block=%s",
                    session_id,
                    event_type,
                    json.dumps(block),
                )

    async def _wait_for_session_output(self, session_id: str, timeout_seconds: int = 120) -> tuple[str, SessionStatus, dict[str, Any]]:
        deadline = asyncio.get_running_loop().time() + timeout_seconds
        last_events: dict[str, Any] = {}
        while asyncio.get_running_loop().time() < deadline:
            session = await self._run_ant_json(["beta:sessions", "retrieve", "--session-id", session_id, "--format", "json"])
            last_events = await self._run_ant_json(
                ["beta:sessions:events", "list", "--session-id", session_id, "--order", "asc", "--limit", "200", "--format", "json"]
            )
            content = self._extract_agent_message_text(last_events)
            status = SessionStatus.COMPLETED if session.get("status") in {"idle", "terminated"} else SessionStatus.RUNNING
            if status == SessionStatus.COMPLETED:
                return content, status, last_events
            await asyncio.sleep(1)
        raise RuntimeError(f"Timed out waiting for managed-agent session '{session_id}' to complete.")

    def _get_sdk_client(self) -> Any:
        try:
            from anthropic import Anthropic
        except ImportError as exc:
            raise RuntimeError(
                "The anthropic Python SDK is required for managed-agent session execution. Install the `anthropic` package."
            ) from exc
        kwargs: dict[str, Any] = {"api_key": self.config.settings.anthropic_api_key}
        # Managed-agent runtime should follow the Anthropic SDK quickstart by default.
        # Only pass base_url when the operator explicitly points the client at a
        # non-default endpoint.
        if self.config.settings.anthropic_base_url.rstrip("/") != "https://api.anthropic.com/v1":
            kwargs["base_url"] = self.config.settings.anthropic_base_url
        return Anthropic(**kwargs)

    def _sdk_to_plain_data(self, value: Any) -> Any:
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        if isinstance(value, list):
            return [self._sdk_to_plain_data(item) for item in value]
        if isinstance(value, dict):
            return {str(key): self._sdk_to_plain_data(item) for key, item in value.items()}
        model_dump = getattr(value, "model_dump", None)
        if callable(model_dump):
            for kwargs in (
                {"mode": "json", "warnings": False, "exclude_none": True},
                {"mode": "python", "warnings": False, "exclude_none": True},
                {},
            ):
                try:
                    return self._sdk_to_plain_data(model_dump(**kwargs))
                except TypeError:
                    continue
        for attr in ("to_dict", "dict"):
            method = getattr(value, attr, None)
            if callable(method):
                try:
                    return self._sdk_to_plain_data(method())
                except TypeError:
                    continue
        if hasattr(value, "__dict__"):
            return {
                key: self._sdk_to_plain_data(item)
                for key, item in vars(value).items()
                if not key.startswith("_")
            }
        return str(value)

    def _extract_agent_message_text(self, events_payload: dict[str, Any]) -> str:
        texts: list[str] = []
        for event in self._list_items(events_payload):
            if event.get("type") != "agent.message":
                continue
            for block in event.get("content", []) or []:
                if block.get("type") == "text" and block.get("text"):
                    texts.append(str(block["text"]))
        return "\n".join(texts).strip()

    async def _find_named_resource(
        self,
        command: list[str],
        *,
        metadata_slug: str,
        name: str,
        metadata_key: str = "managed_agents_slug",
        archive_duplicates: tuple[str, str] | tuple[str, str, list[str]] | None = None,
    ) -> dict[str, Any] | None:
        payload = await self._run_ant_json(command)
        matches = self._match_named_resources(payload, metadata_slug=metadata_slug, name=name, metadata_key=metadata_key)
        if not matches:
            return None
        matches.sort(key=self._resource_sort_key, reverse=True)
        canonical = matches[0]
        if archive_duplicates and len(matches) > 1:
            extra_args: list[str] = []
            if len(archive_duplicates) == 3:
                extra_args = archive_duplicates[2]
            await self._archive_duplicate_resources(
                archive_command=archive_duplicates[0],
                id_flag=archive_duplicates[1],
                keep_id=self._extract_id(canonical),
                matches=matches[1:],
                extra_args=extra_args,
            )
        return canonical

    def _match_named_resources(
        self,
        payload: Any,
        *,
        metadata_slug: str,
        name: str,
        metadata_key: str = "managed_agents_slug",
    ) -> list[dict[str, Any]]:
        matches: list[dict[str, Any]] = []
        for item in self._list_items(payload):
            metadata = item.get("metadata") or {}
            same_repo = metadata.get("managed_agents_repo") == "thruflow"
            same_slug = metadata.get(metadata_key) == metadata_slug
            same_name = item.get("name") == name or item.get("display_name") == name
            if (same_repo and same_slug) or same_name:
                matches.append(item)
        return matches

    async def _archive_duplicate_resources(
        self,
        *,
        archive_command: str,
        id_flag: str,
        keep_id: str,
        matches: list[dict[str, Any]],
        extra_args: list[str] | None = None,
    ) -> None:
        for item in matches:
            duplicate_id = self._extract_id(item)
            if duplicate_id == keep_id:
                continue
            await self._run_ant_json(
                [archive_command, "archive", *(extra_args or []), id_flag, duplicate_id, "--format", "json"]
            )

    def _resource_sort_key(self, item: dict[str, Any]) -> tuple[str, str, str]:
        return (
            str(item.get("updated_at") or ""),
            str(item.get("created_at") or ""),
            str(item.get("id") or ""),
        )

    def _list_items(self, payload: Any) -> list[dict[str, Any]]:
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        if isinstance(payload, dict):
            for key in ("data", "items", "results"):
                value = payload.get(key)
                if isinstance(value, list):
                    return [item for item in value if isinstance(item, dict)]
        return []

    def _extract_id(self, payload: dict[str, Any]) -> str:
        for key in ("id", "agent_id", "environment_id", "vault_id", "credential_id", "memory_store_id"):
            value = payload.get(key)
            if value:
                return str(value)
        raise RuntimeError(f"Could not resolve resource ID from payload: {json.dumps(payload)}")

    async def _require_ant(self) -> None:
        if shutil.which(self.config.settings.ant_bin):
            return
        raise RuntimeError(
            f"Anthropic CLI binary '{self.config.settings.ant_bin}' was not found. Install the `ant` CLI and/or "
            "set ANT_BIN to its path before running live managed-agent provisioning."
        )

    async def _resource_exists(self, args: list[str]) -> bool:
        if self.config.settings.thruflow_fake_claude:
            return True
        await self._require_ant()
        try:
            await self._run_ant_json(args)
        except RuntimeError:
            return False
        return True

    async def _archive_resource(self, args: list[str]) -> None:
        if self.config.settings.thruflow_fake_claude:
            return
        await self._require_ant()
        try:
            await self._run_ant_json(args)
        except RuntimeError:
            # Missing resources are already effectively reconciled from our point of view.
            return

    async def _list_managed_resources(self, args: list[str]) -> list[dict[str, Any]]:
        if self.config.settings.thruflow_fake_claude:
            return []
        await self._require_ant()
        payload = await self._run_ant_json(args)
        return [
            item
            for item in self._list_items(payload)
            if (item.get("metadata") or {}).get("managed_agents_repo") == "thruflow"
        ]

    async def _run_ant_json(self, args: list[str], payload: dict[str, Any] | None = None) -> dict[str, Any]:
        output = await self._run_ant_command(args, payload)
        if not output:
            return {}
        return self._parse_ant_json_output(output, args)

    async def _run_ant_command(self, args: list[str], payload: dict[str, Any] | None = None) -> str:
        process = await asyncio.create_subprocess_exec(
            self.config.settings.ant_bin,
            *args,
            stdin=asyncio.subprocess.PIPE if payload is not None else None,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdin_bytes = json.dumps(payload).encode("utf-8") if payload is not None else None
        stdout, stderr = await process.communicate(stdin_bytes)
        if process.returncode != 0:
            payload_text = json.dumps(payload, indent=2, sort_keys=True) if payload is not None else ""
            raise RuntimeError(
                f"Anthropic CLI command failed: {self.config.settings.ant_bin} {' '.join(args)}\n"
                f"{stderr.decode().strip()}\n"
                f"{'Payload:\n' + payload_text if payload_text else ''}"
            )
        return stdout.decode().strip()

    def _parse_ant_json_output(self, output: str, args: list[str]) -> dict[str, Any]:
        try:
            return json.loads(output)
        except json.JSONDecodeError:
            pass

        decoder = json.JSONDecoder()
        parsed_values: list[Any] = []
        index = 0

        while index < len(output):
            next_start = -1
            for candidate in range(index, len(output)):
                if output[candidate] in "[{":
                    next_start = candidate
                    break
            if next_start < 0:
                break
            try:
                value, next_index = decoder.raw_decode(output, next_start)
            except json.JSONDecodeError:
                index = next_start + 1
                continue
            parsed_values.append(value)
            index = next_index

        if parsed_values and isinstance(parsed_values[-1], dict):
            return parsed_values[-1]

        raise RuntimeError(
            f"Anthropic CLI command produced non-JSON or multi-part output that could not be resolved to a final object: "
            f"{self.config.settings.ant_bin} {' '.join(args)}\n{output}"
        )

    def _build_mcp_servers(self, agent: AgentConfig) -> list[dict[str, Any]]:
        servers: list[dict[str, Any]] = []
        for server_name in sorted(agent.tools.mcp):
            server = self.config.tools.mcp_servers.get(server_name)
            if not server or not server.enabled:
                continue
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
        if "executive brief" in text.lower():
            return (
                "Executive brief complete.\n"
                "- Main takeaway: the upstream analysis supports a clear recommendation.\n"
                "- Recommendation: proceed with the highest-confidence option.\n"
                "- Open questions: validate scope, timing, and risk tolerance."
            )
        if "analyze the upstream outputs" in text.lower():
            return (
                "Analysis complete.\n"
                "- Key claims are captured from the upstream research.\n"
                "- Main risks: limited scope, unclear assumptions, and missing validation.\n"
                "- Implication: proceed, but call out uncertainty explicitly."
            )
        if "research this topic" in text.lower():
            return (
                "Research complete.\n"
                "- I identified the main facts, supporting context, and open questions.\n"
                "- The result is ready for downstream analysis."
            )
        return "Completed the task and produced a routed output for the next step."
