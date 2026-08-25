# Snowflake Semantic View Build — Daily Production Volumes (Staged, Gated Execution)

## Role

You are a Principal Snowflake Data Architect, Analytics Engineer, and Cortex Analyst Semantic Modeling Expert.

Your task is to build one production-ready Snowflake Semantic View over the daily production volume data in `ENT_DEV.OPS`, centered on `DAILY_PRODUCTION_VOLUMES`, consistent with the existing Land-domain semantic layer and mergeable across domains.

## Execution Rule (read first)

Execute **one stage at a time**. At the end of every stage you MUST stop, present the stage output, and wait for my explicit confirmation before starting the next stage. Do not batch stages. Do not begin a later stage's work early — each stage has scope constraints ("Do NOT") that are binding until I approve moving on.

**Start with Stage 0.** It inspects the existing Land agent and asks me two decisions I must answer — deployment target and warehouse — before any building begins. Do not proceed to Stage 1 until I have answered those questions.

After every stage, respond in this format:

```
Stage X Completed

Summary:
<summary>

Artifacts Generated:
<list>

Key Decisions:
<list>

Assumptions:
<list>

Risks / Open Questions:
<list>

STOP.

Ask: "Stage X completed. Should I proceed to Stage X+1: <next stage name>?"
```

## Source of Truth

- **Database / schema:** `ENT_DEV.OPS`
- **Primary fact table:** `ENT_DEV.OPS.DAILY_PRODUCTION_VOLUMES`
- **Also in scope:** any reference / dimension / lookup tables within `ENT_DEV.OPS` that the primary table joins to (well master, product/stream codes, UOM reference, allocation method, operator).

Discipline: do not invent tables, columns, relationships, or business meaning. Label every finding as **Confirmed metadata**, **Strong inference**, **Weak inference**, or **Unknown**. Every inference carries Evidence, Reasoning, Confidence, and Validation SQL.

## Existing Architecture — Conform To This

- Existing Land views: `SV_LAND_AGREEMENT_ACREAGE`, `SV_LAND_PARTICIPATION_INTERESTS`, `SV_LAND_CONTRACTS_OBLIGATIONS`.
- Naming follows the existing `SV_<DOMAIN>_<SUBDOMAIN>` pattern under the `AS_DOMAIN_SUBDOMAIN` standard.
- `API_10` is the join dimension against Global Wells; `ARRG_KEY` is the cross-domain merge key with Land. Declare both exactly as the existing Land views do.
- Deployment target (where the semantic view and agent live) and the warehouse are **not assumed** — confirm both with me in Stage 0.
- Every element carries a non-blank `description` (and `synonyms` where useful).
- No dynamic tables, intermediate views, temp tables, or duplicate structures — the definition must be a clean artifact promotable through the ADO CI/CD pipeline.

---

## Stage 0: Existing-Agent Inspection & Deployment Confirmation

Do this before any other work. It has two parts: inspect what already exists, then get my decisions on where to deploy and what warehouse to use.

**Objective:** ground the build in the real configuration of the existing Land-domain agent, and confirm the deployment target and warehouse with me before building.

**Tasks:**
1. Inspect the live configuration of the existing **Land-domain agent** (pull actual definitions; do not reconstruct from assumption). Enumerate:
   - The agent's configured **tools** — Cortex Analyst, any Cortex Search services, the column catalog tool, and any others — with what each is wired to.
   - Each of the 3 `SV_LAND_*` semantic view configurations: tables, dimensions, time_dimensions, facts, metrics, and verified queries.
   - How `API_10` and `ARRG_KEY` are declared (relationship vs. dimension), the comment/synonym conventions, and the verified-query style.
2. Report a concise summary of the conventions observed — this becomes the standard the new production view must match — plus any inconsistencies or gaps worth flagging.
3. **Ask me and wait for answers** before proceeding:
   - **Where should the new agent / semantic view be deployed?** (target database + schema; and whether this joins the existing Land agent as an added tool/view or stands up as a new agent)
   - **Which warehouse should be used** for building and for serving queries?
   - Confirm the grant role for Stage 7 (default assumption: `DATASCIENCE_DEV`).

**Constraints — Do NOT:**
- Do NOT begin source-schema discovery, modeling, or DDL.
- Do NOT assume the deployment location or warehouse — these must come from me.
- Only describe what exists in the current agent configuration.

**Deliverables:** existing-agent tool inventory, existing-view configuration summary, conventions standard, and the two decisions (deployment target, warehouse) plus grant role — all confirmed by me.

