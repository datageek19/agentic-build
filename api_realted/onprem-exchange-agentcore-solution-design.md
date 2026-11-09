# On-Prem Exchange Mailbox Integration for AgentCore — Solution Design

Nov 9, 2026 · Jared Shahidin

## Summary

The recommendation is to read the on-prem group mailbox over **Exchange Web Services (EWS)** from a VPC-attached ingestion Lambda that signs in as the service account. That Lambda copies each new email and attachment into S3 and Aurora. The classification, extraction and reporting agents on AgentCore then work only from that copy. No agent holds the mailbox password or opens a connection to Exchange.

Key decisions:

- **Protocol:** EWS over HTTPS 443, not Microsoft Graph. Graph cannot reach mailboxes hosted on-prem, and Microsoft's guidance is to use EWS for on-prem Exchange.
- **Connector placement:** the connector is ingestion, not an agent. A scheduled Lambda (the *Email Ingestion Service* box in the architecture) polls the mailbox. Agents receive a case ID and read the email through AgentCore Gateway tools backed by S3 and Aurora.
- **Identity:** a dedicated non-interactive AD service account per environment, with FullAccess (or folder-level read) on that environment's group mailbox only. The password lives in AWS Secrets Manager.
- **Network:** Lambda in private subnets, then Transit Gateway, then the client's VPN or Direct Connect, then the on-prem firewall, then the Exchange load balancer. On-prem DNS names resolve through a Route 53 Resolver outbound endpoint.
- **Environments:** dev, UAT and prod each get their own AWS account, mailbox, service account, secret and firewall rule. Prod mail never reaches dev, and UAT sees production-like mail only after ITAR screening and sign-off.
- **Fallback:** if the client will not open inbound access from AWS to Exchange, a small relay on-prem reads EWS and pushes to S3 over outbound HTTPS. The AWS side stays the same.

## What we are actually connecting to

The "legacy Outlook group mailbox" is almost certainly a **shared mailbox on on-premises Exchange Server**. Outlook is only the desktop client people use to view it; our integration talks to the Exchange server behind it, not to Outlook.

Three facts follow from that:

