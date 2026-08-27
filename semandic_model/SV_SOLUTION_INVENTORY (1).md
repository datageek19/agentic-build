# Wells Agent Solution — Component Inventory & Migration Guide

**Purpose:** Complete inventory of all Snowflake objects comprising the Wells Agent solution.  
**Audience:** Support personnel responsible for migrating definitions to production.  
**Source Database:** `DATASCIENCE_DEV`  
**Owner Role:** `DATASCIENCE_DEV_ENT_DEVELOPER_FRL`  
**Last Updated:** 2026-08-27

---

## Table of Contents

1. [Solution Architecture Overview](#1-solution-architecture-overview)
2. [Agents](#2-agents)
3. [MCP Server](#3-mcp-server)
4. [Semantic Views — Wells Domain (DS_ENT_BI)](#4-semantic-views--wells-domain-ds_ent_bi)
5. [Semantic Views — Admin/Support (DS_ENT_EXP)](#5-semantic-views--adminsupport-ds_ent_exp)
6. [Cortex Search Services](#6-cortex-search-services)
7. [Stored Procedures](#7-stored-procedures)
8. [Tables — Domain Knowledge & Admin](#8-tables--domain-knowledge--admin)
9. [Tables — Supporting Knowledge Bases & Catalogs](#9-tables--supporting-knowledge-bases--catalogs)
10. [Streamlit Applications](#10-streamlit-applications)
11. [Stages](#11-stages)
12. [Source Data (ENT_PRD.WELLS)](#12-source-data-ent_prdwells)
13. [Warehouses Referenced](#13-warehouses-referenced)
14. [Migration Dependency Order](#14-migration-dependency-order)
15. [Migration Notes](#15-migration-notes)

---

## 1. Solution Architecture Overview

```
┌──────────────────────────────────────────────────────────────────────────────┐
│  USER INTERFACES                                                              │
│  ┌────────────────┐  ┌──────────────────────┐  ┌─────────────────────────┐  │
│  │ Snowflake      │  │ DK_MANAGER           │  │ External Clients        │  │
│  │ Intelligence   │  │ (Streamlit App)      │  │ (Claude, Cursor, etc.)  │  │
│  └───────┬────────┘  └──────────┬───────────┘  └────────────┬────────────┘  │
└──────────┼──────────────────────┼────────────────────────────┼───────────────┘
           │                      │                            │
           ▼                      ▼                            ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│  AGENTS & MCP                                                                 │
│  ┌────────────────────┐  ┌────────────────────────┐  ┌────────────────────┐ │
│  │ AGENT_WELLS_V3     │  │ DK_MANAGEMENT_AGENT    │  │ SF_MCP_ENT_WELLS   │ │
│  │ (Primary Q&A)      │  │ (Knowledge Mgmt)       │  │ (MCP Protocol)     │ │
│  └─────────┬──────────┘  └───────────┬────────────┘  └─────────┬──────────┘ │
└────────────┼─────────────────────────┼──────────────────────────┼────────────┘
             │                         │                          │
             └─────────────────────────┼──────────────────────────┘
                                       ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│  TOOLS LAYER                                                                  │
│  ┌──────────────────┐  ┌────────────────────┐  ┌─────────────────────────┐  │
│  │ 15 Semantic Views │  │ Cortex Search Svcs │  │ Stored Procedures       │  │
│  │ (DS_ENT_BI)      │  │ CS_SEMANTIC_WELLS_KNOWLEDGE  │  │ DK_INSERT/UPDATE/etc.   │  │
│  │                   │  │ COLUMN_CATALOG_SVC │  │                         │  │
│  └────────┬──────────┘  └────────┬───────────┘  └─────────────────────────┘  │
└───────────┼───────────────────────┼──────────────────────────────────────────┘
            │                       │
            ▼                       ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│  DATA LAYER                                                                   │
│  ┌────────────────────────┐  ┌────────────────────────────────────────────┐  │
│  │ ENT_PRD.WELLS.WV_*     │  │ DATASCIENCE_DEV.DS_ENT_EXP                │  │
│  │ (147 source views)     │  │ SEMANTIC_DOMAIN_KNOWLEDGE (5,249 rows)             │  │
│  │                        │  │ WELLVIEW_COLUMN_CATALOG_V2 (4,880 rows)   │  │
│  └────────────────────────┘  └────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Agents

### AGENT_WELLS_V3

| Field | Value |
|-------|-------|
| **FQN** | `DATASCIENCE_DEV.DS_ENT_EXP.AGENT_WELLS_V3` |
| **Purpose** | Primary Wells domain Q&A agent — deterministic V3 architecture with two-step Cortex Search + Cortex Analyst pattern |
| **Profile** | — |

### DK_MANAGEMENT_AGENT

| Field | Value |
|-------|-------|
| **FQN** | `DATASCIENCE_DEV.DS_ENT_EXP.DK_MANAGEMENT_AGENT` |
| **Purpose** | Domain knowledge CRUD agent — conversational interface for managing SEMANTIC_DOMAIN_KNOWLEDGE entries |
| **Profile** | `{"display_name": "Domain Knowledge Manager", "color": "#6C63FF"}` |

### Agent Dependencies

**AGENT_WELLS_V3** depends on:
- 15 semantic views in `DATASCIENCE_DEV.DS_ENT_BI` (SV_ENT_WELLS_*)
- Cortex Search: `CS_SEMANTIC_WELLS_KNOWLEDGE` (domain knowledge retrieval)
- Cortex Search: `WELLVIEW_COLUMN_CATALOG_SEARCH` (column discovery)

**DK_MANAGEMENT_AGENT** depends on:
- Semantic View: `DATASCIENCE_DEV.DS_ENT_EXP.SV_DOMAIN_KNOWLEDGE_ADMIN`
- Cortex Search: `CS_SEMANTIC_WELLS_KNOWLEDGE`
- Stored Procedures: `DK_CHECK_ACCESS`, `DK_VALIDATE_ENTRY`, `DK_INSERT_ENTRY`, `DK_UPDATE_ENTRY`, `DK_DEACTIVATE_ENTRY`, `DK_BULK_DEACTIVATE`, `DK_SEARCH_SIMILAR`

---

## 3. MCP Server

### SF_MCP_ENT_WELLS

| Field | Value |
|-------|-------|
| **FQN** | `ENT_DEV.ENT_AI.SF_MCP_ENT_WELLS` |
| **Owner Role** | `ENT_DEV_AI_DEVELOPER_FRL` |
| **Created** | 2026-08-25 |

**Note:** The MCP server lives in a different database/schema (`ENT_DEV.ENT_AI`) from the rest of the solution components.

### MCP Server Tools (19 total)

The MCP server exposes the same Wells agent capabilities as a tool-use protocol for external clients (Claude Desktop, Cursor, etc.):

**1. WELL_CONTEXT_SEARCH** — `CORTEX_SEARCH_SERVICE_QUERY`
- Backing: `DATASCIENCE_DEV.DS_ENT_EXP.CS_SEMANTIC_WELLS_KNOWLEDGE`
- Mandatory first step — domain knowledge retrieval with routing/disambiguation logic

**2–18. Semantic View Tools** — all type `CORTEX_ANALYST_MESSAGE`

| # | Tool Name | Backing Semantic View |
|---|-----------|----------------------|
| 2 | SV_ENT_WELLS_GENERAL | `.DS_ENT_BI.SV_ENT_WELLS_GENERAL` |
| 3 | SV_ENT_WELLS_ASSET_MANAGEMENT | `.DS_ENT_BI.SV_ENT_WELLS_ASSET_MANAGEMENT` |
| 4 | SV_ENT_WELLS_CASING_CEMENT_WELLHEADS | `.DS_ENT_BI.SV_ENT_WELLS_CASING_CEMENT_WELLHEADS` |
| 5 | SV_ENT_WELLS_GEOLOGICAL_EVALUATION | `.DS_ENT_BI.SV_ENT_WELLS_GEOLOGICAL_EVALUATION` |
| 6 | SV_ENT_WELLS_INTEGRITY_BARRIERS | `.DS_ENT_BI.SV_ENT_WELLS_INTEGRITY_BARRIERS` |
| 7 | SV_ENT_WELLS_OPERATIONS_CORE | `.DS_ENT_BI.SV_ENT_WELLS_OPERATIONS_CORE` |
| 8 | SV_ENT_WELLS_OPERATIONS_COSTS | `.DS_ENT_BI.SV_ENT_WELLS_OPERATIONS_COSTS` |
| 9 | SV_ENT_WELLS_OPERATIONS_SAFETY_INCIDENTS | `.DS_ENT_BI.SV_ENT_WELLS_OPERATIONS_SAFETY_INCIDENTS` |
| 10 | SV_ENT_WELLS_OPERATIONS_DAILY_REPORTING | `.DS_ENT_BI.SV_ENT_WELLS_OPERATIONS_DAILY_REPORTING` |
| 11 | SV_ENT_WELLS_OTHER | `.DS_ENT_BI.SV_ENT_WELLS_OTHER` |
| 12 | SV_ENT_WELLS_PERFS_STIMS_SWABS | `.DS_ENT_BI.SV_ENT_WELLS_PERFS_STIMS_SWABS` |
| 13 | SV_ENT_WELLS_PRODUCTION_OPERATIONS_FAILURES | `.DS_ENT_BI.SV_ENT_WELLS_PRODUCTION_OPERATIONS_FAILURES` |
| 14 | SV_ENT_WELLS_RESERVOIR_EQUIPMENT_TESTS | `.DS_ENT_BI.SV_ENT_WELLS_RESERVOIR_EQUIPMENT_TESTS` |
| 15 | SV_ENT_WELLS_SURFACE_EQUIPMENT | `.DS_ENT_BI.SV_ENT_WELLS_SURFACE_EQUIPMENT` |
| 16 | SV_ENT_WELLS_TUBING_RODS_OTHER_EQUIPMENT | `.DS_ENT_BI.SV_ENT_WELLS_TUBING_RODS_OTHER_EQUIPMENT` |
| 17 | SV_ENT_WELLS_WELLBORE_SURVEYS_FORMATIONS | `.DS_ENT_BI.SV_ENT_WELLS_WELLBORE_SURVEYS_FORMATIONS` |
| 18 | SV_ENT_WELLS_ZONES_COMPLETIONS | `.DS_ENT_BI.SV_ENT_WELLS_ZONES_COMPLETIONS` |

All semantic view tools above are in database `DATASCIENCE_DEV`.

**19. sql_executor** — `SYSTEM_EXECUTE_SQL`
- Executes SQL generated by Cortex Analyst tools
- Read-only preferred, warehouse: `datascience_dev_adhoc_wh`, timeout: 600s

### MCP Server Dependencies

The MCP server references objects in **two** databases:
- `DATASCIENCE_DEV.DS_ENT_EXP.CS_SEMANTIC_WELLS_KNOWLEDGE` (Cortex Search)
- `DATASCIENCE_DEV.DS_ENT_BI.SV_ENT_WELLS_*` (15 semantic views)
- Warehouse: `datascience_dev_adhoc_wh`

### Migration Considerations for MCP Server

- The MCP server spec contains hardcoded FQNs for all semantic views and the search service
- The `sql_executor` tool specifies warehouse `datascience_dev_adhoc_wh` — must be updated for production
- The server lives in `ENT_DEV.ENT_AI` — determine if the production MCP server should live in the same schema as the agent or a separate shared AI schema
- Tool descriptions contain detailed behavioral instructions (disambiguation rules, anti-patterns) that reference domain knowledge entry types — these are stable and don't need FQN updates

---

## 4. Semantic Views — Wells Domain (DS_ENT_BI)

These are the "thin" semantic views used by AGENT_WELLS_V3. They query source views in `ENT_PRD.WELLS`.

All are in schema `DATASCIENCE_DEV.DS_ENT_BI`:

| # | Name | Description |
|---|------|-------------|
| 1 | SV_ENT_WELLS_GENERAL | Well header, location, classification, status |
| 2 | SV_ENT_WELLS_OPERATIONS_CORE | Drilling ops, job management, rig equipment (consolidated) |
| 3 | SV_ENT_WELLS_OPERATIONS_DAILY_REPORTING | Daily reports, timelogs, mud checks |
| 4 | SV_ENT_WELLS_OPERATIONS_COSTS | AFE, cost tracking, vendor costs |
| 5 | SV_ENT_WELLS_OPERATIONS_SAFETY_INCIDENTS | Safety incidents, hazards |
| 6 | SV_ENT_WELLS_CASING_CEMENT_WELLHEADS | Casing, cement, wellhead equipment |
| 7 | SV_ENT_WELLS_PERFS_STIMS_SWABS | Perforations, stimulations, swabs |
| 8 | SV_ENT_WELLS_WELLBORE_SURVEYS_FORMATIONS | Wellbore, directional surveys, formations |
| 9 | SV_ENT_WELLS_ZONES_COMPLETIONS | Zones, zone status, completions |
| 10 | SV_ENT_WELLS_TUBING_RODS_OTHER_EQUIPMENT | Tubing, rods, downhole equipment |
| 11 | SV_ENT_WELLS_SURFACE_EQUIPMENT | Surface equipment, pumping units |
| 12 | SV_ENT_WELLS_PRODUCTION_OPERATIONS_FAILURES | Production problems, failures |
| 13 | SV_ENT_WELLS_RESERVOIR_EQUIPMENT_TESTS | Well tests, equipment tests, leak-off |
| 14 | SV_ENT_WELLS_INTEGRITY_BARRIERS | Well integrity, barrier items |
| 15 | SV_ENT_WELLS_GEOLOGICAL_EVALUATION | Geological evaluation, logs |
| 16 | SV_ENT_WELLS_ASSET_MANAGEMENT | Asset management |
| 17 | SV_ENT_WELLS_OTHER | Miscellaneous/uncategorized |

**Note:** Also deprecated individual views still present: `SV_ENT_WELLS_OPERATIONS_DRILLING`, `SV_ENT_WELLS_OPERATIONS_JOB_CORE`, `SV_ENT_WELLS_OPERATIONS_RIG_EQUIPMENT` (these were consolidated into `SV_ENT_WELLS_OPERATIONS_CORE`).

---

## 5. Semantic Views — Admin/Support (DS_ENT_EXP)

### SV_DOMAIN_KNOWLEDGE_ADMIN

| Field | Value |
|-------|-------|
| **FQN** | `DATASCIENCE_DEV.DS_ENT_EXP.SV_DOMAIN_KNOWLEDGE_ADMIN` |
| **Purpose** | Read-only admin querying of SEMANTIC_DOMAIN_KNOWLEDGE and DOMAIN_APP_ADMIN_USER tables. Used by DK_MANAGEMENT_AGENT. |

---

## 6. Cortex Search Services

### Core (Required for Wells Agent)

**CS_SEMANTIC_WELLS_KNOWLEDGE**

| Field | Value |
|-------|-------|
| **FQN** | `DATASCIENCE_DEV.DS_ENT_EXP.CS_SEMANTIC_WELLS_KNOWLEDGE` |
| **Search Column** | CONTEXT_PROFILE |
| **Source** | SEMANTIC_DOMAIN_KNOWLEDGE (active, non-null profile) |
| **Rows** | 5,214 |
| **Refresh** | 1 min |
| **Purpose** | Primary domain knowledge retrieval for V3 agent |

**WELLVIEW_COLUMN_CATALOG_SEARCH**

| Field | Value |
|-------|-------|
| **FQN** | `DATASCIENCE_DEV.DS_ENT_EXP.WELLVIEW_COLUMN_CATALOG_SEARCH` |
| **Search Column** | SEARCH_TEXT |
| **Source** | WELLVIEW_COLUMN_CATALOG_V2 |
| **Rows** | 4,880 |
| **Refresh** | 1 min |
| **Purpose** | Column discovery for deterministic routing |

### Legacy/V2 (May be deprecated)

| Name | Source | Rows | Purpose |
|------|--------|------|---------|
| WELLVIEW_KNOWLEDGE_SEARCH | WELLVIEW_KNOWLEDGE_BASE_CURATED | 336 | V2 knowledge search (pre-V3) |
| WELLVIEW_KNOWLEDGE_SEARCH_V2 | WELLVIEW_KNOWLEDGE_BASE_V2 | 338 | V2 structured knowledge |
| WELLVIEW_SYNONYM_SEARCH | WELLVIEW_SYNONYM_MAP | 169 | V2 synonym resolution |

All legacy services are also in `DATASCIENCE_DEV.DS_ENT_EXP`.

### Auto-Generated (Cortex Analyst Filter Services)

These are auto-created by Cortex Analyst for semantic view filter columns:
- `_CORTEX_ANALYST_GW_ENH_WELLHEADER_V_COUNTRY_*`
- `_CORTEX_ANALYST_GW_ENH_WELLHEADER_V_FIELD_NAME_*`
- `_CORTEX_ANALYST_GW_ENH_WELLHEADER_V_REGION_*`
- `_CORTEX_ANALYST_GW_ENH_WELLHEADER_V_WELL_NAME_*`
- `_CORTEX_ANALYST_GW_ENH_WELLHEADER_V_WELL_TYPE_*`

---

## 7. Stored Procedures

All in `DATASCIENCE_DEV.DS_ENT_EXP`. All use `EXECUTE AS CALLER`.

| Name | Signature | Purpose |
|------|-----------|---------|
| DK_CHECK_ACCESS | `(VARCHAR) → VARIANT` | Verify user has ADMINISTRATOR access to a domain |
| DK_VALIDATE_ENTRY | `(VARCHAR×5, ARRAY) → VARIANT` | Dry-run validation: types, duplicates, SVs, content length |
| DK_INSERT_ENTRY | `(VARCHAR×4, ARRAY, VARIANT, FLOAT, VARCHAR×2) → VARIANT` | Insert with validation + auto CONTEXT_PROFILE |
| DK_UPDATE_ENTRY | `(VARCHAR, VARIANT, VARCHAR) → VARIANT` | Update fields + rebuild profile |
| DK_DEACTIVATE_ENTRY | `(VARCHAR×3) → VARIANT` | Soft-delete single entry |
| DK_BULK_DEACTIVATE | `(ARRAY, VARCHAR×2) → VARIANT` | Soft-delete multiple entries |
| DK_SEARCH_SIMILAR | `(VARCHAR×2, NUMBER, VARCHAR) → VARIANT` | Retrieval test via Cortex Search |

References:
- `DATASCIENCE_DEV.DS_ENT_EXP.SEMANTIC_DOMAIN_KNOWLEDGE`
- `DATASCIENCE_DEV.DS_ENT_EXP.DOMAIN_APP_ADMIN_USER`
- `DATASCIENCE_DEV.DS_ENT_EXP.CS_SEMANTIC_WELLS_KNOWLEDGE` (DK_SEARCH_SIMILAR only)

---

## 8. Tables — Domain Knowledge & Admin

All in `DATASCIENCE_DEV.DS_ENT_EXP`:

| Table | Rows | Purpose |
|-------|------|---------|
| SEMANTIC_DOMAIN_KNOWLEDGE | 5,249 | Primary knowledge store (SYNONYM, RULE, METRIC, PATTERN, COLUMN_DESC, HIERARCHY, TABLE_DESC) |
| DOMAIN_APP_ADMIN | 2 | Domain registry (Wells, Land) |
| DOMAIN_APP_ADMIN_USER | 5 | Access control: which users are administrators per domain |

### SEMANTIC_DOMAIN_KNOWLEDGE Schema

| Column | Type | Notes |
|--------|------|-------|
| ENTRY_ID | VARCHAR (PK) | UUID, auto-generated |
| ENTRY_TYPE | VARCHAR (NOT NULL) | SYNONYM/RULE/METRIC/PATTERN/COLUMN_DESC/HIERARCHY/TABLE_DESC |
| SUB_DOMAIN | VARCHAR | Category (DRILLING, EQUIPMENT, PRODUCTION, etc.) |
| NAME | VARCHAR (NOT NULL) | Short descriptive name |
| CONTENT | VARCHAR (NOT NULL) | Full knowledge text |
| CONTEXT_PROFILE | VARCHAR | Pre-computed search text (indexed by CS_SEMANTIC_WELLS_KNOWLEDGE) |
| PROPERTIES | VARIANT | JSON metadata (priority, formulas, canonical refs) |
| ACTIVE | BOOLEAN (default TRUE) | Soft-delete flag |
| CONFIDENCE | FLOAT (default 1.0) | 1.0=human, <1.0=auto-generated |
| PROMOTION_STATE | VARCHAR (default 'none') | none/candidate/promoted/retired |
| RETRIEVAL_COUNT | NUMBER (default 0) | Usage tracking |
| LAST_RETRIEVED | TIMESTAMP_NTZ | Last retrieval time |
| CREATED_AT | TIMESTAMP_NTZ | Creation time |
| UPDATED_AT | TIMESTAMP_NTZ | Last modification |
| SEMANTIC_VIEWS | ARRAY | SV names this entry routes to |
| CATEGORY | VARCHAR | Sub-classification (BUSINESS_RULE, CONSTRAINT, etc.) |
| ROW_IDENTIFIER | NUMBER | Sequential row number |
| DOMAIN_NAME | VARCHAR (default 'WELLS') | Multi-domain identifier |

---

## 9. Tables — Supporting Knowledge Bases & Catalogs

All in `DATASCIENCE_DEV.DS_ENT_EXP`:

| Table | Rows | Purpose |
|-------|------|---------|
| WELLVIEW_COLUMN_CATALOG_V2 | 4,880 | Column metadata for 147 source views across 12 domains. Source for WELLVIEW_COLUMN_CATALOG_SEARCH. |
| WELLVIEW_KNOWLEDGE_BASE_CURATED | 336 | Legacy V2 curated knowledge (superseded by SEMANTIC_DOMAIN_KNOWLEDGE) |
| WELLVIEW_KNOWLEDGE_BASE_V2 | 338 | Legacy V2 structured knowledge (superseded) |
| WELLVIEW_SYNONYM_MAP | 169 | Legacy V2 synonyms (superseded by SEMANTIC_DOMAIN_KNOWLEDGE SYNONYM entries) |
| WELLVIEW_AGENT_TEST_QUESTIONS | 92 | Test question suite for agent evaluation |
| SEMANTIC_DOMAIN_KNOWLEDGE_PRE_V2_SCOPE | 5,224 | Backup/snapshot of pre-expansion data |

---

## 10. Streamlit Applications

### DK_MANAGER

| Field | Value |
|-------|-------|
| **FQN** | `DATASCIENCE_DEV.DS_ENT_EXP.DK_MANAGER` |
| **Purpose** | Domain Knowledge Manager — browse, edit, AI-author, validate, and test domain knowledge entries |
| **Workspace** | `USER$.PUBLIC."Wells"/dk_manager/` |

### DK_MANAGER Application Structure
```
dk_manager/
├── streamlit_app.py           # Entry point (identity resolution, navigation)
├── modules/
│   ├── dk_db.py               # Database operations (direct SQL)
│   ├── profiler.py            # CONTEXT_PROFILE generation
│   ├── validator.py           # Entry validation
│   └── ai_author.py           # LLM-based entry generation
└── pages/
    ├── 0_Home.py              # Domain overview & statistics
    ├── 1_Knowledge_Browser.py # Search, filter, edit entries
    ├── 2_AI_Authoring.py      # Generate entries from natural language
    ├── 5_Semantic_Search_Testing.py # Test retrieval results
    └── 6_Admin.py             # Manage admin users
```

---

## 11. Stages

All in `DATASCIENCE_DEV.DS_ENT_EXP`:

| Name | Type | Purpose |
|------|------|---------|
| TMP | Internal | App assets (icons, logos), temporary files |
| TMP_CSV_EXPORT | Internal | CSV export staging |

---

## 12. Source Data (ENT_PRD.WELLS)

The Wells domain semantic views query **147 source views** in `ENT_PRD.WELLS` prefixed `WV_*`. These are production WellView database views and are NOT part of this migration (they should already exist in the target production database as `ENT_PRD.WELLS.WV_*`).

Key source views include:
- `WV_WELLHEADER` — Well master/header
- `WV_JOB` — Job/operations
- `WV_JOB_REPORT_TIMELOG` — Daily timelogs
- `WV_STIM` / `WV_STIM_INT` — Stimulations
- `WV_PROBLEM` — Equipment failures
- `WV_CAS` / `WV_CEMENT` — Casing & cement
- `WV_ROD` / `WV_TUB` — Rods & tubing
- `WV_ZONE` / `WV_PERFORATION` — Completions
- `WV_WELLBORE_DIR_SURVEY` — Directional surveys

---

## 13. Warehouses Referenced

| Warehouse | Used By |
|-----------|---------|
| `AK_DEV_ADHOC_WH` | Agent tools (procedures), MCP sql_executor |
| `DATASCIENCE_DEV_ADHOC_WH` | CS_SEMANTIC_WELLS_KNOWLEDGE refresh |
| `DATASCIENCE_DEV_DEVOPS_WH` | WELLVIEW_COLUMN_CATALOG_SEARCH refresh |
| `DATASCIENCE_DEV_ENT_WH1` | DK_MANAGER Streamlit |
| `GENERAL_WH` | Legacy search services |

---

## 14. Migration Dependency Order

Objects must be created in this order (each step depends on the previous):

### Layer 1: Source Data (verify exists)
- [ ] `ENT_PRD.WELLS.WV_*` (147 views) — should already exist in production

### Layer 2: Tables (no dependencies)
- [ ] `SEMANTIC_DOMAIN_KNOWLEDGE` (with data — export/import required)
- [ ] `DOMAIN_APP_ADMIN` (2 rows)
- [ ] `DOMAIN_APP_ADMIN_USER` (configure for production users)
- [ ] `WELLVIEW_COLUMN_CATALOG_V2` (with data)

### Layer 3: Cortex Search Services (depend on tables)
- [ ] `CS_SEMANTIC_WELLS_KNOWLEDGE` (depends on SEMANTIC_DOMAIN_KNOWLEDGE)
- [ ] `WELLVIEW_COLUMN_CATALOG_SEARCH` (depends on WELLVIEW_COLUMN_CATALOG_V2)

### Layer 4: Semantic Views (depend on source data)
- [ ] All 15+ `SV_ENT_WELLS_*` semantic views in target BI schema (depend on ENT_PRD.WELLS views)
- [ ] `SV_DOMAIN_KNOWLEDGE_ADMIN` (depends on SEMANTIC_DOMAIN_KNOWLEDGE + DOMAIN_APP_ADMIN_USER)

### Layer 5: Stored Procedures (depend on tables + search services)
- [ ] `DK_CHECK_ACCESS` (depends on DOMAIN_APP_ADMIN_USER)
- [ ] `DK_VALIDATE_ENTRY` (depends on SEMANTIC_DOMAIN_KNOWLEDGE)
- [ ] `DK_INSERT_ENTRY` (depends on DK_CHECK_ACCESS, DK_VALIDATE_ENTRY, SEMANTIC_DOMAIN_KNOWLEDGE)
- [ ] `DK_UPDATE_ENTRY` (depends on DK_CHECK_ACCESS, SEMANTIC_DOMAIN_KNOWLEDGE)
- [ ] `DK_DEACTIVATE_ENTRY` (depends on DK_CHECK_ACCESS, SEMANTIC_DOMAIN_KNOWLEDGE)
- [ ] `DK_BULK_DEACTIVATE` (depends on DK_CHECK_ACCESS, SEMANTIC_DOMAIN_KNOWLEDGE)
- [ ] `DK_SEARCH_SIMILAR` (depends on CS_SEMANTIC_WELLS_KNOWLEDGE)

### Layer 6: Agents (depend on everything above)
- [ ] `AGENT_WELLS_V3` (depends on SV_ENT_WELLS_*, CS_SEMANTIC_WELLS_KNOWLEDGE, WELLVIEW_COLUMN_CATALOG_SEARCH)
- [ ] `DK_MANAGEMENT_AGENT` (depends on SV_DOMAIN_KNOWLEDGE_ADMIN, CS_SEMANTIC_WELLS_KNOWLEDGE, all DK_* procedures)

### Layer 7: MCP Server (depends on semantic views + search services)
- [ ] `SF_MCP_ENT_WELLS` in target AI schema (depends on all SV_ENT_WELLS_* + CS_SEMANTIC_WELLS_KNOWLEDGE)
- **Note:** Determine target database/schema — currently in `ENT_DEV.ENT_AI`, separate from agent objects

### Layer 8: Applications (depend on agents + tables)
- [ ] `DK_MANAGER` Streamlit (depends on SEMANTIC_DOMAIN_KNOWLEDGE, DOMAIN_APP_ADMIN_USER, CS_SEMANTIC_WELLS_KNOWLEDGE)

### Layer 9: Access Grants
- [ ] Grant USAGE on agents to consumer roles
- [ ] Grant SELECT on tables to procedure execution roles
- [ ] Configure DOMAIN_APP_ADMIN_USER for production users

---

## 15. Migration Notes

### Database/Schema References to Update

All objects reference `DATASCIENCE_DEV.DS_ENT_EXP` or `DATASCIENCE_DEV.DS_ENT_BI`. When migrating to production:

1. **Stored procedures** — contain hardcoded FQNs to `DATASCIENCE_DEV.DS_ENT_EXP.*` tables and search services. These must be updated to the production database/schema.

2. **Semantic views** — reference `ENT_PRD.WELLS.WV_*` source views. If the production database already has these, no change needed. If the database name differs, update the `base_table` references in each semantic view YAML.

3. **Cortex Search service definitions** — the SQL defining what rows to index references table FQNs. Update the `definition` SQL.

4. **Agent specifications** — tool_resources reference FQNs for semantic views, search services, and procedure names. Update all FQNs in the agent spec YAML.

5. **Streamlit app** — module code (`dk_db.py`) references table FQNs via the `DOMAIN_APP_ADMIN` registry table, which stores `DOMAIN_TABLE_FQN` and `CORTEX_SEARCH_FQN`. Update those rows rather than code.

6. **MCP server specification** — the `server_spec` JSON contains hardcoded FQNs for every tool's `identifier` field (search service and semantic views) plus the `sql_executor` warehouse name. Export the spec, find/replace database references, and recreate with `CREATE MCP SERVER ... FROM SPECIFICATION`.

### MCP Server Export/Recreate

```sql
-- Export current spec (use DESCRIBE output from dev)
DESCRIBE MCP SERVER ENT_DEV.ENT_AI.SF_MCP_ENT_WELLS;

-- Recreate in production (after updating FQNs in the spec JSON)
CREATE MCP SERVER <PROD_DB>.<PROD_SCHEMA>.SF_MCP_ENT_WELLS
  FROM SPECIFICATION $$ <updated_spec_json> $$;
```

### Data Export Requirements

| Object | Export Method | Notes |
|--------|--------------|-------|
| SEMANTIC_DOMAIN_KNOWLEDGE | `COPY INTO @stage` or CTAS | 5,249 rows; VARIANT and ARRAY columns |
| DOMAIN_APP_ADMIN | Manual INSERT (2 rows) | Update FQNs in DOMAIN_TABLE_FQN column |
| DOMAIN_APP_ADMIN_USER | Manual INSERT | Configure production admin usernames |
| WELLVIEW_COLUMN_CATALOG_V2 | `COPY INTO @stage` or clone | 4,880 rows |

### Workspace Files

The DK_MANAGER Streamlit source code resides in workspace `USER$.PUBLIC."Wells"/dk_manager/`. For production deployment, the Streamlit app should be deployed from a shared workspace or directly from stage files.

### Agent YAML Specs

Agent specifications are stored in workspace at:
- `cortex_project/DK_MANAGEMENT_AGENT.agent.yaml`

The AGENT_WELLS_V3 spec should be exported via `DESCRIBE AGENT` or the Snowsight UI for migration.

### Roles and Grants (Production)

Minimum grants needed for the solution to function:
```sql
-- Consumer role (end users querying the agent)
GRANT USAGE ON DATABASE <prod_db> TO ROLE <consumer_role>;
GRANT USAGE ON SCHEMA <prod_db>.<schema> TO ROLE <consumer_role>;
GRANT USAGE ON AGENT <prod_db>.<schema>.AGENT_WELLS_V3 TO ROLE <consumer_role>;
GRANT USAGE ON AGENT <prod_db>.<schema>.DK_MANAGEMENT_AGENT TO ROLE <consumer_role>;
GRANT USAGE ON WAREHOUSE <wh> TO ROLE <consumer_role>;

-- Agent execution needs
GRANT SELECT ON TABLE <prod_db>.<schema>.SEMANTIC_DOMAIN_KNOWLEDGE TO ROLE <agent_role>;
GRANT SELECT ON TABLE <prod_db>.<schema>.DOMAIN_APP_ADMIN_USER TO ROLE <agent_role>;
GRANT SELECT ON TABLE <prod_db>.<schema>.WELLVIEW_COLUMN_CATALOG_V2 TO ROLE <agent_role>;
GRANT SELECT ON ALL VIEWS IN SCHEMA ENT_PRD.WELLS TO ROLE <agent_role>;
```
