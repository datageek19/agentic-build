# AWS ↔ On-Premises BMC FootPrints (Read-Only SQL, Kerberos) Ticket Ingestion for the Bedrock AgentCore Payment Workflow

**Technical architecture and implementation guide** · version 1.0 · 23 Nov 2026 · author: Jared Shahidin

Companion files:

- `footprints_sql_target_architecture.drawio`, three pages:
  - Level 1 overview
  - Level 2 detailed per-environment architecture
  - Dev / UAT / Prod topology
- `footprints_sql_sequence.drawio`: the runtime sequence, including failure paths.
- PNG exports of each page, for slides and email.

Circled step numbers ①–⑱ and failure codes F1–F6 are the same in all files. The design deliberately mirrors the Exchange (EWS) ingestion package, so both sources share one pattern, one set of AWS components and one hand-off contract into the agentic workflow.

---

## 0. How to read this guide

| Tag | Meaning |
| --- | --- |
| **[Confirmed]** | Given as a requirement or fact by Boeing or the project team. |
| **[Recommended]** | Design choice made in this guide. It can be changed, but the alternatives are worse for the reasons given. |
| **[Assumption]** | Believed true but not yet confirmed. Each one has an owner in §15. |
| **[Validate]** | Must be checked against the live environment (FootPrints, SQL Server, AD, network, security) before build or promotion. |
| **[Decision]** | Needs approval from an accountable owner (DBA, FootPrints owner, security, export control). |

Names such as the following are **placeholders** to replace with real values:

- `BOEING.COM` (Kerberos realm)
- `fpsql.boeing.com` (SQL listener)
- `fpapp.boeing.com` (FootPrints server)
- `FootPrints` (database name)
- `svc-fp-ro-nonprod` / `svc-fp-ro-prd`
- the CIDRs

---

## 1. Summary of the recommended design

The FootPrints ingestion runs as two scheduled AWS Lambda functions in private subnets of each environment's AWS GovCloud (US-West) account.

**fp-sync** is the SQL client. Every 5 minutes it:

1. Reads the service-account password from **AWS Secrets Manager**.
2. Obtains a **Kerberos** ticket from the Boeing AD domain controllers (`kinit`, TCP/UDP 88).
3. Opens an **encrypted TDS connection** (TLS, TCP 1433) to the FootPrints SQL Server with **Microsoft ODBC Driver 18**, using `Trusted_Connection=yes`.
4. Reads only the workspace 144 rows that changed since the last run, using the SQL Server `rowversion` column `MASTER144.mrTIMESTAMP` as a watermark.

The read is a parameterised `SELECT` under a login that holds `SELECT` permission only.

Each changed ticket is then:

- written to S3 as a JSON snapshot together with its description and history rows
- recorded in a DynamoDB idempotency ledger
- queued to SQS

**fp-fetch** consumes the queue. It optionally copies the ticket's attachment files over a read-only SMB share (attachments are **not** in the database; see §4.3) and starts one **Step Functions** execution per ticket version. That workflow invokes the **AgentCore** classification, extraction and reporting agents.

**No agent ever connects to SQL Server or sees the service-account password.** Agents read the stored snapshot through read-only AgentCore Gateway tools. This keeps the on-prem database's blast radius to one tightly controlled, auditable query loop.

| Area | Recommendation | Tag |
| --- | --- | --- |
| Access method | Direct read-only SQL on the FootPrints backend tables. The legacy FootPrints SOAP web-service API is not needed. | [Confirmed] permitted · [Recommended] |
| Identity | Two Windows domain service accounts, read-only: `svc-fp-ro-nonprod`, `svc-fp-ro-prd`. | [Confirmed] |
| Authentication | Kerberos from Linux (Lambda) to SQL Server. Password held in Secrets Manager and turned into a TGT with `kinit` at run time. The Linux ODBC driver does **not** fall back to NTLM, so the Kerberos prerequisites in §6.2 are hard requirements. | [Confirmed] intent · [Validate] prerequisites |
| Transport | TLS on every connection (ODBC 18 default `Encrypt=yes`), with server certificate validation (`TrustServerCertificate=no`). | [Recommended] |
| Read target | An AG readable secondary through `ApplicationIntent=ReadOnly`, or a DBA reporting replica. Use the primary only if the DBA accepts the load. | [Decision: DBA] |
| Change detection | `mrTIMESTAMP > @watermark AND mrTIMESTAMP < MIN_ACTIVE_ROWVERSION()`, 200 rows per page, committed per page. | [Recommended] |
| Attachments | Read-only SMB (445, Kerberos) to the FootPrints attachment directory, or ticket text only for MVP. | [Decision: FootPrints owner] |
| Compute | Lambda container image (ODBC 18 + MIT Kerberos), reserved concurrency 1 for fp-sync, plus a DynamoDB lease. | [Recommended] |
| Network | Existing Transit Gateway + Direct Connect / Site-to-Site VPN into the Boeing network. Route 53 Resolver outbound endpoint for `boeing.com`. Firewall opens only 1433, 88, 53 and, optionally, 445. | [Validate: Boeing network team] |
| Region and data | AWS GovCloud (US-West). ITAR-suspected tickets are segregated after classification. | [Decision: export control] |
| Lower environments | Dev uses a schema-identical SQL Server mirror with synthetic tickets. UAT uses non-prod FootPrints with `svc-fp-ro-nonprod`. Prod starts in shadow mode. | [Recommended] |

---

## 2. Requirements, assumptions and validation items

### 2.1 Confirmed requirements

- The workflow must read procurement tickets from **BMC FootPrints workspace 144**. [Confirmed]
- Access is through the **backend tables, read-only**. [Confirmed]
- There are **two read-only Windows domain service accounts**: one for non-production and one for production. [Confirmed]
- The reader runs **in AWS**. It authenticates with the service account **via Kerberos**, and the password is kept in **AWS Secrets Manager**. [Confirmed]
- Tickets must be classified as payment-related; the "email classification agent" is reused for tickets. [Confirmed]
- The extraction agent must pull these from the ticket body and attachments: PO, invoice number, supplier information, transaction date, and details. [Confirmed]
- A connectivity path (routing, firewall rules, DNS) from AWS into the Boeing network is needed. [Confirmed]
- The work must be built and proven in Dev and UAT before Production. [Confirmed]

### 2.2 Assumptions (each must be confirmed; owners in §15)

| # | Assumption |
| --- | --- |
| A1 | FootPrints 11.6 runs on **Microsoft SQL Server**. `mrTIMESTAMP` exists on SQL Server only. |
| A2 | Workspace 144 is the procurement workspace, so "procurement-related" equals "all live tickets in workspace 144". If not, a category field filter is added (§7.4). |
| A3 | A non-production FootPrints instance exists, with workspace 144 and representative data. |
| A4 | The Boeing network already has DX/VPN connectivity to the project's AWS GovCloud accounts, as for the Exchange integration. |
| A5 | The SQL Server service account has an SPN registered for the name the client connects to. If an AG is used, this covers the listener and every replica host that read-only routing can redirect to. |
| A6 | Ticket volume in workspace 144 is moderate (thousands of changes per day at most). |

### 2.3 Items to validate before build

| # | Item to validate | Where |
| --- | --- | --- |
| V1 | SQL Server version, edition, HA topology (AG / FCI / standalone), listener name, port (static 1433?) | §7.1 |
| V2 | SPNs (`setspn -L <sql service account>`), and that the AES encryption types are enabled on the service accounts | §6.2 |
| V3 | SQL Server TLS certificate is CA-issued and its SAN includes the connection FQDN(s) | §7.2 |
| V4 | KDC / DC IPs reachable on 88; DNS reachable on 53; clock skew < 5 min | §5 |
| V5 | Exact column names of the workspace 144 custom fields (PO, invoice, supplier, amount…) | §4.2 |
| V6 | Whether `mrTIMESTAMP` is updated for every ticket change the workflow cares about (edit, description append, status change) | §7.4 |
| V7 | Load of an unindexed `mrTIMESTAMP` range scan on the chosen read target | §7.5 |
| V8 | Attachment directory path and layout on the FootPrints server, and whether a read-only share is allowed | §4.3, §8.6 |
| V9 | Availability in GovCloud (US-West) of GuardDuty Malware Protection for S3, the Step Functions → AgentCore integration, and the Bedrock models selected | §9 |

---

## 3. Architecture overview

### 3.1 Options considered

| Option | Description | Verdict |
| --- | --- | --- |
| **A. Direct read-only SQL from AWS (Kerberos)** | fp-sync queries the FootPrints DB (or its readable secondary) across DX/VPN. | **[Recommended]**. This is what Boeing has permitted. No on-prem software to build, and it gives near-real-time incremental reads. |
| B. On-prem export job | A Boeing-run SQL Agent / SSIS job pushes deltas to S3. | Use only if the firewall/Kerberos path from AWS is refused. It moves build and support effort to Boeing teams. |
| C. DBA reporting replica | Log shipping, replication or AG secondary dedicated to reporting. | **Preferred target for Option A in Prod** if the DBA has or can build one. It removes all load from the OLTP primary. |
| D. FootPrints SOAP web services | The legacy `MRWebServices` API. | Not needed. It needs an application (agent) user, is slower, and gives no change feed. |
| E. Agent queries SQL directly as a tool | AgentCore tool runs SQL against FootPrints on demand. | **Rejected**. It puts credentials and on-prem reachability inside the agent runtime, makes load unpredictable, and leaves no stored evidence of what the agent saw. |

### 3.2 Component responsibilities (Level 2 diagram)

