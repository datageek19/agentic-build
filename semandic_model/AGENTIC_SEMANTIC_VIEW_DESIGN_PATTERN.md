# Design Pattern: Deterministic Agentic Text-to-SQL over Large Data Estates

**Purpose:** Reusable architectural blueprint for building deterministic natural-language-to-SQL systems using Snowflake Cortex (Search + Analyst + Agent/MCP) over domains with many tables, complex terminology, and business rules that cannot be encoded in a single semantic view.

**Audience:** Data engineers, architects, and AI/ML teams building agentic systems over new domains.

---

## 1. When to Use This Pattern

This pattern applies when your domain has:

- **More than 20 source tables** with numerous columns that exceed practical semantic view token budgets (~100K tokens) if combined into one view
- **Domain-specific jargon** that users employ to refer to columns, filters, or concepts that don't match physical column names
- **Business rules** that constrain valid queries (mandatory filters, calculation conventions, exclusion logic)
- **Cross-cutting concepts** — terms, hierarchies, or rules that apply across multiple logical groupings of tables
- **A need for determinism** — the same question should produce the same answer regardless of when asked or which LLM is orchestrating

If your domain is small (5-10 tables, columns with self-documenting names, no jargon), a single enriched semantic view with verified queries is sufficient. This pattern addresses the scale where that approach breaks down.

---

## 2. Core Design Philosophy

The system separates two fundamentally different concerns:

| Concern | Question It Answers | Mechanism | Artifact |
|---------|-------------------|-----------|----------|
| **Interpretation** | What does the user mean? | Precomputed domain knowledge retrieved via semantic search | Domain Knowledge Table + Cortex Search Service |
| **Generation** | What SQL answers the resolved question? | Text-to-SQL against governed schema | Sub-domain Semantic Views via Cortex Analyst |

**Why separate them:**

1. **Different change cadences.** Schema structure changes quarterly; business terminology changes weekly. Coupling them means redeploying structural artifacts for vocabulary changes.
2. **Different scale characteristics.** A domain may have 1,400+ synonym mappings, 200+ business rules, 80+ metric formulas. Encoding these in semantic view YAML produces token bloat and combinatorial explosion in verified queries.
3. **Cross-cutting knowledge.** A synonym like "active wells" resolves to a filter (`WELL_STATUS = 'ACTIVE'`) that applies to every sub-domain view. In a YAML-only approach, this rule must be duplicated across every view and kept in sync.
4. **Automation potential.** A single knowledge table with typed entries supports automated discovery, proposal, promotion, and retirement workflows. YAML files do not.

---

