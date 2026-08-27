# WellView Agentic Design — Reference Architecture

**Domain:** WELLS (WellView)
**Last modified:** 2026-08-27 CDT
**Status:** Production — all components deployed and operational (V3.1 — consolidated OPERATIONS_CORE)

---

## 1. Purpose & Scope

This document is the authoritative reference for the WellView agentic system deployed in Snowflake. It describes the architecture, components, and behavioral contracts that enable deterministic SQL generation from natural-language questions about well data.

This document also serves as a **reusable template** for teams building similar agentic systems over other source domains. The pattern — unified knowledge store → context retrieval → sub-domain routing → text-to-SQL — is domain-agnostic.

---

## 2. Design Philosophy

The system separates two concerns:

| Concern | Mechanism | Artifact |
|---------|-----------|----------|
| **What does the user mean?** | Precomputed context via Cortex Search | `SEMANTIC_DOMAIN_KNOWLEDGE` table + `CS_SEMANTIC_WELLS_KNOWLEDGE` |
| **What SQL answers the resolved question?** | Cortex Analyst against governed schema | 15 sub-domain semantic views |

The agent makes exactly two decisions per question:
1. "What context do I need?" → call context search (mandatory, exactly once)
2. "Which sub-domain answers this?" → route based on `SEMANTIC_VIEWS` field returned by search

**If context does not unambiguously resolve the user's terms, the agent asks for clarification instead of guessing.** This is enforced by the Clarification Protocol (§5).

---