| Step | Component | Responsibility |
| --- | --- | --- |
| ① | EventBridge Scheduler | `rate(5 minutes)` per environment; async invoke of fp-sync with `{env, workspace: 144}`. |
| ② ⑩ | DynamoDB `fp_checkpoint` | Lease (single active reader) and the committed rowversion watermark. |
| ③ | Secrets Manager `fp/svc-fp-ro-<env>` | `{realm, username, password}`, encrypted with a customer-managed KMS key; read through a VPC endpoint. |
| ④ | Route 53 Resolver outbound endpoint | Forwards `boeing.com` queries (SQL listener, KDCs, SRV records) to Boeing DNS. |
| ⑤ ⑥ | AD domain controllers (KDC) | AS exchange (TGT) and TGS exchange (service ticket for `MSSQLSvc/fpsql.boeing.com:1433`). |
| ⑦ ⑧ | SQL Server (listener / readable secondary) | TLS + Kerberos login, then parameterised `SELECT` on `MASTER144*`. |
| ⑨ | S3 `fp-raw-<env>`, DynamoDB `fp_ledger`, SQS `fp-ticket-queue` | Immutable snapshot, idempotency record, one message per changed ticket. |
| ⑪–⑭ | fp-fetch Lambda | Attachments (optional), manifest, ledger update, `StartExecution`. |
| ⑮–⑰ | Step Functions `case-workflow` + AgentCore | Malware gate, pre-processing, classification, extraction, persistence. |
| ⑱ | Reporting agent | Scheduled consolidated payment-inquiry report. |

### 3.3 Trust boundaries

1. **Boeing on-prem trust zone**: FootPrints, SQL Server, AD, PKI, DNS. AWS reaches it only through the firewall rules in §5.4.
2. **Ingestion boundary (AWS, per environment)**: fp-sync and fp-fetch. These are the *only* principals that can read the service-account secret or reach on-prem.
3. **Agentic boundary**: Step Functions, AgentCore Runtime / Gateway and tool Lambdas. They read S3/Aurora only and have no route to on-prem.
4. **ITAR segregation**: an ITAR-suspected case is copied into `fp-itar-<env>` under a separate KMS key and access policy. [Decision: export control]

---

## 4. FootPrints data model facts that drive the design

### 4.1 Tables per workspace

FootPrints keeps all data in one database. Each workspace is a sub-database with its own fields. On SQL Server, workspace 144 is held in five tables:

| Table | Key columns used | Purpose |
| --- | --- | --- |
| `MASTER144` | `mrID` (int, PK), `mrTITLE`, `mrSTATUS`, `mrPRIORITY`, `mrDESCRIPTION` (latest description), `mrALLDESCRIPTIONS`, `mrASSIGNEES`, `mrATTACHMENTS`, `mrSUBMITTER`, `mrSUBMITDATE`, `mrUPDATEDATE`, `mrTIMESTAMP`, custom fields | One row per ticket. |
| `MASTER144_DESCRIPTIONS` | `mrID`, `mrGENERATION`, `mrDESCRIPTION` | Every description generation (the conversation trail). |
| `MASTER144_HISTORY` | `mrID`, `mrGENERATION`, `mrHISTORY`, `mrCOMMENT` | Audit trail of edits / status changes. |
| `MASTER144_ABDATA` | `mrID`, `Last__bName`, `First__bName`, `Email__bAddress`, … | Contact (requester / supplier contact) on the ticket. |
| `MASTER144_TIMETRACKING` | `mrID`, `mrGENERATION`, … | Time tracking. **Not needed**, so not granted. |

### 4.2 Columns and conventions

- **Custom fields** are columns named after the field, with spaces encoded as `__b`. For example, a field "PO Number" would appear as `PO__bNumber`. The exact names of the PO, invoice, supplier, amount and currency fields in workspace 144 must be listed by the FootPrints admin or DBA. [Validate V5]
- **`mrDESCRIPTION`** in `MASTER144` is always the newest description. Earlier ones are in `MASTER144_DESCRIPTIONS` by `mrGENERATION`. Each starts with a date/time/user stamp.
- **`mrSTATUS`** includes internal values such as `_DELETED_` and `_SOLVED_` (Knowledge Base solutions); `_REQUEST_` marks customer requests not yet accepted. The design:
  - ingests all statuses except `_SOLVED_`
  - treats `_DELETED_` as a tombstone (no agent run)
  - leaves including `_REQUEST_` as a decision [Decision: business owner]
- **`mrTIMESTAMP`** is a SQL Server `timestamp` column, i.e. `rowversion`. SQL Server bumps it automatically on every insert or update of the row, which makes it a reliable change marker. FootPrints added it for full-text indexing, and **it has no index** in the documented schema. See §7.5.

### 4.3 Attachments are not in the database

`mrATTACHMENTS` holds **file names only**, one per line. The files themselves live in the FootPrints attachment directory on the FootPrints server's file system (11.x default: `…\html\tmp\Attachments`; configurable).

The read-only SQL access that has been permitted therefore **does not cover attachments**. Choose one:

| Choice | What it means |
| --- | --- |
| **MVP: ticket text only** | No extra access. The agents extract from title, descriptions, history and custom fields. Attachments are listed by name only and flagged `ATTACHMENT_NOT_RETRIEVED`. |
| **Read-only SMB share** [Recommended for full scope] | The FootPrints owner shares the attachment directory (or a read-only replica of it) and grants read to the same service account. fp-fetch reads over SMB 3 with Kerberos on TCP 445. |
| On-prem copy job | A Boeing-run job copies new attachment folders to S3. Use it if SMB from AWS is refused. |

[Decision: FootPrints owner / security] [Validate V8: path layout, e.g. per-workspace and per-`mrID` folders]

---

## 5. Network connectivity design

### 5.1 Path

Each environment's VPC and its private subnets (2 AZs) connect over a Transit Gateway attachment to the hub / network account. From there, traffic goes over Direct Connect (private/transit VIF) to the Boeing WAN, or over Site-to-Site VPN as backup. It then passes the Boeing firewall to reach the FootPrints SQL Server, the AD DCs, DNS and, optionally, the FootPrints server for SMB. [Assumption A4 / Validate]

### 5.2 Routing

- Workload VPC route tables: Boeing on-prem prefixes → TGW. Everything else stays in the VPC (VPC endpoints). **There is no NAT or internet gateway on the ingestion subnets.** [Recommended]
- TGW: environment route tables are isolated. Dev, UAT and Prod do not route to each other.
- Boeing side: the workload VPC CIDRs must be advertised or accepted over DX/VPN, with no overlap with Boeing ranges. Return routes must be in place.

### 5.3 DNS

- Route 53 Resolver **outbound endpoint** (2 ENIs, 2 AZs) plus a forwarding rule for `boeing.com` (or the narrower zones that hold the SQL listener, DCs and FootPrints server), pointing to Boeing DNS servers.
- Kerberos needs forward lookups of the SQL FQDN and of the KDCs. KDCs are either listed explicitly in `krb5.conf` [Recommended] or discovered through `_kerberos._tcp.BOEING.COM` SRV records.
- Reverse DNS is **not** used: `rdns = false` in `krb5.conf`, so a missing PTR record cannot break the SPN.

### 5.4 Firewall and port matrix

| # | Source | Destination | Port / protocol | Purpose | Required |
| --- | --- | --- | --- | --- | --- |
| R1 | Ingestion subnets (per env) | FootPrints SQL listener **and** every replica host read-only routing can redirect to | TCP 1433 (static port) | TDS over TLS (Kerberos login + queries) | Yes |
| R2 | Ingestion subnets | AD domain controllers for `BOEING.COM` (and any trusted realm on the path) | TCP 88 and UDP 88 | Kerberos AS / TGS | Yes |
| R3 | Resolver outbound endpoint IPs | Boeing DNS servers | UDP 53 and TCP 53 | Name resolution | Yes |
| R4 | Ingestion subnets | FootPrints server (attachment share) | TCP 445 | SMB 3 read of attachments | Only if the SMB option is approved |
| — | — | SQL Browser UDP 1434 | — | Not needed if a static port is used | No |
| — | — | TCP 464 (kpasswd) | — | Not needed: the service never changes its own password | No |

**Non-prod and prod rule sets are separate.** The Dev and UAT subnets may reach only non-prod FootPrints. The Prod subnets may reach only prod FootPrints.

### 5.5 Request for the Boeing network / connectivity team (ready to send)

I can't introduce you to Boeing's network team from here. The table below is what they will ask for, so it can be pasted into the request (or the network change ticket) as-is.

> **Subject: AWS GovCloud → Boeing network connectivity for BMC FootPrints read-only SQL access (Agentic Payment Resolution)**
>
> **Requestor / sponsor:** [name, org]. **Application:** Agentic payment resolution (AWS Bedrock AgentCore). **Data owner:** FootPrints workspace 144 (procurement).
>
> **AWS side.** For each environment (Dev, UAT, Prod), provide:
> - AWS account ID
> - VPC CIDR
> - ingestion subnet CIDRs
> - Resolver outbound endpoint IPs
> - TGW / DX gateway / VPN identifiers
>
> **Flows requested** (all initiated from AWS; no inbound flows to AWS are needed):
>
> | Env | Source CIDR | Destination (FQDN / IP) | Port | Purpose |
> | --- | --- | --- | --- | --- |
> | Dev, UAT | [subnets] | Non-prod FootPrints SQL listener / replicas | TCP 1433 | Read-only SQL (TLS) |
> | Dev, UAT | [subnets] | AD DCs (KDC) | TCP+UDP 88 | Kerberos |
> | Dev, UAT | [resolver IPs] | Boeing DNS | TCP+UDP 53 | DNS forwarding |
> | Dev, UAT | [subnets] | Non-prod FootPrints server | TCP 445 | Attachments (optional) |
> | Prod | [subnets] | Prod FootPrints SQL listener / replicas | TCP 1433 | Read-only SQL (TLS) |
> | Prod | [subnets] | AD DCs (KDC) | TCP+UDP 88 | Kerberos |
> | Prod | [resolver IPs] | Boeing DNS | TCP+UDP 53 | DNS forwarding |
> | Prod | [subnets] | Prod FootPrints server | TCP 445 | Attachments (optional) |
>
> **Also requested:**
> - route advertisement / acceptance of the VPC CIDRs over the existing DX/VPN
> - confirmation of no CIDR overlap
> - DNS server IPs to forward `boeing.com` to
> - the list of DC IPs to use as KDCs
> - whether TLS inspection sits on the path (it must **not** intercept 1433 or 88)
>
> **Traffic profile:** One connection every 5 minutes per environment. Each run lasts seconds to a few minutes and transfers well under 100 MB, except during the one-time initial load, which is scheduled with the DBA.

