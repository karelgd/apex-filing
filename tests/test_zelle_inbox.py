import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from flask import Flask
from flask_login import LoginManager
from flask_wtf import CSRFProtect
from jinja2 import ChoiceLoader, DictLoader

from models import AgencyCaseManager, AgencyUser, db
from zelle_inbox import ZelleDepositAlert, register_zelle_inbox


class ZelleInboxTest(unittest.TestCase):
    def setUp(self):
        app = Flask(__name__, template_folder="../templates")
        app.config.update(TESTING=True, SECRET_KEY="test", SQLALCHEMY_DATABASE_URI="sqlite://",
                          WTF_CSRF_ENABLED=False, ODAISA_ZELLE_INBOX_ENABLED=True, ODAISA_LEAD_AGENCY_ID=1)
        app.jinja_loader = ChoiceLoader([
            DictLoader({"base.html": "{{ unread_zelle_count }} unread{% block content %}{% endblock %}"}),
            app.jinja_loader,
        ])
        CSRFProtect(app)
        db.init_app(app)
        users = {
            "owner": AgencyUser(id=1, agency_id=1, is_active=True),
            "manager": AgencyCaseManager(id=1, agency_id=1, is_active=True),
            "other": AgencyUser(id=2, agency_id=2, is_active=True),
        }
        LoginManager(app).user_loader(users.get)
        register_zelle_inbox(app, lambda value: value - timedelta(hours=5))
        now = datetime(2026, 11, 16, 15)
        clock = patch("zelle_inbox.utc_now", return_value=now)
        clock.start()
        self.addCleanup(clock.stop)
        with app.app_context():
            db.create_all()
            for identifier, agency, name, age, amount in [
                (1, 1, "Recent Sender", timedelta(hours=72), 42),
                (2, 1, "Expired Sender", timedelta(hours=72, seconds=1), 42),
                (3, 2, "Other Agency", timedelta(hours=1), 150),
            ]:
                db.session.add(ZelleDepositAlert(id=identifier, agency_id=agency, sender_name=name,
                                              source_message_id=str(identifier), received_at=now-age, amount=amount))
            db.session.commit()
        self.client = app.test_client()

    def sign_in(self, identity):
        with self.client.session_transaction() as session:
            session["_user_id"] = identity

    def test_manager_cutoff_filters_and_direct_read(self):
        self.sign_in("manager")
        page = self.client.get("/agency/zelle?sender=Sender&date_from=2026-11-13&date_to=2026-11-13&amount=42")
        self.assertIn(b"Recent Sender", page.data)
        self.assertNotIn(b"Expired Sender", page.data)
        self.assertIn(b"1 unread", page.data)
        self.assertEqual(self.client.post("/agency/zelle/2/read").status_code, 404)
        self.assertEqual(self.client.post("/agency/zelle/1/read").status_code, 303)
        self.assertIn(b"0 unread", self.client.get("/agency/zelle").data)

    def test_owner_history_and_independent_unread(self):
        self.sign_in("manager")
        self.client.post("/agency/zelle/1/read")
        self.sign_in("owner")
        page = self.client.get("/agency/zelle")
        self.assertIn(b"Expired Sender", page.data)
        self.assertNotIn(b"Other Agency", page.data)
        self.assertIn(b"2 unread", page.data)
        self.sign_in("other")
        self.assertEqual(self.client.get("/agency/zelle").status_code, 403)
