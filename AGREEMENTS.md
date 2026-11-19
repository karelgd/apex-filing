# Agreements

## Workflow

Agency owners create versioned, plain-text templates under Agreements. Staff can
send a template from a client's Agreements tab and select multiple existing cases.
The submitted template, client/company contact details, service prices, and
invoice discounts are snapshotted. Later edits do not change that agreement.
Only owners can archive agreements. Archiving hides them and revokes signing
links; it does not destroy the completed PDF or evidence.

Karel Gonzalez is currently the fixed company signatory. Sending requires an
affirmative confirmation of authority to apply that automatic signature. Do not
enable this feature for an agency whose CEO or authorization differs.

Clients choose English or Spanish UI independently of template content, verify
access to their email using a one-time code, open the PDF, accept electronic-record
disclosures, affirm their intent to sign, and type their first and last names.
The retained PDF contains the terms, pricing, signatures, timestamps, snapshot
digest, and signing evidence. Email verification proves mailbox access, not
government-ID identity. This is not notarization or a certificate-based digital
signature.

## Deployment

For the existing Apex PythonAnywhere account, run
`python tools/deploy_agreements.py` after pulling the latest code. It validates
the existing secret/email setup, makes private SQLite/WSGI backups, creates the
three agreement tables, and appends the canonical HTTPS/secure-cookie settings.
It does not send invitations or reload the app. Reload only the Apex web app
after the helper confirms success. A repeat invocation stops instead of
appending duplicate WSGI settings.

1. Back up the production database and deploy the code.
2. Set AGREEMENT_PUBLIC_BASE_URL to the canonical HTTPS application origin,
   for example https://apexcomputenergy.pythonanywhere.com (no trailing path).
   This is explicit configuration, not inferred from the incoming Host header.
3. Confirm SECRET_KEY is a strong deployment-specific secret, not dev-change-me.
   Configure secure HTTPS session cookies in the production Flask configuration.
4. Confirm existing POSTMARK_SERVER_TOKEN, POSTMARK_FROM_EMAIL, and sending stream.
5. Initialize the new AgreementTemplate, Agreement, and AgreementEvent tables
   using the existing database initialization workflow, then reload the app.
6. Use a test client and a reviewed template to verify delivery, both languages,
   typed signing, PDF download, and the client/owner history before client use.

Existing AUTO_INIT_DB startup creates the additive tables. No existing client,
invoice, lead, or payment data migration is needed. Do not send real invitations
from local preview fixtures.

Pending invitations expire after 30 days. Verification codes expire after ten
minutes, have five attempts and a one-minute resend cooldown. Verified access
lasts 30 minutes; clients can reverify to download an executed agreement later.
Completion email failures appear in the agency-side evidence history.

## Legal And Operational Review

The implementation is designed to support consent, attribution, accessible
retained records, and evidence under Florida Statutes 668.50 and 15 USC 7001.
It does not guarantee that any particular contract or signing process is legally
enforceable. Counsel must review the templates, consumer disclosures, company
signature authority, record-retention policy, and any special requirements
applicable to the services before production use.

Sources checked November 18, 2026:

- https://flsenate.gov/Laws/Statutes/2026/668.50
- https://uscode.house.gov/us/usc/119-124/t15/s7001/

Paper copies and paper signing are represented as no-fee alternatives in the
disclosures; the office must honor these commitments. Templates are stored as
plain text, not executed as HTML or template code. The final PDF is stored in the
database with its SHA-256 digest; database backups must include these records.
Database administrators can change stored records, so hashes are integrity
checks, not independent third-party timestamping or tamper-proof storage.
Protect signing tokens in access logs, monitor delivery failures, and use an
appropriate backup/retention policy.

## Verification

Tests cover agency isolation, staff/owner permissions, CSRF, OTP expiration and
attempt limits, required consent and PDF access, snapshot stability, discounts,
typed signatures, duplicate execution rejection, immutable PDF retrieval,
declining, archiving, templates, and delivery failure.