### 5.6 Security groups and VPC endpoints

`sg-fp-client`, attached to both Lambdas. Inbound: none. Outbound:

- TCP 1433 → SQL listener and replica IPs
- TCP/UDP 88 → DC IPs
- TCP 445 → FootPrints server (optional)
- TCP 443 → `sg-vpce`

Interface VPC endpoints:

- `secretsmanager`
- `kms`
- `sqs`
- `states`
- `logs`
- `monitoring`
- `sts`
- `ecr.api` and `ecr.dkr` (container image pull)

Gateway endpoints: `s3` and `dynamodb`.

---

## 6. Service account and Kerberos authentication

### 6.1 How the Kerberos login works (steps ③–⑦)

1. fp-sync reads `{realm, username, password}` from Secrets Manager ③ and keeps it in memory only.
2. `kinit svc-fp-ro-prd@BOEING.COM` sends an **AS-REQ** with AES pre-authentication to a DC on port 88 ⑤. The DC returns a **TGT**, which is cached in `/tmp/krb5cc_fp` (`KRB5CCNAME`). The password is discarded.
3. On `pyodbc.connect(...Trusted_Connection=yes...)`, the ODBC driver asks GSSAPI for a service ticket for `MSSQLSvc/fpsql.boeing.com:1433` (**TGS-REQ** ⑥).
4. The driver opens TCP 1433, negotiates TLS (PRELOGIN), and sends the Kerberos AP-REQ inside LOGIN7 ⑦. SQL Server validates it with its own key and maps it to the Windows login `BOEING\svc-fp-ro-prd`.
5. The TGT stays valid for the domain's ticket lifetime (typically 10 h). Warm Lambda environments reuse it, and the code re-runs `kinit` well before expiry.

### 6.2 Kerberos prerequisites (AD team + DBA)

| # | Prerequisite | How to check |
| --- | --- | --- |
| K1 | SPN `MSSQLSvc/<fqdn>:1433` (and `MSSQLSvc/<fqdn>`) registered on the **SQL Server service account**, for the listener name and every replica host the client may be routed to. No duplicates. | `setspn -L <sqlsvc>`; `setspn -X` (duplicates) |
| K2 | Service accounts `svc-fp-ro-*` are enabled, not "smart card required", and have "This account supports Kerberos AES 256 bit encryption". The SQL service account supports AES too. | AD Users & Computers / `Get-ADUser -Properties msDS-SupportedEncryptionTypes` |
| K3 | Password-never-expires or a managed rotation process (§6.5). Account lockout threshold known. | AD policy |
| K4 | Logon restrictions (`userWorkstations`, "Deny access from network", Authentication Policy Silos) do not block the account. | AD team |
| K5 | If the SQL Server is in a different domain or forest from the service account, cross-realm trust / `[capaths]` / `[domain_realm]` entries. | AD team |
| K6 | Time: Lambda uses the Amazon Time Sync Service; DCs must be within 5 minutes of UTC. | `KRB_AP_ERR_SKEW` if not |
| K7 | Confirmation after first connect: `SELECT auth_scheme FROM sys.dm_exec_connections WHERE session_id=@@SPID` returns `KERBEROS`. | §14 checklist |

### 6.3 `krb5.conf` (baked into the image at `/opt/krb5/krb5.conf`)

```ini
[libdefaults]
    default_realm = BOEING.COM
    dns_lookup_realm = false
    dns_lookup_kdc = false            # KDCs listed explicitly below [Recommended]
    rdns = false                      # do not canonicalise via PTR records
    dns_canonicalize_hostname = false # SPN = exactly the FQDN in the connection string
    udp_preference_limit = 1          # always use TCP 88 (large AD tickets with PAC)
    ticket_lifetime = 10h
    renew_lifetime = 7d
    forwardable = false
    default_tkt_enctypes = aes256-cts-hmac-sha1-96 aes128-cts-hmac-sha1-96
    default_tgs_enctypes = aes256-cts-hmac-sha1-96 aes128-cts-hmac-sha1-96
    permitted_enctypes   = aes256-cts-hmac-sha1-96 aes128-cts-hmac-sha1-96

[realms]
    BOEING.COM = {
        kdc = dc01.boeing.com
        kdc = dc02.boeing.com
    }

[domain_realm]
    .boeing.com = BOEING.COM
    boeing.com = BOEING.COM
```

`udp_preference_limit = 1` makes the client use TCP for Kerberos. UDP 88 is still requested in §5.4 because some KDC referral paths use it. [Validate with AD team]

### 6.4 Permission model (DBA script, run once per environment)

Grant only what the workflow reads. Prefer DBA-owned views in a separate schema, so that FootPrints upgrades do not silently widen access. [Recommended]

```sql
-- Run by the DBA in the FootPrints database (non-prod with svc-fp-ro-nonprod, prod with svc-fp-ro-prd)
USE [master];
CREATE LOGIN [BOEING\svc-fp-ro-prd] FROM WINDOWS WITH DEFAULT_DATABASE = [FootPrints];
GO
USE [FootPrints];
CREATE USER [svc_fp_ro] FOR LOGIN [BOEING\svc-fp-ro-prd];

-- Option 1: direct table grants (simplest)
GRANT SELECT ON dbo.MASTER144              TO [svc_fp_ro];
GRANT SELECT ON dbo.MASTER144_DESCRIPTIONS TO [svc_fp_ro];
GRANT SELECT ON dbo.MASTER144_HISTORY      TO [svc_fp_ro];
GRANT SELECT ON dbo.MASTER144_ABDATA       TO [svc_fp_ro];
GRANT VIEW DEFINITION ON dbo.MASTER144     TO [svc_fp_ro];   -- schema-drift check (§7.6)

-- Option 2 [Recommended]: DBA-owned views in schema airead, exposing only required columns
-- CREATE SCHEMA airead AUTHORIZATION dbo;
-- CREATE VIEW airead.fp144_ticket AS SELECT mrID, mrTIMESTAMP, mrTITLE, mrSTATUS, ... FROM dbo.MASTER144;
-- GRANT SELECT ON SCHEMA::airead TO [svc_fp_ro];

-- Explicitly nothing else: no db_datareader, no INSERT/UPDATE/DELETE/EXECUTE, no access to other workspaces.
DENY INSERT, UPDATE, DELETE, EXECUTE, ALTER TO [svc_fp_ro];
```

For an AG, the login must exist on every replica **with the same SID**. For a Windows login the SID comes from AD, so `CREATE LOGIN … FROM WINDOWS` on each replica is sufficient. [Validate with DBA]

### 6.5 Credential storage, retrieval and rotation

- Secret `fp/svc-fp-ro-<env>`: `{"realm":"BOEING.COM","username":"svc-fp-ro-prd","password":"…"}`. It is encrypted with the CMK `alias/fp-<env>`, and its resource policy allows only the fp-sync and fp-fetch roles plus a break-glass admin role.
- **Lockout protection:** a failed `kinit` (wrong or expired password) is **not retried** within a run. The run emits `KerberosFailure` and stops (F1). One bad secret version therefore costs at most one failed AD logon every 5 minutes, below typical lockout thresholds. [Validate threshold]
- **Rotation:** the AD team owns the password. The procedure is:
  1. They set a new password.
  2. They update the secret in the same change window (`PutSecretValue`).
  3. fp-sync picks it up on the next `kinit`, because the secret is never cached beyond one call.

  Automated rotation with a Lambda that changes the AD password over LDAPS is possible later. It needs the account to have "change password" rights and LDAPS reachability (636), so it is out of scope for the MVP. [Decision: AD team]
- Never log the password. The `kinit` stderr never contains it. CloudTrail records every `GetSecretValue`.

---

## 7. SQL Server access and query patterns

### 7.1 Connection target

| Topology [Validate V1] | Connect to | Notes |
| --- | --- | --- |
| Always On AG with readable secondary | AG **listener** FQDN with `ApplicationIntent=ReadOnly;MultiSubnetFailover=Yes` | Read-only routing redirects the session to a secondary. The SPN, firewall rule and TLS certificate must also cover the secondary hosts. |
| Dedicated reporting replica | Replica FQDN | Best isolation. Data lag (e.g. log-shipping interval) adds to the end-to-end latency. |
| Standalone / FCI | Instance FQDN | Only if the DBA accepts the read load (§7.5). Use the read-committed default; do **not** use `NOLOCK` (dirty reads can create phantom ticket versions). |

### 7.2 TLS and certificate validation

- ODBC Driver 18 defaults to `Encrypt=yes`. Keep it, together with `TrustServerCertificate=no`.
- The SQL Server certificate must be issued by the Boeing enterprise CA, and its SAN must contain every FQDN used: listener and replicas. [Validate V3]
- The Boeing root and intermediate CA certificates are added to the image's OS trust store (`/etc/pki/ca-trust/source/anchors/` + `update-ca-trust`).
- If the certificate names differ from the connection name (common with AG listeners), use the ODBC 18 keyword `HostNameInCertificate=<name on cert>` rather than disabling validation.
- Kerberos protects the login but does not encrypt the data stream; **TLS is what protects ticket data in transit.**

