# AWS ↔ On-Premises Exchange (EWS) Email Ingestion for the Bedrock AgentCore Payment Workflow

**Technical architecture and implementation guide** · version 1.0 · 12 Nov 2026 · author: Jared Shahidin

Companion files:

- `aws_exchange_ews_target_architecture.drawio`: page 1 shows the detailed per-environment architecture; page 2 shows the Dev/UAT/Prod topology.
- `aws_exchange_ews_sequence.drawio`: the runtime sequence, including failure paths.

Circled step numbers ①–⑱ and failure codes F1–F6 are the same in all three files.

---

## 0. How to read this guide

Every statement that matters for build or approval carries one of these tags:

| Tag | Meaning |
| --- | --- |
| **[Confirmed]** | Given as a requirement or fact by the client or project team. |
| **[Recommended]** | Design choice made in this guide. It can be changed, but the alternatives are worse for the reasons given. |
| **[Assumption]** | Believed true but not yet confirmed. Each one has an owner in §14. |
| **[Validate]** | Must be checked against the live environment (Exchange, AD, network, security) before build or promotion. |
| **[Decision]** | Needs approval from an accountable owner (security, export control, architecture board). |

Placeholders such as `corp.example`, `mail.corp.example`, `ap-payments@corp.example`, `svc-apmail-*` and the CIDRs are examples. Replace them with real values.

---

## 1. Summary of the recommended design

The core design is as follows. An **AWS Lambda function in private subnets** acts as the EWS client and polls the on-prem Exchange shared mailbox every 2 minutes. It authenticates with the dedicated AD service account over **EWS (SOAP over HTTPS 443)**. Traffic leaves AWS through **Transit Gateway** and the existing **Direct Connect / Site-to-Site VPN**, and reaches the Exchange load-balanced namespace through the enterprise firewall.

Ingestion is incremental, using `SyncFolderItems` sync state. Every email is recorded in a DynamoDB ledger for idempotency and fetched as full MIME by a second Lambda. Raw email, parts and attachments are stored in S3 and handed to a **Step Functions** workflow. That workflow invokes the **AgentCore** classification, extraction and reporting agents.

**No agent ever talks to Exchange or sees the mailbox credential.** Agents read the stored copy through read-only AgentCore Gateway tools.

| Area | Recommendation | Tag |
| --- | --- | --- |
| Mailbox API | EWS on Exchange Server. Microsoft Graph cannot reach on-prem mailboxes; hybrid REST access ended in July 2023. EWS retirement applies only to Exchange Online, not Exchange Server. | [Confirmed] constraint · [Recommended] |
| EWS client component | Two Lambda functions from one container image: `ews-sync` (poller, reserved concurrency 1) and `ews-fetch` (fetcher, SQS-triggered, max concurrency 2–4). | [Recommended] |
| Trigger | EventBridge Scheduler, `rate(2 minutes)` in prod. | [Recommended] |
| Authentication | NTLMv2 via Negotiate over TLS 1.2+, with channel binding (Extended Protection). Kerberos if NTLM is restricted. No Basic auth. OAuth is not used (see §5.1). | [Recommended] · [Validate] |
| Mailbox permission | Delegate access: `FullAccess` (AutoMapping off) on the shared mailbox only, or `Reviewer` on the Inbox for strict read-only. No `ApplicationImpersonation`. | [Recommended] · [Validate] |
| Credential | AWS Secrets Manager with a customer-managed KMS key. Only the two ews-* Lambda roles can read it. Rotation is driven by the client's PAM process. | [Recommended] |
| Connectivity | Direct Connect (primary) plus Site-to-Site VPN (backup) through a Transit Gateway in a network hub account. Route 53 Resolver outbound endpoint for `corp.example`. Firewall allows only env CIDRs → Exchange VIP TCP 443. | [Recommended] · [Validate] existing circuits |
| Incremental sync | `SyncFolderItems` SyncState is the watermark, stored in DynamoDB with a lease. The ledger is keyed per item with conditional writes. | [Recommended] |
| Hand-off | `StartExecution` on a Standard Step Functions workflow, with execution name = ledger id (natural idempotency). | [Recommended] |
| Environments | Separate AWS account, mailbox, AD service account, secret, TGW route table and firewall rule set for Dev, UAT and Prod. Dev/UAT use synthetic or approved replay data only. | [Recommended] · [Decision] |
| ITAR | The whole ingestion account must sit inside the approved ITAR boundary (e.g. AWS GovCloud (US-West)), because mail is stored before it is classified. | [Decision] export control |
| Fallback | Option B: an on-prem relay running the same client, pushing outbound to AWS. Used only if inbound AWS → Exchange 443 is refused. | [Recommended] alternative |

---

## 2. Requirements, assumptions and validation items

### 2.1 Confirmed requirements

- The shared mailbox is hosted on on-premises Microsoft Exchange, not Exchange Online.
- Microsoft Graph is not available for this integration.
- A dedicated service account exists for the shared mailbox (production).
- Integration must use EWS, subject to validating the Exchange version, authentication configuration and mailbox permissions.
- AWS retrieves new emails periodically with scheduled jobs.
- Bodies and attachments go into an AWS agentic workflow: classification (payment-related, ITAR/non-ITAR), extraction, then reporting.
- Dev, UAT and Prod environments are required.

### 2.2 Assumptions (each must be confirmed; owners in §14)

| # | Assumption | Why it matters |
| --- | --- | --- |
| A1 | Exchange is 2019 CU14+ or Exchange Server SE. | Extended Protection is on by default from 2019 CU14, which affects the load balancer and client. 2016/2019 are out of support. |
| A2 | The "group mailbox" is a **shared mailbox**, not a distribution group or public folder. | A distribution group has no mailbox to read. Public folders need different EWS calls and headers. |
| A3 | A hybrid network path between AWS and the data center exists or can be extended (DX and/or VPN). | It is the longest lead-time item. |
| A4 | Only a production Exchange organization exists (no lab org). | It drives the Dev/UAT mailbox strategy (§10). |
| A5 | Mail volume is low-to-moderate (hundreds to a few thousand messages per day). | It sizes polling, concurrency and throttling. |
| A6 | Only the production service account exists today. Dev/UAT accounts and mailboxes must be provisioned. | Stated, and must be requested. |

### 2.3 Items to validate before build (summary; detail in §5.9 and §13)

- The Exchange build: `Get-ExchangeServer | Format-List Name,Edition,AdminDisplayVersion`.
- EWS virtual-directory authentication methods and Extended Protection settings.
- The load-balancer TLS mode for the namespace.
- The mailbox type, permission model, throttling policy, and any EWS application access policy.
- The NTLM restriction status in the domain.
- Firewall/routing ownership and the internal CA chain.
- The ITAR boundary decision.

---

## 3. Architecture overview

### 3.1 Component responsibilities

| Component | Where | Responsibility | Talks to |
| --- | --- | --- | --- |
| EventBridge Scheduler | Workload account | Fires `ews-sync` on a schedule ① | `ews-sync` (async invoke) |
| **`ews-sync` Lambda (EWS client 1)** | Private subnets, `sg-ews-client` | Takes the lease, reads the secret, authenticates to EWS, runs `SyncFolderItems`, writes ledger rows, enqueues one SQS message per new email, commits SyncState ②–⑨ | Secrets Manager, DynamoDB, SQS (via VPC endpoints); Exchange VIP:443 (via TGW) |
| **`ews-fetch` Lambda (EWS client 2)** | Private subnets, `sg-ews-client` | `GetItem` with MIME, parses, stores to S3, updates the ledger, optionally categorizes the item, starts the workflow ⑩–⑭ | Exchange VIP:443; S3, DynamoDB, Step Functions |
| DynamoDB `ews_checkpoint` | Workload account | One row per mailbox: SyncState, lease, last success | — |
| DynamoDB `ews_ledger` | Workload account | One row per email: ids, status, S3 prefix, hashes, execution ARN | — |
| SQS `ews-fetch-queue` + DLQ | Workload account | Decouples discovery from fetch; retry and DLQ | — |
| S3 `email-raw-<env>` | Workload account | Immutable raw `.eml`, parts, attachments, manifest; GuardDuty malware scan | — |
| Step Functions `case-workflow` | Workload account | Malware gate, preprocessing, agent invocations, routing, persistence, error handling ⑮–⑰ | Lambda, AgentCore, Aurora |
| AgentCore Runtime agents | Workload account (VPC mode) | Classification ⑯, extraction ⑰, reporting ⑱ | AgentCore Gateway (tools) and Bedrock models |
| AgentCore Gateway + tool Lambdas | Workload account | Read-only `get_email`, `get_attachment_text`, `query_cases` | S3, Aurora |
| Aurora PostgreSQL | Workload VPC | Case, extraction and audit store (existing) | — |
| Transit Gateway, DX/VPN, Route 53 Resolver | Network hub account | Hybrid routing and DNS | On-prem network |
| Exchange VIP / CAS / Mailbox | On-prem | EWS endpoint `/EWS/Exchange.asmx`; mailbox store | AD DCs for NTLM/Kerberos validation |

