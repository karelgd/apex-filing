# Apex Document Filing

A Flask MVP for a subscription-based agency platform. Apex super admins manage agencies; agencies manage clients and cases; clients complete case questionnaires and upload documents; agencies can review answers and generate a PDF answer summary when the agency has the Form Filler subscription.

## Stack

- Python Flask
- SQLite
- SQLAlchemy
- Flask-Login
- Werkzeug password hashing
- Flask-WTF CSRF protection
- Jinja2 templates
- Bootstrap 5
- ReportLab PDF generation
- Local file uploads in `uploads/`

## Local Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
$env:SECRET_KEY="replace-with-a-random-secret"
flask --app app init-db
python app.py
```

Open `http://127.0.0.1:5000`.

Initial Apex super admin:

- Username: `apexadmin`
- Password: `ChangeMe123!`

Change this password immediately after first login in any real environment.

## MVP Flow

1. Log in as Apex at `/login/apex`.
2. Create an agency and assign the Form Filler subscription.
3. Log in as the agency at `/login/agency`.
4. Create a client account.
5. Create an `I-485` case for that client.
6. Log in as the client at `/login/client`.
7. Open the case questionnaire, answer questions, upload documents, and save progress.
8. Log back in as the agency and review the answers.
9. Click Generate Form to create a PDF answer summary under the case documents.

## Environment Variables

- `SECRET_KEY`: Required in production. Use a long random value.
- `DATABASE_URL`: Optional. Defaults to SQLite at `instance/app.db`.
- `UPLOAD_FOLDER`: Optional. Defaults to `uploads/`.
- `MAX_CONTENT_LENGTH`: Optional upload limit in bytes. Defaults to 16 MB.
- `AUTO_INIT_DB`: Optional. Defaults to `1`, which creates/updates base seed rows on import.
- `POSTMARK_SERVER_TOKEN`: Optional until email notifications are enabled. Postmark server API token used for client notifications.
- `POSTMARK_FROM_EMAIL`: Required for Postmark sending. Must be a confirmed Postmark sender, for example `Apex Document Filing <notifications@yourdomain.com>`.
- `POSTMARK_MESSAGE_STREAM`: Optional. Defaults to `outbound`.
- `CLIENT_PORTAL_URL`: Optional. Defaults to `https://apexdf.com` and is included in client notification emails.

When `POSTMARK_SERVER_TOKEN` and `POSTMARK_FROM_EMAIL` are configured, CRM case status changes automatically email the related client. If either value is missing, the status change still saves and the Activity Log records that email delivery was not configured.

## PythonAnywhere Deployment

1. Upload or clone the project into your PythonAnywhere home directory, for example:

   ```bash
   cd ~
   git clone <your-repo-url> apex-filing
   cd apex-filing
   ```

2. Create a virtual environment:

   ```bash
   mkvirtualenv --python=/usr/bin/python3.10 apex-filing
   pip install -r requirements.txt
   ```

3. Initialize the SQLite database:

   ```bash
   export SECRET_KEY="replace-with-a-random-secret"
   flask --app app init-db
   ```

4. In the PythonAnywhere Web tab:

   - Choose Manual configuration.
   - Choose the same Python version used for the virtualenv.
   - Set the virtualenv path, for example `/home/yourusername/.virtualenvs/apex-filing`.
   - Set the source code path to `/home/yourusername/apex-filing`.

5. Edit the WSGI file and point it to this Flask app:

   ```python
   import os
   import sys

   project_home = "/home/yourusername/apex-filing"
   if project_home not in sys.path:
       sys.path.insert(0, project_home)

   os.environ["SECRET_KEY"] = "replace-with-a-random-secret"
   os.environ["DATABASE_URL"] = "sqlite:////home/yourusername/apex-filing/instance/app.db"
   os.environ["UPLOAD_FOLDER"] = "/home/yourusername/apex-filing/uploads"
   os.environ["POSTMARK_SERVER_TOKEN"] = "your-postmark-server-token"
   os.environ["POSTMARK_FROM_EMAIL"] = "Apex Document Filing <notifications@yourdomain.com>"
   os.environ["POSTMARK_MESSAGE_STREAM"] = "outbound"
   os.environ["CLIENT_PORTAL_URL"] = "https://apexdf.com"

   from app import app as application
   ```

6. In Static files, add:

   - URL: `/static/`
   - Directory: `/home/yourusername/apex-filing/static/`

7. Reload the web app.

## Notes For Future USCIS PDF Mapping

The current Generate Form action intentionally creates a simple ReportLab PDF summary. The future real USCIS form population should be added around `create_answer_summary_pdf()` in `app.py`, replacing or extending the placeholder with a service that maps `CaseQuestion.field_key` values to official USCIS PDF fields.

The question architecture is already database-backed:

- `CaseQuestion` defines reusable fields for each case type.
- `CaseAnswer` stores one answer per case/question.
- `GeneratedForm` tracks generated outputs.

Add new case types by seeding more `CaseQuestion` rows and then adding mapping logic for those fields.