### 7.3 Connection string

```text
Driver={ODBC Driver 18 for SQL Server};
Server=tcp:fpsql.boeing.com,1433;
Database=FootPrints;
Trusted_Connection=yes;
Encrypt=yes;
TrustServerCertificate=no;
ApplicationIntent=ReadOnly;
MultiSubnetFailover=Yes;
```

- The login timeout is set through `pyodbc.connect(..., timeout=15)`.
- The query timeout is set through `conn.timeout = 60`.
- There is no `UID`/`PWD`; the Kerberos ticket cache (`KRB5CCNAME`) is the credential.
- Do not rely on `ServerSPN` on Linux. The driver derives the SPN from `Server`, which is why `Server` must be the exact FQDN registered in K1.

### 7.4 Incremental read (steps ⑧–⑩)

```sql
-- 1. Upper bound: every rowversion below this value belongs to a committed transaction
SELECT MIN_ACTIVE_ROWVERSION();                 -- -> @upper (binary(8))

-- 2. One page of changed tickets (parameters: @watermark, @upper)
SELECT TOP (200)
       m.mrID, m.mrTIMESTAMP, m.mrTITLE, m.mrSTATUS, m.mrPRIORITY,
       m.mrSUBMITDATE, m.mrUPDATEDATE, m.mrSUBMITTER, m.mrASSIGNEES,
       m.mrATTACHMENTS, m.mrDESCRIPTION
       /* + configured custom fields, e.g. m.[PO__bNumber], m.[Invoice__bNumber], m.[Supplier__bName] */
FROM   dbo.MASTER144 AS m
WHERE  m.mrTIMESTAMP >  ?        -- @watermark (last committed)
  AND  m.mrTIMESTAMP <  ?        -- @upper
  AND  m.mrSTATUS    <> '_SOLVED_'
ORDER  BY m.mrTIMESTAMP;

-- 3. Child rows for the page (IN list of up to 200 parameter markers)
SELECT mrID, mrGENERATION, mrDESCRIPTION        FROM dbo.MASTER144_DESCRIPTIONS WHERE mrID IN (?, ?, ...) ORDER BY mrID, mrGENERATION;
SELECT mrID, mrGENERATION, mrHISTORY, mrCOMMENT FROM dbo.MASTER144_HISTORY      WHERE mrID IN (?, ?, ...) ORDER BY mrID, mrGENERATION;
SELECT *                                        FROM dbo.MASTER144_ABDATA       WHERE mrID IN (?, ?, ...);
```

How the watermark works:

- **Why `MIN_ACTIVE_ROWVERSION()` and not `@@DBTS`:** a transaction that is still open when the query runs may already hold a lower rowversion than rows committed after it. Reading only below the lowest active rowversion guarantees that such a row is not skipped. That row is picked up on the next run instead.
- **Watermark commit:** after the page's S3 writes, ledger records and SQS messages all succeed, set `fp_checkpoint.watermark = last mrTIMESTAMP of the page`. If the run dies mid-page, the next run re-reads that page, and the ledger makes it idempotent.
- **A ticket appears once per version.** Each edit bumps `mrTIMESTAMP`, so the key `144#mrID#rowversion` is unique, and agents can re-evaluate a ticket when its content changes.
  - Suppress re-runs for changes the workflow does not care about (e.g. assignee only) by comparing a content hash of (title, descriptions, custom fields) with the last processed version. [Recommended]
- **Initial load:** start with the watermark `0x0000000000000000`. Optionally add `AND mrSUBMITDATE >= @cutover_date` for a first run agreed with the business. Run it in a DBA-approved window.
- **Procurement filter:** if workspace 144 contains non-procurement tickets (Assumption A2), add a predicate on the category custom field.
- **Re-baseline:** if the database is restored from backup, rowversions can go backwards. Detect this: alarm if `MIN_ACTIVE_ROWVERSION()` is lower than the stored watermark, and re-baseline with the DBA.

[Validate V6] In UAT, confirm that a description append, status change and custom field edit each change `MASTER144.mrTIMESTAMP`. Test by editing a ticket and comparing the value.

### 7.5 Load on the FootPrints database

- `mrTIMESTAMP` has no index, so the range predicate is a scan of `MASTER144`. At 5-minute intervals this is usually acceptable on a readable secondary or replica. [Validate V7 with the DBA, using the actual row count and an execution plan]
- If the cost is too high on the chosen target, there are three ways to reduce it:
  1. **DBA adds a nonclustered index on `mrTIMESTAMP`** on the replica (or primary). This is a vendor-schema change and needs BMC / FootPrints owner approval. [Decision]
  2. **Lengthen the interval** to 15 minutes.
  3. **Use an `mrUPDATEDATE` window with overlap** (`mrUPDATEDATE >= @last_run - 10 min`) as a cheaper filter alongside `mrTIMESTAMP`. This is useful only if an index exists on `mrUPDATEDATE`.
- **Session hygiene:** one connection per run, closed at the end. Use `SET LOCK_TIMEOUT 5000` and query timeout 60 s, and set an application name (`APP=fp-sync-prd`) so the DBA can identify the session.

### 7.6 Schema-drift check (start of every run)

```sql
SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE
FROM   INFORMATION_SCHEMA.COLUMNS
WHERE  TABLE_SCHEMA = 'dbo' AND TABLE_NAME IN ('MASTER144','MASTER144_DESCRIPTIONS','MASTER144_HISTORY','MASTER144_ABDATA');
```

The run stops with the `SchemaDrift` alarm if any required or configured column is missing or has changed type. This happens, for example, after a workspace field is renamed in FootPrints.

---

## 8. Scheduled ingestion design

### 8.1 Compute choice

| Choice | Notes |
| --- | --- |
| **Lambda container image** [Recommended] | The image contains Python 3.12, `msodbcsql18`, unixODBC, MIT Kerberos client (`krb5-workstation`), `pyodbc`, `smbprotocol` + `pyspnego[kerberos]`, and the Boeing CA chain. |
| ECS Fargate scheduled task | Same image. Use it if runs exceed 15 minutes (e.g. a long initial load). |

### 8.2 Schedule and sizing (initial values; tune in UAT)

| Setting | Value |
| --- | --- |
| Schedule | `rate(5 minutes)`; flexible time window off |
| fp-sync | 1024 MB · timeout 240 s · reserved concurrency 1 · async retry 0 · max event age 60 s |
| Time budget per run | 180 s; then commit the watermark and exit |
| Page size | 200 tickets |
| fp-fetch | 1024 MB · timeout 300 s · SQS batch 5 · maximum concurrency 2 · visibility timeout 1800 s · maxReceiveCount 5 |

### 8.3 Watermark and lease (`fp_checkpoint`)

| Attribute | Example |
| --- | --- |
| `pk` | `prd#144` |
| `watermark_hex` | `0x00000000A1B2C3D4` |
| `lease_owner` / `lease_until` | `<request id>` / epoch + 600 |
| `last_success_at`, `last_rows` | metrics source |

The lease is taken with a conditional update: `attribute_not_exists(lease_until) OR lease_until < :now`. The watermark is updated with the condition `lease_owner = :me`, so a stalled run can never overwrite a newer watermark.

### 8.4 Idempotency ledger (`fp_ledger`)

`pk = 144#<mrID>#<rowversion hex>`; status transitions are `SNAPSHOT → STORED → HANDED_OFF → COMPLETED | NEEDS_REVIEW | FAILED_AGENT | DELETED_AT_SOURCE`.

Other attributes:

- `content_sha256`
- `s3_prefix`
- `execution_arn`
- `attachment_status` (`NONE | RETRIEVED | ATTACHMENT_NOT_RETRIEVED | ATTACHMENT_UNAVAILABLE`)
- TTL of 400 days on terminal states

GSI `mrID-index` for "all versions of ticket N".

### 8.5 Storage layout (S3 `fp-raw-<env>`, SSE-KMS, versioning, Object Lock governance 90 days [Decision])

```text
tickets/144/{mrID}/{rowversion}/ticket.json         # MASTER144 row + ABDATA row, typed and decoded
tickets/144/{mrID}/{rowversion}/descriptions.json   # all generations, oldest first
tickets/144/{mrID}/{rowversion}/history.json
tickets/144/{mrID}/{rowversion}/attachments/{n}-{sanitised name}
tickets/144/{mrID}/{rowversion}/manifest.json       # sha256 + size of every object, source, run id
```

`ticket.json` also carries `custom_fields`, with each `__b` decoded back to a space (`PO__bNumber` → `"PO Number"`), so that agents see business names.

### 8.6 Attachments (step ⑫, optional)

- fp-fetch splits `mrATTACHMENTS` on new lines and reads each file from `\\fpapp.boeing.com\FPAttachments$\<path for workspace 144 / mrID>\<name>` [Validate V8]. It uses SMB 3 with signing/encryption and Kerberos from the same ticket cache.
- Limits: 25 MB per file and 100 MB per ticket by default. Larger files are flagged, not copied.
- Every object goes to S3. GuardDuty Malware Protection for S3 tags it `NO_THREATS_FOUND` or `THREATS_FOUND`, and the workflow waits for the tag before any text extraction ⑮.

### 8.7 Hand-off to the agentic workflow (step ⑭)

`StartExecution` on `case-workflow` uses:

- `name = fp144-<mrID>-<rowversion hex>` (deterministic, so redelivery cannot create a duplicate)
- input `{caseId, source: "footprints", workspace: 144, mrID, rowversion, manifestUri}`

The input shape is the same as the EWS source except for `source`, so the same workflow serves both channels.

### 8.8 Failure, retry and reprocessing

