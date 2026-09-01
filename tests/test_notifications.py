import os
import tempfile
import unittest
from unittest.mock import patch


TEST_DB = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
TEST_DB.close()
os.environ["AUTO_INIT_DB"] = "0"
os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB.name.replace(os.sep, '/')}"

from app import app  # noqa: E402
from models import Agency, AgencyCaseManager, AgencyPreparer, AgencyUser, Client, CrmCase, CrmCaseStatusHistory, CrmSurvey, Message, MessageAssignmentLog, Notification, SubscriptionTool, db  # noqa: E402


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

            owner = AgencyUser(
                agency_id=agency.id,
                username="agencyowner",
            )
            owner.set_password("owner-password")
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
            db.session.add_all([owner, manager, client])
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
            self.manager_id = manager.id
            self.owner_id = owner.id

        self.web = app.test_client()
        response = self.web.post(
            "/login/agency",
            data={"username": "manager", "password": "test-password"},
        )
        self.assertEqual(response.status_code, 302)

    def test_completing_case_creates_linked_unread_alert_and_opening_marks_read(self):
        with patch("app.send_postmark_email", return_value=(True, "Email sent.")) as send_email:
            response = self.web.post(
                f"/agency/crm/cases/{self.case_id}/edit",
                data={
                    "title": "XYZ",
                    "status": "Completed",
                    "price": "0",
                    "case_manager_id": str(self.manager_id),
                    "form_preparer_id": "",
                    "notes": "",
                },
            )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(send_email.call_count, 1)
        self.assertEqual(send_email.call_args.args[1], "QUEREMOS SABER TU OPINION")
        self.assertIn("http://localhost/survey/", send_email.call_args.args[2])

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

        with app.app_context():
            survey = CrmSurvey.query.one()
            self.assertEqual(survey.case_id, self.case_id)
            self.assertIsNotNone(survey.email_sent_at)
            survey_token = survey.token
            survey_id = survey.id

        survey_page = self.web.get(f"/survey/{survey_token}")
        self.assertEqual(survey_page.status_code, 200)
        self.assertIn("Queremos saber tu opinión".encode("utf-8"), survey_page.data)
        self.assertEqual(survey_page.data.count(b"Morgan Manager"), 2)
        self.assertIn("Nuestro conocimiento y experiencia".encode("utf-8"), survey_page.data)

        with patch("app.send_postmark_email", return_value=(True, "Email sent.")) as resend_email:
            resent = self.web.post(f"/agency/crm/surveys/{survey_id}/resend")
        self.assertEqual(resent.status_code, 302)
        self.assertIn("http://localhost/survey/", resend_email.call_args.args[2])

        submitted = self.web.post(
            f"/survey/{survey_token}",
            data={
                "overall_satisfaction": "5",
                "communication_rating": "4",
                "process_clarity_rating": "5",
                "recommendation_rating": "1",
                "comments": "Podrían ampliar el horario de atención.",
            },
        )
        self.assertEqual(submitted.status_code, 200)
        self.assertIn("Gracias por tu opinión".encode("utf-8"), submitted.data)

        with app.app_context():
            survey = CrmSurvey.query.one()
            self.assertIsNotNone(survey.submitted_at)
            self.assertEqual(survey.case_manager_name, "Morgan Manager")
            self.assertEqual(survey.overall_satisfaction, 5)
            self.assertEqual(survey.recommendation_rating, 1)
            manager_alert = Notification.query.filter_by(
                notification_type="survey_submitted",
                recipient_role="agency_case_manager",
                recipient_id=self.manager_id,
            ).one()
            owner_alert = Notification.query.filter_by(
                notification_type="survey_customer_service_issue",
                recipient_role="agency_user",
                recipient_id=self.owner_id,
            ).one()
            manager_alert_id = manager_alert.id
            owner_alert_id = owner_alert.id

        manager_alert_page = self.web.get(f"/agency/notifications/{manager_alert_id}")
        self.assertEqual(manager_alert_page.status_code, 200)
        self.assertIn(b">Survey</a>", manager_alert_page.data)
        self.assertIn(f"/agency/crm/surveys/{survey_id}".encode(), manager_alert_page.data)

        report = self.web.get("/agency/crm/reports?report_type=surveys")
        self.assertEqual(report.status_code, 200)
        self.assertIn(b"Survey Report", report.data)
        self.assertIn(b"100.0%", report.data)
        self.assertIn(b"Alex Client", report.data)
        self.assertIn(b"survey-severity-yellow", report.data)
        self.assertIn(b"background-color:#fff3c9", report.data)

        self.web.get("/logout")
        owner_login = self.web.post(
            "/login/agency",
            data={"username": "agencyowner", "password": "owner-password"},
        )
        self.assertEqual(owner_login.status_code, 302)
        owner_alert_page = self.web.get(f"/agency/notifications/{owner_alert_id}")
        self.assertEqual(owner_alert_page.status_code, 200)
        self.assertIn(f"/agency/crm/clients/{self.client_id}".encode(), owner_alert_page.data)
        self.assertIn(f"/agency/crm/surveys/{survey_id}".encode(), owner_alert_page.data)

    def test_completed_case_tracking_is_saved_and_linked_in_newest_first_client_timeline(self):
        edit_page = self.web.get(f"/agency/crm/cases/{self.case_id}/edit")
        self.assertEqual(edit_page.status_code, 200)
        self.assertIn(b"Scan with handheld scanner", edit_page.data)
        self.assertNotIn(b"Camera barcode scanning", edit_page.data)

        with patch("app.send_postmark_email", return_value=(True, "Email sent.")):
            response = self.web.post(
                f"/agency/crm/cases/{self.case_id}/edit",
                data={
                    "title": "XYZ",
                    "status": "Completed",
                    "price": "0",
                    "case_manager_id": str(self.manager_id),
                    "form_preparer_id": "",
                    "notes": "",
                    "has_tracking_number": "yes",
                    "tracking_number": "9400 1000-0000 0000 0000 00",
                },
            )
        self.assertEqual(response.status_code, 302)

        with app.app_context():
            case = db.session.get(CrmCase, self.case_id)
            self.assertEqual(case.tracking_number, "9400100000000000000000")
            history = CrmCaseStatusHistory.query.filter_by(case_id=self.case_id).order_by(CrmCaseStatusHistory.changed_at.desc()).all()
            self.assertEqual(history[0].status, "Completed")
            self.assertEqual(history[0].tracking_number, case.tracking_number)

        self.web.get("/logout")
        login = self.web.post(
            "/login/client",
            data={"username": "alexclient", "password": "client-password"},
        )
        self.assertEqual(login.status_code, 302)
        detail = self.web.get(f"/client/crm-cases/{self.case_id}")
        self.assertEqual(detail.status_code, 200)
        tracking = b"9400100000000000000000"
        self.assertIn(b"https://tools.usps.com/go/TrackConfirmAction?tLabels=" + tracking, detail.data)
        self.assertLess(detail.data.index(b"Completed"), detail.data.index(b"Open"))

    def test_client_message_reassigns_from_inactive_creator_and_preserves_sender_names(self):
        with app.app_context():
            client = db.session.get(Client, self.client_id)
            manager = db.session.get(AgencyCaseManager, self.manager_id)
            owner = db.session.get(AgencyUser, self.owner_id)
            manager.is_active = False
            owner.is_active = False
            client.created_by_role = "agency_case_manager"
            client.created_by_id = manager.id
            client.created_by_label = manager.full_name
            client.assigned_to_role = "agency_case_manager"
            client.assigned_to_id = manager.id
            client.assigned_to_label = manager.full_name
            preparer = AgencyPreparer(
                agency_id=client.agency_id,
                full_name="Taylor Responder",
                username="taylor",
                is_active=True,
            )
            preparer.set_password("responder-password")
            db.session.add(preparer)
            db.session.commit()
            preparer_id = preparer.id

        self.web.get("/logout")
        self.assertEqual(
            self.web.post("/login/client", data={"username": "alexclient", "password": "client-password"}).status_code,
            302,
        )
        sent = self.web.post("/client/messages", data={"message": "Necesito una actualización de mi caso."})
        self.assertEqual(sent.status_code, 302)

        with app.app_context():
            client = db.session.get(Client, self.client_id)
            self.assertEqual(client.created_by_label, "Morgan Manager")
            self.assertEqual(client.assigned_to_role, "agency_preparer")
            self.assertEqual(client.assigned_to_id, preparer_id)
            self.assertEqual(client.assigned_to_label, "Taylor Responder")
            assignment = MessageAssignmentLog.query.filter_by(client_id=client.id).one()
            self.assertEqual(assignment.from_label, "Morgan Manager")
            self.assertEqual(assignment.to_label, "Taylor Responder")
            alert = Notification.query.filter_by(
                notification_type="client_message",
                recipient_role="agency_preparer",
                recipient_id=preparer_id,
            ).one()
            self.assertIsNone(alert.read_at)

        self.web.get("/logout")
        inactive_login = self.web.post("/login/agency", data={"username": "manager", "password": "test-password"})
        self.assertEqual(inactive_login.status_code, 200)
        self.assertIn(b"inactive", inactive_login.data)
        responder_login = self.web.post("/login/agency", data={"username": "taylor", "password": "responder-password"})
        self.assertEqual(responder_login.status_code, 302)
        conversation = self.web.get(f"/agency/messages?client_id={self.client_id}")
        self.assertEqual(conversation.status_code, 200)
        self.assertIn("Necesito una actualización".encode("utf-8"), conversation.data)
        replied = self.web.post(
            "/agency/messages",
            data={"client_id": str(self.client_id), "message": "Estamos revisando su caso."},
        )
        self.assertEqual(replied.status_code, 302)

        self.web.get("/logout")
        self.web.post("/login/client", data={"username": "alexclient", "password": "client-password"})
        client_chat = self.web.get("/client/messages")
        self.assertEqual(client_chat.status_code, 200)
        self.assertIn(b"Taylor Responder", client_chat.data)
        self.assertIn(b"Estamos revisando su caso.", client_chat.data)
        with app.app_context():
            names = [message.sender_label for message in Message.query.order_by(Message.id).all()]
            self.assertEqual(names, ["Alex Client", "Taylor Responder"])


if __name__ == "__main__":
    unittest.main()