### 3.2 Trust boundaries

1. **On-prem ↔ AWS.** Only `ews-sync` and `ews-fetch` initiate connections to Exchange (TCP 443), plus the Resolver outbound endpoint for DNS (53). Nothing on-prem initiates connections into AWS in Option A.
2. **Credential boundary.** The service-account password exists in Secrets Manager (encrypted) and in Lambda memory during an invocation. It is never written to logs, S3, DynamoDB, environment variables, or agent context.
3. **Agent boundary.** Agents and their tools have no route to Exchange (`sg` and route tables) and no IAM permission on the secret. They read only S3/Aurora through read-only tools.
4. **ITAR boundary.** The account and Region that store raw mail must be approved for ITAR data (§8.3).

---

## 4. Network connectivity design

### 4.1 Recommended path (production)

`ews-*` Lambda ENI (private subnet) → VPC route table → Transit Gateway attachment (workload VPC) → TGW route table `rt-prd` → Direct Connect gateway attachment (primary) or Site-to-Site VPN attachment (backup) → customer router → enterprise firewall → Exchange load balancer VIP `mail.corp.example:443` → Exchange front end (IIS `/EWS/Exchange.asmx`) → mailbox server.

| Option | Use when | Notes | Tag |
| --- | --- | --- | --- |
| **Direct Connect (private or transit VIF) + S2S VPN backup** | The client already has DX into AWS or will extend it | Predictable latency. DX is not encrypted by default. TLS protects EWS end-to-end; add MACsec or run VPN over DX if policy requires layer-3 encryption. | **[Recommended] for Prod** · [Validate] existing circuits |
| Site-to-Site VPN only (2 tunnels, BGP) | No DX, or Dev/UAT | Adequate for this traffic volume; plan for tunnel maintenance windows. | Alternative |
| Enterprise reverse proxy / WAF in front of EWS | Security policy forces all inbound traffic through a proxy | The proxy must **SSL-bridge with the same certificate** as Exchange and pass `Authorization`/`WWW-Authenticate` unchanged. SSL offloading breaks Extended Protection. | Alternative · [Validate] |
| **Option B: on-prem relay** | Inbound AWS → Exchange 443 is refused | The same container runs on a client VM or Kubernetes host and pushes `.eml` plus metadata to S3/SQS over outbound 443 using IAM Roles Anywhere. The AWS side from SQS onward is unchanged. | Fallback |
| Expose EWS to the internet with an IP allow-list | — | Not recommended: it increases attack surface on a server class with a history of critical vulnerabilities. | Rejected |

### 4.2 Routing

- **Workload VPC route table (`rtb-private-<env>`):** on-prem CIDRs (example `10.0.0.0/8` [Validate]) → TGW attachment; VPC CIDR → local; **no default route** (no IGW/NAT). AWS APIs are reached through VPC endpoints.
- **TGW route tables:** one per environment (`rt-dev`, `rt-uat`, `rt-prd`). Each workload VPC attachment is associated with its own table, and on-prem routes are propagated from the DX/VPN attachments. Environments have no routes to each other.
- **On-prem return path:** the client network must route each environment's VPC CIDR back via DX/VPN, and the firewall must permit the return flow (stateful).
- **CIDRs:** non-overlapping between Dev, UAT, Prod and on-prem. Request them from the client's IPAM.

### 4.3 DNS

- Create a **Route 53 Resolver outbound endpoint** (two AZs) in the hub VPC and a **forwarding rule** `corp.example` → on-prem DNS server IPs. Share the rule to the workload accounts with AWS RAM and associate it with each workload VPC. Lambda then resolves `mail.corp.example` through the VPC resolver (`.2`).
- Use the **same FQDN that is on the Exchange certificate SAN**. Never connect by IP, because TLS hostname verification would fail.

### 4.4 Firewall and port matrix

| # | Source | Destination | Protocol / port | Purpose | Required |
| --- | --- | --- | --- | --- | --- |
| 1 | VPC-`<env>` private subnets (Lambda ENIs) | Exchange VIP | TCP 443 | EWS SOAP over HTTPS | Yes |
| 2 | Resolver outbound endpoint ENI IPs | On-prem DNS servers | UDP+TCP 53 | Resolve `corp.example` | Yes |
| 3 | VPC-`<env>` private subnets | AD domain controllers | TCP+UDP 88 | Kerberos (only if NTLM is not allowed) | Conditional |
| 4 | Exchange front end | AD domain controllers | Existing | NTLM pass-through / Kerberos validation | Existing |
| 5 | Lambda ENIs | VPC interface endpoints | TCP 443 | Secrets Manager, KMS, SQS, Step Functions, Logs, CloudWatch, STS, AgentCore | Yes (inside VPC) |
| — | Anything else from AWS | On-prem | — | Deny | — |

### 4.5 TLS, certificates and Extended Protection

- Require **TLS 1.2 or higher** and full certificate-chain and hostname validation. Bundle the client's **internal root and intermediate CA** in the container image or a Lambda layer, and point `REQUESTS_CA_BUNDLE` at it. Never disable verification.
- **Extended Protection (EP)** is enabled by default from Exchange 2019 CU14. It binds NTLM/Kerberos authentication to the TLS channel with a Channel Binding Token (CBT). Consequences:
    - The load balancer or proxy must not **SSL-offload**. Use L4 pass-through or SSL bridging with the **same certificate** as the Exchange IIS front end.
    - The client must send CBT. `requests-ntlm` sends channel bindings over HTTPS by default (`send_cbt=True`), and `requests-gssapi`/`requests-kerberos` bind as well. Verify this in Dev against the real VIP. **[Validate]**
    - NTLMv1 does not work with EP; the client uses NTLMv2.

### 4.6 Security groups and VPC endpoints

- **`sg-ews-client`** (attached to both ews-* Lambdas):
    - egress TCP 443 → Exchange VIP `/32`
    - egress TCP 443 → `sg-vpce`
    - (Kerberos only) egress TCP/UDP 88 → DC IPs
    - no inbound rules
- **`sg-vpce`:** inbound TCP 443 from `sg-ews-client` and from the tool/agent SGs.
- **Interface endpoints:** `secretsmanager`, `kms`, `sqs`, `states`, `logs`, `monitoring`, `sts`, `bedrock-agentcore` (plus `bedrock-agentcore.gateway` if Gateway is called from the VPC), and `bedrock-runtime` if models are called from VPC code.
- **Gateway endpoints:** `s3`, `dynamodb`.
- Add endpoint policies that restrict access to this account's resources.

---

## 5. Service account and EWS authentication

### 5.1 Authentication options for on-prem EWS

| Mechanism | Works for a daemon against on-prem EWS? | Prerequisites | Verdict |
| --- | --- | --- | --- |
| **NTLMv2 (via Negotiate)** | Yes. This is the most common pattern for service accounts. | Windows auth enabled on the EWS vdir; NTLMv2 allowed by domain policy; client sends CBT if EP is on. | **[Recommended] default** |
| **Kerberos (Negotiate/SPNEGO)** | Yes | Lambda reaches the KDCs on 88; correct SPN (`HTTP/mail.corp.example`, typically on the Exchange **Alternate Service Account** for load-balanced namespaces); `krb5.conf` + keytab or password `kinit`; GSSAPI libraries in the image; clock sync. | Use if NTLM is restricted. **[Validate]** with AD |
| Basic over TLS | Only if Basic is enabled on the EWS vdir | Sends a reusable password on every request; often disabled by policy. | Not recommended |
| OAuth via **Hybrid Modern Authentication** (Entra ID) | Supported for EWS, but only in an Exchange **hybrid** organization with HMA configured. | Requires Entra ID integration, which is not available per the constraints. | Not applicable [Validate] |
| OAuth via **ADFS Modern Auth** (Exchange 2019 CU13+, pure on-prem) | Designed for interactive clients (Outlook for Windows), not daemon access. | ADFS 2019+, CU13 on all front ends, auth policies. | Do not plan on it [Validate] |

