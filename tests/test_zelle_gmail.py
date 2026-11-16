import base64
import unittest
from datetime import datetime
from decimal import Decimal
from email.message import EmailMessage

from zelle_gmail import BANK_SENDER, parse_bofa_deposit


class ZelleGmailTest(unittest.TestCase):
    def message(self, subject="Sample Payer sent you $1,250.25", sender=BANK_SENDER, auth=None, body=None):
        notice = EmailMessage()
        notice["From"] = sender
        notice["Subject"] = subject
        for result in auth or ["mx.google.com; dmarc=pass (p=reject) header.from=ealerts.bankofamerica.com"]:
            notice["Authentication-Results"] = result
        notice.set_content(
            body or f"<h1>{subject}</h1><p>Please allow up to 5 minutes for the<br>"
                    "money to deposit to your account.</p><footer>Zelle</footer>",
            subtype="html",
        )
        return {"id": "test-id", "internalDate": "1794597240000",
                "raw": base64.urlsafe_b64encode(notice.as_bytes()).decode("ascii").rstrip("=")}

    def test_incoming_notice(self):
        alert = parse_bofa_deposit(self.message())
        self.assertEqual(alert["sender_name"], "Sample Payer")
        self.assertEqual(alert["amount"], Decimal("1250.25"))
        self.assertIsInstance(alert["received_at"], datetime)
        self.assertNotIn("transaction_at", alert)

    def test_non_deposit_or_unverified_notice(self):
        for notice in [
            self.message(subject="You sent Sample Payer $50.00"),
            self.message(subject="Sample Payer requested $50.00"),
            self.message(sender="payments@example.com"),
            self.message(body="Sample Payer sent you $1,250.25 via Zelle"),
            self.message(auth=[
                "mx.google.com; dmarc=fail header.from=ealerts.bankofamerica.com",
                "mx.google.com; dmarc=pass header.from=ealerts.bankofamerica.com",
            ]),
            self.message(auth=["mx.google.com; dmarc=pass header.from=bankofamerica.com.example.com"]),
        ]:
            self.assertIsNone(parse_bofa_deposit(notice))