- **Graph is not an option while the mailbox stays on-prem.** Microsoft ended Graph/REST access to on-prem mailboxes in hybrid deployments in July 2023 ([Microsoft Learn](https://learn.microsoft.com/en-us/graph/hybrid-rest-support)). Calls to an on-prem mailbox return `MailboxNotEnabledForRESTAPI`. The *Microsoft 365 Mailbox* and *Microsoft Graph Reply Service* boxes in the current architecture therefore need to change to EWS, unless the client moves this mailbox to Exchange Online.
- **EWS remains the supported API on-prem.** The EWS retirement from October 2026 to April 2027 applies only to Exchange Online, and Microsoft states there are no changes to EWS in Exchange Server ([Exchange Team blog](https://techcommunity.microsoft.com/blog/Exchange/exchange-online-ews-your-time-is-almost-up/4492361)).
- **The Exchange version matters.** Exchange 2016 and 2019 left support on October 14, 2025. Exchange Server Subscription Edition (SE), generally available since July 1, 2025, is the only supported on-prem version ([Exchange Team blog](https://techcommunity.microsoft.com/blog/Exchange/exchange-server-subscription-edition-se-is-now-available/4424924)). EWS works on all three, but an unsupported 2016 or 2019 server is a security finding the client's risk team will raise.

We should confirm the mailbox type with the Exchange team before building. A *shared mailbox* is what we expect. A *distribution group* has no mailbox to read, so mail would have to be copied into one. A *public folder* uses different EWS calls.

## Connection options

Option A, EWS pull from a VPC Lambda, is the recommended path. Option B is the fallback if the client's security team will not allow inbound connections from AWS to Exchange. The other options are listed so the decision is on record.

| Option | How it works | Traffic direction | Strengths | Weaknesses | Verdict |
| --- | --- | --- | --- | --- | --- |
| A. EWS pull from AWS | A scheduled Lambda in a private subnet calls Exchange EWS over HTTPS as the service account | AWS to on-prem, port 443, over VPN or Direct Connect | Fully supported API; keeps Exchange metadata (conversation ID, internet message ID, categories); all code and logs in AWS; one deployable per environment | Needs a firewall rule from AWS into the Exchange network and on-prem DNS resolution | **Recommended** |
| B. On-prem relay pushes to AWS | A small container or Windows service inside the client network reads EWS and uploads the email and attachments to S3 | On-prem to AWS, outbound 443 only | No inbound path into the client network; fits strict network policies | One more component that the client must host, patch and monitor; needs an AWS credential on-prem (e.g. IAM Roles Anywhere) | **Fallback** |
| C. Transport rule copy to Amazon SES | An Exchange mail-flow rule BCCs inbound mail for the group address to an SES receiving address; SES writes the raw message to S3 | On-prem SMTP to AWS | No polling and no mailbox credential | Auto-forwarding outside the company is usually blocked by policy; external SMTP for ITAR mail needs export-control approval; no read state | Not recommended |
| D. IMAP over TLS | Lambda reads the mailbox over IMAP | AWS to on-prem, 993 | Simple libraries | IMAP is often disabled on Exchange; it loses Exchange metadata and needs basic auth | Not recommended |
| E. Agent calls EWS directly as a tool | The AgentCore agent gets a tool that logs in to Exchange | AWS to on-prem, 443 | Fewer components | Puts the mailbox credential within the agent's reach; polling becomes non-deterministic; harder to audit and replay | Not recommended |
| F. Move the mailbox to Exchange Online | The client migrates the group mailbox, and we use Graph with an app registration | AWS to Microsoft 365, 443 | Matches the original architecture | Client decision, outside this team's control and timeline | Track as a later option |

A and B produce the same output: the email in S3 plus a row in Aurora. Everything after ingestion is identical, so the team can start on A and switch to B without changing the agents.

## Target architecture

![Target architecture: on-prem Exchange to AgentCore, per environment](target-architecture.png)

*Editable source: `target-architecture.drawio` (open in draw.io / diagrams.net).*

The highlighted ingestion Lambda is the only component with a network route and a credential to Exchange. The agents get a case ID from Step Functions and read the stored copy through Gateway tools. If the client chooses the on-prem relay (option B), only the top half changes: the relay pushes into the same S3 bucket and queue.

## Service account design

Each environment uses its own AD service account, with access to that environment's group mailbox only. Its password is stored only in that environment's Secrets Manager.

| Setting | What to ask the Exchange or AD team for | Why |
| --- | --- | --- |
| Account | A non-interactive AD account per environment, e.g. `svc-apmail-dev`, `svc-apmail-uat`, `svc-apmail-prd`; interactive and RDP logon denied by GPO | A leaked dev password cannot read prod mail; blocks human use |
| Mailbox permission (option 1) | `Add-MailboxPermission -Identity <group mailbox> -User <svc> -AccessRights FullAccess -AutoMapping $false` | Lets the account read and also tag or move processed mail; simplest to run |
| Mailbox permission (option 2, read-only) | Folder-level `Reviewer` on the Inbox (`Add-MailboxFolderPermission`) | True read-only; we then track processed state only in Aurora, not in the mailbox |
| No impersonation | Do not grant `ApplicationImpersonation` | Impersonation reaches every mailbox in scope; delegate access to one mailbox is enough |
| No Send As yet | Grant `Send As` later, only if replies go out from this mailbox | Phase 1 only reads mail |
| EWS enabled | `Set-CASMailbox <svc> -EwsEnabled $true`; add our client to the EWS allow list if the org uses `EwsApplicationAccessPolicy` | Some orgs block EWS except for listed apps |
| Throttling | Confirm the default EWS throttling policy fits about 1 poll a minute, or attach a dedicated policy | Avoids `ErrorServerBusy` during backlogs |
| Password | Non-expiring or on a known rotation run through the client's privileged-access tool, with a 2-week warning to our team | A silent expiry or lockout stops ingestion |

**Authentication protocol.** Start with NTLMv2 (Negotiate) over TLS. It needs only port 443 to Exchange and works from Lambda with the Python `exchangelib` library. Microsoft deprecated NTLM in June 2024 and plans to disable it by default in a future Windows Server release, so ask whether the client already restricts NTLM. If it does, use Kerberos instead. That needs network access to the domain controllers on port 88, on-prem DNS, and a keytab, so it means more firewall work. Basic auth over TLS is a last resort.

**Where the credential lives.** Store it in AWS Secrets Manager, encrypted with a customer-managed KMS key. A resource policy lets only the ingestion Lambda's IAM role read it. AgentCore Identity's outbound credential providers are designed for OAuth and API keys, so a Windows username and password fits Secrets Manager better. The agents never get the secret.

**Open point for the Exchange team:** whether the service account needs its own mailbox for delegate EWS access in their setup. Giving it a small mailbox is often the simplest fix.

## Interaction sequence

Only steps 1–9 touch Exchange, and they run in a deterministic Lambda. The agents start at step 11 and work from data that is already in AWS.

**Ingestion: Lambda to Exchange**

1. EventBridge Scheduler triggers the ingestion Lambda every 1–2 minutes. Reserved concurrency is set to 1, so two polls never overlap.
2. The Lambda reads the service-account secret from Secrets Manager through a VPC interface endpoint.
3. It resolves the Exchange hostname (e.g. `mail.corp.example`) through a Route 53 Resolver outbound endpoint that forwards to the client's DNS.
4. It opens HTTPS 443 to `https://<exchange>/EWS/Exchange.asmx`. Traffic goes through Transit Gateway, the VPN or Direct Connect, the client firewall, and then the Exchange load balancer. TLS is verified against the client's internal CA certificate, which ships in a Lambda layer.
5. It authenticates as the service account and calls `SyncFolderItems` on the group mailbox's Inbox, passing the sync state saved by the previous run. Exchange returns only the items that are new or changed since then.
6. For each new item, it fetches the full MIME message and the attachments. It writes the raw `.eml` and each attachment to the environment's S3 bucket, encrypted with KMS.
7. It inserts a row in Aurora keyed by the email's `InternetMessageId`, with the conversation ID, sender, subject, received time and S3 keys. If that ID already exists, the item is skipped. This makes retries safe.
8. Optionally, with FullAccess, it stamps the item with an `AI-Ingested` category in the mailbox. It saves the new sync state only after every item is committed.
9. It sends an `email.ingested` message (with the case ID) to SQS, which has a dead-letter queue.

**Processing: agents read from AWS, never from Exchange**

10. Step Functions starts the case. The preprocessing Lambda extracts text from the attachments, masks PII and applies Bedrock Guardrails.
11. The **classification agent** (AgentCore Runtime) gets the case ID. It calls the `get_email` and `get_attachment_text` tools through AgentCore Gateway; those are Lambda targets that read S3 and Aurora. It returns *payment-related or not*, *ITAR or non-ITAR*, and a confidence score. Low-confidence or suspected-ITAR cases go to the human review queue.
12. The **extraction agent** pulls PO numbers, invoice numbers, supplier, amounts, dates and the requested action from the body and attachments. It writes them to the extracted-context table in Aurora.
13. The **reporting agent** reads aggregated extracted data through a read-only `query_cases` tool and produces the insight report.

The agent's Gateway tools never call EWS. That keeps the mailbox credential out of reach of the models, keeps load on Exchange predictable, and lets us replay any case from S3.

## Ingestion Lambda sketch

The connector is about 80 lines of Python using [exchangelib](https://ecederstrand.github.io/exchangelib) (5.x). It uses delegate access to the group mailbox, incremental `SyncFolderItems`, and idempotent writes. Environment-specific values come from environment variables, so the same artifact is promoted from dev to prod unchanged.

```python
import hashlib, json, os, boto3
from exchangelib import Account, Configuration, Credentials, DELEGATE, NTLM, FaultTolerance
from exchangelib.attachments import FileAttachment, ItemAttachment

# Env config: EWS_URL, MAILBOX, SECRET_ID, BUCKET, QUEUE_URL; REQUESTS_CA_BUNDLE points at the client CA in a layer
sm, s3, sqs = boto3.client("secretsmanager"), boto3.client("s3"), boto3.client("sqs")

def _account():
    sec = json.loads(sm.get_secret_value(SecretId=os.environ["SECRET_ID"])["SecretString"])
    cfg = Configuration(
        service_endpoint=os.environ["EWS_URL"],            # https://mail.corp.example/EWS/Exchange.asmx
        credentials=Credentials(sec["username"], sec["password"]),   # DOMAIN\\svc-apmail-dev
        auth_type=NTLM,
        retry_policy=FaultTolerance(max_wait=120),          # backs off on ErrorServerBusy
    )
    return Account(os.environ["MAILBOX"], config=cfg, autodiscover=False, access_type=DELEGATE)

def handler(event, context):
    acct = _account()
    inbox = acct.inbox
    state = load_sync_state()                               # Aurora or DynamoDB, one row per mailbox
    new_ids = [item for change, item in inbox.sync_items(sync_state=state,
               only_fields=["id", "changekey"], max_changes_returned=100) if change == "create"]

    for msg in acct.fetch(ids=new_ids):
        if already_ingested(msg.message_id):                # unique key on InternetMessageId
            continue
        key = f"raw/{hashlib.sha256(msg.message_id.encode()).hexdigest()}"
        s3.put_object(Bucket=os.environ["BUCKET"], Key=f"{key}/message.eml", Body=msg.mime_content)
        att_keys = []
        for a in msg.attachments or []:
            if isinstance(a, FileAttachment):
                k = f"{key}/att/{a.name}"
                s3.put_object(Bucket=os.environ["BUCKET"], Key=k, Body=a.content)
                att_keys.append(k)
            elif isinstance(a, ItemAttachment):             # forwarded email inside the email
                k = f"{key}/att/{a.name}.eml"
                s3.put_object(Bucket=os.environ["BUCKET"], Key=k, Body=a.item.mime_content)
                att_keys.append(k)
        case_id = insert_email_row(msg, key, att_keys)       # conversation_id, sender, subject, datetime_received
        sqs.send_message(QueueUrl=os.environ["QUEUE_URL"],
                         MessageBody=json.dumps({"case_id": case_id, "type": "email.ingested"}))

    save_sync_state(inbox.item_sync_state)                  # only after every item is committed
```

Build notes:

- **Packaging:** use a container image or a layer with `exchangelib`, its `requests_ntlm` dependency, and the client CA bundle. If Kerberos is required, add `requests-gssapi` plus a `krb5.conf` and keytab.
- **Lambda settings:** private subnets, a security group with egress only to the Exchange VIP on port 443 and to the VPC endpoints, a timeout of 5–10 minutes, reserved concurrency of 1, and a dead-letter queue.
- **First run:** with no saved sync state, `SyncFolderItems` returns the whole Inbox. Seed the state once with a dry run, or filter by `datetime_received` to an agreed start date, so the agents don't process years of history.
- **Large mailboxes:** `max_changes_returned=100` caps each batch. The next scheduled run picks up the rest from the saved state.
- **Health check:** a separate Lambda in the same subnets runs a nightly `GetFolder` on the Inbox and alerts on authentication, DNS or TLS failures before users notice.

## Dev, UAT and prod strategy

Every environment gets the same code with its own mailbox, service account, secret, AWS account and firewall rule. What changes between environments is only configuration and the realism of the data.

| | Local / unit | Dev | UAT | Prod |
| --- | --- | --- | --- | --- |
| Purpose | Build agents without Exchange access | Prove connectivity and the end-to-end pipeline | Business validation on realistic mail | Live processing, starting in shadow mode |
| Mail source | `FileMailSource` adapter reading `.eml` fixtures | `EwsMailSource` to a dev group mailbox | `EwsMailSource` to a UAT group mailbox | `EwsMailSource` to the real group mailbox |
| Exchange target | None | Client's non-prod Exchange if one exists; otherwise a dev mailbox on the production Exchange org | Same as dev, separate mailbox | Production Exchange |
| Service account | None | `svc-apmail-dev`, rights on the dev mailbox only | `svc-apmail-uat`, rights on the UAT mailbox only | `svc-apmail-prd`, rights on the prod mailbox only |
| AWS account | Developer sandbox | Dev account | UAT account | Prod account |
| Network | None | Dev VPC to TGW to VPN/DX, firewall rule to the Exchange VIP on 443 | Same pattern, own rule | Same pattern, own rule, change-managed |
| Data | Synthetic golden set | Synthetic emails sent by a test harness | Synthetic set plus sanitized real samples approved by export control | Real mail |
| Outbound actions | None | None | None | None in shadow mode; human-approved later |

**Build the mail source as an interface.** Agents and pipeline code depend on a `MailSource` with `poll()` and `get(message_id)`. `FileMailSource` serves fixtures for unit tests and for agent prompt and eval work. `EwsMailSource` is the real connector. This lets the agent team work in parallel while network and account requests are still open, and those requests are usually the longest lead time.

**Test data.** Build a golden set of labelled emails before dev testing starts:

- Payment queries with PO numbers, invoice numbers and remittance PDFs
- Non-payment procurement mail
- Replies and forwarded chains on the same conversation
- Multiple and nested attachments
- Out-of-office and bounce messages
- Cases with ITAR *markers*

The ITAR examples must be synthetic: real export-controlled text never goes into a lower environment. A small harness sends this set to the dev and UAT mailboxes over SMTP from test sender accounts, so tests exercise the real Exchange path.

**If the client has only one Exchange org.** Many enterprises do. Dev and UAT mailboxes then live on production Exchange servers, and each environment's isolation comes from separate mailboxes and accounts, not separate servers. Agree this with the client's security team early. Some teams also ask that dev traffic come only from an approved CIDR.

**Promotion gates**

- [ ] Dev to UAT: connectivity smoke test passes (DNS, TLS, auth, `GetFolder`); a rerun creates no duplicates; recovery after a forced failure is shown; classification and extraction meet the agreed golden-set thresholds; everything deploys from the pipeline with no console changes
- [ ] UAT to prod: business sign-off on UAT results; export control approves ITAR handling and the model region; prod service account, mailbox permission and firewall rule delivered through the client's change process; alarms and runbook in place
- [ ] Prod shadow to live: for an agreed period the system reads and classifies real mail with no outbound action, and the results are compared with how analysts handled the same emails; go-live once accuracy is accepted

**Pipeline.** Deploy one container image or Lambda artifact to every environment. Infrastructure as code (CDK or Terraform) creates each account's VPC wiring, buckets, queues and empty secrets. The client's admins enter the secret values directly, so no password goes through the pipeline or the repository.

## Risks, ITAR and security

The biggest risk is not technical: ITAR mail is copied into AWS *before* the classifier decides it is ITAR. The hosting region therefore has to be approved for ITAR data from the first byte.

| Risk | Impact | Mitigation |
| --- | --- | --- |
| ITAR content lands in a commercial AWS region before classification | Possible export-control violation | Get the client's export-control decision on the region before any prod ingestion. AgentCore has been GA in AWS GovCloud (US-West) since May 5, 2026 ([AWS](https://aws.amazon.com/about-aws/whats-new/2026/05/bedrock-agentcore-launch-aws-govcloud-us/)); confirm the models and AgentCore features you need are offered there. Lower environments use synthetic ITAR markers only |
| Inbound firewall rule from AWS into the Exchange network is refused | No connectivity; schedule slip | Raise the network request in week 1; keep option B (on-prem relay, outbound only) designed as the fallback |
| Service account password expires, rotates or locks out | Ingestion stops silently | Nightly health check with an alarm; rotation through the client's privileged-access process with notice; alarm on zero emails ingested over a business-hours window |
| NTLM restricted under the client's hardening plan | Auth fails after a GPO change | Ask now; build and test the Kerberos path in dev if NTLM is already restricted |
| Exchange still on 2016 or 2019 (out of support since October 14, 2025) | Security finding; possible forced upgrade mid-project | Confirm the version; EWS behavior is the same on SE, so plan a regression run after the upgrade |
| Duplicate or missed emails during retries or Exchange failover | Double-processing or lost queries | `InternetMessageId` unique key; sync state saved only after commit; a weekly reconciliation that compares Inbox item counts with Aurora |
| Malicious attachments or prompt injection in email bodies | Agent manipulation; malware in S3 | Malware scan on S3 upload; Bedrock Guardrails and the preprocessing service before any agent; agents get read-only tools and no direct mailbox access |
| Agents gain write access to the mailbox later | Unreviewed emails sent to suppliers | Replies are a separate phase: `Send As` granted only then, every send behind human approval and logged in the audit table |

Security baseline for every environment:

- Private subnets only.
- VPC endpoints for S3, Secrets Manager, SQS, KMS and AgentCore.
- Security groups with egress only to the Exchange VIP on 443.
- S3 and Aurora encrypted with customer-managed KMS keys.
- CloudTrail plus AgentCore Observability for agent and tool calls.
- Mail body text is not written to CloudWatch logs.

## Open questions and next steps

The network request and the export-control decision have the longest lead times. Start both this week, while the agent team builds against fixtures.

**For the Exchange team**

- [ ] Exchange version and build (2016, 2019 or SE)? Is there a non-prod Exchange org?
- [ ] Is the group address a shared mailbox, a distribution group or a public folder? What is its SMTP address, and roughly how many emails a day and how much history?
- [ ] EWS URL and load balancer VIP; is EWS restricted by `EwsApplicationAccessPolicy` or per-mailbox settings?
- [ ] Can we have FullAccess (`AutoMapping` off), or only folder-level Reviewer? Does the service account need its own mailbox?
- [ ] Throttling policy for the service account; any maintenance windows?

**For AD and identity**

- [ ] Service accounts for dev, UAT and prod: naming, password policy, rotation tool, lockout threshold
- [ ] Is NTLM restricted or audited? Is Kerberos required?

**For network**

- [ ] The existing path from AWS (Direct Connect, VPN, Transit Gateway) and which AWS accounts are attached
- [ ] Firewall rule from each environment's subnets to the Exchange VIP on 443; internal CA certificate chain; DNS forwarder IPs for the Route 53 Resolver
- [ ] If inbound from AWS is refused: will they host the on-prem relay (option B)?

**For security and export control**

- [ ] Approved AWS region for ITAR-bearing mail (commercial or GovCloud); approved Bedrock models
- [ ] Rules for sanitized real samples in UAT; retention period for raw `.eml` in S3

**Next steps for our team**

1. Send the questions above to the client's Exchange, network and security leads.
2. Build the `MailSource` interface, `FileMailSource` and the golden test set so agent work starts now.
3. Stand up the dev VPC wiring (Resolver endpoint, TGW attachment, VPC endpoints) in infrastructure as code.
4. When the dev firewall rule and account land, deploy `EwsMailSource` and run the connectivity smoke test.
5. Update the C4 diagram: replace the Graph-based mailbox and reply boxes with the EWS ingestion path.

## Sources

- [Use REST APIs to access mailboxes in Exchange hybrid deployments (deprecated) — Microsoft Learn](https://learn.microsoft.com/en-us/graph/hybrid-rest-support)
- [Exchange Online EWS, your time is almost up — Exchange Team blog](https://techcommunity.microsoft.com/blog/Exchange/exchange-online-ews-your-time-is-almost-up/4492361)
- [Exchange Server Subscription Edition (SE) is now available — Exchange Team blog](https://techcommunity.microsoft.com/blog/Exchange/exchange-server-subscription-edition-se-is-now-available/4424924)
- [Configure AgentCore Runtime and tools for VPC — AWS docs](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/agentcore-vpc.html)
- [AgentCore now available in AWS GovCloud (US-West) — AWS](https://aws.amazon.com/about-aws/whats-new/2026/05/bedrock-agentcore-launch-aws-govcloud-us/)
- [exchangelib documentation](https://ecederstrand.github.io/exchangelib)
