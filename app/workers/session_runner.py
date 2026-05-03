from __future__ import annotations

from app.claude.resources import ClaudeProviderResourceService
from app.claude.sessions import ClaudeSessionService
from app.config import RuntimeConfig
from app.models import AgentOutputRecord, ClaudeSessionRequest, RouteDispatch, SessionRecord, SessionStatus
from app.repositories.outputs import AgentOutputRepository
from app.repositories.sessions import SessionRepository
from app.utils.ids import new_id
from app.utils.time import utc_now
from app.workers.output_handler import OutputHandler


class SessionRunner:
    def __init__(
        self,
        config: RuntimeConfig,
        resources: ClaudeProviderResourceService,
        session_service: ClaudeSessionService,
        sessions: SessionRepository,
        outputs: AgentOutputRepository,
        output_handler: OutputHandler,
    ) -> None:
        self.config = config
        self.resources = resources
        self.session_service = session_service
        self.sessions = sessions
        self.outputs = outputs
        self.output_handler = output_handler

    async def run_dispatch(self, dispatch: RouteDispatch) -> tuple[SessionRecord, AgentOutputRecord]:
        _, memory_store_id = await self.resources.ensure()
        # Runtime sessions should reuse the vault IDs provisioned during deploy.
        vault_ids = self.resources.resolve_agent_vault_ids(dispatch.agent_id)
        provider_agent_id = self.resources.resolve_agent_provider_id(dispatch.agent_id)
        agent = self.config.get_agent(dispatch.agent_id)
        now = utc_now()
        session = SessionRecord(
            id=new_id("sess"),
            route_id=dispatch.route_id,
            agent_id=agent.agent_id,
            message_id=dispatch.parent_message.id,
            correlation_id=dispatch.parent_message.correlation_id,
            prompt=dispatch.prompt,
            status=SessionStatus.PENDING,
            external_session_id=None,
            memory_store_id=memory_store_id,
            output_message_id=None,
            created_at=now,
            updated_at=now,
        )
        self.sessions.create(session)
        request = ClaudeSessionRequest(
            agent_id=provider_agent_id,
            system_prompt=self.config.get_agent_system_prompt(agent.agent_id),
            task_prompt=dispatch.prompt,
            memory_store_id=memory_store_id,
            memory_access=agent.memory.access,
            correlation_id=dispatch.parent_message.correlation_id,
            vault_ids=vault_ids,
            metadata={"route_id": dispatch.route_id, "parent_message_id": dispatch.parent_message.id},
        )
        result = await self.session_service.run(request)
        self.sessions.update_status(session.id, result.status, external_session_id=result.external_session_id)
        output = AgentOutputRecord(
            id=new_id("out"),
            session_id=session.id,
            agent_id=agent.agent_id,
            correlation_id=dispatch.parent_message.correlation_id,
            content=result.content,
            summary=self.output_handler.build_summary(result.content),
            metadata={
                "memory_store_id": result.attached_memory_store_id,
                # Route metadata is copied onto agent output so later handlers can make connector-aware decisions.
                "route_id": dispatch.route_id,
                **({"reply": dispatch.reply.model_dump(mode="json")} if dispatch.reply else {}),
            },
            created_at=utc_now(),
        )
        self.outputs.create(output)
        return self.sessions.get(session.id) or session, output
