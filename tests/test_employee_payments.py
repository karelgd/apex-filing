import io
import os
import unittest
from decimal import Decimal

from flask import Flask
from flask_login import LoginManager, UserMixin, current_user
from flask_wtf.csrf import CSRFProtect
from jinja2 import ChoiceLoader, DictLoader

from employee_payments import EmployeePayment, PaymentEmployee, read_odaisa_payments, register_employee_payments
from models import Agency, db


class ReportUser(UserMixin):
    role = "agency"
    agency = True

    def __init__(self, identity):
        self.id = identity
        self.agency_id = 2 if identity == "other" else 1


class EmployeePaymentsTest(unittest.TestCase):
    def setUp(self):
        app = Flask(__name__, template_folder="../templates")
        app.config.update(SECRET_KEY="test", TESTING=True, SQLALCHEMY_DATABASE_URI="sqlite://",
                          WTF_CSRF_ENABLED=False, ODAISA_LEAD_AGENCY_ID=1)
        app.jinja_loader = ChoiceLoader([DictLoader({"base.html": "{% block content %}{% endblock %}"}), app.jinja_loader])
        app.add_url_rule("/reports", "crm_reports", lambda: "")
        CSRFProtect(app)
        db.init_app(app)
        login = LoginManager(app)
        login.user_loader(ReportUser)
        register_employee_payments(app, bool, lambda: current_user.id != "staff")
        self.app = app
        self.client = app.test_client()
        self.path = "/agency/crm/reports/employee-payments"
        with app.app_context():
            db.create_all()

    def login(self, identity):
        with self.client.session_transaction() as session:
            session["_user_id"] = identity

    def test_manual_filters_edit_and_agency_isolation(self):
        self.login("owner")
        payment = dict(paid_to="new", employee_name="Test Payee", paid_on="2026-05-02",
                       amount="123.45", method="Check", note="Test note")
        self.assertEqual(self.client.post(self.path, data=payment).status_code, 303)
        self.assertIn(b"Total Paid: $123.45", self.client.get(self.path + "?date_from=2026-05-02&date_to=2026-05-02").data)
        self.assertIn(b"Total Paid: $0.00", self.client.get(self.path + "?date_to=2026-05-01").data)
        self.assertIn(b"Total Paid: $0.00", self.client.get(self.path + "?employee_id=999").data)
        self.assertEqual(self.client.get(self.path + "/1/edit").status_code, 200)
        self.assertEqual(self.client.post(self.path + "/1/edit", data=dict(payment, amount="150.00")).status_code, 303)
        self.login("staff")
        page = self.client.get(self.path)
        self.assertIn(b"Total Paid: $150.00", page.data)
        self.assertNotIn(b"Add Payment", page.data)
        self.assertEqual(self.client.post(self.path, data=payment).status_code, 403)
        self.assertEqual(self.client.get(self.path + "/1/edit").status_code, 403)
        self.login("other")
        self.assertNotIn(b"Test Payee", self.client.get(self.path).data)
        self.assertEqual(self.client.post(self.path + "/1/edit", data=payment).status_code, 404)

    def test_parser_ignores_incoming_checks_and_duplicate_references(self):
        line = '01/12/2026,"Zelle payment to Keily USA for "Commission"; Conf# abc","-100.00","500.00"\n'
        source = io.StringIO('Date,Description,Amount,Running Bal.\n' + line + line
                             + '01/12/2026,Zelle payment from Keily USA Conf# def,200.00,700.00\n'
                             + '01/12/2026,Check 1045,-150.00,550.00\n')
        result = read_odaisa_payments(source)
        self.assertEqual(list(result), ["zelle:ABC"])
        self.assertEqual(result["zelle:ABC"][0], "Keily Gonzalez")
        self.assertEqual(result["zelle:ABC"][2], Decimal("100.00"))

    @unittest.skipUnless(os.environ.get("ODAISA_TEST_STATEMENT"), "Requires the private bank export")
    def test_actual_import_is_idempotent(self):
        with self.app.app_context():
            db.session.add(Agency(id=1, agency_name="Odaisa", tax_id="test", street_address="test",
                                  city="test", state="FL", zip_code="12345", ceo_email="test@example.com",
                                  ceo_phone="test", registered_owners="test"))
            db.session.commit()
        args = ["import-odaisa-employee-payments", os.environ["ODAISA_TEST_STATEMENT"]]
        runner = self.app.test_cli_runner()
        for extra, expected in [([], "ready to import 108"), (["--apply"], "imported 108"),
                                (["--apply"], "imported 0")]:
            result = runner.invoke(args=args + extra)
            self.assertEqual(result.exit_code, 0, repr(result.exception))
            self.assertIn(expected, result.output)
        with self.app.app_context():
            self.assertEqual(EmployeePayment.query.count(), 108)
            self.assertEqual(sum(p.amount for p in EmployeePayment.query.all()), Decimal("64257.60"))
            for name, count, total in [
                ("Keily Gonzalez", 5, "1204.25"),
                ("KG Strategic Solutions LLC", 33, "24521.75"),
            ]:
                rows = EmployeePayment.query.join(PaymentEmployee).filter(PaymentEmployee.name == name).all()
                self.assertEqual(len(rows), count)
                self.assertEqual(sum(row.amount for row in rows), Decimal(total))
