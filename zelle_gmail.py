import base64
import os
import re
from datetime import datetime, timedelta, timezone
from email import policy
from email.parser import BytesParser
from urllib.parse import quote

import click
import requests
from bs4 import BeautifulSoup


MAILBOX = "immigration@odaisa.com"
BANK_SENDER = "customerservice@ealerts.bankofamerica.com"
DEPOSIT_SUBJECT = re.compile(r"(?P<sender>[^\r\n]{1,200}?) sent you \$(?P<amount>(?:[0-9]+|[0-9]{1,3}(?:,[0-9]{3})+)\.[0-9]{2})", re.I)
DEPOSIT_TEXT = "please allow up to 5 minutes for the money to deposit to your account"
GMAIL = "https://gmail.googleapis.com/gmail/v1/users/me"


def parse_bofa_deposit(message):
    """Extract only authenticated incoming deposit notices, without following email links."""
    from employee_payments import payment_amount

    raw = message["raw"]
    email = BytesParser(policy=policy.default).parsebytes(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))
    from_headers = email.get_all("From", [])
    if len(from_headers) != 1:
        return None
    addresses = from_headers[0].addresses
    if len(addresses) != 1 or addresses[0].addr_spec.casefold() != BANK_SENDER:
        return None
    # Gmail prepends its authentication results. Never accept a later, sender-supplied result.
    auth = str(email.get("Authentication-Results", "")).casefold()
    if not re.match(r"^mx\.google\.com\s*;", auth):
        return None
    if not re.search(r"(?:^|;)\s*dmarc=pass\b[^;]*\bheader\.from=(?:ealerts\.)?bankofamerica\.com(?=\s|;|$)", auth):
        return None
    if len(email.get_all("Subject", [])) != 1:
        return None
    subject = " ".join(str(email["Subject"]).split())
    match = DEPOSIT_SUBJECT.fullmatch(subject)
    if not match:
        return None
    body = email.get_body(preferencelist=("plain", "html"))
    if body is None:
        return None
    body_text = body.get_content()
    if body.get_content_type() == "text/html":
        body_text = BeautifulSoup(body_text, "html.parser").get_text(" ")
    body_text = " ".join(body_text.casefold().split())
    if subject.casefold() not in body_text or DEPOSIT_TEXT not in body_text or "zelle" not in body_text:
        return None
    try:
        amount = payment_amount(match["amount"].replace(",", ""))
        received_at = datetime.fromtimestamp(int(message["internalDate"]) / 1000, timezone.utc).replace(tzinfo=None)
    except (ValueError, ArithmeticError, OSError, KeyError):
        return None
    return {
        "source_message_id": message["id"], "sender_name": match["sender"],
        "amount": amount, "received_at": received_at,
    }


def gmail_json(response):
    if not response.ok:
        raise click.ClickException(f"Google returned HTTP {response.status_code}. Check the mailbox authorization and Gmail API configuration.")
    return response.json()


def register_zelle_sync(app):
    @app.cli.command("sync-odaisa-zelle")
    @click.option("--days", type=click.IntRange(1, 3660), default=7, show_default=True)
    @click.option("--apply", is_flag=True, help="Save matching alerts; otherwise preview counts only.")
    def sync_odaisa_zelle(days, apply):
        from models import Agency, db
        from zelle_inbox import ZelleDepositAlert

        agency_id = app.config.get("ODAISA_LEAD_AGENCY_ID")
        agency = db.session.get(Agency, agency_id) if agency_id else None
        if agency is None or "odaisa" not in agency.agency_name.casefold():
            raise click.ClickException("Configure the Odaisa agency before syncing.")
        credentials = {key: os.environ.get(f"ODAISA_GMAIL_{key.upper()}") for key in ("client_id", "client_secret", "refresh_token")}
        if not all(credentials.values()):
            raise click.ClickException("Gmail read-only OAuth credentials are not configured.")
        try:
            with requests.Session() as session:
                token = gmail_json(session.post(
                    "https://oauth2.googleapis.com/token",
                    data=dict(credentials, grant_type="refresh_token"), timeout=20,
                ))["access_token"]
                session.headers["Authorization"] = f"Bearer {token}"
                profile = gmail_json(session.get(f"{GMAIL}/profile", timeout=20))
                if profile["emailAddress"].casefold() != MAILBOX:
                    raise click.ClickException("The authorized Gmail mailbox is not the Odaisa immigration mailbox.")
                existing = {row[0] for row in db.session.query(ZelleDepositAlert.source_message_id).filter_by(agency_id=agency_id)}
                query = f'from:{BANK_SENDER} subject:"sent you" after:{int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp())}'
                params = {"q": query, "maxResults": 100}
                pending, rejected = [], 0
                while True:
                    listing = gmail_json(session.get(f"{GMAIL}/messages", params=params, timeout=20))
                    for row in listing.get("messages", []):
                        message_id = row["id"]
                        if message_id in existing:
                            continue
                        existing.add(message_id)
                        message = gmail_json(session.get(f"{GMAIL}/messages/{quote(message_id, safe='')}", params={"format": "raw"}, timeout=20))
                        try:
                            alert = parse_bofa_deposit(message)
                        except (ValueError, AttributeError, LookupError):
                            alert = None
                        if alert:
                            pending.append(alert)
                        else:
                            rejected += 1
                    if not listing.get("nextPageToken"):
                        break
                    params["pageToken"] = listing["nextPageToken"]
        except (requests.RequestException, ValueError, KeyError) as error:
            raise click.ClickException("Gmail sync failed before saving any alerts.") from error
        if apply:
            db.session.add_all(ZelleDepositAlert(agency_id=agency_id, **alert) for alert in pending)
            db.session.commit()
        click.echo(f"{'Saved' if apply else 'Matched'} {len(pending)} new incoming Zelle alerts; skipped {rejected} unverified or unsupported notices.")