**Do not assume Exchange Online behaviour.** App-only OAuth, application access policies, RBAC for Applications and Graph throttling do not apply to on-prem EWS.

Microsoft has deprecated NTLM and is hardening against NTLM relay (Extended Protection on Exchange by default). Ask the AD team for the NTLM roadmap now. If restrictions are planned within the project's life, build and test the Kerberos path in Dev from the start. **[Decision]**

### 5.2 Permission model

| Model | What it grants | Use? |
| --- | --- | --- |
| **Delegate, `FullAccess` on the shared mailbox (AutoMapping off)** | Read every folder; mark, categorize and move items | **[Recommended]** when the integration should tag or move processed items |
| **Folder-level `Reviewer` on Inbox** (plus `FolderVisible` on the mailbox root if required) | Read-only Inbox | **[Recommended alternative]** for strict read-only; processed state is kept only in the ledger [Validate binding in Dev] |
| `ApplicationImpersonation` RBAC role | Act as any mailbox in the management scope | **Not recommended**: over-privileged for one mailbox |
| `Send As` / `Send on Behalf` | Send as the mailbox | Not in phase 1. Add only when an approved reply flow exists |

Example commands, to be run by Exchange administrators (placeholders):

```powershell
# Read/modify delegate access, no Outlook auto-mapping
Add-MailboxPermission -Identity "ap-payments@corp.example" -User "CORP\svc-apmail-prd" `
  -AccessRights FullAccess -InheritanceType All -AutoMapping $false

# OR strict read-only on the Inbox
Add-MailboxFolderPermission -Identity "ap-payments@corp.example:\Inbox" -User "CORP\svc-apmail-prd" -AccessRights Reviewer

# EWS enabled for the service account; check org/mailbox EWS access policies
Set-CASMailbox -Identity "svc-apmail-prd" -EwsEnabled $true
Get-OrganizationConfig | Format-List Ews*
Get-CASMailbox svc-apmail-prd | Format-List Ews*

# Dedicated throttling policy (values agreed with Exchange team)
New-ThrottlingPolicy -Name "svc-apmail" -ThrottlingPolicyScope Regular -EwsMaxConcurrency 10
Set-ThrottlingPolicyAssociation -Identity "svc-apmail-prd" -ThrottlingPolicy "svc-apmail"

# Inventory to send back to the integration team
Get-ExchangeServer | Format-List Name,Edition,AdminDisplayVersion
Get-WebServicesVirtualDirectory -ADPropertiesOnly | Format-List Server,InternalUrl,*Authentication*,ExtendedProtection*
Get-Recipient "ap-payments@corp.example" | Format-List RecipientTypeDetails,PrimarySmtpAddress
```

If the organization enforces an EWS allow-list (`EwsApplicationAccessPolicy = EnforceAllowList`), add the client's User-Agent string to `EwsAllowList`. Set an explicit, stable User-Agent in the client (e.g. `ap-payments-ingest/1.0`).

Whether the service account needs a mailbox of its own depends on the environment. Many organizations give EWS service accounts a small mailbox to avoid edge cases. **[Validate]**

### 5.3 Throttling

EWS throttling on-prem is controlled by throttling policies such as `EWSMaxConcurrency`. Defaults vary by Exchange version and can be changed by administrators, so the client must back off whenever it is throttled. The design keeps load low by design:

- one `ews-sync` at a time (reserved concurrency 1 + DynamoDB lease)
- `ews-fetch` maximum concurrency 2–4
- `MaxChangesReturned` 100 (the protocol maximum is 512)
- honour `BackOffMilliseconds` on `ErrorServerBusy`

### 5.4 Credential storage, retrieval and rotation

- **Secret:** `ews/svc-apmail-<env>` in Secrets Manager, JSON `{"domain":"CORP","username":"svc-apmail-prd","password":"…","ews_url":"https://mail.corp.example/EWS/Exchange.asmx"}`. Encrypt it with a CMK, `alias/ews-secret-<env>`.
- **Access:** a resource policy plus the key policy allow `secretsmanager:GetSecretValue` / `kms:Decrypt` only to the `ews-sync` and `ews-fetch` execution roles (deny all else, including CI roles). Alarm on any other principal reading it (CloudTrail → EventBridge rule).
- **Retrieval:** at cold start through the VPC endpoint, cached in memory for at most 5 minutes (e.g. the AWS Parameters and Secrets Lambda Extension or an in-process cache). If EWS returns 401, re-read the secret once to pick up a rotation; on a second 401, stop (F1).
- **Rotation [Decision]:** the AD password is owned by the client's identity team.
    - **Preferred:** their PAM tool (e.g. CyberArk) rotates the AD password and writes the new value to Secrets Manager (through an integration or a controlled runbook) within the same change window.
    - **Alternative:** a Secrets Manager rotation Lambda that changes the AD password over LDAPS (636) to a DC. This requires another firewall rule and directory write rights, so only use it with AD team approval.
    - Either way, alarm 14 days before password expiry if the account is not set to never-expire.
- **Audit:**
    - AWS: CloudTrail (Secrets Manager, KMS, S3 data events, DynamoDB, Step Functions), CloudWatch Logs.
    - On-prem: IIS logs for `/EWS/Exchange.asmx` (user, User-Agent, status), AD security events for the service account (4624 logon, 4625 failure, 4740 lockout, 4776 NTLM validation), and Exchange mailbox audit logging on the shared mailbox.
    - Agree retention with the security team.

### 5.5 Step by step: establishing the session and reading the mailbox (NTLMv2)

1. **Invocation ①.** The scheduler invokes `ews-sync`.
2. **Lease and checkpoint ②.** A conditional `UpdateItem` acquires a 10-minute lease and returns the stored SyncState.
3. **Secret ③.** `GetSecretValue` runs through the VPC endpoint, and KMS decrypts it.
4. **DNS ④.** `mail.corp.example` resolves through the Resolver forwarding rule to the VIP address.
5. **TLS ⑤.** TCP 443 to the VIP, then a TLS 1.2+ handshake. The server certificate is validated against the enterprise CA bundle and the hostname. With L4/bridged load balancing, the certificate the client sees is the Exchange certificate, which is required for the CBT to match.
6. **NTLM handshake ⑥.**
    - `POST /EWS/Exchange.asmx` without credentials returns `401` with `WWW-Authenticate: Negotiate, NTLM`.
    - The client sends `NTLM <Type 1>`, and the server returns `401` with `NTLM <Type 2 challenge>`.
    - The client sends `NTLM <Type 3>`: the NTLMv2 response, with channel bindings derived from the server certificate. The SOAP body travels with it.
    - Exchange validates the response with a DC (Netlogon pass-through) and processes the request on that authenticated connection. The HTTP session keeps connection affinity; reuse the connection (keep-alive) for subsequent calls.
7. **Sync ⑦.** `SyncFolderItems` runs on `DistinguishedFolderId="inbox"` with `<Mailbox>` set to the shared mailbox (delegate access). Headers include `X-AnchorMailbox: ap-payments@corp.example` (exchangelib sets this automatically) and `RequestServerVersion`.
8. **Record ⑧ and commit ⑨.** The client records changes, enqueues fetch messages, commits the new SyncState and loops until `IncludesLastItemInRange=true`.
9. **Fetch ⑩–⑪.** `ews-fetch` repeats steps 3–6 (connection reuse within a warm environment) and calls `GetItem` with `IncludeMimeContent=true`.

**Kerberos variant.** Steps 1–5 are the same. Then:

- `kinit` with a keytab from Secrets Manager writes to `/tmp/krb5cc`.
- The client sends `Authorization: Negotiate <SPNEGO token for HTTP/mail.corp.example>` with CBT.
- Exchange validates the ticket locally (ASA credential) without contacting the DC per request.

Requirements: TCP/UDP 88 to the KDCs, DNS SRV records `_kerberos._tcp.corp.example`, and time skew under 5 minutes (Lambda uses Amazon Time Sync).

### 5.6 What to confirm with the Exchange and AD teams

1. Exchange build per server, and whether all front ends are behind one namespace and load balancer.
2. EWS vdir: `WindowsAuthentication`, `BasicAuthentication`, `OAuthAuthentication`, `ExtendedProtectionTokenChecking`, and the internal/external URLs.
3. Load-balancer mode (L4 / SSL bridging / offload) and the certificate on the load balancer vs IIS.
4. Recipient type of the group address (`RecipientTypeDetails`), and its approximate size and item count.
5. Permission model approved (FullAccess vs Reviewer); service-account mailbox requirement.
6. Organization- and mailbox-level EWS access policies, and the required User-Agent.
7. Throttling policy for the service account.
8. NTLM policy (NTLMv2 allowed? auditing or blocking planned?), Kerberos SPN/ASA configuration, password policy (expiry, lockout threshold), PAM ownership.
9. Internal CA chain (PEM) and DNS server IPs for the Resolver rule.
10. Maintenance windows and change process for Exchange and firewall.

---

## 6. EWS API interactions and request patterns

All calls are `POST https://mail.corp.example/EWS/Exchange.asmx` with `Content-Type: text/xml; charset=utf-8`. Use a version header supported by the target server (e.g. `Exchange2013_SP1`, which works across 2016/2019/SE). **[Validate]**