| Code | Failure | Behaviour |
| --- | --- | --- |
| F1 | Kerberos rejected: pre-auth failed, password expired, clock skew, KDC unreachable, SPN unknown | No retry in the run (protects the lockout counter); alarm; watermark unchanged. |
| F2 | SQL login rejected (18456, 4060) or TLS validation error | Stop; alarm; DBA checks login, user and certificate. |
| F3 | Query timeout, deadlock victim (1205), blocking; schema drift | Back off and end the run. The next run re-reads from the last committed page. Schema drift stops ingestion until the mapping is updated. |
| F4 | fp-fetch error | SQS partial batch failure → redelivery → DLQ after 5 receives; alarm; redrive is idempotent through the ledger. |
| F5 | Attachment unavailable or access denied | Flag the ledger and continue with ticket text. |
| F6 | Agent / task error | Step Functions Retry (3, backoff), then Catch → `FAILED_AGENT` → manual queue. Reprocess with a new execution name suffix `#r2`. |

### 8.9 Reference implementation (Python 3.12)

The code is syntax-checked. The ODBC, Kerberos and SMB calls must be exercised in Dev/UAT.

**Container image**

```dockerfile
# Dockerfile — fp-sync and fp-fetch share one image (different CMD)
FROM public.ecr.aws/lambda/python:3.12
# Microsoft ODBC 18 (RHEL 9 compatible repo for Amazon Linux 2023) [Validate in pipeline]
RUN curl -sSL https://packages.microsoft.com/config/rhel/9/prod.repo -o /etc/yum.repos.d/mssql-release.repo \
 && ACCEPT_EULA=Y microdnf install -y msodbcsql18 unixODBC krb5-workstation krb5-libs \
                                     gcc python3-devel krb5-devel unixODBC-devel \
 && microdnf clean all
COPY certs/boeing-root.pem certs/boeing-issuing.pem /etc/pki/ca-trust/source/anchors/
RUN update-ca-trust
COPY krb5.conf /opt/krb5/krb5.conf
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt   # pyodbc, smbprotocol, pyspnego[kerberos], boto3
COPY app/ ${LAMBDA_TASK_ROOT}/
CMD ["fp_sync.handler"]
```

**Shared module: Kerberos, connection, schema check**

```python
# fp_common.py — Kerberos ticket, SQL connection, schema check (shared by fp-sync and fp-fetch)
import json
import os
import subprocess
import time

import boto3
import pyodbc

os.environ.setdefault("KRB5_CONFIG", "/opt/krb5/krb5.conf")
CCACHE = "/tmp/krb5cc_fp"
os.environ["KRB5CCNAME"] = f"FILE:{CCACHE}"

SECRET_ID = os.environ["FP_SECRET_ID"]            # fp/svc-fp-ro-<env>
SQL_SERVER = os.environ["FP_SQL_SERVER"]          # fpsql.boeing.com (exact FQDN of the SPN)
SQL_DATABASE = os.environ.get("FP_SQL_DATABASE", "FootPrints")
WORKSPACE = int(os.environ.get("FP_WORKSPACE", "144"))   # int() guards the table-name f-strings
APP_NAME = f"fp-ingest-{os.environ.get('ENV', 'dev')}"

_sm = boto3.client("secretsmanager")
_last_kinit = 0.0
TICKET_MAX_AGE_S = 3600  # refresh well inside the AD ticket lifetime


class KerberosError(Exception):
    """Raised on kinit failure. Callers must not retry in the same run (lockout protection)."""


def ensure_kerberos_ticket() -> None:
    global _last_kinit
    fresh = time.time() - _last_kinit < TICKET_MAX_AGE_S
    if fresh and subprocess.run(["klist", "-s", "-c", CCACHE]).returncode == 0:
        return
    secret = json.loads(_sm.get_secret_value(SecretId=SECRET_ID)["SecretString"])
    principal = f"{secret['username']}@{secret['realm'].upper()}"
    result = subprocess.run(
        ["kinit", "-c", CCACHE, principal],
        input=secret["password"] + "\n",
        text=True,
        capture_output=True,
        timeout=30,
    )
    secret.clear()
    if result.returncode != 0:
        # stderr never contains the password; truncate to keep logs small
        raise KerberosError(result.stderr.strip()[:300])
    _last_kinit = time.time()


def connect() -> pyodbc.Connection:
    conn_str = (
        "Driver={ODBC Driver 18 for SQL Server};"
        f"Server=tcp:{SQL_SERVER},1433;Database={SQL_DATABASE};"
        "Trusted_Connection=yes;Encrypt=yes;TrustServerCertificate=no;"
        "ApplicationIntent=ReadOnly;MultiSubnetFailover=Yes;"
        f"APP={APP_NAME};"
    )
    conn = pyodbc.connect(conn_str, timeout=15, readonly=True)  # timeout = login timeout (s)
    conn.timeout = 60                                          # query timeout (s)
    conn.execute("SET LOCK_TIMEOUT 5000;")
    return conn


T_MASTER = f"dbo.MASTER{WORKSPACE}"
T_DESC = f"dbo.MASTER{WORKSPACE}_DESCRIPTIONS"
T_HIST = f"dbo.MASTER{WORKSPACE}_HISTORY"
T_AB = f"dbo.MASTER{WORKSPACE}_ABDATA"

CORE_COLUMNS = [
    "mrID", "mrTIMESTAMP", "mrTITLE", "mrSTATUS", "mrPRIORITY", "mrSUBMITDATE",
    "mrUPDATEDATE", "mrSUBMITTER", "mrASSIGNEES", "mrATTACHMENTS", "mrDESCRIPTION",
]
# Custom fields confirmed in UAT [Validate V5], e.g. "PO__bNumber,Invoice__bNumber,Supplier__bName"
CUSTOM_COLUMNS = [c for c in os.environ.get("FP_CUSTOM_COLUMNS", "").split(",") if c]


class SchemaDrift(Exception):
    pass


def check_schema(conn: pyodbc.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA='dbo' AND TABLE_NAME=?",
        f"MASTER{WORKSPACE}",
    ).fetchall()
    present = {r.COLUMN_NAME for r in rows}
    missing = [c for c in CORE_COLUMNS + CUSTOM_COLUMNS if c not in present]
    if missing:
        raise SchemaDrift(f"missing columns in MASTER{WORKSPACE}: {missing}")
    return CORE_COLUMNS + CUSTOM_COLUMNS


def decode_field_name(column: str) -> str:
    return column.replace("__b", " ")
```

**fp-sync handler: steps ①–⑩**

