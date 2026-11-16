import unittest
from datetime import datetime, timedelta
from subprocess import CompletedProcess, TimeoutExpired
from unittest.mock import patch

from flask import Flask
from flask_login import LoginManager, current_user
from flask_wtf import CSRFProtect
from jinja2 import ChoiceLoader, DictLoader

from app import recalc_crm_invoice
from models import AgencyCaseManager, AgencyUser, Client, CrmCase, CrmClientActivityLog, CrmInvoice, CrmInvoiceActivity, db
from zelle_inbox import ZelleDepositAlert, ZelleDepositRead, ZellePaymentAllocation, ZellePaymentLink, register_zelle_inbox


class ZelleInboxTest(unittest.TestCase):
    def setUp(self):
        app = Flask(__name__, template_folder="../templates")
        app.config.update(TESTING=True, SECRET_KEY="test", SQLALCHEMY_DATABASE_URI="sqlite://",
                          WTF_CSRF_ENABLED=False, ODAISA_ZELLE_INBOX_ENABLED=True, ODAISA_LEAD_AGENCY_ID=1)
        app.jinja_loader = ChoiceLoader([
            DictLoader({"base.html": "{{ unread_zelle_count }} unread"
                        "{% for message in get_flashed_messages() %}{{ message }}{% endfor %}"
                        "{% block content %}{% endblock %}"}),
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
        def log_client(client, action, details):
            db.session.add(CrmClientActivityLog(client_id=client.id, agency_id=client.agency_id,
                                                 user_label=current_user.get_id(), action=action, details=details))

        register_zelle_inbox(
            app, lambda value: value - timedelta(hours=5),
            can_use_crm=lambda agency: app.config.get("TEST_CRM_ENABLED", True),
            current_user_label=lambda: current_user.get_id(), recalc_invoice=recalc_crm_invoice, log_client=log_client,
        )
        now = datetime(2026, 11, 17, 4)
        self.now = now
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

    def seed_link_targets(self):
        with self.client.application.app_context():
            for ident, agency_id, first_name, last_name in (
                (10, 1, "Alicia", "Rivera"), (11, 1, "Alex", "Reed"), (12, 2, "Alicia", "Rivera"),
            ):
                db.session.add(Client(
                    id=ident, agency_id=agency_id, first_name=first_name, last_name=last_name,
                    a_number="A-123-456-789", phone="+1 (305) 555-0199", email=f"client{ident}@example.com",
                    street_address="1 Example St", city="Miami", state="FL", zip_code="33101", username=f"client{ident}",
                ))
                db.session.add(CrmCase(id=ident, agency_id=agency_id, client_id=ident, title="I-765 Work Permit", price=100))
                db.session.add(CrmInvoice(
                    id=ident, agency_id=agency_id, client_id=ident, case_id=ident,
                    invoice_number=f"INV-{ident}", total=100, subtotal=100,
                ))
            db.session.add(CrmInvoice(id=13, agency_id=1, client_id=10, case_id=10, invoice_number="VOID", status="Void"))
            db.session.commit()

    def test_link_search_and_invoices_are_agency_scoped(self):
        self.seed_link_targets()
        self.sign_in("manager")
        for query in ("Ali Riv", "A123-456", "(305) 555"):
            response = self.client.get("/agency/zelle/clients", query_string={"q": query})
            self.assertEqual(response.headers["Cache-Control"], "no-store")
            self.assertIn(10, [row["id"] for row in response.json])
            self.assertNotIn(12, [row["id"] for row in response.json])
            self.assertIn({"id": 10, "name": "Alicia Rivera", "a_number": "123456789",
                           "phone": "3055550199"}, response.json)
        response = self.client.get("/agency/zelle/clients/10/invoices")
        self.assertEqual(response.json, [{"id": 10, "number": "INV-10", "case": "I-765 Work Permit", "balance": "100.00"}])
        self.assertEqual(self.client.get("/agency/zelle/clients/12/invoices").status_code, 404)

    def test_manager_links_once_using_alert_data_and_current_time(self):
        self.seed_link_targets()
        self.sign_in("manager")
        data = {"client_id": 10, "invoice_id": 10, "amount": "1", "payment_method": "Cash"}
        response = self.client.post("/agency/zelle/1/link?sender=Recent", data=data)
        self.assertEqual(response.location, "/agency/zelle?sender=Recent")
        self.assertIn(b"Linked", self.client.get(response.location).data)
        self.sign_in("owner")
        self.assertEqual(self.client.post("/agency/zelle/1/link", data={"client_id": 11, "invoice_id": 11}).status_code, 303)
        with self.client.application.app_context():
            activity = CrmInvoiceActivity.query.one()
            self.assertEqual((activity.invoice_id, activity.activity_type, activity.payment_method, activity.amount),
                             (10, "Payment", "Zelle", 42))
            self.assertEqual(activity.created_at, self.now)
            self.assertEqual(activity.activity_date, (self.now - timedelta(hours=5)).date())
            self.assertEqual(activity.description, "Zelle sender: Recent Sender")
            self.assertEqual(activity.recorded_by_id, "agency_case_manager:1")
            self.assertEqual(activity.invoice.balance_due, 58)
            self.assertEqual(ZellePaymentAllocation.query.one().activity_id, activity.id)
            self.assertEqual(CrmClientActivityLog.query.one().action, "Invoice payment linked from Zelle")

    def test_partial_allocation_across_clients_and_overallocation_protection(self):
        self.seed_link_targets()
        self.sign_in("manager")
        url = "/agency/zelle/1/link"
        first = {"client_id": 10, "invoice_id": 10, "amount_mode": "partial",
                 "amount": "12.25", "expected_remaining": "42.00"}
        self.client.post(url, data=first)
        self.client.post(url, data=first)
        self.assertIn(b"$29.75 remaining", self.client.get("/agency/zelle").data)
        second = {"client_id": 11, "invoice_id": 11, "amount_mode": "partial", "expected_remaining": "29.75"}
        for invalid in ("29.76", "12.001", "0", "-1"):
            self.client.post(url, data=dict(second, amount=invalid))
        with self.client.application.app_context():
            self.assertEqual(CrmInvoiceActivity.query.count(), 1)
        self.client.post(url, data=dict(second, amount_mode="full"))
        self.client.post(url, data=second)
        with self.client.application.app_context():
            activities = CrmInvoiceActivity.query.order_by(CrmInvoiceActivity.invoice_id).all()
            self.assertEqual([(activity.invoice_id, str(activity.amount)) for activity in activities],
                             [(10, "12.25"), (11, "29.75")])
            self.assertEqual(CrmClientActivityLog.query.count(), 2)

    def test_existing_full_link_cannot_be_allocated_again(self):
        self.seed_link_targets()
        with self.client.application.app_context():
            db.session.add(ZellePaymentLink(alert_id=1, invoice_number="LEGACY"))
            db.session.commit()
        self.sign_in("owner")
        self.client.post("/agency/zelle/1/link", data={
            "client_id": 10, "invoice_id": 10, "amount_mode": "partial", "amount": "1",
        })
        self.assertIn(b"LEGACY", self.client.get("/agency/zelle").data)
        with self.client.application.app_context():
            self.assertEqual(CrmInvoiceActivity.query.count(), 0)

    def test_cannot_link_hidden_alert_or_invalid_invoice(self):
        self.seed_link_targets()
        self.sign_in("manager")
        for alert_id, client_id, invoice_id in ((2, 10, 10), (3, 10, 10), (1, 12, 12), (1, 10, 11), (1, 10, 13)):
            response = self.client.post(f"/agency/zelle/{alert_id}/link", data={"client_id": client_id, "invoice_id": invoice_id})
            self.assertEqual(response.status_code, 404)
        with patch.dict(self.client.application.config, WTF_CSRF_ENABLED=True):
            self.assertEqual(self.client.post("/agency/zelle/1/link", data={"client_id": 10, "invoice_id": 10}).status_code, 400)
        with patch.dict(self.client.application.config, TEST_CRM_ENABLED=False):
            self.assertEqual(self.client.get("/agency/zelle/clients?q=Ali").status_code, 403)
            self.assertEqual(self.client.post("/agency/zelle/1/link", data={"client_id": 10, "invoice_id": 10}).status_code, 403)
        with self.client.application.app_context():
            self.assertEqual(CrmInvoiceActivity.query.count(), 0)

    def sign_in(self, identity):
        with self.client.session_transaction() as session:
            session["_user_id"] = identity

    def test_mark_selected_read_preserves_filters_and_is_per_user(self):
        self.sign_in("owner")
        self.assertEqual(self.client.get("/agency/zelle").data.count(b'form="zelle-mark-selected"'), 2)
        response = self.client.post(
            "/agency/zelle/read?sender=Sender&page=1", data={"alert_id": ["1", "2", "2", "3"]}
        )
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.location, "/agency/zelle?sender=Sender&page=1")
        self.assertIn(b"0 unread", self.client.get(response.location).data)
        self.sign_in("manager")
        self.assertIn(b'id="zelle-select-all"', self.client.get("/agency/zelle").data)
        page = self.client.get("/agency/zelle")
        self.assertIn(b"1 unread", page.data)
        self.assertEqual(page.data.count(b'<tr class="table-active">'), 1)
        self.assertEqual(self.client.post("/agency/zelle/read", data={"alert_id": ["1", "2", "3"]}).status_code, 303)
        self.assertIn(b"0 unread", self.client.get("/agency/zelle").data)
        self.assertNotIn(b'<tr class="table-active">', self.client.get("/agency/zelle").data)
        with self.client.application.app_context():
            self.assertEqual(sorted(row.alert_id for row in ZelleDepositRead.query.all()), [1, 1, 2])

    def test_mark_selected_read_rejects_unauthorized_and_invalid_requests(self):
        self.sign_in("other")
        self.assertEqual(self.client.post("/agency/zelle/read", data={"alert_id": "1"}).status_code, 403)
        self.sign_in("manager")
        self.assertEqual(self.client.post("/agency/zelle/read", data={"alert_id": "invalid"}).status_code, 400)
        with patch.dict(self.client.application.config, WTF_CSRF_ENABLED=True):
            self.assertEqual(self.client.post("/agency/zelle/read", data={"alert_id": "1"}).status_code, 400)
        self.assertIn(b"1 unread", self.client.get("/agency/zelle").data)

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

    @patch("zelle_inbox.subprocess.run")
    def test_manual_sync_is_agency_only_and_reports_result(self, run):
        self.sign_in("other")
        self.assertNotIn(b"Sync now", self.client.get("/agency/zelle").data)
        self.assertEqual(self.client.post("/agency/zelle/sync").status_code, 403)
        run.assert_not_called()
        self.sign_in("owner")
        self.assertIn(b"Sync now", self.client.get("/agency/zelle").data)
        self.assertEqual(self.client.get("/agency/zelle/sync").status_code, 405)
        run.return_value = CompletedProcess([], 0, "Saved 1 new incoming Zelle alerts;", "")
        response = self.client.post("/agency/zelle/sync?sender=Recent&page=2")
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.location, "/agency/zelle?sender=Recent")
        self.assertEqual(run.call_args.args[0][-2:], ["--apply", "--manual"])
        self.assertIn(b"Imported 1 new Zelle alert.", self.client.get(response.location).data)

    @patch("zelle_inbox.subprocess.run")
    def test_manager_can_sync_without_access_to_older_alerts(self, run):
        self.sign_in("manager")
        self.assertIn(b"Sync now", self.client.get("/agency/zelle").data)
        run.return_value = CompletedProcess([], 0, "Saved 1 new incoming Zelle alerts;", "")
        response = self.client.post("/agency/zelle/sync", follow_redirects=True)
        self.assertIn(b"Zelle sync completed.", response.data)
        self.assertIn(b"Recent Sender", response.data)
        self.assertNotIn(b"Expired Sender", response.data)
        self.assertNotIn(b"Imported 1 new Zelle alert.", response.data)
        run.assert_called_once()

    @patch("zelle_inbox.subprocess.run")
    def test_manual_sync_requires_csrf_and_handles_busy_or_timeout(self, run):
        self.sign_in("owner")
        with patch.dict(self.client.application.config, WTF_CSRF_ENABLED=True):
            self.assertEqual(self.client.post("/agency/zelle/sync").status_code, 400)
        run.assert_not_called()
        run.return_value = CompletedProcess([], 75, "", "")
        response = self.client.post("/agency/zelle/sync", follow_redirects=True)
        self.assertIn(b"A Zelle sync is running or just ran.", response.data)
        run.side_effect = TimeoutExpired("runner", 25)
        response = self.client.post("/agency/zelle/sync", follow_redirects=True)
        self.assertIn(b"Zelle sync did not finish.", response.data)