### 6.1 SyncFolderItems: discover new items ⑦

```xml
<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/"
               xmlns:t="http://schemas.microsoft.com/exchange/services/2006/types"
               xmlns:m="http://schemas.microsoft.com/exchange/services/2006/messages">
  <soap:Header>
    <t:RequestServerVersion Version="Exchange2013_SP1"/>
  </soap:Header>
  <soap:Body>
    <m:SyncFolderItems>
      <m:ItemShape><t:BaseShape>IdOnly</t:BaseShape></m:ItemShape>
      <m:SyncFolderId>
        <t:DistinguishedFolderId Id="inbox">
          <t:Mailbox><t:EmailAddress>ap-payments@corp.example</t:EmailAddress></t:Mailbox>
        </t:DistinguishedFolderId>
      </m:SyncFolderId>
      <m:SyncState>H4sIAAAA…</m:SyncState>          <!-- omit on the very first call -->
      <m:MaxChangesReturned>100</m:MaxChangesReturned> <!-- 1–512 -->
      <m:SyncScope>NormalItems</m:SyncScope>
    </m:SyncFolderItems>
  </soap:Body>
</soap:Envelope>
```

The response contains `Changes` (`Create`, `Update`, `Delete`, `ReadFlagChange`), a new `SyncState` and `IncludesLastItemInRange`. Only `Create` items are ingested. The others are ignored, but you can log them for audit. `SyncFolderItems` cannot return body or attachments; use `GetItem` for those.

**First run.** With no SyncState, the response enumerates the whole Inbox. Choose one approach **[Decision]**:

- (a) seed only: run once with the enqueue step disabled, then commit SyncState
- (b) ingest only items newer than a `DateTimeReceived` floor (properties fetched with `AdditionalProperties` or in `GetItem`)
- (c) a controlled backfill

### 6.2 GetItem: full MIME and key properties ⑪

```xml
<m:GetItem>
  <m:ItemShape>
    <t:BaseShape>IdOnly</t:BaseShape>
    <t:IncludeMimeContent>true</t:IncludeMimeContent>
    <t:AdditionalProperties>
      <t:FieldURI FieldURI="message:InternetMessageId"/>
      <t:FieldURI FieldURI="item:DateTimeReceived"/>
      <t:FieldURI FieldURI="item:Size"/>
      <t:FieldURI FieldURI="item:HasAttachments"/>
      <t:FieldURI FieldURI="item:ItemClass"/>
      <t:FieldURI FieldURI="item:Subject"/>
      <t:FieldURI FieldURI="message:From"/>
      <t:FieldURI FieldURI="item:ConversationId"/>
    </t:AdditionalProperties>
  </m:ItemShape>
  <m:ItemIds><t:ItemId Id="AAMk…" ChangeKey="CQAA…"/></m:ItemIds>
</m:GetItem>
```

`MimeContent` is base64 RFC 5322 including all attachments. Parse it with Python's `email` package (`policy=email.policy.default`). Handle these cases:

- nested `message/rfc822` (forwarded emails)
- inline images
- `winmail.dat` (TNEF), e.g. with `tnefparse` **[Validate]**
- calendar and NDR item classes: process only `IPM.Note*`; mark others `SKIPPED_NON_MAIL`

### 6.3 Optional mailbox-side marking (FullAccess only) ⑬

- `UpdateItem` with `SetItemField item:Categories` = `AI-Ingested` (with `ConflictResolution="AutoResolve"`), **or**
- `MoveItem` to a pre-created `AI-Processed` folder. A move changes the `ItemId`, which is fine after fetch because the ledger dedupes by `InternetMessageId`.

The ledger stays the system of record; mailbox marking is for human visibility only.

### 6.4 CreateItem: controlled replay into Dev/UAT test mailboxes

`CreateItem` with `MessageDisposition="SaveOnly"`, `SavedItemFolderId` = the test mailbox Inbox, and `<t:MimeContent>` = an approved, sanitized `.eml`. This lets the test harness load realistic messages without SMTP. It needs FullAccess on the **test** mailbox only.

### 6.5 Error handling

| EWS / HTTP result | Meaning | Handling | Code |
| --- | --- | --- | --- |
| 401 after Type 3 / Negotiate | Bad or expired password, locked account, EP/CBT mismatch, disabled EWS | Re-read the secret once; if still 401, emit `EwsAuthFailure`, alarm, **stop** (no retries, to protect the AD lockout counter) | F1 |
| 503, `ErrorServerBusy`, `ErrorExceededConnectionCount`, `ErrorTimeoutExpired` | Throttled or overloaded | Sleep `BackOffMilliseconds` (or exponential with jitter); end the run early; SyncState unchanged | F2 |
| `ErrorInvalidSyncState` | Sync state no longer valid | Clear SyncState; resync with a received-date floor; ledger conditional writes drop duplicates | F3 |
| Timeout, 5xx on fetch, S3/DynamoDB error | Transient | SQS partial-batch failure, then redelivery with backoff, then DLQ after 5 receives; redrive after the fix | F4 |
| `ErrorItemNotFound` | Item moved or deleted before fetch | Ledger `SKIPPED_DELETED`; acknowledge | F5 |
| `ErrorNonExistentMailbox`, `ErrorAccessDenied`, `ErrorImpersonateUserDenied` | Wrong address or permission | Alarm; stop; fix configuration | — |
| Response larger than the EWS request/response size limits | Very large message (EWS default request limit about 64 MB, base64-inflated) | Fetch attachments individually with `GetAttachment`, or mark `TOO_LARGE` for manual handling | — |

---

## 7. Scheduled ingestion design

### 7.1 Compute choice

**Lambda is recommended** [Recommended]:

- polls are short (seconds) and bursty
- Lambda supports VPC, container images, up to 10 GB memory and ephemeral storage, and a 15-minute timeout
- reserved concurrency gives single-poller semantics
- SQS event source mappings give per-message retry and maximum concurrency

**Use ECS on Fargate instead** if:

- Kerberos needs long-lived ticket caches or extra system libraries that you'd rather not ship in a Lambda image
- individual messages routinely exceed what one invocation can handle in 15 minutes
- the client mandates containers on long-running hosts

The code is the same image either way.

Streaming or pull notifications (EWS subscriptions) are **not** used. They need long-lived connections and server affinity, and offer no material benefit at 1–5-minute latency. Polling with `SyncFolderItems` is simpler and self-healing.

### 7.2 Schedule and sizing (initial values to tune in UAT)

| Setting | Dev | UAT | Prod |
| --- | --- | --- | --- |
| EventBridge Scheduler | `rate(5 minutes)` or manual | `rate(2 minutes)` | `rate(2 minutes)`; flexible time window OFF; retry policy 0, max event age 60 s |
| `ews-sync` | 1024 MB, timeout 5 min, reserved concurrency 1 | same | same |
| `ews-fetch` | 2048 MB, timeout 5 min | 2048–3072 MB | 3072 MB; raise if large attachments are common |
| SQS `ews-fetch-queue` | visibility timeout ≥ 6 × fetch timeout (30 min); maxReceiveCount 5 → DLQ; SSE-KMS | same | same |
| Event source mapping | batch 1, max concurrency 2 | batch 1–5, max concurrency 2–4 | batch 1–5, max concurrency 2–4 (≤ throttling policy); `ReportBatchItemFailures` on |
| `MaxChangesReturned` | 50 | 100 | 100 |
| Time budget per sync run | stop paging at 4 min and commit | same | same |