## 3. Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  User Question                                               │
└─────────────────────┬───────────────────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────────────────┐
│  STEP 1: Context Retrieval (MANDATORY — once per turn)       │
│  Tool: CS_SEMANTIC_WELLS_KNOWLEDGE (Cortex Search)                      │
│  Source: SEMANTIC_DOMAIN_KNOWLEDGE                              │
│  Returns: synonyms, rules, metrics, patterns, routing hints  │
│  GENERAL domain entries apply universally to all sub-domains │
└─────────────────────┬───────────────────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────────────────┐
│  STEP 2: Enriched Query → Sub-Domain Semantic View           │
│  Route via SEMANTIC_VIEWS field from Step 1                   │
│  15 sub-domain SV_ENT_WELLS_* views (Cortex Analyst)         │
│  Max 2 views per question (primary + one secondary)          │
└─────────────────────┬───────────────────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────────────────┐
│  SQL Result returned to user                                 │
└─────────────────────────────────────────────────────────────┘
```

---

## 4. Object Catalog

### 4.1 Knowledge Store

| Object | Type | FQN | Purpose |
|--------|------|-----|---------|
| `SEMANTIC_DOMAIN_KNOWLEDGE` | TABLE | `DATASCIENCE_DEV.DS_ENT_EXP.SEMANTIC_DOMAIN_KNOWLEDGE` | Central knowledge table |

**Key columns:**
- `ENTRY_ID` (PK, UUID)
- `ENTRY_TYPE` — SYNONYM, RULE, METRIC, PATTERN, COLUMN_DESC, HIERARCHY, TABLE_DESC
- `DOMAIN` — routing domain (GENERAL applies to all sub-domains; others are sub-domain-specific)
- `NAME`, `CONTENT` — human-readable knowledge
- `CONTEXT_PROFILE` — precomputed searchable profile (vector-indexed)
- `SEMANTIC_VIEWS` (ARRAY) — routing hints to sub-domain views
- `SCOPE` — view | global
- `CATEGORY` — fine-grained classification
- `CONFIDENCE` — entry quality score
- `PROMOTION_STATE` — none | candidate | promoted | retired
- `ACTIVE` (BOOLEAN) — soft-delete flag

### 4.2 Cortex Search Service

| Object | Type | FQN | Purpose |
|--------|------|-----|---------|
| `CS_SEMANTIC_WELLS_KNOWLEDGE` | CORTEX SEARCH SERVICE | `DATASCIENCE_DEV.DS_ENT_EXP.CS_SEMANTIC_WELLS_KNOWLEDGE` | Unified semantic search over knowledge store |

**Configuration:**
- **Search column:** `CONTEXT_PROFILE`
- **Embedding model:** `snowflake-arctic-embed-m-v1.5`
- **Attribute columns:** `ENTRY_TYPE`, `DOMAIN`, `SEMANTIC_VIEWS`, `PROMOTION_STATE`
- **Target lag:** 1 minute (incremental refresh)
- **Warehouse:** `DATASCIENCE_DEV_ADHOC_WH`
- **Source filter:** `ACTIVE = TRUE AND CONTEXT_PROFILE IS NOT NULL`
- **Indexed rows:** ~5,200

### 4.3 Sub-Domain Semantic Views (15)

All deployed in **`DATASCIENCE_DEV.DS_ENT_BI`** with prefix `SV_ENT_WELLS_`.

Each semantic view represents a **sub-domain** of the WELLS domain — a logical grouping of related WellView source tables that can be queried together. Every sub-domain includes `WV_WELLHEADER` as the join anchor with standard filterable dimensions (BU, AREA, REGION, DISTRICT, WELL_STATUS, WELL_CONFIG_TYPE).

**V3.1 consolidation:** The former OPERATIONS_DRILLING, OPERATIONS_JOB_CORE, and OPERATIONS_RIG_EQUIPMENT views have been merged into a single `SV_ENT_WELLS_OPERATIONS_CORE` (31 tables, ~41K tokens). This enables cross-domain queries spanning drilling, job management, and rig equipment in a single Analyst call.

| # | Semantic View | Sub-Domain | Coverage |
|---|---------------|-----------|----------|
| 1 | `SV_ENT_WELLS_GENERAL` | General | Well header, identity, location, status, lifecycle |
| 2 | `SV_ENT_WELLS_ASSET_MANAGEMENT` | Asset Management | Notes, responsible teams, custom attributes |
| 3 | `SV_ENT_WELLS_CASING_CEMENT_WELLHEADS` | Casing & Cement | Casing strings, cement jobs, wellhead assemblies |
| 4 | `SV_ENT_WELLS_GEOLOGICAL_EVALUATION` | Geology | Formation tops, logs, reservoir properties |
| 5 | `SV_ENT_WELLS_INTEGRITY_BARRIERS` | Integrity | Integrity programs, barrier envelopes, assessments |
| 6 | **`SV_ENT_WELLS_OPERATIONS_CORE`** | **Operations (consolidated)** | **Jobs + Drilling + Rig Equipment (31 tables)** |
| 7 | `SV_ENT_WELLS_OPERATIONS_COSTS` | Costs | AFEs, budget vs actual, vendor invoices |
| 8 | `SV_ENT_WELLS_OPERATIONS_SAFETY_INCIDENTS` | Safety | Incidents, kicks, lost circ, problems, lessons |
| 9 | `SV_ENT_WELLS_OPERATIONS_DAILY_REPORTING` | Daily Ops | Daily reports, timelog, mud, emissions, costs |
| 10 | `SV_ENT_WELLS_OTHER` | Other | Custom attributes, depth annotations, misc |
| 11 | `SV_ENT_WELLS_PERFS_STIMS_SWABS` | Completions | Stimulations, perforations, frac stages, fluids |
| 12 | `SV_ENT_WELLS_PRODUCTION_OPERATIONS_FAILURES` | Production | Prod settings, lift methods, failures, root cause |
| 13 | `SV_ENT_WELLS_RESERVOIR_EQUIPMENT_TESTS` | Tests | Well tests, DSTs, LOT/FIT, equipment pressure tests |
| 14 | `SV_ENT_WELLS_SURFACE_EQUIPMENT` | Surface Equipment | Pumping units, prime movers |
| 15 | `SV_ENT_WELLS_TUBING_RODS_OTHER_EQUIPMENT` | Downhole Equipment | Tubing, rods, packers, ESPs, other in-hole |
| 16 | `SV_ENT_WELLS_WELLBORE_SURVEYS_FORMATIONS` | Wellbore | Trajectory, directional surveys, key depths |
| 17 | `SV_ENT_WELLS_ZONES_COMPLETIONS` | Zones | Completion zones, status history |

**Deprecated (replaced by OPERATIONS_CORE):** `SV_ENT_WELLS_OPERATIONS_DRILLING`, `SV_ENT_WELLS_OPERATIONS_JOB_CORE`, `SV_ENT_WELLS_OPERATIONS_RIG_EQUIPMENT`

### 4.4 Cortex Agent

| Object | Type | FQN | Purpose |
|--------|------|-----|---------|
| `AGENT_WELLS_V3` | CORTEX AGENT | `DATASCIENCE_DEV.DS_ENT_EXP.AGENT_WELLS_V3` | Deterministic well data Q&A agent |

**Configuration:**
- **Default version:** VERSION$17 (V3.1 — consolidated OPERATIONS_CORE)
- **Model:** auto (orchestration)
- **Tools:** 1 Cortex Search + 15 Cortex Analyst (sub-domain views)
- **Max results from search:** 12
- **Warehouse:** `DATASCIENCE_DEV_ADHOC_WH`

**Behavioral directives:**
1. ALWAYS call `context_search` first — no exceptions
2. Route to the sub-domain indicated by `SEMANTIC_VIEWS` in search results
3. Resolve synonyms (including GENERAL domain) before composing the enriched question
4. Apply Priority 1-2 RULE entries as mandatory filters
5. Never hallucinate column/table names not present in context
6. Max 2 semantic view calls per question (primary + one secondary)
7. Clarification Protocol: if context reveals ambiguity — ASK, do not guess (§5)
8. Max ONE context_search call per turn; do not retry with different terms

### 4.5 MCP Server

| Object | Type | FQN | Purpose |
|--------|------|-----|---------|
| `SF_MCP_ENT_WELLS` | MCP SERVER | `ENT_DEV.ENT_AI.SF_MCP_ENT_WELLS` | External MCP access to the well data system |

**Configuration:**
- **Owner:** `ENT_DEV_AI_DEVELOPER_FRL`
- **Created:** 2026-08-05 15:26 CDT
- **Authentication:** Snowflake OAuth (per-user identity passthrough)

**Tools exposed (18):**
- `WELL_CONTEXT_SEARCH` — unified knowledge retrieval (Cortex Search)
- 15x `SV_ENT_WELLS_*` — text-to-SQL via Cortex Analyst (including consolidated OPERATIONS_CORE)
- `sql_executor` — execute generated SQL (read_only: false, timeout: 600s, warehouse: datascience_dev_adhoc_wh)

**RBAC model:** The MCP server authenticates each external client as a Snowflake user. SQL execution runs under the authenticated user's role — full RBAC is enforced identically to the internal agent.

---

## 5. Clarification Protocol

The agent enforces a strict **"ask, don't guess"** policy when context retrieval reveals ambiguity. After the single mandatory context_search call, the agent evaluates whether results unambiguously resolve every user term. If ANY of these conditions are true, the agent **stops and asks a clarification question**:

| Condition | Description |
|-----------|-------------|
| **(a)** No match | User term has no SYNONYM match and no single canonical value |
| **(b)** Multiple mappings | Context returns multiple possible values without definitive 1:1 resolution |
| **(c)** RULE broadens SYNONYM | A RULE maps the term to multiple values/conditions broader than a single SYNONYM |
| **(d)** Column not in SV | SYNONYM resolves to a column not exposed in the target sub-domain's dimensions/measures |
| **(e)** Low confidence | All returned entries have confidence below 0.7 |

**Anti-patterns explicitly banned:**
- Rationalizing past ambiguity
- Exploratory SQL probing columns not in the semantic model
- Making >1 tool call before deciding to ask for clarification
- Dismissing RULEs as "advisory" to avoid asking
- Substituting approximate columns (e.g. region instead of BU)

---

## 6. Knowledge Entry Types

| Entry Type | Purpose | Example |
|-----------|---------|---------|
| `SYNONYM` | Maps user jargon to canonical column/value | "Permian" maps to `BU = 'LOWER 48 - HMRO PERMIAN'` |
| `RULE` | Business logic, mandatory filters (priority 1-2) | "All Permian wells" requires BU + Region ILIKE patterns |
| `METRIC` | Calculation formulas | "NPT" = sum of timelog hours where trouble_code != NULL |
| `PATTERN` | Verified SQL templates for known questions | "How many wells spudded in 2024?" with full SQL template |
| `COLUMN_DESC` | Column meanings with sample values | `WV_JOB.TOTAL_DEPTH_DRILLED` = final drilled depth |
| `HIERARCHY` | Hierarchical relationships | BU → AREA → REGION → DISTRICT |
| `TABLE_DESC` | Table-level descriptions | `WV_STIM` = stimulation treatment headers |

**GENERAL domain entries** (DOMAIN = 'GENERAL') are cross-cutting knowledge — BU/area synonyms, lift type mappings, unit conversions, well status mappings. They have NULL SEMANTIC_VIEWS and apply universally to any sub-domain.

---

## 7. Data Flow

```
┌───────────────────────────────────────────────────────────────┐
│                   SEMANTIC_DOMAIN_KNOWLEDGE                       │
│  ~5,200 active entries across 15 sub-domains + GENERAL        │
│  Entry types: SYNONYM | RULE | METRIC | PATTERN |             │
│               COLUMN_DESC | HIERARCHY | TABLE_DESC             │
└───────────────────────────┬───────────────────────────────────┘
                            │ (indexed by CONTEXT_PROFILE)
                            ▼