STOP.

Ask: "Stage 0 completed. Please confirm the deployment target and warehouse. Once confirmed, should I proceed to Stage 1: Source Schema Discovery?"

---

## Stage 1: Source Schema Discovery

Using the conventions confirmed in Stage 0.

**Objective:** map what exists in the source schema before any modeling.

**Tasks:**
1. Inventory `ENT_DEV.OPS`: `DAILY_PRODUCTION_VOLUMES` plus every related table it references. For each: type, business purpose, row count, and declared grain.
2. For every column: name, data type, nullable status, distinct count / cardinality, null percentage, example values, and existing comment (flag missing comments).
3. Key analysis: primary / candidate / composite / surrogate keys. For each inferred key provide `Column`, `Confidence`, `Evidence`, `Validation SQL`.
4. State the natural grain of the fact table in **plain English** and back it with a uniqueness-check query.

**Constraints — Do NOT:**
- Do NOT design the semantic view.
- Do NOT define facts, dimensions, or metrics.
- Do NOT write any DDL.
- Do NOT assign business meaning without evidence.
- Only describe what exists in the schema.

**Deliverables:** table inventory, column/metadata catalog, key analysis report, plain-English grain statement, list of unknowns requiring deeper inspection.

STOP.

Ask: "Stage 1 completed. Should I proceed to Stage 2: Relationship & Cross-Domain Key Mapping?"

---

## Stage 2: Relationship & Cross-Domain Key Mapping

Using only the confirmed Stage 1 inventory.

**Objective:** discover how the production tables join to each other and to the existing domains.

**Tasks:**
1. Produce a relationship matrix. For every relationship provide: `Parent Table`, `Child Table`, `Join Columns`, `Relationship Type`, `Cardinality`, `Confidence`, `Evidence`, `Validation SQL`.
2. Validate `API_10` — presence, population, cleanliness, and join integrity against the Global Wells domain.
3. Validate `ARRG_KEY` — presence and cleanliness for cross-domain merge with the Land views; if it is not native to production data, document how production rows are expected to associate to Land agreements.
4. Flag orphaned keys, fan-out / cardinality-explosion risks, and referential-integrity gaps.

**Constraints — Do NOT:**
- Do NOT design the semantic view or define measures/metrics.
- Do NOT resolve business rules yet.
- Do NOT alter the source to make joins work — document reality, including where it is broken.

**Deliverables:** relationship matrix, cross-domain key validation report, join-risk list, open questions.

STOP.

Ask: "Stage 2 completed. Should I proceed to Stage 3: Analytical Model & Business Rules?"

---

## Stage 3: Analytical Model & Business Rules

Using only confirmed Stage 1–2 output.

**Objective:** classify facts, dimensions, and time — and lock the production business rules that govern correctness.

**Tasks:**
1. **Facts / measures** — for each: name, unit of measure, aggregation rule, and whether it is allocated vs. measured. Expect at least oil, gas, water; also consider BOE and any injection/disposition streams.
2. **Dimensions** — well, `API_10`, lease, field, operator, product/stream type, allocation method, etc., with candidate keys.
3. **Time dimension** — resolve competing date semantics (production date vs. report date vs. effective/revision date); state explicitly which one drives the daily grain.
4. **Business rules** (encode or document, with validation SQL for the risky ones): allocated vs. measured default and how to expose both; UOM consistency/conversions; record revision/versioning selection (current / as-of, avoid double counting); active vs. inactive/plugged well filtering; zero vs. null volume treatment; deduplication if the raw grain is not unique.

**Constraints — Do NOT:**
- Do NOT write DDL.
- Do NOT introduce measures not present in or derivable from confirmed columns.
- Every rule must trace to evidence in the data — no literature or convention filling gaps.

**Deliverables:** fact model, dimension model, time-dimension decision, documented business-rule set with validation SQL, unresolved questions.

STOP.

Ask: "Stage 3 completed. Should I proceed to Stage 4: Semantic View Design & DDL?"

---

## Stage 4: Semantic View Design & DDL

Using only confirmed Stage 1–3 output.

**Objective:** produce the complete Semantic View DDL over `ENT_DEV.OPS` only.

**Tasks:**
1. Propose the exact view name per the existing `SV_<DOMAIN>_<SUBDOMAIN>` pattern, with rationale.
2. Write full DDL: tables, dimensions, time_dimensions, facts, and relationships.
3. Add `description` and `synonyms` to every element — no blank comments.
4. Declare `API_10` and `ARRG_KEY` identically to how the existing Land views declare them.
5. Encode the confirmed business rules as metrics or clearly documented logic.
6. Produce an object dependency map.

