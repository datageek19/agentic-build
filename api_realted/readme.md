legacy system api call


agenda list:

Boeing Agentic AI — Working Session Agenda

10:00 – 10:15 · Introductions

10:15 – 11:00 · Use case scope & progress

Use case scope for all 5 epics
Discovery executed for all 5 epics
Current progress on the 2 in-flight epics
Work executed so far
Plan to Dec 20 demo
Plan post Dec 20 for full agent deployment
Demo acceptance criteria — what "good" looks like on Dec 20 (accuracy bar, eval dataset, success metrics)
Human-in-the-loop design — review/approval gates and escalation paths

11:00 – 11:40 · Agent architecture & orchestration (new)

Epic-to-agent mapping and orchestration pattern (supervisor vs. sequential)
Model selection per agent (Bedrock / AgentCore)
Idempotency & transactional integrity for payment actions — no double-resolution on retry
Guardrails and prompt-injection defense on ingested content

11:40 – 12:20 · Source system integration status

Outlook
FootPrints
S/4HANA
Ariba
Datalake
Others — SPVR, Business Partner / Purchasing Agent lists
Agent-to-system authentication & secrets (service principals / OAuth / token lifecycle)

12:20 – 12:50 · Security, compliance & ITAR (new)

Data classification & ITAR handling (what agents can see/retain, US-person access controls)
Audit trail — immutable logging of every agent decision and action
PII handling and data retention

12:50 – 1:15 · Admin

Engagement cadence — daily, weekly, ad-hoc
Milestones
Invoicing

1:15 – 2:00 · Production readiness & deployment (expanded from "full deployment")

Environments & promotion path (dev/test/prod, IaC)
Observability — agent tracing, monitoring, and token/compute cost at scale
Resiliency & fallback when a source system is down (retries, queueing, graceful degradation)

2:00 – 2:40 · Next steps / future work

Work being executed with BGS
Monthly innovation sessions
Introduction to Evalueserve Data & AI

2:40 – 3:00 · Asks & open floor

Asks from the Boeing team
Suggestions / initiatives Evalueserve should focus on or deprioritize