## 3. Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│  User Question                                               │
└─────────────────────┬───────────────────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────────────────┐
│  STEP 1: Context Retrieval (MANDATORY — once per turn)       │
│  Tool: Cortex Search Service                                 │
│  Source: Domain Knowledge Table                              │
│  Returns: resolved terms, rules, metrics, routing hints      │
│  Cross-cutting entries apply universally to all sub-domains  │
└─────────────────────┬───────────────────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────────────────┐
│  DECISION GATE: Is the question unambiguously resolved?      │
│  YES → Proceed to Step 2                                     │
│  NO  → Ask clarification question (do not guess)             │
└─────────────────────┬───────────────────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────────────────┐
│  STEP 2: Enriched Query → Sub-Domain Semantic View           │
│  Route via SEMANTIC_VIEWS field from Step 1 results          │
│  Cortex Analyst generates SQL against governed schema        │
│  Max 2 views per question (primary + one secondary)          │
└─────────────────────┬───────────────────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────────────────┐
│  SQL Result returned to user                                 │
└─────────────────────────────────────────────────────────────┘
```

**The agent makes exactly two decisions:**
1. "What context do I need?" → single search call (always)
2. "Which sub-domain answers this?" → follow the routing hint from search results

Both decisions are informed by precomputed knowledge, not runtime LLM reasoning. This minimizes variance.

---

## 4. Component Design

### 4.1 Domain Knowledge Table

A single table housing all non-structural domain intelligence. Every piece of knowledge is a row with a type, a domain assignment, and structured metadata.

**Schema:**

```sql
CREATE TABLE <DOMAIN>_DOMAIN_KNOWLEDGE (
    ENTRY_ID         VARCHAR       DEFAULT UUID_STRING() PRIMARY KEY,
    ENTRY_TYPE       VARCHAR       NOT NULL,
    DOMAIN           VARCHAR,           -- NULL = cross-domain (applies universally)
    NAME             VARCHAR       NOT NULL,
    CONTENT          VARCHAR       NOT NULL,
    CONTEXT_PROFILE  VARCHAR,           -- Precomputed searchable text
    PROPERTIES       VARIANT,           -- Structured metadata (priority, formulas, etc.)
    SEMANTIC_VIEWS   ARRAY,             -- Routing hints: which view(s) this applies to
    SCOPE            VARCHAR       DEFAULT 'view',   -- view | domain | global
    CATEGORY         VARCHAR,           -- Fine-grained classification
    ACTIVE           BOOLEAN       DEFAULT TRUE,
    CONFIDENCE       FLOAT         DEFAULT 1.0,
    PROMOTION_STATE  VARCHAR       DEFAULT 'none',   -- none | candidate | promoted | retired
    RETRIEVAL_COUNT  NUMBER        DEFAULT 0,
    LAST_RETRIEVED   TIMESTAMP_NTZ,
    CREATED_AT       TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP(),
    UPDATED_AT       TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP()
);
```

**Key design decisions:**

| Decision | Rationale |
|----------|-----------|
| Single table (not separate tables per entry type) | One schema to maintain, one search service to index, one automation surface. No cross-table synchronization needed. |
| `ENTRY_TYPE` column (typed rows) | Enables filtering by purpose (retrieve only SYNONYMs, only RULEs) while keeping all knowledge co-located. |
| `CONTEXT_PROFILE` (precomputed text) | Search quality depends on rich, pre-assembled text — not raw entry content. Profiles aggregate related context into a single searchable document per entry. |
| `SEMANTIC_VIEWS` array | Precomputes routing. The agent doesn't decide which view to call — the knowledge entry tells it. Eliminates a decision point. |
| `SCOPE` field | Distinguishes view-specific, domain-wide, and truly global entries. Global entries (e.g., "exclude abandoned entities") apply to every question regardless of routing. |
| `CONFIDENCE` score | Enables automated discovery at lower confidence (0.5–0.7) with human approval required before full trust. |
| `PROMOTION_STATE` lifecycle | Supports a mature pipeline: entries begin as candidates, prove value via retrieval metrics, get promoted to verified queries in semantic views, or retired after disuse. |
| `ACTIVE` flag (soft delete) | Never lose knowledge history. Deactivated entries remain for auditing and potential reactivation. |

### 4.2 Entry Types and Their Rationale

Each entry type serves a distinct purpose in the resolution pipeline:

#### SYNONYM — Term Resolution

**Purpose:** Maps user-facing jargon to canonical column names, filter values, or table references.

**Why it exists:** Users never speak in column names. "Active wells" means `STATUS = 'ACTIVE'`. "Permian" means `BUSINESS_UNIT = 'LOWER 48 - HMRO PERMIAN'`. Without explicit synonym entries, the agent must guess — and guessing introduces variance.

**Structure:**
```
NAME: "Permian"
CONTENT: "Permian maps to BU = 'LOWER 48 - HMRO PERMIAN' in the well header"
PROPERTIES: {
  "canonical_column": "BUSINESS_UNIT",
  "canonical_value": "LOWER 48 - HMRO PERMIAN",
  "also_known_as": ["Permian Basin", "PB"]
}
SEMANTIC_VIEWS: [all views containing the header table]
DOMAIN: "GENERAL"
```

**Key rule:** If the canonical value contains a wildcard (`%`), the agent must use `ILIKE` rather than `=` equality. This is a common source of silent failures if not enforced.

#### RULE — Business Logic Enforcement

**Purpose:** Encodes mandatory or advisory constraints that the agent must apply when generating SQL.

**Why it exists:** Business rules constrain valid queries in ways that cannot be inferred from schema alone. "Always exclude indirect wells from counts" is a business convention — not a database constraint.

**Priority system:**
- **Priority 1-2:** MANDATORY. Always apply. Non-negotiable business logic.
- **Priority 3-4:** Advisory. Apply when relevant, but the user can override.

**Structure:**
```
NAME: "Exclude indirect wells from operated counts"
CONTENT: "When counting operated wells, exclude wells where name contains 'indirect'"
PROPERTIES: {
  "priority": 1,
  "category": "FILTER_CONVENTION",
  "sql_hint": "WHERE WELL_NAME NOT ILIKE '%indirect%'"
}
SCOPE: "global"
```

**Critical design choice:** A RULE that broadens a SYNONYM (maps a term to multiple entities) always triggers clarification, regardless of priority. This prevents the agent from silently expanding scope in ways the user didn't intend.

#### METRIC — Canonical Calculations

**Purpose:** Defines the single correct formula for a named business metric.

**Why it exists:** Domain metrics (NPT, OEE, drilling rate) have precise definitions that users expect. Without canonical formulas, the agent might invent plausible-but-wrong calculations.

**Structure:**
```
NAME: "NPT (Non-Productive Time)"
CONTENT: "Sum of timelog hours where a trouble code is assigned"
PROPERTIES: {
  "formula": "SUM(HOURS) WHERE TROUBLE_CODE IS NOT NULL",
  "unit": "hours",
  "aggregation": "SUM"
}
SEMANTIC_VIEWS: ["SV_ENT_<DOMAIN>_OPERATIONS_DAILY_REPORTING"]
```

#### PATTERN — Verified SQL Templates

**Purpose:** Provides pre-validated question-to-SQL mappings for known high-value queries.

**Why it exists:** For frequently asked, business-critical questions, you want locked-in SQL behavior — not LLM generation. Patterns serve as few-shot examples delivered via context retrieval.

**Lifecycle:** Patterns enter at `PROMOTION_STATE = 'none'`, prove value via retrieval metrics, get promoted to `'candidate'` then `'promoted'` (at which point they migrate into the semantic view's native verified queries), or get retired after 90 days of zero retrievals.

**Structure:**
```
NAME: "Count of wells spudded in a given year"
CONTENT: "How many wells were spudded in [YEAR]?"
PROPERTIES: {
  "verified_sql": "SELECT COUNT(DISTINCT WELL_ID) FROM WV_WELLHEADER WHERE YEAR(SPUD_DATE) = :year",
  "parameters": ["year"]
}
PROMOTION_STATE: "promoted"
```

#### COLUMN_DESC — Column Business Context

**Purpose:** Provides the business meaning, valid values, and usage notes for individual columns.

**Why it exists:** Column names like `WELL_CONFIG_TYPE` or `TOTAL_DEPTH_DRILLED` are technically correct but semantically opaque. Users need to know that `WELL_CONFIG_TYPE` is "the operational classification: Horizontal, Vertical, Directional, Deviated."

**Note:** These are deliberately kept OUT of the semantic view YAML to avoid token bloat. The semantic view carries only enough description for Cortex Analyst to disambiguate within its scope. Rich business context is delivered via the enriched question.

#### HIERARCHY — Ontological Relationships

**Purpose:** Encodes parent-child, part-whole, or classification hierarchies.

**Why it exists:** Users ask about groups ("all rotating equipment") that expand to specific values ("pump, compressor, turbine"). Hierarchies let the agent expand or roll up correctly.

**Structure:**
```
NAME: "Geographic Hierarchy"
CONTENT: "Business Unit → Area → Region → District"
PROPERTIES: {
  "levels": ["BUSINESS_UNIT", "AREA", "REGION", "DISTRICT"],
  "direction": "top_down"
}
SCOPE: "global"
```

#### TABLE_DESC — Table-Level Context

**Purpose:** Describes what a source table represents, its grain, and how it joins to the anchor.

**Why it exists:** When the agent composes an enriched question for Cortex Analyst, knowing that "WV_STIM contains stimulation treatment headers, one row per treatment" helps the Analyst generate correct aggregations and join paths.

### 4.3 The GENERAL Domain (Cross-Cutting Knowledge)

Entries with `DOMAIN = 'GENERAL'` and `SEMANTIC_VIEWS = NULL` represent knowledge that applies universally — regardless of which sub-domain view the question routes to.

**Examples of GENERAL entries:**
- Geographic synonyms (BU/area/region mappings)
- Status code mappings (what "active" means across all views)
- Unit conversion rules
- Universal exclusion filters

**Why a separate domain:** Without this, cross-cutting rules must be duplicated into every sub-domain's entries. GENERAL entries are applied by the agent after routing — they're mandatory context regardless of destination.

### 4.4 Context Profiles (The "Flattened GraphRAG" Pattern)

Raw knowledge entries are individually narrow. A user asking "show me pump health for the north field" needs to retrieve the synonym mapping, the OEE metric formula, the relevant business rules, and the table routing — all from a single search call.

**Context profiles** solve this by precomputing an enriched text block for each entry that aggregates related context:

```
Concept: Net Production
Also known as: net oil, net gas, net volume, after-deductions production
Definition: Gross production minus flare, fuel, and shrinkage.
Always use NET unless user explicitly requests GROSS.
Tables: PROD_MONTHLY (NET_OIL_BBL, NET_GAS_MCF), PROD_DAILY
Formula: GROSS_OIL_BBL - FLARED_OIL_BBL - FUEL_USE_BBL = NET_OIL_BBL
Priority: 1 (mandatory)
Semantic view: SV_<DOMAIN>_PRODUCTION
```

**Why precompute rather than assemble at runtime:**
- Search operates on a single text field — richer text produces better retrieval
- No graph traversal or multi-hop assembly at query time
- Profile rebuild is a batch operation (cheap, schedulable)
- The agent receives ready-to-use context — no interpretation overhead

**Rebuild trigger:** Profiles are rebuilt automatically when entries are created or updated. A scheduled task (daily or on-change) regenerates profiles for modified entries and their neighbors.

### 4.5 Cortex Search Service

A single search service indexes the `CONTEXT_PROFILE` column with attribute filtering:

```sql
CREATE OR REPLACE CORTEX SEARCH SERVICE <DOMAIN>_CONTEXT_SVC
  ON (
    SELECT ENTRY_ID, NAME, ENTRY_TYPE, DOMAIN, SCOPE,
           SEMANTIC_VIEWS, CONFIDENCE, PROMOTION_STATE, CONTEXT_PROFILE
    FROM <DOMAIN>_DOMAIN_KNOWLEDGE
    WHERE ACTIVE = TRUE AND CONTEXT_PROFILE IS NOT NULL
  )
  WAREHOUSE = <warehouse>
  TARGET_LAG = '1 minute'
  SEARCH_COLUMN = 'CONTEXT_PROFILE'
  ATTRIBUTES = 'ENTRY_TYPE', 'DOMAIN', 'SCOPE', 'SEMANTIC_VIEWS', 'PROMOTION_STATE';
