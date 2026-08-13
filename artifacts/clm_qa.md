1. The four gaps we believe exist (highest priority)

These are negative findings. If any is wrong, the architecture changes materially — so ask them as assertions, not open questions.

Q1. Is there a task, work-queue or assignment object in Gemini Enterprise beyond Inbox notifications — now or on the roadmap?
We've concluded reviewer assignment, status, escalation and reassignment are entirely custom. This is the single largest block of custom scope.

Q2. Does data store refresh replace indexed content with no version lineage — and is versioning on the roadmap?
Determines whether the custom version ledger is permanent or transitional.

Q3. Is there any path to generating Word tracked-changes markup on Google Cloud — Document AI, Workspace APIs, anything planned?
If not, a custom or commercial OOXML component is unavoidable. One of three S1 risks.

Q4. Is access control document-level only, with no sub-document or chunk-level ACL planned?
Decides whether clause-level visibility is a platform control or an application control.

2. Orchestration — where the state machine lives

Q5. Should the workflow state machine sit in Cloud Workflows or in an Agent Runtime long-running agent?
We've chosen Workflows, primarily so the audit trail doesn't live inside agent session events. Want their view.

Q6. Can a single ADK long-running tool be resumed by multiple independent callers, or is it one-pause-one-resumer?
Central to our conclusion that ADK long-running tools don't fit a multi-party review barrier.

Q7. Can a tool be configured to always require confirmation, rather than relying on the model to call an approval tool?
A gate whose invocation depends on inference isn't a control. If this exists, it simplifies the design.

Q8. What are the current maximum execution and callback-wait durations for Cloud Workflows, and can they be raised?
Multi-week negotiation cycles may exceed a single execution lifetime.

3. Reviewer surface — our largest open design decision

Q9. What A2UI version is supported today, is rendering still restricted to the A2A registration path, and can the component catalogue be extended?
Three parts, one decision: whether a clause-diff grid can live inside Gemini Enterprise at all.

Q10. For an A2UI agent, is delegated user identity available to the agent's tool calls, or does the agent act with its own service identity?
Determines whether clause-level authorisation can be enforced safely inside a GEP-hosted agent.

4. Document processing

Q11. What are the current page limits, and when does DOCX layout parsing reach general availability?
Long master agreements with exhibits will exceed the 500-page OCR cap.

Q12. Are page offsets, character offsets and bounding boxes stable across parser versions? What's the deprecation policy?
Our source-location model pins parser version; we need to know the guarantee behind it.

5. Security, data handling and commercials

Q13. Confirm our exact edition and the applicable Service Specific Terms, including the scope of the Training Restriction — and provide the executed terms.
Data-handling posture is tier-dependent. Edition selection is effectively a security control.

Q14. Precisely what prompt, response and trace content is logged and retained, by default and when configured — with retention windows per surface?
Traces will contain supplier commercial terms. Also: can Memory Bank and third-party model routing both be disabled at org-policy level?

Q15. For third-party connectors, is FQDN-restricted egress the only mitigation, or is a private connectivity path planned?
VPC Service Controls doesn't enclose connector egress. This is the item our CISO will fix on.

The two closing questions

Worth asking explicitly at the end, because they surface what the structured list misses:

Is there anything in this design that fights the platform, or that you'd approach differently on Gemini Enterprise?

In comparable engagements, where does this class of solution most commonly fail?