Expected end-to-end latency from arrival to agent start is about 2–4 minutes in prod.

### 7.3 Watermark and lease (`ews_checkpoint`)

```
pk = "MAILBOX#ap-payments@corp.example#inbox"
sync_state        (string, opaque, from Exchange)
lease_owner       (Lambda request id)
lease_expires_at  (epoch s)
last_success_at   (epoch s)
floor_received_at (ISO-8601, used only for full resync)
```

- **Acquire the lease** with `ConditionExpression: attribute_not_exists(lease_expires_at) OR lease_expires_at < :now`.
- **Commit:** `SET sync_state = :new, last_success_at = :now` happens **only after** every ledger `PutItem` and every `SendMessageBatch` entry for that page has succeeded. If the run dies before commit, the next run re-reads the same changes, and ledger conditional writes make that harmless (at-least-once discovery, exactly-once ledger rows).

### 7.4 Idempotency ledger (`ews_ledger`)

```
pk  = sha256(mailbox + "|" + InternetMessageId)  -- set at fetch; at discovery the provisional key is sha256(mailbox|ItemId)
GSI1: internet_message_id
attrs: item_id, change_key, received_at, size, has_attachments, status, s3_prefix, sha256_mime,
       execution_arn, attempts, last_error, env, created_at, updated_at, ttl (optional, per retention)
status: DISCOVERED → STORED → HANDED_OFF → (COMPLETED | NEEDS_REVIEW | FAILED_AGENT)
        side states: DUPLICATE, SKIPPED_DELETED, SKIPPED_NON_MAIL, TOO_LARGE, FAILED_FETCH (after DLQ)
```

Rules:

- Every status change is a conditional update on the expected previous status.
- A message whose `InternetMessageId` is already `STORED` or later becomes `DUPLICATE`.
- If `InternetMessageId` is missing, fall back to `sha256(MIME)`.
- The workflow updates the terminal states.

### 7.5 Storage layout (S3 `email-raw-<env>`)

```
raw/{yyyy}/{mm}/{dd}/{ledgerId}/message.eml          -- exact MimeContent bytes
raw/.../{ledgerId}/parts/body.txt | body.html
raw/.../{ledgerId}/attachments/{n}-{safe_filename}
raw/.../{ledgerId}/manifest.json
```

```json
{
  "ledgerId": "9f2c…", "env": "prd", "mailbox": "ap-payments@corp.example",
  "internetMessageId": "<…@supplier.com>", "ewsItemId": "AAMk…", "conversationId": "AAQk…",
  "receivedAt": "2026-11-12T14:03:11Z", "from": "billing@supplier.com", "subject": "Remittance query PO 4500123456",
  "sizeBytes": 482113, "sha256Mime": "…",
  "attachments": [{"name": "INV-88812.pdf", "contentType": "application/pdf", "bytes": 210331, "sha256": "…", "key": "…"}],
  "classification": null
}
```

Bucket settings:

- versioning on, SSE-KMS with a bucket key, Block Public Access, TLS-only bucket policy
- optionally S3 Object Lock (governance) for audit retention **[Decision]**
- lifecycle per the retention policy
- GuardDuty Malware Protection for S3 with object tagging; tag-based access control so nothing reads objects not yet tagged `NO_THREATS_FOUND`

Do not put export-controlled content in bucket names, object keys or tags. Keys contain only ids and dates.

### 7.6 Attachment and size limits

- Exchange transport limits already cap what arrives (organization, connector and mailbox limits **[Validate]**). EWS has its own request size limits; the default EWS request limit is about 64 MB, which yields about 48 MB of real content because of base64 overhead.
- Pipeline guardrails **[Recommended]**:
    - store everything that arrives
    - extract text only for files ≤ 25 MB and ≤ 200 pages
    - mark larger files `TOO_LARGE` for manual review
    - never pass binaries to agents: pass S3 references and extracted text through tools
- Step Functions payloads stay small (≤ 256 KB): pass `ledgerId` and the manifest S3 URI, never content.

### 7.7 Hand-off to the agentic workflow ⑭

`ews-fetch` calls `states:StartExecution` with:

- `name = ledgerId` (unique per state machine; a duplicate start returns `ExecutionAlreadyExists`, which is treated as success)
- `input = {"ledgerId":…, "manifestUri":"s3://…/manifest.json", "env":"prd", "traceId":…}`

It then sets the ledger to `HANDED_OFF` with the `executionArn`, and returns success so SQS deletes the message.

**Which component calls AgentCore?** The Step Functions workflow, not the ingestion Lambdas and not Exchange. Use either:

- the Step Functions AWS SDK integration for Amazon Bedrock AgentCore (`InvokeAgentRuntime`), or
- a small `agent-invoker` Lambda, when you need custom handling of streamed responses.

**[Validate]** the integration features available in the chosen Region/partition.

### 7.8 Failure, retry and reprocessing summary

| Failure | Where handled | Retry | Terminal state / action |
| --- | --- | --- | --- |
| Lease held | `ews-sync` | Next schedule | Exit quietly |
| Auth failure (F1) | `ews-sync` / `ews-fetch` | None | Alarm; on-call fixes credential/policy; ingestion resumes from the committed SyncState |
| Throttling (F2) | EWS client | Backoff within run; next schedule | Metric `EwsThrottled` |
| Invalid sync state (F3) | `ews-sync` | Full resync with floor | Ledger dedupes |
| Fetch / S3 / DynamoDB error (F4) | SQS + ESM | Up to 5 receives with backoff | DLQ, `FAILED_FETCH`; redrive via SQS redrive after the fix |
| Item gone (F5) | `ews-fetch` | — | `SKIPPED_DELETED` |
| Malware found | Step Functions | — | Quarantine prefix, security alert, `NEEDS_REVIEW` |
| Agent/task error (F6) | Step Functions Retry/Catch | 3 × exponential | `FAILED_AGENT`, manual queue |
| Manual reprocess | Ops runbook | — | `StartExecution(name = ledgerId#r2)` from the stored manifest; no re-fetch from Exchange needed |

### 7.9 Reference implementation (Python, exchangelib 5.x)

```python
# ews_client.py — shared by ews-sync and ews-fetch (one container image)
import json, os, boto3
from exchangelib import Account, Configuration, Credentials, DELEGATE, NTLM, FaultTolerance, Build, Version
from exchangelib.protocol import BaseProtocol

BaseProtocol.USERAGENT = "ap-payments-ingest/1.0"          # stable UA for EWS allow-lists and IIS log filtering
# REQUESTS_CA_BUNDLE=/opt/certs/corp-ca.pem is set on the function (enterprise root + intermediate)

_sm = boto3.client("secretsmanager")
_cache = {}

def account():
    if "acct" not in _cache:
        s = json.loads(_sm.get_secret_value(SecretId=os.environ["SECRET_ARN"])["SecretString"])
        cfg = Configuration(
            service_endpoint=s["ews_url"],                       # https://mail.corp.example/EWS/Exchange.asmx (FQDN on cert)
            credentials=Credentials(f'{s["domain"]}\\{s["username"]}', s["password"]),
            auth_type=NTLM,                                      # requests_ntlm, sends channel binding (CBT) over HTTPS
            retry_policy=FaultTolerance(max_wait=60),            # honours EWS back-off on ErrorServerBusy, bounded
            version=Version(build=Build(15, 2)),                 # 15.2 = Exchange 2019 / SE (15.1 = 2016); set per [Validate]
        )
        # Delegate access to the shared mailbox; exchangelib adds X-AnchorMailbox = this address
        _cache["acct"] = Account(os.environ["MAILBOX_SMTP"], config=cfg, autodiscover=False, access_type=DELEGATE)
    return _cache["acct"]
```