```

**Design choices:**
- **One service per domain** (not per entry type or per sub-domain). Reduces tool count; the agent makes one search call.
- **Attribute columns** enable post-retrieval filtering without separate queries.
- **1-minute target lag** means new knowledge entries are searchable almost immediately after insertion.
- **Active-only indexing** (`ACTIVE = TRUE`) means deactivated entries vanish from search without deletion.

### 4.6 Sub-Domain Semantic Views

Semantic views are **structural contracts only**. They define:
- Tables and their columns (dimensions, facts, metrics)
- Join relationships (keys, cardinality)
- Aggregation rules
- Minimal column descriptions (just enough for Analyst to disambiguate within scope)

**They deliberately do NOT carry:**
- Synonym mappings
- Business rules
- Rich column descriptions
- Exhaustive verified queries

**Partitioning guidance:**
- Group tables by analytical sub-domain (logical clusters that users query together)
- Each view should have a **shared anchor table** — the entity that all sub-domain tables join through
- Aim for 5–25 sub-domains per domain
- Keep individual views under ~50K tokens (quality degrades beyond ~100K)
- Consolidate aggressively when cross-sub-domain queries are frequent (prefer one 40K-token view over three 15K-token views that require multi-view routing)

**Verified queries in semantic views:** Curate 20–50 per view covering the highest-frequency, highest-stakes patterns. The knowledge store's PATTERN entries handle the long tail. A promotion pipeline migrates proven patterns into native verified queries over time.

### 4.7 Agent / MCP Server

The orchestration layer (Cortex Agent or MCP Server) enforces a strict two-step flow:

1. **ALWAYS** call context search first — no exceptions, no retries
2. Route to the semantic view indicated by search results
3. Compose an enriched question incorporating resolved synonyms, applied rules, and metric formulas
4. Call Cortex Analyst against the indicated view

**Behavioral constraints the agent must enforce:**
- Max ONE context search call per turn (prevents retry-divergence)
- Max TWO semantic view calls per question (primary + one secondary)
- Never reference columns or joins not present in the semantic view
- Never invent metric formulas — use only those from knowledge entries
- Apply Priority 1-2 RULE entries as mandatory filters
- If SYNONYM canonical value contains `%`, use `ILIKE` (not `=`)

### 4.8 The Clarification Protocol

The system enforces "ask, don't guess" when context reveals ambiguity. After the mandatory search call, the agent evaluates whether results unambiguously resolve every user term.

**Triggers for clarification (stop, do not proceed to SQL):**

| Condition | Example |
|-----------|---------|
| No match — user term has no SYNONYM and no single canonical value | User says "north field" but no SYNONYM maps this to a specific AREA |
| Multiple mappings without 1:1 resolution | "Permian" could mean one BU or a broader set of 4 BUs depending on context |
| RULE broadens SYNONYM — a rule maps term to multiple values beyond what the SYNONYM alone suggests | SYNONYM: Permian → BU 'HMRO PERMIAN'; RULE: "All Permian wells span 4 BUs" |
| Resolved value not in target view | SYNONYM resolves to a column the destination semantic view doesn't expose |
| Low confidence — all returned entries score below 0.7 | Uncommon jargon with no strong match |

**Anti-patterns (never do these):**
- Rationalizing past ambiguity to avoid asking
- Making exploratory SQL queries to probe column values
- Making more than one tool call before deciding to clarify
- Dismissing a RULE to avoid the clarification it requires
- Substituting an approximate column (e.g., region instead of BU)

---

## 5. Step-by-Step Build Process

### Phase 1: Domain Analysis and Partitioning

1. **Inventory source tables.** List all tables in your domain with row counts, column counts, and logical groupings.
2. **Identify the anchor entity.** Every domain has a central entity that all other tables join through (e.g., WELL, CUSTOMER, ASSET, ORDER).
3. **Define sub-domains.** Group related tables into logical clusters. Each cluster becomes a semantic view. Criteria:
   - Tables that are queried together frequently
   - Tables that share a common join path through the anchor
   - Distinct analytical concerns (operations vs. finance vs. safety)
4. **Estimate token budgets.** Count columns × ~15 tokens per column (with minimal descriptions). If a sub-domain exceeds 50K tokens, split it. If multiple sub-domains together are under 40K tokens and often queried together, consolidate.

### Phase 2: Semantic View Construction

1. **Create one semantic view per sub-domain.** Include the anchor table in every view for cross-sub-domain filtering.
2. **Define joins explicitly.** Specify foreign keys, cardinality (one-to-one, one-to-many, many-to-many).
3. **Classify columns** as dimensions (categorical, filterable), facts (numeric, aggregatable), or time dimensions.
4. **Write minimal descriptions.** One sentence per column — just enough for Analyst to disambiguate within the view.
5. **Add 5-10 verified queries** covering the most common patterns for each view (you'll add more after testing).
6. **Validate with Cortex Analyst** — submit test questions directly to each view and verify SQL quality.

### Phase 3: Knowledge Store Population

**Order of population (highest to lowest priority):**

1. **TABLE_DESC entries** for every source table (grain, purpose, join relationship to anchor)
2. **COLUMN_DESC entries** for key columns (start with dimensions and commonly-referenced facts)
3. **SYNONYM entries** for known jargon (start with terms you know users employ)
4. **RULE entries** for mandatory business logic (Priority 1-2 only initially)
5. **METRIC entries** for named calculations
6. **HIERARCHY entries** for classification structures
7. **PATTERN entries** — add after initial testing reveals common questions

**For each entry:**
- Assign `SEMANTIC_VIEWS` routing (which view(s) can answer questions involving this entry?)
- Assign `DOMAIN` (GENERAL if cross-cutting, specific sub-domain otherwise)
- Set `SCOPE` (global for always-applicable rules, view for targeted entries)

### Phase 4: Context Profile Generation

Build a profile generation procedure that, for each entry:
1. Aggregates the entry's own content
2. Adds related entries (same table, same concept, related synonyms)
3. Includes routing metadata
4. Produces a single searchable text block

Run profile generation for all entries. Validate by spot-checking that a user-like question retrieves the right profiles.

### Phase 5: Search Service and Agent Deployment

1. Create the Cortex Search Service over `CONTEXT_PROFILE`
2. Test retrieval quality: submit 20-50 representative questions, verify correct entries are returned in top-12
3. Create the agent (or MCP server) with the two-step flow
4. Encode behavioral directives (clarification protocol, routing rules, constraint enforcement)
5. Validate end-to-end: question → search → routing → SQL → correct answer

---

## 6. Testing and Incremental Improvement

### 6.1 Initial Release Testing

Testing proceeds in layers — validate each component before testing end-to-end.

#### Layer 1: Retrieval Quality (Knowledge Store + Search)

**What to test:** Given a user question, does the search service return the correct entries?

**Method:**
1. Assemble 50–100 representative questions spanning all sub-domains
2. For each question, define expected entries (which SYNONYMs, RULEs, METRICs should be retrieved?)
3. Run each question against the search service
4. Measure: hit rate (≥1 correct entry returned), full coverage (all needed entries returned), noise ratio (irrelevant entries in top-12)

**Target metrics:**
- Retrieval hit rate: ≥95% (at least one correct entry for every question)
- Full routing accuracy: ≥90% (correct sub-domain identified)
- Noise ratio: <30% (most returned entries are relevant)

**Fixing failures:**
- Missed entry → improve the `CONTEXT_PROFILE` text (add more searchable terms, synonyms)
- Wrong routing → fix `SEMANTIC_VIEWS` array on the entry
- Low confidence scores → enrich profile with more contextual text

#### Layer 2: SQL Generation Quality (Semantic Views + Analyst)

**What to test:** Given a well-formed, enriched question, does Cortex Analyst generate correct SQL?

**Method:**
1. Take the same 50–100 questions
2. Manually compose the "ideal enriched question" (resolved terms, applied rules)
3. Submit directly to the appropriate semantic view
4. Verify: correct tables joined, correct columns selected, correct filters applied, correct aggregation

**Fixing failures:**
- Wrong join → fix the semantic view's join definition
- Missing column → add the column to the semantic view
- Wrong aggregation → fix the metric definition in the semantic view
- Incorrect filter → verify the enriched question correctly conveys the rule

#### Layer 3: End-to-End (Full Agent Pipeline)

**What to test:** User question → correct SQL result with no hallucination.

**Method:**
1. Submit questions to the live agent
2. Verify the agent called search first, routed correctly, composed a good enriched question, and produced correct SQL
3. Check for clarification protocol compliance: did it ask when it should have? Did it proceed when it shouldn't have?

**Determinism test:** Run the same 50 questions 5× each. Measure answer variance. A well-built system should produce identical SQL for identical questions in >95% of runs.

### 6.2 Ongoing Maintenance and Improvement

#### Automated Gap Detection

Set up a weekly pipeline that identifies knowledge gaps:

```sql
-- Questions where the agent asked for clarification or produced errors
SELECT thread_id, user_message, agent_response
FROM AGENT_INTERACTION_LOG
WHERE response_type IN ('CLARIFICATION_NEEDED', 'NO_RESULTS', 'ERROR')
  AND created_at > DATEADD(day, -7, CURRENT_TIMESTAMP());
