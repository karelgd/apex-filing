import os
import tempfile
import unittest


TEST_DB = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
TEST_DB.close()
os.environ["AUTO_INIT_DB"] = "0"
os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB.name.replace(os.sep, '/')}"

from app import app  # noqa: E402
from models import Agency, AgencyCaseManager, Client, CrmCase, Notification, SubscriptionTool, db  # noqa: E402


class CompletedCaseNotificationTest(unittest.TestCase):
    @classmethod
    def tearDownClass(cls):
        try:
            os.remove(TEST_DB.name)
        except OSError:
            pass

    def setUp(self):
        app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
        with app.app_context():
            db.drop_all()
            db.create_all()

            crm_tool = SubscriptionTool(name="CRM")
            agency = Agency(
                agency_name="Notification Test Agency",
                tax_id="00-0000000",
                street_address="1 Main Street",
                city="Los Angeles",
                state="CA",
                zip_code="90001",
                ceo_email="owner@example.com",
                ceo_phone="555-0100",
                registered_owners="Test Owner",
                total_ips_allowed=5,
            )
            agency.subscriptions.append(crm_tool)
            db.session.add_all([crm_tool, agency])
            db.session.flush()

            manager = AgencyCaseManager(
                agency_id=agency.id,
                full_name="Morgan Manager",
                username="manager",
            )
            manager.set_password("test-password")
            client = Client(
                agency_id=agency.id,
                first_name="Alex",
                last_name="Client",
                phone="555-0101",
                email="alex@example.com",
                street_address="2 Main Street",
                city="Los Angeles",
                state="CA",
                zip_code="90001",
                username="alexclient",
            )
            client.set_password("client-password")
            db.session.add_all([manager, client])
            db.session.flush()

            case = CrmCase(
                agency_id=agency.id,
                client_id=client.id,
                case_manager_id=manager.id,
                title="XYZ",
                status="Open",
            )
            db.session.add(case)
            db.session.commit()
            self.case_id = case.id
            self.client_id = client.id

        self.web = app.test_client()
        response = self.web.post(
            "/login/agency",
            data={"username": "manager", "password": "test-password"},
        )
        self.assertEqual(response.status_code, 302)

    def test_completing_case_creates_linked_unread_alert_and_opening_marks_read(self):
        response = self.web.post(
            f"/agency/crm/cases/{self.case_id}/edit",
            data={
                "title": "XYZ",
                "status": "Completed",
                "price": "0",
                "case_manager_id": "1",
                "form_preparer_id": "",
                "notes": "",
            },
        )
        self.assertEqual(response.status_code, 302)

        with app.app_context():
            notification = Notification.query.one()
            self.assertEqual(notification.sender_label, "System")
            self.assertEqual(
                notification.message,
                "Case No. XYZ for Client Alex Client has been completed.",
            )
            self.assertIsNone(notification.read_at)
            notification_id = notification.id

        history = self.web.get("/agency/notifications")
        self.assertEqual(history.status_code, 200)
        self.assertIn(b"Case No. XYZ for Client Alex Client has been completed.", history.data)
        self.assertIn(b"New", history.data)

        detail = self.web.get(f"/agency/notifications/{notification_id}")
        self.assertEqual(detail.status_code, 200)
        self.assertIn(f"/agency/crm/cases/{self.case_id}".encode(), detail.data)
        self.assertIn(f"/agency/crm/clients/{self.client_id}".encode(), detail.data)

        with app.app_context():
            self.assertIsNotNone(db.session.get(Notification, notification_id).read_at)

        history = self.web.get("/agency/notifications")
        self.assertNotIn(b"notification-new-label", history.data)


if __name__ == "__main__":
    unittest.main()
