# AR Inbox-to-Cash Agent

Agentic accounts-receivable demo: reads the AR shared mailbox, interprets remittance advices,
short-payment explanations, promises to pay and document requests, and turns them into
SAP S/4HANA actions (payment advice, dispute case, promise to pay) after human approval.

**Stack:** n8n (self-hosted) · Gemini · MCP server for SAP AR tools · Postgres + pgvector (RAG) ·
SAP Business Accelerator Hub sandbox (read) + S/4 AR mock (writes)

**Design rule:** the LLM proposes, deterministic code disposes. The agent can read and draft;
only an approved, deterministic executor writes to SAP.

> Status: work in progress. Synthetic and SAP sandbox demo data only.