```

Each gap produces candidate entries:
- Unresolved terms → candidate SYNONYM entries
- Incorrect filters → candidate RULE corrections
- Wrong table routing → candidate TABLE_DESC / SEMANTIC_VIEWS fixes

#### Schema Drift Detection

A daily check for columns added or removed from source tables:

```sql
-- Find new columns not yet described in the knowledge store
SELECT c.TABLE_NAME, c.COLUMN_NAME, c.DATA_TYPE
FROM INFORMATION_SCHEMA.COLUMNS c
LEFT JOIN <DOMAIN>_DOMAIN_KNOWLEDGE dk
  ON dk.ENTRY_TYPE = 'COLUMN_DESC'
  AND dk.NAME = c.COLUMN_NAME
  AND dk.PROPERTIES:table_name::VARCHAR = c.TABLE_NAME
WHERE c.TABLE_SCHEMA = '<source_schema>'
  AND dk.ENTRY_ID IS NULL;
```

New columns get auto-generated descriptions at `CONFIDENCE = 0.6`, flagged for human review.

#### Promotion Pipeline (Verified Queries)

A weekly scoring job identifies high-value PATTERN entries:

```
Score = RETRIEVAL_COUNT / (AGE_DAYS + 1)
```

Top-scoring patterns at `PROMOTION_STATE = 'none'` → promoted to `'candidate'`.
Human reviewer approves candidates → `'promoted'`.
Promoted patterns are injected into the semantic view's native `WITH VERIFIED_QUERIES` clause on next view regeneration.

#### Retirement Pipeline

Entries with `RETRIEVAL_COUNT = 0` over 90 days → flagged for retirement.
Retired entries are set `ACTIVE = FALSE` (soft delete). They can be reactivated if usage patterns change.

#### Human Governance

| Action | Automation Level |
|--------|-----------------|
| New COLUMN_DESC for new columns | Auto-created at low confidence, human-reviewed |
| New SYNONYM from usage patterns | Auto-proposed, human-approved |
| New RULE (Priority 1-2) | Always human-authored |
| New RULE (Priority 3-4) | Auto-proposed, human-approved |
| Semantic view changes | Always human-reviewed |
| Profile rebuilds | Fully automated |
| Entry retirement | Auto-flagged, human-confirmed |

### 6.3 Measuring System Health

Track these metrics continuously:

| Metric | What It Tells You | Healthy Target |
|--------|-------------------|----------------|
| Retrieval hit rate | Knowledge coverage | ≥98% |
| Clarification rate | Ambiguity in knowledge | <15% of questions |
| SQL execution error rate | Structural correctness | <2% |
| Answer correctness (sampled) | End-to-end quality | ≥90% |
| Mean entries per search result | Profile density | 8–12 per question |
| Zero-retrieval entries (90-day) | Knowledge store bloat | <10% of active entries |

---

## 7. Naming Conventions

Consistent naming enables tooling and automation across domains:

| Component | Convention | Example |
|-----------|-----------|---------|
| Semantic views | `SV_ENT_<DOMAIN>_<SUBDOMAIN>` | `SV_ENT_LAND_AGREEMENTS` |
| Knowledge table | `<DOMAIN>_DOMAIN_KNOWLEDGE` | `LAND_DOMAIN_KNOWLEDGE` |
| Search service | `<DOMAIN>_CONTEXT_SVC` | `LAND_CONTEXT_SVC` |
| Agent | `AGENT_<DOMAIN>` | `AGENT_LAND` |
| MCP server | `SF_MCP_ENT_<DOMAIN>` | `SF_MCP_ENT_LAND` |

---

## 8. Common Pitfalls and How to Avoid Them

### Over-Partitioning Semantic Views

**Symptom:** Cross-domain questions routinely need 3+ views, hitting the 2-view routing cap.

**Fix:** Consolidate views that are frequently queried together. One 40K-token view is better than three 15K-token views if users regularly ask questions spanning all three.

**Test:** Track how often the agent needs to call multiple views. If >30% of questions require 2+ views, you're over-partitioned.

### Under-Populating the Knowledge Store

**Symptom:** High clarification rate; agent frequently says "I don't know what X means."

**Fix:** Prioritize SYNONYM entries for all known user jargon. Mine query history for terms users actually type. Auto-propose from agent interaction logs.

### Stale Profiles After Knowledge Changes

**Symptom:** New entries exist but aren't retrieved by search — profile rebuild hasn't run.

**Fix:** Ensure profile rebuild is triggered on every knowledge table update (via stream + task, or scheduled at high frequency during active development).

### Overly Rich Semantic View Descriptions

**Symptom:** Token budget warnings; Analyst quality degrading on large views.

**Fix:** Move rich column descriptions to the knowledge store as COLUMN_DESC entries. Keep semantic view descriptions to one sentence per column.

### Missing GENERAL Domain Entries

**Symptom:** The same rule or synonym resolves correctly for one sub-domain but fails for another.

**Fix:** Cross-cutting knowledge (status codes, geographic mappings, universal exclusion rules) must have `DOMAIN = 'GENERAL'` and `SEMANTIC_VIEWS = NULL`. The agent applies GENERAL entries regardless of routing target.

---

## 9. Scaling Considerations

| Domain Size | Sub-Domains | Knowledge Entries | Notes |
|-------------|-------------|-------------------|-------|
| Small (5-20 tables) | 2-4 views | 200-500 entries | Consider whether this pattern is needed vs. a single enriched view |
| Medium (20-80 tables) | 5-10 views | 500-2,000 entries | Sweet spot for this pattern |
| Large (80-200 tables) | 10-20 views | 2,000-8,000 entries | Full pattern with consolidation analysis |
| Very large (200+ tables) | 15-30 views | 8,000+ entries | Consider domain splitting (multiple independent systems) |

**Token budget heuristic:** columns × 15 tokens (minimal descriptions) or columns × 40 tokens (rich descriptions). If the total exceeds 100K for a single view, split into sub-domains.

---

## 10. Summary of Design Choices and Their Rationale

| Decision | Rationale | Alternative Rejected | Why Rejected |
|----------|-----------|---------------------|--------------|
| Single knowledge table (not fragmented stores) | One place to maintain, one search service, clear automation surface | Separate tables for synonyms, rules, patterns | Maintenance divergence; multiple update paths; synchronization overhead |
| Typed rows (not schema per type) | All knowledge co-located; single INSERT surface; attribute-filtered retrieval | Dedicated tables per entry type | Too many tables; cross-type queries become joins; automation requires multi-table awareness |
| Precomputed profiles (not runtime assembly) | Better search quality; no query-time graph traversal; deterministic retrieval | GraphRAG with runtime traversal | Operational complexity; unfamiliar to data teams; traversal logic adds variance |
| Thin semantic views (not exhaustive YAML) | Token budget compliance; single source of truth for knowledge; fast update cycle | Enriched YAML with all synonyms/rules | Token explosion; combinatorial verified queries; slow change cadence |
| Routing precomputed into knowledge entries | Eliminates a decision point; agent follows hints rather than reasoning | Agent selects view from descriptions | View descriptions alone lack semantic signal at scale; introduces variance |
| Two-step agent flow (not multi-hop) | Minimal decision points; no intermediate reasoning; deterministic | Multi-step chains (resolve → retrieve → compose → generate) | Every LLM call is a variance opportunity; more steps = more drift |
| Clarification protocol (not guess-and-proceed) | Prevents silent errors from ambiguous resolution | Proceed with best-guess interpretation | Users get wrong answers confidently; trust erodes |
| Promotion pipeline (not static verified queries) | Verified queries earn their place via usage evidence; stale ones retire | Manual curation of all verified queries | Doesn't scale; human bottleneck; stale queries accumulate |

---

## Appendix: Checklist for New Domain Onboarding

- [ ] Domain anchor entity identified
- [ ] Source tables inventoried (count, columns, relationships)
- [ ] Sub-domain groupings defined (5-25 per domain)
- [ ] Token budgets estimated per sub-domain (<50K target)
- [ ] Semantic views created (one per sub-domain, anchor table in each)
- [ ] Semantic views validated with Cortex Analyst (10 test questions per view)
- [ ] Knowledge table created with initial entries:
  - [ ] TABLE_DESC for all source tables
  - [ ] COLUMN_DESC for key dimensions and facts
  - [ ] SYNONYM entries for known domain jargon (minimum 50)
  - [ ] RULE entries for mandatory business logic (Priority 1-2)
  - [ ] METRIC entries for named calculations
  - [ ] GENERAL domain entries for cross-cutting knowledge
- [ ] Context profiles generated for all entries
- [ ] Cortex Search service created and indexed
- [ ] Retrieval quality validated (50+ test questions, ≥95% hit rate)
- [ ] Agent/MCP server deployed with two-step flow
- [ ] Clarification protocol encoded in agent instructions
- [ ] End-to-end testing passed (50+ questions, ≥90% correctness)
- [ ] Determinism validated (same questions, same answers, 5 runs)
- [ ] Monitoring configured (gap detection, schema drift, promotion pipeline)
- [ ] Human governance workflow established (review queue for auto-proposed entries)

---

## Appendix B: Skill-to-Pattern Cross-Reference

Maps each step of the `/agentic-domain-builder` skill to the sections in this document that provide the underlying rationale, constraints, and templates.

| Skill Step | Skill Action | Design Pattern Sections | Key Guidance |
|:---:|---|---|---|
| **1** | Domain Identification | §1 When to Use This Pattern; §5 Phase 1: Domain Analysis and Partitioning | Confirms domain meets multi-view criteria (>20 tables, jargon, business rules). Identify anchor entity. |
| **2** | Source Table Inventory | §5 Phase 1 (steps 1–2); §9 Scaling Considerations | Token budget heuristic: columns × 15. Threshold for multi-view: >50 columns or >100K tokens. |
| **3** | Sub-Domain Partitioning | §4.6 Sub-Domain Semantic Views (Partitioning guidance); §5 Phase 1 (steps 3–4); §8 Pitfall: Over-Partitioning | Shared anchor table in every view. Consolidate when <40K combined tokens and frequently co-queried. |
| **4** | Semantic View Generation | §4.6 Sub-Domain Semantic Views; §5 Phase 2: Semantic View Construction | Structural contracts only: tables, columns, joins, dim/measure. Minimal descriptions. 5–10 verified queries to start. |
| **5** | Knowledge Store Design | §4.1 Domain Knowledge Table; §4.2 Entry Types and Their Rationale; §4.3 The GENERAL Domain | 7 entry types. GENERAL domain for cross-cutting. Priority 1-2 rules are mandatory. |
| **6** | Knowledge Table Population | §4.1 (Schema); §4.2 (PROPERTIES per type); §5 Phase 3: Knowledge Store Population | Population order: TABLE_DESC → COLUMN_DESC → SYNONYM → RULE → METRIC → HIERARCHY → PATTERN. CONFIDENCE: 1.0 human, 0.6–0.7 auto. |
| **7** | Context Profile Generation | §4.4 Context Profiles; §5 Phase 4 | Precompute enriched text per entry. Aggregate related entries, rules, and routing metadata into one searchable block. |
| **8** | Cortex Search Service | §4.5 Cortex Search Service; §5 Phase 5 (steps 1–2) | Single service per domain. Index CONTEXT_PROFILE. Attribute filters on ENTRY_TYPE, DOMAIN, SCOPE. Validate ≥95% hit rate. |
| **9** | Agent / MCP Server Assembly | §3 Architecture Overview; §4.7 Agent / MCP Server; §4.8 Clarification Protocol | Two decisions: "what context?" + "which view?". Max 1 search, max 2 views. Encode clarification triggers and anti-patterns. |
| **10** | End-to-End Validation | §6.1 Initial Release Testing; §6.3 Measuring System Health | Three-layer testing: retrieval → SQL generation → full pipeline. Determinism (5 runs). Clarification compliance. |
| **11** | Maintenance Setup | §6.2 Ongoing Maintenance and Improvement; §8 Pitfall: Stale Profiles | Schema drift (daily), gap detection (weekly), promotion pipeline, retirement after 90 days zero retrieval. |

### Implementation Checklist (by phase)

**Phase A: Discovery & Structure (Steps 1–3)**
- [ ] Domain name, anchor entity, source/target schemas confirmed
- [ ] Table inventory complete; token budget estimated
- [ ] Sub-domain groupings approved; consolidation decisions made

**Phase B: Artifacts (Steps 4–8)**
- [ ] Semantic views generated, joins validated, Analyst test queries pass
- [ ] Knowledge entries gathered (SYNONYMs, RULEs, METRICs, GENERAL)
- [ ] Knowledge table created and populated
- [ ] Context profiles generated and spot-checked
- [ ] Search service created; retrieval hit rate ≥95%

**Phase C: Orchestration & Validation (Steps 9–11)**
- [ ] Agent/MCP spec deployed with two-step flow and clarification protocol
- [ ] End-to-end validation passed (≥90% correctness, ≥95% determinism)
- [ ] Maintenance tasks configured (or consciously deferred)
