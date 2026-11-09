import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from threading import Barrier
from unittest.mock import patch

from bs4 import BeautifulSoup
from cryptography.fernet import Fernet
from sqlalchemy import inspect, text

with patch.dict(os.environ, {"AUTO_INIT_DB": "0", "DATABASE_URL": "sqlite://"}):
    from app import create_app, ensure_sqlite_schema

from lead_workflow import FOLLOW_UP_DESCRIPTION, process_lead_reminders
from models import (
    Agency, AgencyCaseManager, AgencyUser, Client, CrmAppointment, CrmCase, CrmClientNote,
    CrmInvoice, CrmLead, CrmLeadNote, CrmLeadTask, Notification, SubscriptionTool, db,
)


class OdaisaLeadTest(unittest.TestCase):
    LIST = "/agency/crm/leads"
    INTAKE = "/odaisa/contacto"
    DETAILS = {
        "first_name": "Ana", "middle_name": "Maria", "last_name": "Example",
        "a_number": "001234567", "phone": "(305) 555-0123", "email": "ana@example.com",
        "street_address": "123 Example St", "city": "Miami", "state": "FL", "zip_code": "33101",
    }

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        with patch.dict(os.environ, {
            "SECRET_KEY": "test-only-lead-secret",
            "DATABASE_URL": f"sqlite:///{os.path.join(directory.name, 'test.db').replace(os.sep, '/')}",
            "ODAISA_LEAD_AGENCY_ID": "1",
            "ODAISA_LEAD_ENCRYPTION_KEY": Fernet.generate_key().decode(),
            "UPLOAD_FOLDER": directory.name,
        }):
            self.app = create_app()
        self.app.config["TESTING"] = True
        with self.app.app_context():
            db.create_all()
            crm = SubscriptionTool(name="CRM")
            for number, name in ((1, "Odaisa Test"), (2, "Other Agency")):
                agency = Agency(
                    id=number, agency_name=name, tax_id=f"00-000000{number}", street_address="123 Example St",
                    city="Miami", state="FL", zip_code="33101", ceo_email="owner@example.com",
                    ceo_phone="3055550100", registered_owners="Test Owner",
                )
                agency.subscriptions.append(crm)
                db.session.add(agency)
            for agency_id, username in ((1, "manager"), (1, "second"), (2, "external")):
                manager = AgencyCaseManager(agency_id=agency_id, username=username, full_name=username.title())
                manager.set_password("test-password")
                db.session.add(manager)
            owner = AgencyUser(agency_id=1, username="owner")
            owner.set_password("test-password")
            db.session.add(owner)
            db.session.commit()
            self.manager_id = AgencyCaseManager.query.filter_by(username="manager").one().id
        self.addCleanup(self.dispose_database)

    def dispose_database(self):
        with self.app.app_context():
            db.session.remove()
            db.engine.dispose()

    @staticmethod
    def value(response, name):
        return BeautifulSoup(response.data, "html.parser").find("input", attrs={"name": name})["value"]

    def login(self, username):
        web = self.app.test_client()
        csrf = self.value(web.get("/login/agency"), "csrf_token")
        self.assertEqual(web.post("/login/agency", data={
            "username": username, "password": "test-password", "csrf_token": csrf,
        }).status_code, 302)
        return web, csrf

    def submit_intake(self):
        web = self.app.test_client()
        csrf = self.value(web.get(self.INTAKE), "csrf_token")
        form = web.post(self.INTAKE, data={
            "csrf_token": csrf, "action": "consent", "consent": "y", "topic": "family",
        }, follow_redirects=True)
        intake = BeautifulSoup(form.data, "html.parser")
        self.assertEqual(intake.select_one("[data-progress]")["max"], "6")
        self.assertEqual([step["data-step"] for step in intake.select("[data-step]")], ["2", "3", "4", "5", "6"])
        self.assertFalse(intake.select('input[type="checkbox"]'))
        success = web.post(self.INTAKE, data={
            **self.DETAILS, "csrf_token": csrf, "action": "submit", "submission_id": self.value(form, "submission_id"),
        }, follow_redirects=True)
        self.assertIn(b"Nuestra oficina va a ponerse en contacto.", success.data)
        with self.app.app_context():
            lead = CrmLead.query.one()
            self.assertEqual((lead.tag, lead.agency_id, lead.topic), ("New", 1, "family"))
            self.assertIsNone(lead.assigned_case_manager_id)
            self.assertEqual(Client.query.count(), 0)
            return f"{self.LIST}/{lead.id}"

    def test_marketing_is_disclosed_only_in_bilingual_terms(self):
        web = self.app.test_client()
        spanish = web.get("/odaisa/privacidad?lang=es").data
        english = web.get("/odaisa/privacidad?lang=en").data
        self.assertIn(b"cobrar por mensajes y datos", spanish)
        self.assertIn(b"message and data rates", english)
        self.assertIn(b"se requiere un consentimiento separado", spanish)
        self.assertIn(b"separate consent is required", english)
        self.assertIn(b"<strong>STOP</strong>", spanish)
        self.assertIn(b"<strong>STOP</strong>", english)

    def test_intake_claim_notes_and_conversion(self):
        profile = self.submit_intake()
        manager, csrf = self.login("manager")
        other, other_csrf = self.login("second")
        owner, owner_csrf = self.login("owner")
        external, _ = self.login("external")
        self.assertEqual(external.get(profile).status_code, 403)
        self.assertIn(b"Claim Lead", manager.get(profile).data)
        self.assertIn(b"Unassigned", owner.get(profile).data)
        self.assertEqual(manager.post(profile + "/claim").status_code, 400)
        self.assertEqual(manager.post(profile + "/claim", data={"csrf_token": csrf}).status_code, 302)
        self.assertEqual(other.post(profile + "/claim", data={"csrf_token": other_csrf}).status_code, 409)
        self.assertNotIn(b"001234567", other.get(profile).data)
        self.assertIn(b"Manager", manager.get(self.LIST + "?q=Ana&tag=Assigned").data)
        self.assertEqual(other.post(profile + "/notes", data={"csrf_token": other_csrf}).status_code, 403)
        self.assertEqual(manager.post(profile + "/notes", data={"csrf_token": csrf, "kind": "unknown", "body": "Test"}).status_code, 400)
        self.assertEqual(manager.post(profile + "/notes", data={"csrf_token": csrf, "kind": "call", "body": "Llamada de prueba"}).status_code, 302)
        page = manager.get(profile)
        self.assertEqual(page.headers["Cache-Control"], "no-store")
        self.assertIn(b"Llamada de prueba", page.data)
        self.assertIn(b"collected at least one payment", page.data)
        self.assertNotIn(b"Consentimiento:", page.data)
        self.assertNotIn(b"Servicio:", page.data)
        self.assertIn(b"Llamada de prueba", owner.get(profile).data)
        self.assertEqual(manager.post(profile + "/notes", data={
            "csrf_token": csrf, "kind": "email", "body": "Second note before conversion",
        }).status_code, 302)
        self.assertEqual(manager.post(profile + "/tasks", data={
            "csrf_token": csrf, "title": "Llamar", "description": "A las 5 pm", "due_on": "2099-01-01",
        }).status_code, 302)
        self.assertEqual(owner.post(profile + "/convert", data={"csrf_token": owner_csrf, "payment_confirmed": "yes"}).status_code, 403)
        self.assertEqual(other.post(profile + "/convert", data={"csrf_token": other_csrf, "payment_confirmed": "yes"}).status_code, 403)
        self.assertEqual(manager.post(profile + "/convert", data={"csrf_token": csrf}).status_code, 400)
        with patch("app.postmark_configured", return_value=True), patch("app.send_postmark_email", return_value=(True, "Sent")) as email:
            response = manager.post(profile + "/convert", data={"csrf_token": csrf, "payment_confirmed": "yes"})
            self.assertEqual(response.status_code, 302)
            self.assertEqual(email.call_args.args[0], self.DETAILS["email"])
            self.assertEqual(email.call_count, 1)
            self.assertEqual(manager.post(profile + "/convert", data={"csrf_token": csrf, "payment_confirmed": "yes"}).status_code, 404)
            self.assertEqual(email.call_count, 1)
        client_url = response.location
        self.assertEqual(manager.get(client_url).status_code, 200)
        self.assertEqual(manager.get(profile).status_code, 404)
        self.assertNotIn(b"Ana Maria Example", manager.get(self.LIST).data)
        with self.app.app_context():
            lead = CrmLead.query.one()
            self.assertIsNotNone(lead.consented_at)
            client = Client.query.one()
            self.assertEqual(lead.converted_client_id, client.id)
            self.assertEqual((client.assigned_to_role, client.assigned_to_id), ("agency_case_manager", self.manager_id))
            self.assertEqual((client.full_name, client.a_number, client.street_address), ("Ana Maria Example", "001234567", "123 Example St"))
            self.assertTrue(client.check_password(client.portal_password))
            self.assertEqual([note.kind for note in CrmLeadNote.query.order_by(CrmLeadNote.id)], ["call", "email"])
            self.assertIsNotNone(CrmLeadTask.query.one().cancelled_at)
            for model in (CrmClientNote, CrmCase, CrmInvoice, CrmAppointment):
                self.assertEqual(model.query.count(), 0)
        self.assertEqual(manager.post(client_url + "/notes", data={
            "csrf_token": csrf, "note_text": "Client note after conversion",
        }).status_code, 302)
        client_profile = manager.get(client_url + "?tab=lead-notes")
        self.assertEqual(client_profile.headers["Cache-Control"], "no-store")
        page = BeautifulSoup(client_profile.data, "html.parser")
        self.assertEqual(page.select("[data-crm-tab]")[-1]["data-crm-tab"], "lead-notes")
        lead_notes = page.select_one('[data-crm-panel="lead-notes"].active')
        self.assertEqual([note.get_text(strip=True) for note in lead_notes.select("article p")], [
            "Second note before conversion", "Llamada de prueba",
        ])
        self.assertIsNone(lead_notes.find("form"))
        self.assertNotIn("Client note after conversion", lead_notes.get_text())
        client_notes = page.select_one('[data-crm-panel="notes"]').get_text()
        self.assertIn("Client note after conversion", client_notes)
        self.assertNotIn("Llamada de prueba", client_notes)
        self.assertIn(b"Second note before conversion", owner.get(client_url).data)
        self.assertNotIn(b"Second note before conversion", other.get(client_url).data)
        self.assertEqual(external.get(client_url).status_code, 404)

    def test_client_without_a_lead_has_an_empty_lead_notes_tab(self):
        with self.app.app_context():
            client = Client(agency_id=1, username="existing-client", **self.DETAILS)
            client.set_password("test-password")
            db.session.add(client)
            db.session.commit()
            client_id = client.id
        owner, _ = self.login("owner")
        response = owner.get(f"/agency/crm/clients/{client_id}?tab=lead-notes")
        self.assertEqual(response.status_code, 200)
        page = BeautifulSoup(response.data, "html.parser")
        self.assertIn("Lead Notes", page.select_one('[data-crm-tab="lead-notes"].active').get_text())
        self.assertIn("No lead notes.", page.select_one('[data-crm-panel="lead-notes"].active').get_text())

        with self.app.app_context():
            # SQLite can reuse a deleted client's ID if foreign keys were not enforced.
            old_conversion = db.session.get(Client, client_id).created_at - timedelta(days=1)
            previous_lead = CrmLead(
                agency_id=1, submission_id="previous-lead", name="Previous client", topic="other",
                converted_client_id=client_id, converted_at=old_conversion,
            )
            db.session.add(previous_lead)
            db.session.flush()
            db.session.add(CrmLeadNote(
                lead_id=previous_lead.id, author_label="Manager", kind="call",
                body="Note for the deleted client", created_at=old_conversion - timedelta(days=1),
            ))
            db.session.commit()
        response = owner.get(f"/agency/crm/clients/{client_id}?tab=lead-notes")
        self.assertNotIn(b"Note for the deleted client", response.data)

    def test_two_managers_cannot_claim_the_same_lead(self):
        profile = self.submit_intake()
        users = [self.login("manager"), self.login("second")]
        start = Barrier(2)

        def claim(session):
            web, csrf = session
            start.wait(timeout=5)
            return web.post(profile + "/claim", data={"csrf_token": csrf}).status_code

        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sorted(pool.map(claim, users)), [302, 409])

    def test_note_history_shows_an_icon_for_each_type(self):
        profile = self.submit_intake()
        manager, csrf = self.login("manager")
        self.assertEqual(manager.post(profile + "/claim", data={"csrf_token": csrf}).status_code, 302)
        types = {
            "call": ("Call", "\U0001f4de"),
            "text": ("Text message", "\U0001f4ac"),
            "whatsapp": ("WhatsApp", "\U0001f4f2"),
            "email": ("Email", "\U0001f4e7"),
        }
        for kind in types:
            response = manager.post(profile + "/notes", data={"csrf_token": csrf, "kind": kind, "body": "Test note"})
            self.assertEqual(response.status_code, 302)
        page = BeautifulSoup(manager.get(profile).data, "html.parser")
        icons = page.select('#notes article strong > [aria-hidden="true"]')
        self.assertCountEqual(
            [(icon.next_sibling.strip(), icon.get_text()) for icon in icons],
            types.values(),
        )

    def test_manual_tasks_and_automatic_followups(self):
        profile = self.submit_intake()
        manager, csrf = self.login("manager")
        self.assertEqual(manager.post(profile + "/claim", data={"csrf_token": csrf}).status_code, 302)
        base = date(2026, 11, 9)
        with self.app.app_context():
            lead = CrmLead.query.one()
            lead.follow_up_from = base
            db.session.commit()
        self.assertEqual(manager.post(profile + "/tasks", data={
            "csrf_token": csrf, "title": "Llamar", "description": "A las 5 pm", "due_on": "2099-01-01",
        }).status_code, 302)
        with self.app.app_context():
            manual_id = CrmLeadTask.query.one().id

        def run(days):
            day = base + timedelta(days=days)
            return process_lead_reminders(1, day, datetime.combine(day, datetime.min.time()))

        with self.app.app_context():
            self.assertEqual(run(1), (0, 0, 0))
            self.assertEqual(run(2), (1, 1, 0))
            self.assertEqual(run(2), (0, 0, 0))
            first = CrmLeadTask.query.filter_by(is_automatic=True).one()
            self.assertEqual((first.title, first.description), ("Follow-up", FOLLOW_UP_DESCRIPTION))
            first_alert_id = Notification.query.one().id
        self.assertIn(b"View lead tasks", manager.get(f"/agency/notifications/{first_alert_id}").data)
        tasks = manager.get(profile + "?tab=tasks").data
        self.assertIn(b"Created by: System", tasks)
        self.assertIn(b"Created by: Case Manager: Manager (manager)", tasks)
        with patch("app.eastern_datetime", return_value=datetime(2026, 11, 12)):
            self.assertEqual(manager.post(profile + "/notes", data={
                "csrf_token": csrf, "kind": "whatsapp", "body": "Se envio mensaje.",
            }).status_code, 302)
        with self.app.app_context():
            self.assertIsNotNone(CrmLeadTask.query.filter_by(is_automatic=True).one().cancelled_at)
            self.assertEqual(run(4), (0, 0, 0))
            self.assertEqual(run(5), (1, 1, 0))
            self.assertEqual(run(7), (1, 1, 0))
            self.assertEqual(run(9), (1, 1, 0))
            self.assertEqual(run(10), (0, 0, 0))
            self.assertEqual(run(11), (0, 0, 1))
            self.assertEqual(run(12), (0, 0, 0))
            self.assertEqual(CrmLead.query.one().tag, "Not interested")
            self.assertEqual(Notification.query.filter_by(recipient_id=self.manager_id, notification_type="lead_task_due").count(), 4)
            self.assertIsNone(db.session.get(CrmLeadTask, manual_id).cancelled_at)

        task_url = f"{profile}/tasks/{manual_id}"
        self.assertEqual(manager.post(task_url, data={
            "csrf_token": csrf, "action": "edit", "title": "Llamar de nuevo",
            "description": "A las 6 pm", "due_on": "2000-01-01",
        }).status_code, 302)
        self.assertIn(b"Llamar de nuevo", manager.get(profile + "?tab=tasks").data)
        self.assertEqual(manager.post(task_url, data={"csrf_token": csrf, "action": "complete"}).status_code, 302)
        completed = manager.get(profile + "?tab=tasks").data
        self.assertIn(b"Completed tasks", completed)
        self.assertIn(b"Created by: Case Manager: Manager (manager)", completed)
        with self.app.app_context():
            task = db.session.get(CrmLeadTask, manual_id)
            self.assertEqual((task.description, task.due_on), ("A las 6 pm", date(2000, 1, 1)))
            self.assertIsNotNone(task.alerted_at)
            self.assertIsNotNone(task.completed_at)
        self.assertEqual(self.app.test_cli_runner().invoke(args=["process-lead-reminders"]).exit_code, 0)

    def test_migration_preserves_legacy_leads_and_legacy_conversion_requires_details(self):
        with self.app.app_context():
            legacy = CrmLead(agency_id=1, submission_id="legacy", name="Legacy Lead", topic="other")
            db.session.add(legacy)
            db.session.flush()
            db.session.add(CrmLeadTask(
                lead_id=legacy.id, title="Legacy Task", description="Test",
                due_on=date(2026, 11, 9), created_at=datetime(2026, 11, 8),
            ))
            db.session.add(CrmLeadTask(
                lead_id=legacy.id, is_automatic=True, title="Seguimiento",
                description="Haz un seguimiento a este lead hoy, empezando por llamar, dejar mensaje de voz, y luego mandar mensaje de texto.",
                due_on=date(2026, 11, 9), created_at=datetime(2026, 11, 8),
            ))
            db.session.commit()
            profile = f"{self.LIST}/{legacy.id}"
            for table, columns in {
                "crm_lead": ("assigned_case_manager_id", "assigned_at", "follow_up_from", "follow_up_count", "converted_at", "converted_client_id"),
                "notification": ("lead_id",),
                "crm_lead_task": ("created_by_label",),
            }.items():
                previous = ", ".join(column["name"] for column in inspect(db.engine).get_columns(table) if column["name"] not in columns)
                db.session.execute(text(f"CREATE TABLE old_{table} AS SELECT {previous} FROM {table}"))
                db.session.execute(text(f"DROP TABLE {table}"))
                db.session.execute(text(f"ALTER TABLE old_{table} RENAME TO {table}"))
            db.session.commit()
            ensure_sqlite_schema()
            ensure_sqlite_schema()
            self.assertIn("lead_id", {column["name"] for column in inspect(db.engine).get_columns("notification")})
            self.assertEqual((CrmLead.query.one().name, CrmLead.query.one().follow_up_count), ("Legacy Lead", 0))
            self.assertEqual(CrmLeadTask.query.filter_by(is_automatic=False).one().creator_label, "Not recorded")
            follow_up = CrmLeadTask.query.filter_by(is_automatic=True).one()
            self.assertEqual((follow_up.title, follow_up.description), ("Follow-up", FOLLOW_UP_DESCRIPTION))
        manager, csrf = self.login("manager")
        manager.post(profile + "/claim", data={"csrf_token": csrf})
        result = manager.post(profile + "/convert", data={"csrf_token": csrf, "payment_confirmed": "yes"})
        self.assertEqual(result.status_code, 422)
        with patch("app.notify_client_welcome", return_value=True):
            result = manager.post(profile + "/convert", data={
                "csrf_token": csrf, "payment_confirmed": "yes", **self.DETAILS,
            })
        self.assertEqual(result.status_code, 302)
