"""Initialize the agreement feature in the existing PythonAnywhere Apex app."""

import ast
import os
import runpy
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path


WSGI = Path("/var/www/apexcomputenergy_pythonanywhere_com_wsgi.py")
ORIGIN = "https://apexcomputenergy.pythonanywhere.com"
MARKER = "# Apex agreement signing configuration"


def configure_wsgi(source):
    if MARKER in source:
        raise ValueError("Signing configuration is already present; verify it in the Web panel instead.")
    updated = source.rstrip() + (
        "\n\n" + MARKER + "\n"
        "import os as _agreement_os\n"
        f'_agreement_os.environ["AGREEMENT_PUBLIC_BASE_URL"] = "{ORIGIN}"\n'
        'application.config["SESSION_COOKIE_SECURE"] = True\n'
    )
    ast.parse(updated)
    return updated


def main():
    if not WSGI.is_file() or WSGI.is_symlink():
        raise RuntimeError("Run this command in the existing Apex PythonAnywhere console.")
    source = WSGI.read_text(encoding="utf-8")
    updated = configure_wsgi(source)
    previous = os.environ.get("AUTO_INIT_DB")
    os.environ["AUTO_INIT_DB"] = "0"
    try:
        app = runpy.run_path(str(WSGI))["application"]
    finally:
        if previous is None:
            os.environ.pop("AUTO_INIT_DB", None)
        else:
            os.environ["AUTO_INIT_DB"] = previous
    if not app.secret_key or app.secret_key == "dev-change-me" or len(app.secret_key) < 32:
        raise RuntimeError("Configure a strong production SECRET_KEY before enabling signing. No key was changed.")
    if not all(os.environ.get(key) for key in ("POSTMARK_SERVER_TOKEN", "POSTMARK_FROM_EMAIL")):
        raise RuntimeError("Existing Postmark email configuration is missing. No configuration was changed.")
    from agreements import Agreement, AgreementEvent, AgreementTemplate
    from models import db

    private = Path.home() / ".apex-agreement-backups"
    if private.is_symlink():
        raise RuntimeError("Backup directory must not be a symbolic link.")
    private.mkdir(mode=0o700, exist_ok=True)
    private.chmod(0o700)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    with app.app_context():
        if db.engine.dialect.name != "sqlite":
            raise RuntimeError("This deployment helper requires SQLite. Back up other databases separately.")
        database = Path(db.engine.url.database)
        if not database.is_file() or database.is_symlink():
            raise RuntimeError("Production SQLite file was not found or is a symbolic link.")
        backup = private / f"before-agreements-{stamp}.sqlite3"
        with database.open("rb"):
            pass
        backup.touch(mode=0o600, exist_ok=False)
        with sqlite3.connect(database) as source_db, sqlite3.connect(backup) as target_db:
            source_db.backup(target_db)
        backup.chmod(0o600)
        backup_wsgi = private / f"before-agreements-{stamp}.wsgi.py"
        backup_wsgi.touch(mode=0o600, exist_ok=False)
        backup_wsgi.write_text(source, encoding="utf-8")
        db.metadata.create_all(db.engine, tables=[
            AgreementTemplate.__table__, Agreement.__table__, AgreementEvent.__table__,
        ])
        descriptor, temporary = tempfile.mkstemp(prefix=".apex-agreements-", dir=WSGI.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(updated)
            os.chmod(temporary, WSGI.stat().st_mode & 0o777)
            os.replace(temporary, WSGI)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    print("Agreement tables initialized; database and WSGI backups saved privately.")
    print("HTTPS signing URL and secure session cookies configured.")
    print("No invitations sent. Reload apexcomputenergy.pythonanywhere.com in the Web panel.")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"Deployment stopped: {error}", file=sys.stderr)
        raise SystemExit(1)
