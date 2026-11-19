import io
import json
import os
import re
import unittest
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from flask import Flask
from flask_login import LoginManager, current_user
from flask_wtf import CSRFProtect
from jinja2 import ChoiceLoader, DictLoader
from pypdf import PdfReader

from agreements import Agreement, AgreementEvent, AgreementTemplate, digest, now_utc, register_agreements
from models import Agency, AgencyCaseManager, AgencyUser, Client, CrmCase, CrmInvoice, CrmInvoiceActivity, db


class AgreementTest(unittest.TestCase):
    def setUp(self):
        app = Flask(__name__, template_folder="../templates")
        app.config.update(TESTING=True, SECRET_KEY="test", WTF_CSRF_ENABLED=False, SQLALCHEMY_DATABASE_URI="sqlite://")
        app.jinja_loader = ChoiceLoader([
            DictLoader({"base.html": "{% block content %}{% endblock %}"}), app.jinja_loader,
        ])
        CSRFProtect(app)
        db.init_app(app)
        users = {"owner": AgencyUser(id=1, agency_id=1, is_active=True),
                 "manager": AgencyCaseManager(id=1, agency_id=1, is_active=True),
                 "other": AgencyUser(id=2, agency_id=2, is_active=True)}
        LoginManager(app).user_loader(users.get)
        app.jinja_env.globals["is_agency_owner"] = lambda: isinstance(current_user._get_current_object(), AgencyUser)
        self.mail = []

        def send(to, subject, body):
            self.mail.append((to, subject, body))
            return not app.config.get("TEST_EMAIL_FAILURE"), "sent"

        register_agreements(app, can_use_crm=lambda agency: True,
                            is_owner=lambda: isinstance(current_user._get_current_object(), AgencyUser),
                            user_label=lambda: "Test agency user", send_email=send, log_client=lambda *args: None)
        app.add_url_rule("/clients/<int:client_id>", endpoint="crm_client_detail", view_func=lambda client_id: "Client")
        with app.app_context():
            db.create_all()
            for identifier in (1, 2):
                agency = Agency(id=identifier, agency_name=f"Agency {identifier}", tax_id="TEST",
                                street_address="100 Example St", city="Miami", state="FL", zip_code="33101",
                                ceo_email="office@example.com", ceo_phone="3055550100", registered_owners="Test")
                db.session.add(agency)
                db.session.add(Client(id=identifier, agency_id=identifier, first_name="Maria", last_name="Perez Lopez",
                                      a_number="123456789", phone="3055550101", email="client@example.com",
                                      street_address="1 Example St", city="Miami", state="FL", zip_code="33101",
                                      username=f"test{identifier}"))
                db.session.add(CrmCase(id=identifier, agency_id=identifier, client_id=identifier,
                                      title="I-765 Work Permit", price=Decimal("500")))
                db.session.add(AgreementTemplate(id=identifier, agency_id=identifier, name="Service Agreement v1",
                                                 content="English terms.\nTerminos en espanol."))
            db.session.add(CrmInvoice(id=1, agency_id=1, client_id=1, case_id=1, invoice_number="INV1", total=500))
            db.session.add(CrmInvoiceActivity(invoice_id=1, agency_id=1, activity_type="Discount", amount=25))
            db.session.commit()
        self.app = app
        self.client = app.test_client()
        self.env = patch.dict(os.environ, AGREEMENT_PUBLIC_BASE_URL="https://sign.example.com")
        self.env.start()
        self.addCleanup(self.env.stop)

    def login(self, user):
        with self.client.session_transaction() as session:
            if user:
                session["_user_id"] = user
            else:
                session.pop("_user_id", None)

    def send_agreement(self):
        self.login("manager")
        response = self.client.post("/agency/crm/clients/1/agreements/new",
                                    data={"template_id": 1, "case_id": [1], "authorize": "yes"})
        self.assertEqual(response.status_code, 302)
        token = re.search(r"/sign/agreements/([A-Za-z0-9_-]+)", self.mail[-1][2])[1]
        self.login(None)
        return "/sign/agreements/" + token

    def verify(self, url):
        self.client.post(url, data={"action": "code"})
        code = re.search(r"Code / Codigo: ([0-9]{6})", self.mail[-1][2])[1]
        self.client.post(url, data={"action": "verify", "code": code})

    def signing_data(self):
        return {"action": "sign", "lang": "es", "first": "Maria", "last": "Perez Lopez",
                "access": "yes", "consent": "yes", "intent": "yes"}

    def test_snapshot_typed_signature_and_fixed_pdf(self):
        url = self.send_agreement()
        self.assertEqual(self.client.get(url + "/pdf").status_code, 403)
        with self.app.app_context():
            agreement = Agreement.query.one()
            snapshot = json.loads(agreement.snapshot)
            self.assertEqual(snapshot["total"], "475.00")
            self.assertEqual(snapshot["services"][0]["discount"], "25.00")
            self.assertEqual(agreement.snapshot_hash, digest(agreement.snapshot))
            template = db.session.get(AgreementTemplate, 1)
            template.content = "Edited later"
            db.session.commit()
        self.verify(url)
        self.assertEqual(self.client.post(url, data=self.signing_data()).status_code, 400)
        preview = self.client.get(url + "/pdf")
        self.assertEqual(preview.status_code, 200)
        data = self.signing_data()
        data.pop("consent")
        self.assertEqual(self.client.post(url, data=data).status_code, 400)
        self.assertIn("English terms.", "\n".join(page.extract_text() for page in PdfReader(io.BytesIO(preview.data)).pages))
        self.assertEqual(self.client.post(url, data=self.signing_data()).status_code, 303)
        first_pdf = self.client.get(url + "/pdf").data
        self.assertIn("Maria Perez Lopez", "\n".join(page.extract_text() for page in PdfReader(io.BytesIO(first_pdf)).pages))
        self.assertEqual(self.client.post(url, data=self.signing_data()).status_code, 409)
        with self.app.app_context():
            agreement = Agreement.query.one()
            self.assertEqual(agreement.status, "Signed")
            self.assertEqual(agreement.pdf_hash, digest(first_pdf))
            self.assertEqual(AgreementEvent.query.filter_by(action="Client signed").count(), 1)
            self.assertNotIn("Edited later", agreement.snapshot)
        self.assertEqual(first_pdf, self.client.get(url + "/pdf").data)
        with self.app.app_context():
            Agreement.query.one().code_at = now_utc() - timedelta(minutes=2)
            db.session.commit()
        self.verify(url)  # Signed clients can regain access after their first session ends.

    def test_tenant_permissions_templates_and_archive(self):
        self.login("manager")
        self.assertEqual(self.client.get("/agency/agreements").status_code, 403)
        self.assertEqual(self.client.post("/agency/agreements/templates/new", data={"name": "X", "content": "X"}).status_code, 403)
        self.assertEqual(self.client.get("/agency/crm/clients/2/agreements/new").status_code, 404)
        url = self.send_agreement()
        self.login("other")
        self.assertEqual(self.client.get("/agency/agreements/1").status_code, 404)
        self.login("manager")
        self.assertEqual(self.client.post("/agency/agreements/1/delete").status_code, 403)
        self.assertEqual(self.client.get("/agency/agreements/1/pdf").status_code, 200)
        self.login("owner")
        self.assertEqual(self.client.get("/agency/agreements").status_code, 200)
        self.assertEqual(self.client.get("/agency/agreements/1").status_code, 200)
        self.assertEqual(self.client.post("/agency/agreements/1/delete").status_code, 302)
        self.assertEqual(self.client.get(url).status_code, 404)
        with self.app.app_context():
            self.assertEqual(Agreement.query.count(), 1)
            self.assertIsNotNone(Agreement.query.one().archived_at)

    def test_verification_attempts_expiry_and_consent(self):
        url = self.send_agreement()
        self.client.post(url, data={"action": "code"})
        self.assertEqual(len(self.mail), 2)
        self.client.post(url, data={"action": "code"})
        self.assertEqual(len(self.mail), 2)  # Resend cooldown
        for _ in range(6):
            self.client.post(url, data={"action": "verify", "code": "bad"})
        with self.app.app_context():
            self.assertEqual(Agreement.query.one().code_attempts, 5)
        self.assertEqual(self.client.get(url + "/pdf").status_code, 403)
        with self.app.app_context():
            Agreement.query.one().expires_at = now_utc() - timedelta(seconds=1)
            db.session.commit()
        self.assertIn(b"expired", self.client.get(url).data)
        self.assertEqual(self.client.post(url, data={"action": "code"}).status_code, 409)

    def test_decline_and_csrf(self):
        url = self.send_agreement()
        with patch.dict(self.app.config, WTF_CSRF_ENABLED=True):
            self.assertEqual(self.client.post(url, data={"action": "code"}).status_code, 400)
        self.verify(url)
        self.assertEqual(self.client.post(url, data={"action": "decline"}).status_code, 303)
        self.assertEqual(self.client.post(url, data=self.signing_data()).status_code, 409)
        with self.app.app_context():
            self.assertEqual(Agreement.query.one().status, "Declined")
            self.assertIsNone(Agreement.query.one().pdf)

    def test_template_creation_and_foreign_cases_are_rejected(self):
        self.login("owner")
        self.assertEqual(self.client.get("/agency/agreements/templates/new").status_code, 200)
        self.assertEqual(self.client.post("/agency/agreements/templates/new",
                                         data={"name": "Bilingual v2", "content": "English / Espanol"}).status_code, 302)
        self.assertEqual(self.client.get("/agency/crm/clients/1/agreements/new").status_code, 200)
        self.assertEqual(self.client.post("/agency/crm/clients/1/agreements/new",
                                         data={"template_id": 1, "case_id": [2], "authorize": "yes"}).status_code, 400)
        self.assertEqual(self.client.post("/agency/crm/clients/1/agreements/new",
                                         data={"template_id": 2, "case_id": [1], "authorize": "yes"}).status_code, 404)
        self.assertEqual(self.mail, [])

    def test_failed_invitation_is_recorded_and_cannot_be_signed(self):
        self.login("manager")
        with patch.dict(self.app.config, TEST_EMAIL_FAILURE=True):
            self.client.post("/agency/crm/clients/1/agreements/new",
                             data={"template_id": 1, "case_id": [1], "authorize": "yes"})
        with self.app.app_context():
            self.assertEqual(Agreement.query.one().status, "Delivery failed")
            self.assertIsNone(Agreement.query.one().sent_at)


if __name__ == "__main__":
    unittest.main()
