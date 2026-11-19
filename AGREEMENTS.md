# Agreements

## Workflow

Agency owners create versioned, plain-text templates under Agreements. Staff can
send a template from a client's Agreements tab and select multiple existing cases.
The submitted template, client/company contact details, service prices, and
invoice discounts are snapshotted. Later edits do not change that agreement.
Only owners can archive agreements. Archiving hides them and revokes signing
links; it does not destroy the completed PDF or evidence.

Case managers can search the agency agreement list and see agreement names,
clients, dates, and statuses in that list and client profiles. They cannot open
agreement details, view/download PDFs, or manage templates. The server enforces
these restrictions, including public signing links while logged in as a case
manager. Agency owners retain document access.

On signing, an unread Alert is created for the client's active assigned case
manager. If there is no active assigned client manager, active managers of the
selected service cases receive the Alert instead. A repeated signing submission
cannot create duplicate execution or alerts.

Owners and case managers can resend a Pending agreement from either list. Resend
uses the snapshotted client email, not another manually entered recipient, and
preserves the original terms and pricing. Successful delivery replaces the old
signing token and renews the signing period to 30 days; failed delivery retains
the old token and deadline. Resends have a one-minute cooldown and are logged.
Signed, declined, or archived agreements cannot be resent.

Karel Gonzalez is currently the fixed company signatory. Sending requires an
affirmative confirmation of authority to apply that automatic signature. Do not
enable this feature for an agency whose CEO or authorization differs.

Clients choose English or Spanish UI independently of template content, review
the complete agreement directly on the signing page, accept electronic-record
disclosures, affirm their intent to sign, and type their first and last names.
The retained PDF contains the terms, pricing, signatures, timestamps, snapshot
digest, and signing evidence. The emailed invitation link is the access credential;
there is no separate verification code. Anyone with a forwarded link can access
the agreement. Link access does not prove the signer's identity or independently
verify mailbox ownership. This is not notarization or a certificate-based digital
signature. The interface and audit history do not claim independent verification.

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

Pending invitations expire after 30 days. A valid link permits reviewing and
downloading the document without an email-code step. Signed links continue to
permit retrieving the executed document unless the agreement is archived.
Before signing, the client must load the agreement page in their current browser
and affirm access, consent, and intent. An optional PDF download remains available.
Legacy verification columns are retained for database compatibility but unused.
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

Tests cover agency isolation, staff/owner permissions, CSRF, invitation expiration,
invalid tokens, required consent and agreement-page access, snapshot stability, discounts,
typed signatures, duplicate execution rejection, immutable PDF retrieval,
declining, archiving, templates, mandatory sender authorization, and delivery failure.
