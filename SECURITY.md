# Security

Report vulnerabilities privately to the repository owner. Never include credentials, customer data, provider prompts/responses, or audit records in public issues.

SecureQuote validates external input and uploads, constrains upload type/size, minimizes provider data, validates structured AI output, enforces server-controlled state transitions, and requires human approval before a quote can be approved. AI cannot send quotes, charge customers, or finalize pricing.

Keep `.env.local`, provider keys, customer records, and JSONL logs outside Git. Internal review endpoints currently require a trusted external authentication/authorization boundary before any public deployment.