```python
# ews_sync.py (handler) — steps ②–⑨. Calls the SyncFolderItems service one page at a time, so each page is
# recorded and its SyncState committed before the next request (Folder.sync_items() pages internally and only
# exposes the final state, which defeats per-page checkpoints and the time budget).
import time, os, boto3
from exchangelib.items import ID_ONLY
from exchangelib.services import SyncFolderItems
from ews_client import account

ddb = boto3.resource("dynamodb")
ckpt = ddb.Table(os.environ["CKPT_TABLE"])
PK = f'MAILBOX#{os.environ["MAILBOX_SMTP"]}#inbox'

def handler(event, ctx):
    state = acquire_lease(ckpt, PK, ctx.aws_request_id)          # conditional UpdateItem; None → another run active
    if state is None:
        return {"skipped": "lease held"}
    acct, deadline = account(), time.time() + 240
    sync_state = state.get("sync_state")                          # None on the very first run → see §6.1 first-run options
    try:
        while time.time() < deadline:
            svc = SyncFolderItems(account=acct)
            new_ids = [item for change, item in svc.call(folder=acct.inbox, shape=ID_ONLY, additional_fields=set(),
                                                         sync_state=sync_state, ignore=None,
                                                         max_changes_returned=100, sync_scope="NormalItems")
                       if change == "create"]                    # ignore update / delete / read_flag_change
            record_and_enqueue(new_ids)                           # ledger PutItem (attribute_not_exists) + SendMessageBatch
            commit_sync_state(ckpt, PK, svc.sync_state)           # only after both succeeded
            if svc.includes_last_item_in_range or svc.sync_state == sync_state:
                break
            sync_state = svc.sync_state
    finally:
        release_lease(ckpt, PK, ctx.aws_request_id)
```

```python
# ews_fetch.py (handler) — steps ⑩–⑭, SQS partial batch responses
def handler(event, ctx):
    failures = []
    for rec in event["Records"]:
        msg = json.loads(rec["body"])
        try:
            item = next(iter(account().fetch(ids=[(msg["itemId"], msg["changeKey"])],
                       only_fields=["mime_content", "message_id", "datetime_received", "size",
                                    "has_attachments", "item_class", "subject", "sender", "conversation_id"])))
            if isinstance(item, Exception):                      # exchangelib returns errors inline, e.g. ErrorItemNotFound
                handle_item_error(msg, item); continue
            if is_duplicate(item.message_id):
                mark(msg, "DUPLICATE"); continue
            manifest_uri = store_mime_and_parts(item)             # parse with email.policy.default, PutObject SSE-KMS
            mark(msg, "STORED", manifest_uri=manifest_uri)
            start_workflow(ledger_id(item), manifest_uri)         # StartExecution(name=ledgerId); ExecutionAlreadyExists = ok
            mark(msg, "HANDED_OFF")
        except Exception as e:                                    # transient → retry via SQS; after 5 → DLQ
            log_error(msg, e)
            failures.append({"itemIdentifier": rec["messageId"]})
    return {"batchItemFailures": failures}
```

Packaging and build:

- Base on the Lambda Python container image and add `exchangelib`, `requests-ntlm` (pyspnego), the CA bundle, and (Kerberos only) `krb5` libraries, `requests-gssapi` and `krb5.conf`.
- Use Powertools for AWS Lambda for structured logs, metrics and traces.
- Scrub `Authorization` headers and bodies from any debug logging.

---

## 8. Agentic processing integration

### 8.1 Workflow (Step Functions Standard, `case-workflow`)

1. **ScanGate ⑮.** Wait for the GuardDuty tag `GuardDutyMalwareScanStatus` on `message.eml` and attachments: an EventBridge rule plus a task token, or a short poll. `THREATS_FOUND` leads to quarantine and an alert.
2. **Preprocess ⑮.** A Lambda extracts attachment text (PDF/Office parsers), masks PII as agreed, and applies the Bedrock Guardrails policy. Output goes to `parts/` and a case record.
3. **Classify ⑯.** `InvokeAgentRuntime` on the classification agent, with `runtimeSessionId = "case-" + ledgerId` (≥ 33 characters). Payload: `{ledgerId, manifestUri}`. The agent reads content through Gateway tools. Output: `{paymentRelated, itarSuspected, confidence, rationale}`.
4. **Route.**
    - `itarSuspected` or confidence below threshold → **ITAR/human-review path**: copy to the ITAR-segregated store, status `NEEDS_REVIEW`.
    - Not payment-related → `COMPLETED_NON_PAYMENT`.
    - Payment-related and cleared → continue.
5. **Extract ⑰.** The extraction agent returns attributes validated against a JSON schema (PO, invoice numbers, supplier ids and names, amounts and currency, dates, requested action, attachment references).
6. **Persist.** Aurora tables (`case`, `email_thread`, `extracted_context`, `agent_run`, `audit`) and ledger status `COMPLETED`.
7. **Report ⑱.** The reporting agent runs on a schedule (e.g. hourly or daily, through a separate scheduler) or on demand. It reads aggregates through `query_cases` (read-only) and writes the consolidated report to S3 and Aurora.

Each agent task has `Retry` (3 attempts, exponential backoff, jitter) and `Catch` → `FAILED_AGENT`, then a manual queue (F6).

### 8.2 Agent and tool boundaries

- Agents run on AgentCore Runtime in VPC mode with private endpoints, with no route to on-prem and no IAM access to `ews/*` secrets.
- Gateway tools are Lambda targets with **read-only** IAM: `s3:GetObject` on `raw/*`, a read-only Aurora role, and scoping by `ledgerId`.
- Tool responses return extracted text and metadata, not raw binaries. The prompt-injection risk from email bodies is mitigated by Guardrails, by tool scoping, and because agents have no write tools in the ingestion path.

### 8.3 ITAR and data segregation **[Decision: export control]**

- **The ingestion account must be inside the approved ITAR boundary.** Raw mail is copied into S3 before the classifier decides whether it is ITAR. AWS GovCloud (US) supports ITAR compliance, and AgentCore is generally available in AWS GovCloud (US-West). Confirm with export control:
    - the partition and Region
    - that every service used (AgentCore features, Bedrock models, Guardrails, GuardDuty Malware Protection for S3, Step Functions integrations) is available and authorized there
    - US-person access controls for all humans and roles
- **Do not assume a commercial Region is authorized for ITAR-controlled data.** If only a commercial Region is approved, an on-prem pre-screen is needed before mail leaves the data center. That is a different design (Option B with on-prem classification) and needs its own approval.
- After classification, ITAR-flagged cases are copied to `s3://email-itar-<env>` under `kms-itar-<env>`, whose key policy allows only roles approved for US persons. Processing is restricted to approved models and tools, or to human review.
- Lower environments use **synthetic ITAR markers only**. Real export-controlled text never goes to Dev or UAT.

### 8.4 Traceability

- Correlation id = `ledgerId`, carried in SQS message attributes, the Step Functions execution name, the AgentCore `runtimeSessionId`, Aurora rows, structured logs and X-Ray/ADOT trace annotations (W3C `traceparent` passed to `InvokeAgentRuntime`).
- A single query from the ledger row reaches: the EWS item id → the S3 objects → the execution history → the agent runs → the final case.

---

## 9. AWS component configuration and IAM

| Resource | Name (per env) | Key configuration |
| --- | --- | --- |
| Lambda | `ews-sync-<env>` | VPC private subnets (2 AZs), `sg-ews-client`, container image, 1024 MB, 300 s, reserved concurrency 1, async retries 0, env: `MAILBOX_SMTP`, `SECRET_ARN`, `CKPT_TABLE`, `LEDGER_TABLE`, `FETCH_QUEUE_URL`, `REQUESTS_CA_BUNDLE` |
| Lambda | `ews-fetch-<env>` | Same network; 3072 MB, 300 s; SQS ESM batch 1–5, max concurrency 2–4, `ReportBatchItemFailures` |
| Scheduler | `ews-sync-<env>` | `rate(2 minutes)` (prod), retry 0, max event age 60 s, DLQ for the scheduler target |
| Secrets Manager | `ews/svc-apmail-<env>` | CMK `alias/ews-secret-<env>`, resource policy limited to the two roles |
| DynamoDB | `ews_checkpoint_<env>`, `ews_ledger_<env>` | On-demand, PITR on, CMK, GSI on `internet_message_id`, TTL per retention |
| SQS | `ews-fetch-<env>`, `ews-fetch-dlq-<env>` | SSE-KMS, visibility 1800 s, maxReceiveCount 5, DLQ retention 14 days |
| S3 | `email-raw-<env>`, `email-itar-<env>`, `email-reports-<env>` | Versioning, SSE-KMS, BPA, TLS-only, GuardDuty Malware Protection on `email-raw` |
| Step Functions | `case-workflow-<env>` | Standard; logging ALL to CloudWatch (redact payloads), X-Ray on |
| AgentCore | runtimes `classify`, `extract`, `report`; Gateway `ap-tools` | VPC mode, endpoint per env, approved model list, Guardrails |
| Networking | VPC, TGW attachment, endpoints, Resolver rule association | §4 |

