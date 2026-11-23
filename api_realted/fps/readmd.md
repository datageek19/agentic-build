footprint ingestion via backend table access, below are the steps:

We understand the API remediation can only start after the 11 December commitment, and we'd appreciate any news from the discussion as it comes. In the meantime, we'll plan Epic 4 ticket intake on read-only SQL access. A few points and questions so we can set this up:
1.	Service accounts
We don't yet hold a Windows domain service account for this project. Could you point us to the right contact for provisioning? We'd request two read-only accounts: one for non-production and one for production. Could you also confirm who will own the password and its rotation?
2.	Authentication from AWS
The workload reading FootPrints will run in our AWS environmente. Our intended approach is to use the service account via Kerberos from AWS, with the password held in AWS Secrets Manager. Could you confirm this is supported, and share:
•	The AD domain (Kerberos realm) name, and the domain controllers and DNS servers we should use
3.	Database details
•	Which instance we'd connect to (production, a reporting replica with its sync lag, or non-production), and whether a non-production FootPrints instance exists
•	Host name, port and database name
4.	Network connectivity
Could you connect us with the team that manages connectivity from AWS into the Boeing network (routing, firewall rules, DNS)? Once we have the host names above, we'll share our subnet ranges and the exact host and port list