```python
# fp_sync.py — steps ①–⑩: lease, Kerberos, incremental read, snapshot, ledger, queue, watermark
import hashlib
import json
import os
import time
import uuid
from datetime import date, datetime
from decimal import Decimal

import boto3
from botocore.exceptions import ClientError

import fp_common as fp

ENV = os.environ.get("ENV", "dev")
BUCKET = os.environ["FP_RAW_BUCKET"]
QUEUE_URL = os.environ["FP_QUEUE_URL"]
CHECKPOINT = boto3.resource("dynamodb").Table(os.environ["FP_CHECKPOINT_TABLE"])
LEDGER = boto3.resource("dynamodb").Table(os.environ["FP_LEDGER_TABLE"])
s3 = boto3.client("s3")
sqs = boto3.client("sqs")
cw = boto3.client("cloudwatch")

PAGE = 200
TIME_BUDGET_S = 180
CP_KEY = {"pk": f"{ENV}#{fp.WORKSPACE}"}


def _metric(name: str, value: float = 1.0) -> None:
    cw.put_metric_data(Namespace="FootPrintsIngest",
                       MetricData=[{"MetricName": name, "Value": value,
                                    "Dimensions": [{"Name": "Env", "Value": ENV}]}])


def _jsonable(v):
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    if isinstance(v, (bytes, bytearray)):
        return "0x" + bytes(v).hex()
    if isinstance(v, Decimal):
        return str(v)
    return v


def _acquire_lease(owner: str) -> bytes | None:
    now = int(time.time())
    try:
        item = CHECKPOINT.update_item(
            Key=CP_KEY,
            UpdateExpression="SET lease_owner = :o, lease_until = :u",
            ConditionExpression="attribute_not_exists(lease_until) OR lease_until < :n",
            ExpressionAttributeValues={":o": owner, ":u": now + 600, ":n": now},
            ReturnValues="ALL_NEW",
        )["Attributes"]
    except ClientError as e:
        if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return None
        raise
    return bytes.fromhex(item.get("watermark_hex", "0x" + "00" * 8)[2:])


def _commit_watermark(owner: str, rv: bytes, rows: int) -> None:
    CHECKPOINT.update_item(
        Key=CP_KEY,
        UpdateExpression="SET watermark_hex = :w, last_success_at = :t, last_rows = :r",
        ConditionExpression="lease_owner = :o",
        ExpressionAttributeValues={":w": "0x" + rv.hex(), ":t": int(time.time()), ":r": rows, ":o": owner},
    )


def _release_lease(owner: str) -> None:
    try:
        CHECKPOINT.update_item(Key=CP_KEY, UpdateExpression="REMOVE lease_owner, lease_until",
                               ConditionExpression="lease_owner = :o",
                               ExpressionAttributeValues={":o": owner})
    except ClientError:
        pass


def _children(cur, sql_template: str, ids: list[int]) -> dict[int, list[dict]]:
    marks = ",".join("?" * len(ids))
    out: dict[int, list[dict]] = {}
    cur.execute(sql_template.format(marks=marks), *ids)
    cols = [c[0] for c in cur.description]
    for row in cur.fetchall():
        rec = {c: _jsonable(v) for c, v in zip(cols, row)}
        out.setdefault(rec["mrID"], []).append(rec)
    return out


def _put_json(key: str, body: dict) -> str:
    data = json.dumps(body, ensure_ascii=False, default=_jsonable).encode()
    s3.put_object(Bucket=BUCKET, Key=key, Body=data, ContentType="application/json",
                  ServerSideEncryption="aws:kms")
    return hashlib.sha256(data).hexdigest()


def handler(event, context):
    owner = getattr(context, "aws_request_id", str(uuid.uuid4()))
    watermark = _acquire_lease(owner)
    if watermark is None:
        return {"status": "skipped", "reason": "lease held"}
    started, total = time.time(), 0
    try:
        try:
            fp.ensure_kerberos_ticket()
        except fp.KerberosError:
            _metric("KerberosFailure")
            raise                                       # F1: no retry this run
        try:
            conn = fp.connect()
        except Exception:
            _metric("SqlLoginFailure")                  # F2
            raise
        with conn:
            columns = fp.check_schema(conn)             # raises SchemaDrift
            cur = conn.cursor()
            upper = bytes(cur.execute("SELECT MIN_ACTIVE_ROWVERSION()").fetchone()[0])
            if upper < watermark:
                _metric("RowversionWentBackwards")       # restore detected: re-baseline with the DBA
                return {"status": "stopped", "reason": "rowversion regression"}
            select_cols = ", ".join(f"m.[{c}]" for c in columns)
            page_sql = (f"SELECT TOP ({PAGE}) {select_cols} FROM {fp.T_MASTER} AS m "
                        "WHERE m.mrTIMESTAMP > ? AND m.mrTIMESTAMP < ? AND m.mrSTATUS <> '_SOLVED_' "
                        "ORDER BY m.mrTIMESTAMP")
            while time.time() - started < TIME_BUDGET_S:
                cur.execute(page_sql, watermark, upper)
                names = [c[0] for c in cur.description]
                rows = [dict(zip(names, r)) for r in cur.fetchall()]
                if not rows:
                    break
                ids = [r["mrID"] for r in rows]
                descs = _children(cur, f"SELECT mrID, mrGENERATION, mrDESCRIPTION FROM {fp.T_DESC} "
                                       "WHERE mrID IN ({marks}) ORDER BY mrID, mrGENERATION", ids)
                hist = _children(cur, f"SELECT mrID, mrGENERATION, mrHISTORY, mrCOMMENT FROM {fp.T_HIST} "
                                      "WHERE mrID IN ({marks}) ORDER BY mrID, mrGENERATION", ids)
                ab = _children(cur, f"SELECT * FROM {fp.T_AB} WHERE mrID IN ({{marks}})", ids)
                messages = []
                for r in rows:
                    mr_id, rv = r["mrID"], "0x" + bytes(r["mrTIMESTAMP"]).hex()
                    prefix = f"tickets/{fp.WORKSPACE}/{mr_id}/{rv}/"
                    core = {k: _jsonable(v) for k, v in r.items() if k in fp.CORE_COLUMNS}
                    custom = {fp.decode_field_name(k): _jsonable(r[k]) for k in fp.CUSTOM_COLUMNS}
                    ticket = {"workspace": fp.WORKSPACE, **core, "custom_fields": custom,
                              "contact": (ab.get(mr_id) or [{}])[0]}
                    sha = _put_json(prefix + "ticket.json", ticket)
                    _put_json(prefix + "descriptions.json", {"items": descs.get(mr_id, [])})
                    _put_json(prefix + "history.json", {"items": hist.get(mr_id, [])})
                    deleted = (r["mrSTATUS"] or "").strip() == "_DELETED_"
                    try:
                        LEDGER.put_item(
                            Item={"pk": f"{fp.WORKSPACE}#{mr_id}#{rv}", "mrID": mr_id,
                                  "status": "DELETED_AT_SOURCE" if deleted else "SNAPSHOT",
                                  "s3_prefix": prefix, "content_sha256": sha,
                                  "created_at": int(time.time())},
                            ConditionExpression="attribute_not_exists(pk)")
                    except ClientError as e:
                        if e.response["Error"]["Code"] != "ConditionalCheckFailedException":
                            raise
                        continue                         # already recorded by an earlier run
                    if not deleted:
                        attachments = [a for a in (r.get("mrATTACHMENTS") or "").splitlines() if a.strip()]
                        messages.append({"mrID": mr_id, "rowversion": rv, "s3Prefix": prefix,
                                         "attachments": attachments})
                for i in range(0, len(messages), 10):
                    batch = messages[i:i + 10]
                    resp = sqs.send_message_batch(QueueUrl=QUEUE_URL, Entries=[
                        {"Id": str(n), "MessageBody": json.dumps(m)} for n, m in enumerate(batch)])
                    if resp.get("Failed"):
                        raise RuntimeError(f"SQS send failed: {resp['Failed']}")   # watermark not advanced
                watermark = bytes(rows[-1]["mrTIMESTAMP"])
                _commit_watermark(owner, watermark, len(rows))
                total += len(rows)
                if len(rows) < PAGE:
                    break
        _metric("TicketsChanged", total)
        _metric("LastSuccessfulSync", 1)
        return {"status": "ok", "rows": total, "watermark": "0x" + watermark.hex()}
    finally:
        _release_lease(owner)
```

**fp-fetch handler: steps ⑪–⑭**

```python
# fp_fetch.py — steps ⑪–⑭: attachments (optional SMB), manifest, ledger, StartExecution
import hashlib
import json
import os
import re

import boto3
import smbclient
from botocore.exceptions import ClientError

import fp_common as fp

BUCKET = os.environ["FP_RAW_BUCKET"]
LEDGER = boto3.resource("dynamodb").Table(os.environ["FP_LEDGER_TABLE"])
STATE_MACHINE_ARN = os.environ["CASE_WORKFLOW_ARN"]
SMB_ENABLED = os.environ.get("FP_SMB_ENABLED", "false") == "true"
SMB_SERVER = os.environ.get("FP_SMB_SERVER", "")              # fpapp.boeing.com
SMB_ROOT = os.environ.get("FP_SMB_ROOT", "")                  # \\fpapp.boeing.com\FPAttachments$ [Validate V8]
MAX_FILE = int(os.environ.get("FP_MAX_ATTACHMENT_BYTES", str(25 * 1024 * 1024)))
s3 = boto3.client("s3")
sfn = boto3.client("stepfunctions")
_smb_ready = False


def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", name)[:120]


def _attachment_path(mr_id: int, name: str) -> str:
    # Layout under the attachment root to be confirmed on the FootPrints server [Validate V8]
    return f"{SMB_ROOT}\\{fp.WORKSPACE}\\{mr_id}\\{name}"


def _copy_attachments(msg: dict) -> tuple[list[dict], str]:
    global _smb_ready
    if not msg["attachments"]:
        return [], "NONE"
    if not SMB_ENABLED:
        return [], "ATTACHMENT_NOT_RETRIEVED"
    fp.ensure_kerberos_ticket()
    if not _smb_ready:
        smbclient.register_session(SMB_SERVER, auth_protocol="kerberos", encrypt=True)
        _smb_ready = True
    entries, status = [], "RETRIEVED"
    for n, name in enumerate(msg["attachments"], 1):
        try:
            with smbclient.open_file(_attachment_path(msg["mrID"], name), mode="rb") as f:
                data = f.read(MAX_FILE + 1)
        except OSError:
            status = "ATTACHMENT_UNAVAILABLE"            # F5: continue with ticket text
            continue
        if len(data) > MAX_FILE:
            status = "ATTACHMENT_UNAVAILABLE"
            continue
        key = f"{msg['s3Prefix']}attachments/{n}-{_safe(name)}"
        s3.put_object(Bucket=BUCKET, Key=key, Body=data, ServerSideEncryption="aws:kms")
        entries.append({"key": key, "name": name, "bytes": len(data),
                        "sha256": hashlib.sha256(data).hexdigest()})
    return entries, status


def _process(msg: dict) -> None:
    pk = f"{fp.WORKSPACE}#{msg['mrID']}#{msg['rowversion']}"
    item = LEDGER.get_item(Key={"pk": pk}).get("Item")
    if item and item["status"] in ("HANDED_OFF", "COMPLETED", "NEEDS_REVIEW"):
        return                                            # redelivery after success
    attachments, att_status = _copy_attachments(msg)
    manifest = {"source": "footprints", "workspace": fp.WORKSPACE, **msg,
                "attachment_status": att_status, "attachment_objects": attachments}
    s3.put_object(Bucket=BUCKET, Key=msg["s3Prefix"] + "manifest.json",
                  Body=json.dumps(manifest).encode(), ServerSideEncryption="aws:kms")
    LEDGER.update_item(Key={"pk": pk}, UpdateExpression="SET #s = :s, attachment_status = :a",
                       ExpressionAttributeNames={"#s": "status"},
                       ExpressionAttributeValues={":s": "STORED", ":a": att_status})
    case_id = f"fp{fp.WORKSPACE}-{msg['mrID']}-{msg['rowversion'][2:]}"
    try:
        arn = sfn.start_execution(
            stateMachineArn=STATE_MACHINE_ARN, name=case_id,
            input=json.dumps({"caseId": case_id, "source": "footprints", "workspace": fp.WORKSPACE,
                              "mrID": msg["mrID"], "rowversion": msg["rowversion"],
                              "manifestUri": f"s3://{BUCKET}/{msg['s3Prefix']}manifest.json"}),
        )["executionArn"]
    except ClientError as e:
        if e.response["Error"]["Code"] != "ExecutionAlreadyExists":
            raise
        arn = "already-started"
    LEDGER.update_item(Key={"pk": pk}, UpdateExpression="SET #s = :s, execution_arn = :e",
                       ExpressionAttributeNames={"#s": "status"},
                       ExpressionAttributeValues={":s": "HANDED_OFF", ":e": arn})


def handler(event, context):
    failures = []
    for record in event.get("Records", []):
        try:
            _process(json.loads(record["body"]))
        except Exception:                                 # F4: redelivered, then DLQ
            failures.append({"itemIdentifier": record["messageId"]})
    return {"batchItemFailures": failures}
```

