import csv
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

import click
from flask import abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

from models import Agency, db


class PaymentEmployee(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    agency_id = db.Column(db.Integer, db.ForeignKey("agency.id", ondelete="CASCADE"), nullable=False)
    name = db.Column(db.String(160), nullable=False)
    name_key = db.Column(db.String(320), nullable=False)
    __table_args__ = (db.UniqueConstraint("agency_id", "name_key"),)


class EmployeePayment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    agency_id = db.Column(db.Integer, db.ForeignKey("agency.id", ondelete="CASCADE"), nullable=False, index=True)
    employee_id = db.Column(db.Integer, db.ForeignKey("payment_employee.id", ondelete="CASCADE"), nullable=False)
    paid_on = db.Column(db.Date, nullable=False)
    amount = db.Column(db.Numeric(12, 2), nullable=False)
    method = db.Column(db.String(32), nullable=False)
    note = db.Column(db.Text, nullable=False, default="")
    source_reference = db.Column(db.String(100))
    employee = db.relationship(PaymentEmployee)
    __table_args__ = (db.UniqueConstraint("agency_id", "source_reference"),)


METHODS = ("Zelle", "Cash", "Check", "ACH", "Wire", "Stripe", "Other")
ODAISA_PAYEES = {
    "anibal rodriguez": "Anibal Rodriguez",
    "marlen garces": "Marlen Garces",
    "yarenmis mena": "Yarenmis Mena",
    "yarenmis oficina": "Yarenmis Mena",
    "jorge pozo": "Jorge Pozo",
    "jorge pozo oficina": "Jorge Pozo",
    "kendry": "Kendry Gonzalez",
    "kendry gonzalez": "Kendry Gonzalez",
    "jose carrilo": "Jose Carrillo",
    "jose carrillo": "Jose Carrillo",
    "lisandra martinez": "Lisandra Martinez",
    "lizandra oficina": "Lisandra Martinez",
    "keily gonzalez": "Keily Gonzalez",
    "keily usa": "Keily Gonzalez",
    "kg strategic solutions": "KG Strategic Solutions LLC",
    "kg strategic solutions llc": "KG Strategic Solutions LLC",
    "ana beatriz": "Ana Beatriz",
    "betty oficina": "Ana Beatriz",
}


def payment_employee(agency_id, name):
    name = " ".join(name.split())
    key = name.casefold()
    employee = PaymentEmployee.query.filter_by(agency_id=agency_id, name_key=key).first()
    if employee is None:
        employee = PaymentEmployee(agency_id=agency_id, name=name, name_key=key)
        db.session.add(employee)
    return employee


def payment_amount(value):
    amount = Decimal(value)
    if not amount.is_finite() or not 0 < amount <= Decimal("9999999999.99") or amount != amount.quantize(Decimal(".01")):
        raise ValueError("Enter a positive amount with up to two decimal places.")
    return amount


def read_odaisa_payments(handle):
    rows = csv.reader(handle)
    for row in rows:
        if row[:3] == ["Date", "Description", "Amount"]:
            break
    else:
        raise ValueError("Bank transaction header was not found.")
    matches = {}
    for row in rows:
        if len(row) < 4 or not row[-2].strip().startswith("-"):
            continue
        # The bank export sometimes leaves quotes inside memo text unescaped.
        description = ",".join(row[1:-2]).strip().rstrip('"')
        match = re.fullmatch(r"Zelle payment to (.+?)(?:\s+for\s+.*?)?\s*;?\s+Conf#\s*(\w+)\s*", description, re.I)
        if not match:
            continue
        payee = ODAISA_PAYEES.get(match[1].strip().casefold())
        if not payee:
            continue
        paid_on = datetime.strptime(row[0], "%m/%d/%Y").date()
        if not date(2026, 1, 1) <= paid_on <= date(2026, 11, 16):
            continue
        reference = "zelle:" + match[2].upper()
        entry = (payee, paid_on, payment_amount(row[-2].strip().replace(",", "")[1:]))
        if reference in matches and matches[reference] != entry:
            raise ValueError("Conflicting rows share a Zelle confirmation ID.")
        matches[reference] = entry
    return matches


def register_employee_payments(app, can_use_crm, is_agency_owner):
    @app.route("/agency/crm/reports/employee-payments", methods=["GET", "POST"])
    @app.route("/agency/crm/reports/employee-payments/<int:payment_id>/edit", methods=["GET", "POST"])
    @login_required
    def crm_employee_payments(payment_id=None):
        if current_user.role != "agency" or not can_use_crm(current_user.agency):
            abort(403)
        owner = is_agency_owner()
        if (payment_id is not None or request.method == "POST") and not owner:
            abort(403)
        agency_id = current_user.agency_id
        query = EmployeePayment.query.filter_by(agency_id=agency_id)
        editing = query.filter_by(id=payment_id).first_or_404() if payment_id is not None else None
        values = request.form
        if request.method == "POST":
            try:
                paid_on = date.fromisoformat(values.get("paid_on", ""))
                amount = payment_amount(values.get("amount", ""))
                method = values.get("method")
                note = values.get("note", "").strip()
                if method not in METHODS or len(note) > 2000:
                    raise ValueError
                if values.get("paid_to") == "new":
                    name = " ".join(values.get("employee_name", "").split())
                    if not name or len(name) > 160:
                        raise ValueError
                    employee = payment_employee(agency_id, name)
                else:
                    employee = PaymentEmployee.query.filter_by(
                        agency_id=agency_id, id=values.get("paid_to", type=int)
                    ).first()
                    if employee is None:
                        raise ValueError
                payment = editing or EmployeePayment(agency_id=agency_id)
                payment.employee = employee
                payment.paid_on, payment.amount, payment.method, payment.note = paid_on, amount, method, note
                db.session.add(payment)
                db.session.commit()
                flash("Employee payment saved.", "success")
                return redirect(url_for("crm_employee_payments"), code=303)
            except (ValueError, InvalidOperation, IntegrityError):
                db.session.rollback()
                flash("Enter a valid employee, date, amount, and method. Notes may contain up to 2,000 characters.", "warning")
        employee_id = request.args.get("employee_id", type=int)
        if employee_id:
            query = query.filter_by(employee_id=employee_id)
        date_filters = {}
        try:
            for key in ("date_from", "date_to"):
                raw = request.args.get(key, "")
                if raw:
                    date_filters[key] = date.fromisoformat(raw)
            if date_filters.get("date_from", date.min) > date_filters.get("date_to", date.max):
                raise ValueError
        except ValueError:
            flash("Enter a valid date range.", "warning")
            query = query.filter(db.false())
        if "date_from" in date_filters:
            query = query.filter(EmployeePayment.paid_on >= date_filters["date_from"])
        if "date_to" in date_filters:
            query = query.filter(EmployeePayment.paid_on <= date_filters["date_to"])
        return render_template(
            "crm_employee_payments.html", owner=owner, editing=editing, methods=METHODS,
            employees=PaymentEmployee.query.filter_by(agency_id=agency_id).order_by(PaymentEmployee.name_key).all(),
            rows=query.order_by(EmployeePayment.paid_on.desc(), EmployeePayment.id.desc()).all(),
            total=query.with_entities(func.sum(EmployeePayment.amount)).scalar() or Decimal(0),
            employee_id=employee_id, values=values,
        )

    @app.cli.command("import-odaisa-employee-payments")
    @click.argument("statement", type=click.File("r", encoding="utf-8-sig"))
    @click.option("--apply", is_flag=True, help="Write verified matches; otherwise preview only.")
    def import_odaisa_employee_payments(statement, apply):
        agency_id = app.config.get("ODAISA_LEAD_AGENCY_ID")
        agency = db.session.get(Agency, agency_id) if agency_id else None
        if agency is None or "odaisa" not in agency.agency_name.casefold() or not can_use_crm(agency):
            raise click.ClickException("Configure ODAISA_LEAD_AGENCY_ID for the Odaisa agency with CRM access.")
        try:
            matches = read_odaisa_payments(statement)
        except (ValueError, InvalidOperation, csv.Error) as error:
            raise click.ClickException(str(error)) from error
        total = sum(entry[2] for entry in matches.values())
        if len(matches) != 108 or total != Decimal("64257.60"):
            raise click.ClickException(f"Expected 108 verified payments totaling $64,257.60; found {len(matches)} totaling ${total:,.2f}.")
        existing = {
            ref for (ref,) in db.session.query(EmployeePayment.source_reference).filter_by(agency_id=agency_id)
            if ref
        }
        pending = {ref: entry for ref, entry in matches.items() if ref not in existing}
        if apply:
            employees = {name: payment_employee(agency_id, name) for name in sorted(set(ODAISA_PAYEES.values()))}
            for ref, (name, paid_on, amount) in pending.items():
                db.session.add(EmployeePayment(
                    agency_id=agency_id, employee=employees[name], paid_on=paid_on,
                    amount=amount, method="Zelle", source_reference=ref,
                ))
            db.session.commit()
        action = "Imported" if apply else "Ready to import"
        click.echo(f"{agency.agency_name}: {action.lower()} {len(pending)} payments; {len(matches) - len(pending)} already recorded. Verified source total: ${total:,.2f}.")
