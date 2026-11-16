"""Run the Odaisa Gmail sync with the same configuration as the hosted Apex app."""

import argparse
import fcntl
import json
import os
import runpy
import stat
import time
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--initialize", action="store_true")
    parser.add_argument("--manual", action="store_true")
    parser.add_argument("--days", default="7")
    args = parser.parse_args()
    private = Path.home() / ".odaisa-zelle"
    credentials_path = private / "credentials.json"
    if any(p.is_symlink() or not p.exists() or stat.S_IMODE(p.stat().st_mode) & 0o077
           for p in (private, credentials_path)):
        parser.error("The Gmail credential and its parent must be private, non-symlink paths.")
    credentials = json.loads(credentials_path.read_text(encoding="utf-8"))
    for key in ("client_id", "client_secret", "refresh_token"):
        os.environ[f"ODAISA_GMAIL_{key.upper()}"] = credentials[key]

    wsgi = Path("/var/www/apexcomputenergy_pythonanywhere_com_wsgi.py")
    with (private / "sync.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("Another Zelle sync is running; skipped.")
            return 75 if args.manual else 0
        if args.manual:
            last_manual = private / "last-manual-sync"
            if last_manual.exists() and time.time() - last_manual.stat().st_mtime < 60:
                print("A manual Zelle sync was requested less than a minute ago; skipped.")
                return 75
            last_manual.touch()
        app = runpy.run_path(str(wsgi))["application"]
        if args.initialize:
            from zelle_inbox import ZelleDepositAlert, ZelleDepositRead, db

            with app.app_context():
                db.metadata.create_all(db.engine, tables=[ZelleDepositAlert.__table__, ZelleDepositRead.__table__])
        command = ["sync-odaisa-zelle", "--days", args.days]
        if args.apply:
            command.append("--apply")
        result = app.test_cli_runner().invoke(args=command)
        print(result.output, end="")
        if result.exit_code and not result.output:
            print("Zelle sync failed before a result was returned.")
        return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