---

## 9. Agentic processing integration

### 9.1 Workflow (`case-workflow`, shared with the EWS source)

| Step | What happens |
| --- | --- |
| ⑮ Malware gate | Wait for the GuardDuty tag on every attachment object. `THREATS_FOUND` → quarantine prefix, `NEEDS_REVIEW`, alert. [Validate V9] |
| ⑮ Pre-processing | Text extraction (PDF/Office/images through Textract), concatenation of descriptions oldest → newest, signature/boilerplate trimming, PII masking where policy requires, Bedrock Guardrails. |
| ⑯ Classification agent | Input: title, descriptions, custom fields, attachment names and text. Output: `{paymentRelated, category (invoice status, payment status, remittance, dispute, other), itarSuspected, confidence, rationale}`. |
| ITAR branch | `itarSuspected` or low confidence → copy to `fp-itar-<env>` (separate CMK) and/or human review. [Decision: export control] |
| ⑰ Extraction agent | Runs for payment-related tickets only. Output is validated against the JSON schema below. |
| Persist | Aurora `case` (keyed by `source = footprints`, `mrID`, `rowversion`), `extraction`, `audit_event`. |
| ⑱ Reporting agent | Scheduled. Uses `query_cases` across both sources, then writes the consolidated report to S3 and Aurora. |

### 9.2 Extraction schema (minimum)

```json
{
  "type": "object",
  "required": ["mrID", "purchaseOrders", "invoices", "supplier", "evidence"],
  "properties": {
    "mrID": {"type": "integer"},
    "purchaseOrders": {"type": "array", "items": {"type": "string"}},
    "invoices": {"type": "array", "items": {"type": "object", "properties": {
      "invoiceNumber": {"type": "string"}, "invoiceDate": {"type": "string", "format": "date"},
      "amount": {"type": "number"}, "currency": {"type": "string"}}}},
    "supplier": {"type": "object", "properties": {
      "name": {"type": "string"}, "supplierId": {"type": "string"}, "contactEmail": {"type": "string"}}},
    "transactionDate": {"type": ["string", "null"], "format": "date"},
    "paymentReference": {"type": ["string", "null"]},
    "requestSummary": {"type": "string"},
    "evidence": {"type": "array", "items": {"type": "object", "properties": {
      "field": {"type": "string"}, "source": {"enum": ["custom_field", "description", "history", "attachment"]},
      "locator": {"type": "string"}, "quote": {"type": "string"}}}}
  }
}
```

Use FootPrints custom fields first, when populated. Text extraction fills the gaps, and every value carries `evidence`, so a reviewer can trace it back to a description generation or attachment page.

### 9.3 Agent and tool boundaries

AgentCore Gateway exposes three read-only tools, each backed by a Lambda with an IAM role scoped to `fp-raw-<env>/tickets/144/*` and the Aurora read user:

- `get_ticket(caseId)`
- `get_attachment_text(caseId, n)`
- `query_cases(filter)`

The agent runtime role has **no** `secretsmanager:GetSecretValue` on `fp/*`, no route to on-prem, and no SQS/DynamoDB ingestion permissions.

### 9.4 Traceability

`caseId fp144-<mrID>-<rowversion>` links:

- the FootPrints ticket
- the S3 snapshot (immutable, sha256 in the manifest)
- the ledger row
- the Step Functions execution
- the AgentCore session (`runtimeSessionId`)
- the Aurora case

The FootPrints `mrID` is shown on every report line, so users can open the original ticket.

---

## 10. AWS component configuration and IAM

| Role | Allowed |
| --- | --- |
| `fp-sync-role` | `secretsmanager:GetSecretValue` on `fp/svc-fp-ro-<env>`; `kms:Decrypt` on `alias/fp-<env>` (via Secrets Manager / S3); `s3:PutObject` on `fp-raw-<env>/tickets/144/*`; DynamoDB `UpdateItem/GetItem` on `fp_checkpoint`, `PutItem` on `fp_ledger`; `sqs:SendMessage` on `fp-ticket-queue`; `cloudwatch:PutMetricData` (namespace `FootPrintsIngest`); VPC ENI permissions. |
| `fp-fetch-role` | Same secret (only if SMB is enabled); S3 `PutObject` (attachments, manifest); DynamoDB `GetItem/UpdateItem` on `fp_ledger`; SQS receive/delete; `states:StartExecution` on `case-workflow`. |
| `case-workflow-role` | AgentCore `InvokeAgentRuntime` on the three runtimes; tool / preprocess Lambdas; S3 read on `fp-raw-<env>`; write to `fp-itar-<env>`. |
| Agent runtime / tool roles | Read-only S3 prefix and Aurora read user. No ingestion permissions. |

Other resource settings:

- **SCPs / guardrails:** deny `secretsmanager:GetSecretValue` on `fp/*` to every principal except the two ingestion roles and break-glass. Deny resource creation outside `us-gov-west-1`.
- **Logging:** CloudTrail (management + S3/Secrets data events), Lambda logs (KMS-encrypted, 1-year retention [Decision]), VPC Flow Logs on the ingestion subnets.

---

## 11. Development, UAT and Production

### 11.1 What each environment uses

| | Dev | UAT | Prod |
| --- | --- | --- | --- |
| FootPrints source | **Schema-identical mirror**: RDS for SQL Server (or SQL Server container in CI) with `MASTER144*` DDL supplied by the DBA (structure only) and **synthetic tickets** | **Non-prod FootPrints** (workspace 144) [Assumption A3] | **Prod FootPrints**: readable secondary or reporting replica [Decision: DBA] |
| Account | Local SQL login on the mirror (Dev only, `FP_AUTH=sql`) | `svc-fp-ro-nonprod` [Confirmed] | `svc-fp-ro-prd` [Confirmed] |
| Kerberos path | Optional manual smoke test against non-prod; **not scheduled** | Full: Secrets Manager → kinit → TLS → TDS | Full |
| Connectivity | None to Boeing needed for the mirror; non-prod rule set only if the smoke test is approved | Non-prod firewall rule set | Prod firewall rule set |
| Attachments | Synthetic files in an S3 stub, or a Samba container | Non-prod share (if approved) | Prod share (if approved) |
| Agents | Mocked / evaluation set | Full agents, evaluation against labelled tickets | Shadow mode, then live |
| Data classification | Synthetic, no CUI/ITAR | Non-prod data may still be real: treat as production-sensitive unless the data owner states otherwise | Production |

**Why Dev does not hold a Kerberos credential:** only two AD accounts exist. Dev and UAT would share `svc-fp-ro-nonprod`, and a Dev bug could lock the account that UAT depends on. Dev therefore proves the logic (watermark, paging, idempotency, hand-off) against the mirror. The Kerberos / network path is proven in UAT.

- If Dev Kerberos parity is wanted later, there are two choices:
  - join the Dev RDS mirror to an AWS Managed Microsoft AD
  - request a third account [Decision]

### 11.2 Building the Dev mirror

1. The DBA scripts the **structure** of `MASTER144`, `MASTER144_DESCRIPTIONS`, `MASTER144_HISTORY` and `MASTER144_ABDATA` from non-prod (SSMS "Generate Scripts", schema only). No data leaves Boeing for Dev.
2. Terraform/CDK creates RDS for SQL Server (or a CI container) and applies the DDL. The `mrTIMESTAMP` `timestamp` column means the mirror generates rowversions exactly like production.
3. A data generator inserts and updates synthetic tickets. It covers:
   - PO / invoice / supplier in custom fields and in free text
   - multiple description generations
   - status changes, `_DELETED_` tickets
   - attachment names
   - long-running open transactions (to test `MIN_ACTIVE_ROWVERSION`)
4. Contract tests run the same SQL as production against the mirror on every pipeline run.

### 11.3 UAT

- Connect with `svc-fp-ro-nonprod` through the full network path.
- Run checklist §14 sections B–E.
- Have the FootPrints admin create **controlled test tickets** in non-prod workspace 144 (payment and non-payment, with attachments, with ITAR-like markers). Verify ingestion latency, classification and extraction against the expected results.
- If non-prod has no representative workspace 144 data, request a DBA refresh of workspace 144 tables from prod into non-prod, with masking. [Decision: data owner + export control]

### 11.4 Production rollout

1. Firewall prod rule set, prod secret, DBA grants on the replica; confirm `auth_scheme = KERBEROS`.
2. Initial load in an agreed window (cut-over date filter), monitored with the DBA.
3. **Shadow mode, 2 weeks [Recommended]:** full ingestion and agent processing, with outputs reviewed by the business and not used downstream. Measure precision/recall of payment classification and field accuracy.
4. Go-live after sign-off. Keep a manual-review queue for low-confidence and ITAR cases.

### 11.5 Promotion gates

| Gate | Evidence |
| --- | --- |
| Dev → UAT | Contract tests green on the mirror; idempotency tests (re-run, duplicate SQS delivery, crash mid-page) green; image scan clean. |
| UAT → Prod | Kerberos login confirmed (`KERBEROS` auth scheme); TLS validated (no `TrustServerCertificate=yes`); grants verified as SELECT-only; V1–V9 closed; DBA load sign-off; agent evaluation thresholds met; security and export-control approvals. |
| Shadow → Live | Business sign-off on shadow results; runbook and alarms tested (F1–F6 drills). |

---

## 12. Security and compliance controls (checklist)

