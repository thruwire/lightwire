Analyze the research artifacts produced by the previous agent.

Research artifacts:
{% for artifact in payload.artifacts %}
- {{ artifact }}
{% endfor %}

Handoff notes:
{% for handoff in payload.handoffs %}
- {{ handoff }}
{% endfor %}

Identify:
- key claims
- uncertainty
- risks
- implications

Read the relevant files from shared memory. Then write your analysis to:

/mnt/memory/artifacts/analysis/{{ correlation_id }}.md

If useful, write a concise handoff note to:

/mnt/memory/handoffs/{{ correlation_id }}-analysis.md

When finished, respond naturally and include the path(s) you wrote under /mnt/memory.
