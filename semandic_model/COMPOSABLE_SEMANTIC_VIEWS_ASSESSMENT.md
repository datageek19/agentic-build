# Composable Semantic Views: Impact Assessment for Agentic Well Data Architecture

**Status:** Private Preview (not yet in production — selected accounts only)  
**Relevance:** Directly addresses the cross-sub-domain join limitation that forced our OPERATIONS view consolidation  
**Recommendation:** Monitor for GA; prepare two-layer refactoring plan  

---

## 1. The Problem Composable Views Solve

Our current architecture enforces a hard constraint: **Cortex Analyst generates SQL against exactly one semantic view per call.** Each view is an isolated structural contract — its declared tables, joins, dimensions, and facts exist in a closed world. There is no mechanism for Analyst to generate a single SQL statement that joins tables from View A with tables from View B.

This creates a ceiling on cross-sub-domain queries. When a user asks "show me drilling cost per foot for wells with casing failures," the answer requires:

- **Drilling tables** (depth drilled, drilling days) — from OPERATIONS_DRILLING
- **Cost tables** (expenditure line items) — from OPERATIONS_COSTS  
- **Casing tables** (failure records) — from CASING_CEMENT_WELLHEADS

With independent views, the agent has three options — all of them inadequate:

| Approach | Problem |
|----------|---------|
| Call 3 views separately, merge results | No SQL-level join; can't compute ratios, filter across tables, or aggregate correctly across different grains |
| Call 2 views (our cap), lose one | Incomplete answer — user doesn't get what they asked for |
| Consolidate views at DDL time | Forces maintainability tradeoffs; inflates token count; conflates separate concerns |

We chose the third option — consolidating 4 OPERATIONS views into 1 (~41K tokens). This works but introduces maintenance burden: changes to drilling definitions require redeploying a view that also contains job management, rig equipment, and cost definitions.

---

## 2. What Composable Semantic Views Introduce

The feature adds an `IMPORTS` clause to `CREATE SEMANTIC VIEW`. One view can pull in the full set of tables, dimensions, facts, metrics, and relationships from one or more other semantic views. The result is a single unified view that Analyst queries as one object, with all imported join paths resolved internally.

```sql
CREATE SEMANTIC VIEW SV_Q_WELLS_DRILLING_COSTS
  IMPORTS (
    DATASCIENCE_DEV.DS_ENT_EXP.SV_ENT_WELLS_DRILLING,
    DATASCIENCE_DEV.DS_ENT_EXP.SV_ENT_WELLS_COSTS,
    DATASCIENCE_DEV.DS_ENT_EXP.SV_ENT_WELLS_CASING_CEMENT_WELLHEADS
  )
  -- Can define new metrics that span imported entities:
  METRICS (
    drill_runs.cost_per_foot AS costs.m_total_expenditure / drill_runs.f_total_depth
  );
```

### Key mechanics

| Capability | What It Means |
|------------|---------------|
| **Automatic subgraph resolution** | Snowflake imports the minimal set of entities and relationships needed to connect the imported calculations. Join paths between imported tables come automatically. |
| **Late binding** | Composed views resolve at query time. Source view changes propagate without redeploying the composed view. |
| **Selective imports** | You can import only specific dimensions, facts, or metrics — controlling token budget. |
| **Diamond import deduplication** | If multiple imported views share a common ancestor (e.g., all import a base WELLHEADER view), the shared entities appear exactly once. |
| **Transitive resolution** | If View A imports View B, and View B imports View C, then View A can query all entities from B and C. |
| **Cross-schema/cross-database** | Any view importable if the role has REFERENCES privilege. |

---

## 3. Why IMPORTS Solves Multi-Sub-Domain Queries

The fundamental improvement is that **authoring granularity no longer dictates query granularity.**

Today, we face a forced tradeoff:

```
Fine-grained views (good for maintenance)  ←→  Broad views (good for cross-domain queries)
                    ↑
          Can't have both — we chose broad (consolidation)
```

With composable views:

```
Fine-grained AUTHORING views (good for maintenance)
            ↓ IMPORTS
Broad QUERY views (good for cross-domain queries)
            ↑
    Both exist simultaneously — no tradeoff
```

### Specifically, IMPORTS enables:

**1. A single Analyst call generates SQL with proper cross-sub-domain joins.**

When the composed view imports DRILLING and COSTS, Analyst sees both sets of tables within one structural contract. It generates `JOIN` clauses between them using the imported relationships. This is real SQL-level composition — not the agent trying to stitch separate result sets together.