- [ ] The SQL login has SELECT on the `MASTER144*` tables or `airead` views only, with explicit DENY on write/execute. Verified with `fn_my_permissions` as the service account.
- [ ] `TrustServerCertificate=no`; the Boeing CA chain is pinned in the image; TLS 1.2+.
- [ ] The secret is readable only by the ingestion roles (resource policy + SCP); CMK key policy is scoped; CloudTrail data events are on.
- [ ] No password in environment variables, logs, images or Step Functions input.
- [ ] Ingestion subnets have no internet egress; SG egress is limited to the §5.6 destinations.
- [ ] Separate non-prod and prod firewall rule sets and accounts; no cross-environment routing.
- [ ] S3: Block Public Access, SSE-KMS, versioning, Object Lock (if approved), malware scanning before use.
- [ ] Agents have no on-prem route and no secret access; Gateway tools are read-only.
- [ ] ITAR segregation and GovCloud residency approved by export control.
- [ ] Every agent output carries evidence pointers; human review for low-confidence and ITAR cases.

---

## 13. Monitoring and operations

| Metric / alarm | Threshold | Action |
| --- | --- | --- |
| `KerberosFailure` (F1) | ≥ 1 | Page; check secret version, AD account status/lockout, clock, KDC reachability, SPN. |
| `SqlLoginFailure` (F2) | ≥ 1 | Page; DBA checks login/user/grant, TLS certificate. |
| `SchemaDrift` | ≥ 1 | Ingestion stops; update the column mapping with the FootPrints admin. |
| `LastSuccessfulSync` missing | > 20 min | Investigate network, DB availability, lease stuck (> 10 min). |
| `RowversionWentBackwards` | ≥ 1 | DB restore suspected; re-baseline with the DBA. |
| `TicketsChanged` | Anomaly band | Unexpected bulk updates (e.g. a mass reassignment) → consider content-hash suppression. |
| SQS DLQ depth | > 0 | Inspect, fix, redrive. |
| Step Functions failures / `FAILED_AGENT` | > 0 | Manual queue; reprocess with `#r2`. |
| Query duration (fp-sync log) | p95 > 30 s | Review with the DBA (§7.5). |

Runbook entries cover:

- rotate the password
- re-baseline the watermark (set `watermark_hex` to a DBA-provided value)
- re-ingest one ticket (delete its ledger rows and lower the watermark below its `mrTIMESTAMP`, or invoke fp-sync with an `mrID` override in a controlled change)
- pause (disable the schedule)

---

## 14. Integration testing and deployment checklist

**A. Dev (mirror)**

- [ ] DDL applied; generator creates tickets, edits, description appends, `_DELETED_`.
- [ ] First run loads all; second run with no changes returns 0 rows.
- [ ] Edit one ticket → exactly one new version flows to Step Functions.
- [ ] Open a transaction that updates a ticket, run fp-sync, commit, run again → the row is picked up on the second run (proves `MIN_ACTIVE_ROWVERSION`).
- [ ] Kill fp-sync mid-page → next run re-reads, the ledger prevents duplicates, and the execution name prevents a duplicate workflow.
- [ ] Duplicate SQS delivery → single execution.

**B. Network (UAT)**

- [ ] From a test Lambda in the ingestion subnets, TCP connect to SQL 1433, DCs 88 and, optionally, FootPrints 445.
- [ ] Resolver: `fpsql.boeing.com` and the DC names resolve through the outbound endpoint.

**C. Kerberos and SQL (UAT)**

- [ ] `kinit` succeeds with the secret; `klist` shows `krbtgt/BOEING.COM`.
- [ ] Connection succeeds; `klist` now also shows `MSSQLSvc/fpsql.boeing.com:1433@BOEING.COM`.
- [ ] `SELECT auth_scheme, encrypt_option FROM sys.dm_exec_connections WHERE session_id=@@SPID` → `KERBEROS`, `TRUE`.
- [ ] `SELECT @@SERVERNAME, DATABASEPROPERTYEX(DB_NAME(),'Updateability')` → secondary / `READ_ONLY` if read-only routing is used.
- [ ] `INSERT`/`UPDATE` attempts fail with a permission error (negative test, in a transaction rolled back by the DBA's test harness).
- [ ] Wrong password in a test secret → F1 path, one failed logon only, alarm fires.

**D. Data (UAT)**

- [ ] Custom-field mapping confirmed (V5); `ticket.json` shows business names.
- [ ] Description append, status change and field edit each create a new version (V6).
- [ ] Attachments copied with matching sha256 (if SMB is approved); otherwise flagged `ATTACHMENT_NOT_RETRIEVED`.

**E. Agents (UAT)**

- [ ] The labelled evaluation set meets the agreed thresholds for payment classification and field extraction.
- [ ] ITAR-like test tickets are routed to segregation / review.

**F. Prod**

- [ ] Gates in §11.5; initial load window agreed; shadow mode started.

---

## 15. Open decisions and dependencies

| # | Item | Owner | Needed by |
| --- | --- | --- | --- |
| D1 | Connectivity request §5.5 (routing, firewall R1–R4, DNS forwarding, TLS-inspection exemption) | Boeing network / connectivity team (project to raise through the Boeing IT sponsor) | UAT |
| D2 | SPNs, AES enctypes, lockout policy, rotation process for `svc-fp-ro-*` (K1–K5) | Boeing AD team | UAT |
| D3 | Read target (AG secondary / replica / primary), grants or `airead` views, load sign-off, optional `mrTIMESTAMP` index | FootPrints DBA | UAT |
| D4 | SQL Server TLS certificate with correct SAN; CA chain for the image | Boeing PKI / DBA | UAT |
| D5 | Workspace 144 custom field names; `_REQUEST_` inclusion; procurement filter (if any) | FootPrints admin / business owner | Dev (mirror) |
| D6 | Attachment access: SMB share, on-prem copy job, or text-only MVP | FootPrints owner + security | UAT |
| D7 | ITAR handling, GovCloud residency, non-prod data refresh/masking | Export control / data owner | UAT |
| D8 | Third AD account for Dev Kerberos parity (optional) | AD team | Optional |
| D9 | GovCloud availability of GuardDuty S3 malware protection, Step Functions → AgentCore integration, selected models | AWS platform team | Dev |

---

## 16. Sources

**BMC FootPrints**

- [About Database Design (FootPrints Service Core 11.6)](https://docs.bmc.com/xwiki/bin/view/More-Products/Footprints/FootPrints-Service-Core-Online-Help/Ftprnts116olh/Chapter-10-Database-Administration/About-Database-Design)
- [Table Design and Indexes (MASTERx tables, columns, indexes)](https://customerservice.ascenderhcm.com/help/FootPrintsHelp/Content/Table_Design_and_Indexes.htm)
- [BMC FootPrints Service Core 11.6 Large Data Model Diagram](https://www.uvm.edu/~fcs/Doc/FootPrints_11.6/BMC%20FootPrints%20Service%20Core%2011.6%20Large%20Data%20Model%20Diagram-PDF.pdf)
- [File Attachments (attachments directory)](https://public.era.nih.gov/help/FootPrintsHelp/content/file_attachments.htm)
- [Changing the location of the file attachments directory (stored on the file system, not the database)](https://docs.bmc.com/docs/fp2018/changing-the-location-of-the-file-attachments-directory-798068465.html)
- BMC FootPrints Service Core 11.6 API documentation (provided by the project)

**Microsoft SQL Server and ODBC**

- [rowversion (Transact-SQL)](https://learn.microsoft.com/en-us/sql/t-sql/data-types/rowversion-transact-sql)
- [MIN_ACTIVE_ROWVERSION (Transact-SQL)](https://learn.microsoft.com/en-us/sql/t-sql/functions/min-active-rowversion-transact-sql)
- [Using Integrated Authentication (ODBC Driver on Linux and macOS)](https://learn.microsoft.com/he-il/sql/connect/odbc/linux-mac/using-integrated-authentication?view=sql-server-2017)
- [ODBC Driver 18.0 for SQL Server released (Encrypt default change)](https://techcommunity.microsoft.com/blog/sqlserver/odbc-driver-18-0-for-sql-server-released/3169228)
- [Register a Service Principal Name for Kerberos connections](https://learn.microsoft.com/en-us/sql/database-engine/configure-windows/register-a-service-principal-name-for-kerberos-connections)
- [Configure read-only routing for an availability group](https://learn.microsoft.com/sql/database-engine/availability-groups/windows/configure-read-only-routing-for-an-availability-group-sql-server)
- [Types of client connections to replicas within an availability group (ApplicationIntent)](https://technet.microsoft.com/en-us/hh510184)

**AWS** (see also the EWS guide §15)

- [Configure read-only routing in an Always On availability group in SQL Server on AWS](https://docs.aws.amazon.com/prescriptive-guidance/latest/patterns/configure-read-only-routing-in-an-always-on-availability-group-in-sql-server-on-aws.html)
- [Route 53 Resolver endpoints and forwarding rules (hybrid DNS)](https://docs.aws.amazon.com/whitepapers/latest/hybrid-cloud-dns-options-for-vpc/route-53-resolver-endpoints-and-forwarding-rules.html)
- [GuardDuty Malware Protection for S3](https://docs.aws.amazon.com/guardduty/latest/ug/gdu-malware-protection-s3.html)
- [Handling errors for an SQS event source in Lambda (partial batch responses)](https://docs.aws.amazon.com/lambda/latest/dg/services-sqs-errorhandling.html)
- [Amazon Bedrock AgentCore is now available in AWS GovCloud (US-West)](https://aws.amazon.com/about-aws/whats-new/2026/05/bedrock-agentcore-launch-aws-govcloud-us/)
- [AWS GovCloud (US): Maintaining ITAR compliance](https://docs.aws.amazon.com/govcloud-us/latest/UserGuide/govcloud-itar.html)

**Libraries**

- [pyodbc](https://github.com/mkleehammer/pyodbc/wiki)
- [smbprotocol / smbclient](https://github.com/jborean93/smbprotocol)