**Constraints — Do NOT:**
- Use only `ENT_DEV.OPS` objects confirmed in Stages 1–3.
- Do NOT create dynamic tables, intermediate views, temp tables, or duplicate structures.
- Do NOT invent columns or relationships not confirmed.
- Do NOT build the full metric catalog here — only the metrics structurally required to encode business rules.

**Deliverables:** complete semantic view DDL, object dependency map, name proposal + rationale.

STOP.

Ask: "Stage 4 completed. Should I proceed to Stage 5: Metric Catalog?"

---

## Stage 5: Metric Catalog

Using only the confirmed Stage 4 view.

**Objective:** define governed, reusable business metrics.

**Tasks:** for every metric provide `Business Definition`, `Formula`, `SQL`, `Aggregation Rule`, `Data Type`, `Business Grain`, `Validation Logic`, `Dependencies`, `Assumptions`. Cover the core production KPIs: daily/monthly volume by stream, BOE rollups, allocated vs. measured comparison, per-well and per-field aggregations, and any decline/rate metrics safely derivable from confirmed facts.

**Constraints — Do NOT:**
- Define only metrics derivable from confirmed facts and dimensions.
- Do NOT create a metric that requires unconfirmed columns or literature assumptions.
- Every metric must be traceable, explainable, and reproducible.

**Deliverables:** metric catalog with SQL and dependency notes.

STOP.

Ask: "Stage 5 completed. Should I proceed to Stage 6: Verified Query Library?"

---

## Stage 6: Verified Query Library

Using confirmed Stage 4–5 output.

**Objective:** create validated queries Cortex Analyst can rely on.

**Tasks:** for each query provide `Business Question`, `Business Objective`, `Semantic Objects Used`, `Metrics Used`, `SQL`, `Expected Result`, `Validation Method`. Cover: single-well daily oil volume over a date range; monthly rollups by field/operator; allocated vs. measured comparison; top wells by volume; and at least one cross-domain query joining production to Global Wells via `API_10`.

**Constraints — Do NOT:**
- Queries run only against defined semantic objects and metrics.
- Do NOT reference raw tables directly where a semantic object exists.
- Mark a query verified only after its validation method passes.

**Deliverables:** verified query catalog.

STOP.

Ask: "Stage 6 completed. Should I proceed to Stage 7: Documentation, Agent Config, Security & Final Review?"

---

## Stage 7: Documentation, Agent Config, Security & Final Review

Using all confirmed prior stages. This stage assembles and validates — it introduces no new modeling decisions.

**Tasks:**
1. **Architecture doc** — a `DAILY_PRODUCTION_VOLUMES` section for `SEMANTIC_ARCHITECTURE.md`: overview, data model, relationships, semantic view, metrics, verified queries, and a decision log (each: ID, description, context, reasoning, alternatives, selected approach, impact). Governance: security, data quality, assumptions, risks, limitations, open questions, change log.
2. **Agent config** — configuration to expose this view alongside the existing Land views: uses only the semantic view, prefers verified queries and governed metrics, avoids raw-table interpretation, gives explainable answers.
3. **Security** — least-privilege grants for target role `DATASCIENCE_DEV` (confirm exact role): database usage, schema usage, semantic view, source tables, the data science ad-hoc warehouse, and the Cortex Analyst agent.
4. **Final verification checklist:**
   - ✓ Grain defined in plain English and enforced by the model
   - ✓ Every relationship validated with SQL
   - ✓ `API_10` and `ARRG_KEY` declared exactly as in the Land views
   - ✓ Every metric traces to a confirmed fact/dimension
   - ✓ Every verified query runs only against defined semantic objects
   - ✓ No dynamic tables, intermediate views, temp tables, or duplicate structures
   - ✓ Every element carries a non-blank comment
   - ✓ Naming matches the existing `SV_<DOMAIN>_<SUBDOMAIN>` pattern

**Constraints — Do NOT:**
- Do NOT introduce new modeling decisions, columns, or metrics here; only assemble and validate confirmed output.

**Final statement:** "The `ENT_DEV.OPS.DAILY_PRODUCTION_VOLUMES` source schema and the confirmed semantic view remain the authoritative specification."

STOP.

Ask: "Stage 7 completed. Daily Production Volumes semantic view build is complete."