Without IMPORTS: The agent calls DRILLING, gets depth data. Calls COSTS, gets expenditure data. Cannot compute `cost / depth` because that requires a JOIN the agent cannot author.

With IMPORTS: Analyst generates `SELECT cost.total / drill.depth FROM costs JOIN drill_runs ON ... WHERE ...` — a single, correct SQL statement.

**2. The anchor entity (WV_WELLHEADER) appears once via diamond resolution.**

Every sub-domain view joins through WELL_ID back to the wellheader. With composable views, if multiple imported views each import a shared base WELLHEADER view, Snowflake deduplicates it. The composed view has one WELLHEADER entity with all filter dimensions (BU, AREA, STATUS, etc.) available for cross-domain filtering.

**3. Metrics that span sub-domains become declarable.**

Today, we cannot formally define "cost per foot drilled" because the numerator and denominator live in different views. With composition, the composed view can declare cross-domain metrics as first-class objects — Analyst knows the formula and generates correct SQL.

**4. The 2-view routing cap becomes unnecessary.**

The cap existed because each view call is independent — calling 3 views means 3 separate SQL statements that cannot be joined. With composed views, the agent still calls ONE view; it just happens to contain the entities from what were previously 3 separate views. The routing problem shifts from "which 1-2 individual views?" to "which composed view covers this combination?"

---

## 4. Proposed Future Architecture: Two-Layer View Structure

### Layer 1: Authoring Views (fine-grained, single-concern)

These are the views that humans maintain. Each covers a natural sub-domain boundary:

```
SV_ENT_WELLS_BASE              — WV_WELLHEADER + core reference (shared anchor)
SV_ENT_WELLS_DRILLING          — BHA runs, drill parameters, bit performance
SV_ENT_WELLS_JOB_CORE          — Job headers, phases, dates
SV_ENT_WELLS_RIG_EQUIPMENT     — Rig assignments, BOP, crew, pumps
SV_ENT_WELLS_COSTS             — Cost items, AFE, expenditure categories
SV_ENT_WELLS_CASING            — Casing strings, cement jobs, wellheads
SV_ENT_WELLS_GEOLOGICAL        — Formations, mud logs, surveys
...
```

Each authoring view:
- Is small (10-20K tokens)
- Has a single owner team
- Changes independently
- Contains minimal descriptions (structural only)

### Layer 2: Query Views (composed, cross-domain)

These are the views the agent routes to. Each imports 2-4 authoring views covering a known cross-domain query pattern:

```sql
-- Operations + costs (drilling economics questions)
CREATE SEMANTIC VIEW SV_Q_WELLS_DRILLING_ECONOMICS
  IMPORTS (SV_ENT_WELLS_BASE, SV_ENT_WELLS_DRILLING, SV_ENT_WELLS_COSTS)
  METRICS (...cross-domain metrics...);

-- Operations + casing + integrity (wellbore health questions)  
CREATE SEMANTIC VIEW SV_Q_WELLS_WELLBORE_HEALTH
  IMPORTS (SV_ENT_WELLS_BASE, SV_ENT_WELLS_CASING, SV_ENT_WELLS_INTEGRITY_BARRIERS)
  METRICS (...);

-- Full operations (current consolidated view — but now via imports)
CREATE SEMANTIC VIEW SV_Q_WELLS_OPERATIONS_FULL
  IMPORTS (SV_ENT_WELLS_BASE, SV_ENT_WELLS_DRILLING, SV_ENT_WELLS_JOB_CORE, 
           SV_ENT_WELLS_RIG_EQUIPMENT, SV_ENT_WELLS_COSTS);
```

### How routing adapts

The knowledge store's `SEMANTIC_VIEWS` field would point to Layer 2 (query views). The context search still resolves meaning and provides routing hints. The agent still follows the two-step deterministic flow. The only change is that its routing targets are composed views rather than monolithic ones.

---

## 5. Benefits Over Current Consolidated Approach