Example IAM statement for the `ews-sync` role (others follow the same least-privilege pattern):

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {"Effect": "Allow", "Action": "secretsmanager:GetSecretValue",
     "Resource": "arn:aws:secretsmanager:REGION:ACCT:secret:ews/svc-apmail-prd-*"},
    {"Effect": "Allow", "Action": "kms:Decrypt", "Resource": "arn:aws:kms:REGION:ACCT:key/EWS-SECRET-KEY-ID",
     "Condition": {"StringEquals": {"kms:ViaService": "secretsmanager.REGION.amazonaws.com"}}},
    {"Effect": "Allow", "Action": ["dynamodb:GetItem", "dynamodb:UpdateItem", "dynamodb:PutItem"],
     "Resource": ["arn:aws:dynamodb:REGION:ACCT:table/ews_checkpoint_prd", "arn:aws:dynamodb:REGION:ACCT:table/ews_ledger_prd"]},
    {"Effect": "Allow", "Action": ["sqs:SendMessage"], "Resource": "arn:aws:sqs:REGION:ACCT:ews-fetch-prd"},
    {"Effect": "Allow", "Action": ["ec2:CreateNetworkInterface", "ec2:DescribeNetworkInterfaces", "ec2:DeleteNetworkInterface"],
     "Resource": "*"}
  ]
}
```

In a GovCloud deployment, ARNs use the `arn:aws-us-gov:` partition.

---

## 10. Development, UAT and Production integration

### 10.1 What each environment needs (all must be provisioned unless marked)

| Item | Dev | UAT | Prod | Owner |
| --- | --- | --- | --- | --- |
| AWS account | to provision | to provision | to provision / existing | Cloud platform |
| VPC + non-overlapping CIDR + TGW attachment + route table | to provision | to provision | to provision | Network (AWS + on-prem) |
| Firewall rule set (VPC CIDR → VIP:443, Resolver → DNS:53) | to provision | to provision | to provision | Network security |
| Shared mailbox | `ap-payments-dev@` **to provision** | `ap-payments-uat@` **to provision** | production mailbox **[Confirmed]** | Exchange |
| AD service account | `svc-apmail-dev` **to provision** | `svc-apmail-uat` **to provision** | `svc-apmail-prd` **[Confirmed]** | AD / identity |
| Mailbox permission for that account only | to provision | to provision | to provision [Validate model] | Exchange |
| Secret `ews/svc-apmail-<env>` (value entered by identity admins) | to provision | to provision | to provision | Identity + cloud |
| Test senders (internal test accounts allowed to mail the test mailbox) | to provision | to provision | — | Exchange |
| AgentCore runtimes and endpoints, Step Functions, data stores, alarms | IaC | IaC | IaC | Delivery team |

A dev service account must never have rights on the prod mailbox, and vice versa. Each secret is readable only in its own account.

### 10.2 When only a production Exchange organization exists [Assumption A4]

- Create the Dev and UAT **shared mailboxes and service accounts on the production Exchange organization**. Isolation comes from separate mailboxes, accounts, permissions, firewall rules and AWS accounts. This is common and acceptable if security agrees. **[Decision]**
- Load on production Exchange from Dev/UAT is negligible (one poller, low concurrency, separate throttling association).
- If a lab or non-prod Exchange organization exists or is built later, point Dev/UAT there with configuration only (EWS URL, mailbox, secret).

### 10.3 Test data strategy

1. **Golden set (synthetic).** 150–300 labelled emails covering:
    - payment queries with PO/invoice references and PDF/XLSX attachments
    - non-payment procurement mail
    - replies and forward chains
    - nested `.eml`, inline images and TNEF
    - out-of-office replies and NDRs
    - large attachments
    - malformed MIME
    - non-English mail
    - **synthetic ITAR markers**

    Store it in a repo with expected labels and extracted fields.
2. **Delivery into test mailboxes.** Either SMTP from test senders (exercises transport and is closest to reality), or EWS `CreateItem` with `MimeContent` (deterministic replay, preserves headers). Both run from a test-harness job in the Dev/UAT account.
3. **Approved replay (UAT only).** A sample of production emails, sanitized (PII masked, non-ITAR confirmed by export control) and approved in writing, replayed with `CreateItem`. **[Decision]**
4. **Unit and agent development.** `FileMailSource` reads `.eml` fixtures from S3 and implements the same interface as `EwsMailSource`, so agent and prompt work proceeds before network access exists.

### 10.4 Configuration strategy

- One container image and one IaC codebase.
- Per-environment values live in SSM Parameter Store (`/ews/<env>/mailbox_smtp`, `/ews/<env>/ews_url`, `/ews/<env>/schedule`, `/ews/<env>/max_concurrency`) and in the per-account secret ARN.
- The pipeline never handles password values: IaC creates the secret empty, and identity admins populate it through a controlled runbook.

### 10.5 Promotion

| Gate | Dev → UAT | UAT → Prod |
| --- | --- | --- |
| Connectivity smoke test (DNS, TLS chain + hostname, auth, `GetFolder` on Inbox) | ✔ | ✔ (run in Prod before enabling the schedule) |
| Idempotency: rerun the same window, zero duplicate ledger rows or executions | ✔ | ✔ |
| Failure drills: wrong password (F1, verify stop and no lockout), throttle simulation (F2), invalid SyncState (F3), DLQ + redrive (F4) | ✔ | ✔ |
| Golden-set thresholds (classification accuracy, ITAR recall = 100% on synthetic markers, extraction field accuracy) | ✔ | ✔ on UAT replay set |
| Security review (IAM least privilege, SG/NACL, endpoint policies, log redaction, secret access alarms) | — | ✔ |
| Export-control sign-off for the target account/Region | — | ✔ **before any live mail** |
| Operational readiness (dashboards, alarms to on-call, runbooks, DLQ redrive procedure) | — | ✔ |
| Prod **shadow mode** (read and classify only, no outbound actions) for an agreed period, compared with analyst handling | — | ✔ then go-live approval |

---

## 11. Security and compliance controls (checklist)

- [ ] Private subnets only. No IGW/NAT in ingestion VPCs. VPC endpoints with endpoint policies.
- [ ] `sg-ews-client` egress limited to the VIP `/32`:443 and endpoints. On-prem firewall limited to env CIDRs → VIP:443.
- [ ] TLS 1.2+, full chain and hostname validation, enterprise CA pinned in the image. No `verify=False`.
- [ ] NTLMv2 with CBT (or Kerberos). No Basic. EP compatibility verified on the real load balancer.
- [ ] Delegate permission on one mailbox per environment. No impersonation. No Send-As in phase 1.
- [ ] Secret: CMK, resource policy limited to two roles, CloudTrail alarm on other access, rotation runbook with PAM, expiry alarm.
- [ ] Logs never include passwords, `Authorization` headers, or message bodies. Payload logging in Step Functions is redacted.
- [ ] S3: SSE-KMS, versioning, BPA, TLS-only, GuardDuty Malware Protection, tag-based read gate, retention/Object Lock per policy.
- [ ] Agents: read-only tools, no route to on-prem, Guardrails, approved model list, prompt-injection test cases in the golden set.
- [ ] ITAR: approved boundary for the ingestion account, separate ITAR bucket/key, US-person access controls, no ITAR data in resource names, keys or tags.
- [ ] Audit: CloudTrail (management + S3 data events) to a central log archive; Exchange mailbox auditing; AD logon/lockout events retained.

---

## 12. Monitoring and operations

| Metric / signal | Source | Alarm |
| --- | --- | --- |
| `EwsAuthFailure` | `ews-*` EMF metric | ≥ 1 → page on-call (P2); polling stops automatically |
| `LastSuccessfulSync` age | checkpoint table / metric | > 15 min in business hours |
| `EwsThrottled` | metric | > 10 per hour → tell the Exchange team; review the throttling policy |
| DLQ depth | SQS | > 0 |
| Ledger items `DISCOVERED` / `STORED` older than 30 min | scheduled query Lambda | > 0 |
| Step Functions `ExecutionsFailed` | CloudWatch | > 0 |
| Agent errors and latency | AgentCore Observability | error rate > 5% over 15 min |
| Secret accessed by an unexpected principal | CloudTrail → EventBridge | any |
| Malware found | GuardDuty / EventBridge | any → security |
| Daily volume anomaly | metric | outside expected band (detects a silently broken mail flow) |

Runbooks to write:

- credential rotation and lockout recovery
- certificate renewal on Exchange (CA bundle unchanged unless the CA changes)
- DLQ inspection and redrive
- full resync
- manual reprocess of a case
- pause/resume ingestion (disable the schedule)
- Exchange maintenance window handling

---

## 13. Integration testing and deployment checklist

**Before Dev build**

- [ ] Exchange inventory returned (§5.6 items 1–7). Assumptions A1, A2 and A4 confirmed.
- [ ] Network path design agreed. CIDRs allocated. DX/VPN capacity confirmed.
- [ ] Dev mailbox, `svc-apmail-dev`, permission and test senders provisioned.
- [ ] Firewall rule for VPC-dev → VIP:443 and DNS 53 in place.
- [ ] Internal CA chain received.

**Dev**

- [ ] Smoke test from a Lambda in VPC-dev: DNS resolves; TLS chain and hostname OK; NTLM auth OK with EP; `GetFolder` Inbox; `SyncFolderItems` returns test items.
- [ ] End-to-end with the golden set: ledger, S3 layout, workflow, agents, Aurora.
- [ ] Failure drills F1–F6.
- [ ] Kerberos path tested if NTLM restrictions are planned.

**UAT**

- [ ] Same as Dev, plus volume test (peak × 3), soak test (72 h), approved replay set, alarm routing to on-call, runbook dry-runs.

**Prod**

- [ ] Export-control sign-off. Security review complete. Change ticket for firewall/permissions approved.
- [ ] `svc-apmail-prd` secret populated by identity admins. Smoke test passes.
- [ ] First-run strategy executed (seed SyncState or floor).
- [ ] Shadow mode enabled. Daily comparison report reviewed.
- [ ] Go-live approval. Hypercare period with daily review.

---

## 14. Open decisions and dependencies

| # | Decision / dependency | Owner | Needed by |
| --- | --- | --- | --- |
| D1 | Exchange version/CU, EWS vdir auth, EP and load-balancer TLS mode (A1) | Exchange admins | Before Dev build |
| D2 | Mailbox type of the group address (A2) and permission model (FullAccess vs Reviewer) | Exchange admins + security | Before Dev build |
| D3 | NTLM policy and roadmap; Kerberos SPN/ASA if needed | AD / identity | Before Dev build |
| D4 | Hybrid connectivity: existing DX/VPN, TGW ownership, CIDRs, firewall change process (A3) | Network (AWS + on-prem) | Before Dev build |
| D5 | Dev/UAT mailboxes and service accounts on the production Exchange org (A4, A6) | Exchange + security | Before Dev build |
| D6 | ITAR boundary: partition/Region, authorized services and models, US-person access | Export control + security | Before any live mail; ideally before UAT |
| D7 | Credential rotation mechanism (PAM-driven vs Secrets Manager rotation over LDAPS) and password expiry policy | Identity + security | Before UAT |
| D8 | First-run backfill strategy and retention / Object Lock policy for raw mail | Business owner + records management | Before Prod |
| D9 | Mailbox-side marking (category / move) vs ledger-only | Business owner + Exchange | Before UAT |
| D10 | Approval of sanitized production replay for UAT | Export control + privacy | Before UAT |
| D11 | Fallback to Option B (on-prem relay) if inbound 443 is refused | Network security | At D4 |

---

## 15. Sources

**Microsoft (Exchange and EWS)**

- [Use REST APIs to access mailboxes in Exchange hybrid deployments (deprecated)](https://learn.microsoft.com/en-us/graph/hybrid-rest-support)
- [Exchange Online EWS, your time is almost up (no changes to EWS in Exchange Server)](https://techcommunity.microsoft.com/blog/Exchange/exchange-online-ews-your-time-is-almost-up/4492361)
- [Exchange Server Subscription Edition (SE) is now available](https://techcommunity.microsoft.com/blog/Exchange/exchange-server-subscription-edition-se-is-now-available/4424924)
- [Configure Windows Extended Protection in Exchange Server](https://learn.microsoft.com/en-us/exchange/plan-and-deploy/post-installation-tasks/security-best-practices/exchange-extended-protection)
- [Released: 2024 H1 Cumulative Update for Exchange Server (EP default, SSL offloading not supported)](https://techcommunity.microsoft.com/blog/exchange/released-2024-h1-cumulative-update-for-exchange-server/4047506)
- [Enabling Modern Auth in Exchange on-premises (ADFS)](https://learn.microsoft.com/en-us/exchange/plan-and-deploy/post-installation-tasks/enable-modern-auth-in-exchange-server-on-premises)
- [Configure Exchange Server on-premises to use Hybrid Modern Authentication (protocol support)](https://learn.microsoft.com/vi-vn/microsoft-365/enterprise/configure-exchange-server-for-hybrid-modern-authentication?view=o365-worldwide)
- [Synchronize items by using EWS in Exchange](https://docs.microsoft.com/exchange/client-developer/exchange-web-services/how-to-synchronize-items-by-using-ews-in-exchange)
- [MaxChangesReturned (1–512)](https://learn.microsoft.com/th-th/exchange/client-developer/web-service-reference/maxchangesreturned)
- [EWS throttling in Exchange](https://learn.microsoft.com/pl-pl/exchange/client-developer/exchange-web-services/ews-throttling-in-exchange)
- [New-ThrottlingPolicy](https://learn.microsoft.com/en-us/powershell/module/exchangepowershell/new-throttlingpolicy)
- [Impersonation and EWS in Exchange (X-AnchorMailbox)](https://msdn.microsoft.com/en-us/library/office/dn722377)
- [Configure client-specific message size limits (EWS 64 MB default)](https://learn.microsoft.com/en-gb/exchange/architecture/client-access/client-message-size-limits?view=exchserver-2016)
- [Mitigating NTLM relay attacks by default (MSRC)](https://msrc.microsoft.com/blog/2024/12/mitigating-ntlm-relay-attacks-by-default)

**AWS**

- [InvokeAgentRuntime API (runtimeSessionId ≥ 33 chars)](https://docs.aws.amazon.com/bedrock-agentcore/latest/APIReference/API_InvokeAgentRuntime.html)
- [Configure AgentCore Runtime and tools for VPC](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/agentcore-vpc.html)
- [AWS Step Functions adds 28 new service integrations, including Amazon Bedrock AgentCore](https://aws.amazon.com/about-aws/whats-new/2026/03/aws-sdk-integrations/)
- [Amazon Bedrock AgentCore is now available in AWS GovCloud (US-West)](https://aws.amazon.com/about-aws/whats-new/2026/05/bedrock-agentcore-launch-aws-govcloud-us/)
- [AWS GovCloud (US): Maintaining ITAR compliance](https://docs.aws.amazon.com/govcloud-us/latest/UserGuide/govcloud-itar.html)
- [AWS GovCloud (US) compliance (ITAR)](https://docs.aws.amazon.com/govcloud-us/latest/UserGuide/govcloud-compliance.html)
- [Route 53 Resolver endpoints and forwarding rules (hybrid DNS)](https://docs.aws.amazon.com/whitepapers/latest/hybrid-cloud-dns-options-for-vpc/route-53-resolver-endpoints-and-forwarding-rules.html)
- [GuardDuty Malware Protection for S3](https://docs.aws.amazon.com/guardduty/latest/ug/gdu-malware-protection-s3.html)
- [Tag-based access control with Malware Protection for S3](https://docs.aws.amazon.com/guardduty/latest/ug/tag-based-access-s3-malware-protection.md)
- [Handling errors for an SQS event source in Lambda (partial batch responses)](https://docs.aws.amazon.com/lambda/latest/dg/services-sqs-errorhandling.html)
- [Lambda parameters for SQS event source mappings (maximum concurrency)](https://docs.aws.amazon.com/lambda/latest/dg/services-sqs-parameters.html)

**Libraries**

- [exchangelib](https://ecederstrand.github.io/exchangelib)
- [requests-ntlm source (send_cbt default True)](https://sources.debian.org/src/python-requests-ntlm/1.3.0-1/requests_ntlm/requests_ntlm.py)
