initial test for connecting to footprints:

  - Confirm that an AWS-hosted test client can resolve and establish an authenticated HTTPS connection to the FootPrints SOAP endpoint.
  - run search query for a known test ticket and confirm the returned ticket identifier matches the FootPrints UI.
  - Invoke getIssueDetails for the test ticket and compare its status, description, requester, history, and relevant custom fields against the application UI.
  - Expose the validated Lambda web adapter through AgentCore Gateway and have the agent retrieve a known procurement ticket through a read-only tool call.
    -  search_footprints_tickets tool (Lambda validates the search type, maps it to an approved FootPrints query, calls search, and returns matching ticket references as JSON)
        -   {
              "search_by": "invoice_number",
              "search_value": "INV-001234"
            }
    - get_footprints_ticket tool (Lambda calls getIssueDetails and normalizes the returned SOAP data into ticket fields suitable for the ticket classification and extraction Agent.)
    -    {
            "workspace_id": "<approved-workspace-id>",
            "issue_number": "<validated-issue-id>"
          }


key questions to ask:
  - Confirmation of whether the SOAP Web Services API is enabled.
  - DEV/UAT SOAP endpoint URLs and WSDL or current API specification.
  - A dedicated read-only test service account and its authorized workspaces.
  - Procurement workspace ID (144) and mapping of ticket numbers to API issue IDs.
  - Searchable field names, custom-field schema, and sample ticket request/response payloads.
  - Network connectivity requirements from AWS to FootPrints.


additional questions to ask:
  - What are the API request limits, maximum search results, and concurrent connection limits?
  - Does ticket search support pagination, sorting, and incremental retrieval?
  - What are typical API response times for search and getIssueDetails?
  - How are invalid credentials, missing tickets, permission failures, and application errors returned?
  - Are there server-side API logs or audit records that can be used to troubleshoot failed requests?
  - Can the application team provide a non-production service account, test ticket IDs, and sanitized sample SOAP request/response payloads?


Procurement-specific ticket data:

  - Which API fields represent Request Type, Request Sub-Type, Subject, and ticket description?
  - Are supplier name, supplier ID, PO number, invoice number, and payment terms stored as structured fields or only in descriptions?
  - Does the API return the complete conversation history, including requester and agent comments?
  - Can we retrieve email attachments or supporting documents associated with a ticket? Is a separate attachment interface required?
  - What ticket status values are used, and how do they map to Open, In Progress, Resolved, and Closed?


Ticket search and retrieval:
  - Is the documented search API supported and enabled in our environment?
  - Is the documented getIssueDetails API supported and enabled?
  - FootPrints workspace/project ID that contains procurement and supplier payment tickets? - Workspace 144
  - Can we retrieve tickets by Ticket Number, PO Number, Invoice Number, supplier identifier, or email subject?
  - What database table names and queryable fields correspond to the procurement workspace?
  - Does the API support filtering by ticket status, date range, request type, and request subtype?
  - Can getIssueDetails retrieve the complete ticket description, comments/history, assigned users, and custom fields?
  - Can we get a dedicated read-only service account for API integration?
  - Are API permissions controlled by workspace, user role, or other access restrictions?
  - Is SOAP API specification available? Can you share it with our team?


AWS AgentCore–FootPrints integration workflow:
  
Amazon Bedrock AgentCore Agent -> (mcp tool invocation) -> AgentCore Gateway ->(Exposes ticket retrieval tools) -> AWS Lambda – FootPrints SOAP Adapter (Builds SOAP requests, handles authentication, converts responses to JSON) -> (HTTPS / approved network connectivity) -> BMC FootPrints Web Services API (search() / getIssueDetails()) -> FootPrints Application / Ticket Database (Legacy procurement ticket records) 