| Dimension | Current (Consolidated) | Future (Composed) |
|-----------|----------------------|-------------------|
| **Maintenance** | Change to drilling logic requires redeploying a 41K-token view containing job, rig, and cost definitions | Change only the 15K-token drilling authoring view; composed views inherit automatically via late binding |
| **Change isolation** | Schema change in rig equipment could break drilling verified queries (shared view) | Schema change in rig equipment only affects SV_ENT_WELLS_RIG_EQUIPMENT; other authoring views untouched |
| **Cross-domain queries** | Only works within the pre-consolidated boundary | Works across any combination of authoring views via purpose-built query views |
| **Token budget control** | Consolidation inflates tokens (all sub-domains in one view even when query only needs 2) | Selective imports let you compose exactly what's needed — smaller effective token count per query |
| **Metric governance** | Cross-domain metrics impossible to declare formally | Cross-domain metrics defined once in the composed view, reusable across queries |
| **Team ownership** | One team owns the monolithic operations view | Each sub-domain team owns their authoring view; a central team manages composition |
| **Verified queries** | All verified queries in one view (hard to scope) | Verified queries can live in authoring views (domain-specific) or query views (cross-domain) |

---

## 6. What Does NOT Change

The knowledge store, context search, agent flow, and clarification protocol remain essential regardless of composable views:

| Component | Role With Composable Views |
|-----------|---------------------------|
| Domain knowledge table | Still resolves "Permian" → BU filter, still enforces business rules, still provides metric formulas |
| Cortex Search service | Still the mandatory first step — meaning resolution before SQL generation |
| Routing via SEMANTIC_VIEWS | Points to composed query views instead of monolithic views — same mechanism |
| Clarification protocol | Ambiguous terms still need human resolution before any SQL is generated |
| Two-step deterministic flow | Unchanged: search → route → enriched question → Analyst |
| 1 search call per turn | Unchanged |
| GENERAL domain entries | Still applied universally regardless of which composed view is the target |

---

## 7. Current Limitations (Private Preview)

These must be resolved or worked around before adoption:

| Limitation | Impact on Our Design | Mitigation |
|------------|---------------------|------------|
| **Snowsight not supported** — can't view/manage composed views in UI | Significant — our team uses Snowsight for view management | Manage via SQL scripts in workspaces; use DESCRIBE SEMANTIC VIEW MODE = EXPANDED for inspection |
| **Not replicated** — skipped during account/database replication | Relevant if we have multi-account (dev/prod) setups | Recreate composed views in target account as part of deployment pipeline |
| **Variables not supported** | Low impact — we don't currently use semantic view variables | None needed |
| **Name collisions across imports** cause creation failure | Moderate risk — multiple views defining same logical alias for WELLHEADER | Enforce naming convention: prefix all entities with sub-domain abbreviation |
| **Late binding failure (error 000904)** if source view removes referenced object | Risk of query-time errors if authoring views change without awareness of downstream compositions | Add validation check: before altering an authoring view, query SHOW SEMANTIC VIEWS to find which composed views import it |

---

## 8. Migration Path

### When to act
When the feature reaches GA and the Snowsight limitation is resolved (or our team adopts SQL-based view management).

### Step 1: Create Base Anchor View
Extract WV_WELLHEADER and core reference tables into `SV_ENT_WELLS_BASE`. All other authoring views import this.

### Step 2: Decompose OPERATIONS_CORE
Split the consolidated 41K-token view back into its natural sub-domains:
- SV_ENT_WELLS_DRILLING (~15K tokens)
- SV_ENT_WELLS_JOB_CORE (~12K tokens)
- SV_ENT_WELLS_RIG_EQUIPMENT (~14K tokens)

Verify each works independently with Analyst for single-sub-domain questions.

### Step 3: Create Composed Query Views
Build 3-5 composed views covering known cross-domain patterns. Use evaluation data (which questions needed multiple views) to determine the combinations.

### Step 4: Update Knowledge Store Routing
Change `SEMANTIC_VIEWS` arrays in knowledge entries to point at composed query views. Run retrieval quality validation.

### Step 5: Update Agent/MCP Routing
Replace the individual view tools with composed view tools. The 2-view cap can be relaxed or removed since composed views handle cross-domain joins internally.

### Step 6: Validate
Run the standard three-layer validation (retrieval → SQL generation → end-to-end). Pay special attention to cross-domain questions that previously failed or required clarification.

---

## 9. Summary

Composable semantic views address the single most impactful platform constraint in our current design: the inability to join across semantic view boundaries within a single Analyst call. This forced us to consolidate sub-domains at the cost of maintainability and token efficiency.

The feature introduces a clean separation between **authoring** (fine-grained, single-team ownership, small token footprint) and **querying** (composed, cross-domain, purpose-built for known query patterns). Our architectural approach — context search for meaning resolution, deterministic routing, clarification protocol — remains unchanged and continues to provide the interpretation layer that composable views do not address.

**When GA is available:** refactor to two-layer views, decompose OPERATIONS, and update routing. The knowledge store, search service, and agent flow remain as-is.