┌───────────────────────────────────────────────────────────────┐
│                    CS_SEMANTIC_WELLS_KNOWLEDGE                            │
│  Cortex Search Service                                        │
│  Embedding: snowflake-arctic-embed-m-v1.5                     │
│  Refresh: incremental, 1-minute lag                           │
│  Attributes: ENTRY_TYPE, DOMAIN, SEMANTIC_VIEWS,              │
│              PROMOTION_STATE                                   │
└───────────────────────────┬───────────────────────────────────┘
                            │ (top-12 results)
                            ▼
┌───────────────────────────────────────────────────────────────┐
│       AGENT_WELLS_V3 / SF_MCP_ENT_WELLS                       │
│  Reads SEMANTIC_VIEWS from results → routes to sub-domain     │
└───────────────────────────┬───────────────────────────────────┘
                            │
                            ▼
┌───────────────────────────────────────────────────────────────┐
│          SV_ENT_WELLS_* (15 Sub-Domain Semantic Views)        │
│  Cortex Analyst text-to-SQL                                   │
│  Schema: DATASCIENCE_DEV.DS_ENT_BI                            │
│  Source tables: ENT_PRD.WELLS.WV_*                            │
└───────────────────────────────────────────────────────────────┘
```

---

## 8. Source Data

- **Source database:** `ENT_PRD.WELLS`
- **Total WellView tables available:** 147 (`WV_*` prefix)
- **Tables wired into semantic views:** 143 unique
- **Join anchor:** Every sub-domain includes `WV_WELLHEADER` joined via `WELL_IDREC` (or `IDWELL` for 55 legacy views)

---

## 9. Maintenance Model

| What | Frequency | Mechanism |
|------|-----------|-----------|
| **Semantic views** | Rarely (schema changes only) | Human-edited DDL, redeployed |
| **Knowledge entries** | Steady growth | Auto-proposed, human-approved |
| **Context profiles** | Automatic | Rebuilt by search service (1-minute incremental refresh) |
| **Verified query promotion** | Weekly | Scored by retrieval_count / (age_days + 1) |
| **Entry retirement** | 90-day cycle | Entries with 0 retrievals → `PROMOTION_STATE = 'retired'` |

---

## 10. Access & Roles

| Object | Owner Role | Consumer Roles |
|--------|-----------|---------------|
| `SEMANTIC_DOMAIN_KNOWLEDGE` | `DATASCIENCE_DEV_ENT_DEVELOPER_FRL` | Agent service account |
| `CS_SEMANTIC_WELLS_KNOWLEDGE` | `DATASCIENCE_DEV_ENT_DEVELOPER_FRL` | Agent service account |
| `SV_ENT_WELLS_*` (15) | `DATASCIENCE_DEV_ENT_DEVELOPER_FRL` | Agent, MCP consumers |
| `AGENT_WELLS_V3` | `DATASCIENCE_DEV_ENT_DEVELOPER_FRL` | End users (CoWork, Snowsight) |
| `SF_MCP_ENT_WELLS` | `ENT_DEV_AI_DEVELOPER_FRL` | External MCP clients (`ENT_DEV_ENT_AI_R_DRL` for USAGE) |

**MCP RBAC:** Every MCP connection authenticates as an individual Snowflake user. SQL execution inherits the caller's role — same access control as the internal agent.

---

## 11. Template Guide (For Other Domains)

To replicate this pattern for a different source domain:

1. **Identify the domain** — e.g., "PRODUCTION", "LAND", "SCADA"
2. **Partition into sub-domains** — group related source tables into logical clusters (aim for 5-25 sub-domains per domain)
3. **Create semantic views** — one per sub-domain, each with a shared anchor table for cross-sub-domain filtering
4. **Build a domain knowledge table** — populate SYNONYM, RULE, METRIC, PATTERN, COLUMN_DESC entries
5. **Create a Cortex Search service** — index CONTEXT_PROFILE with SEMANTIC_VIEWS as routing attribute
6. **Deploy the agent** — two-step flow: context search → sub-domain routing
7. **Deploy an MCP server** (optional) — for external client access with full RBAC

**Naming conventions:**
- Semantic views: `SV_ENT_<DOMAIN>_<SUBDOMAIN>` (e.g., `SV_ENT_LAND_AGREEMENTS`)
- Agent: `AGENT_<DOMAIN>` (e.g., `AGENT_LAND`)
- Knowledge table: `<DOMAIN>_DOMAIN_KNOWLEDGE`
- Search service: `<DOMAIN>_CONTEXT_SVC`
- MCP server: `SF_MCP_ENT_<DOMAIN>`

---

## 12. File Index (Workspace)

| File | Purpose |
|------|---------|
| `Design/WELLVIEW_AGENTIC_DESIGN.md` | This document — architecture reference |
| `Design/WELLVIEW_SEMANTIC_VIEW_MAP.md` | Sub-domain → source table mapping (detailed) |
| `Design/create_mcp_server_v3.sql` | MCP server DDL |
| `Design/generated_semantic_views/SV_ENT_WELLS_*.sql` | DDL for semantic views (15 active + 3 deprecated) |
| `cortex_project/agent_wells_v3.agent.yaml` | Agent specification YAML |
