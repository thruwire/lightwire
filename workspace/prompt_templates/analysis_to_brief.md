Write an executive brief from the analysis artifacts produced by the previous agent.

Analysis artifacts:
{% for artifact in payload.artifacts %}
- {{ artifact }}
{% endfor %}

Handoff notes:
{% for handoff in payload.handoffs %}
- {{ handoff }}
{% endfor %}

Produce:
- title
- 3 bullet summary
- recommendation
- open questions

Read the relevant files from shared memory. Then write the final brief to:

/mnt/memory/artifacts/briefs/{{ correlation_id }}.md

When finished, respond naturally and include the path(s) you wrote under /mnt/memory.
