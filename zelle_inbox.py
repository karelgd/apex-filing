import os
import re
import subprocess
from datetime import date, datetime, time, timedelta, timezone
from decimal import InvalidOperation
from pathlib import Path

from flask import abort, current_app, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import exists, func, or_
from sqlalchemy.exc import IntegrityError

from employee_payments import payment_amount
from models import AgencyCaseManager, AgencyUser, Client, CrmCase, CrmInvoice, CrmInvoiceActivity, db


class ZelleDepositAlert(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    agency_id = db.Column(db.Integer, db.ForeignKey("agency.id", ondelete="CASCADE"), nullable=False)
    source_message_id = db.Column(db.String(160), nullable=False)
    bank_reference = db.Column(db.String(160))
    received_at = db.Column(db.DateTime, nullable=False)
    transaction_at = db.Column(db.DateTime)
    sender_name = db.Column(db.String(200), nullable=False)
    amount = db.Column(db.Numeric(12, 2), nullable=False)
    memo = db.Column(db.String(500))
    __table_args__ = (
        db.UniqueConstraint("agency_id", "source_message_id"),
        db.UniqueConstraint("agency_id", "bank_reference"),
        db.Index("ix_zelle_deposit_alert_agency_received", "agency_id", "received_at"),
        db.CheckConstraint("amount > 0"),
    )


class ZelleDepositRead(db.Model):
    alert_id = db.Column(db.Integer, db.ForeignKey("zelle_deposit_alert.id", ondelete="CASCADE"), primary_key=True)
    reader = db.Column(db.String(80), primary_key=True)


class ZellePaymentLink(db.Model):
    alert_id = db.Column(db.Integer, db.ForeignKey("zelle_deposit_alert.id", ondelete="CASCADE"), primary_key=True)
    activity_id = db.Column(db.Integer, db.ForeignKey("crm_invoice_activity.id", ondelete="SET NULL"))
    invoice_number = db.Column(db.String(32), nullable=False)


def can_read_zelle_inbox():
    agency_id = current_app.config.get("ODAISA_LEAD_AGENCY_ID")
    return bool(
        current_app.config.get("ODAISA_ZELLE_INBOX_ENABLED") and agency_id
        and current_user.is_authenticated and current_user.is_active
        and isinstance(current_user, (AgencyUser, AgencyCaseManager))
        and current_user.agency_id == agency_id
    )


def visible_alerts(now):
    alerts = ZelleDepositAlert.query.filter_by(agency_id=current_user.agency_id)
    if isinstance(current_user, AgencyCaseManager):
        alerts = alerts.filter(ZelleDepositAlert.received_at >= now - timedelta(hours=72))
    return alerts


def read_marker():
    return exists().where(
        ZelleDepositRead.alert_id == ZelleDepositAlert.id,
        ZelleDepositRead.reader == current_user.get_id(),
    )


def utc_now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def register_zelle_inbox(app, eastern_datetime, *, can_use_crm, current_user_label, recalc_invoice, log_client):
    from zelle_gmail import register_zelle_sync

    register_zelle_sync(app)
    app.config.setdefault("ODAISA_ZELLE_INBOX_ENABLED", os.environ.get("ODAISA_ZELLE_INBOX_ENABLED") == "1")

    def eastern_midnight_utc(day):
        midnight = datetime.combine(day, time.min)
        return midnight + (midnight - eastern_datetime(midnight))

    def filter_values():
        return {key: request.args[key] for key in ("sender", "date_from", "date_to", "amount", "page")
                if key in request.args}

    def can_link():
        return can_read_zelle_inbox() and can_use_crm(current_user.agency)

    def require_linkable_alert(alert_id):
        if not can_link():
            abort(403)
        return visible_alerts(utc_now()).filter_by(id=alert_id).first_or_404()

    def eligible_invoices(client_id):
        agency_id = current_user.agency_id
        return CrmInvoice.query.join(CrmInvoice.client).join(CrmInvoice.case).filter(
            CrmInvoice.agency_id == agency_id, CrmInvoice.client_id == client_id,
            Client.agency_id == agency_id, CrmCase.agency_id == agency_id,
            CrmCase.client_id == client_id, CrmInvoice.status != "Void",
        )

    @app.get("/agency/zelle/clients")
    @login_required
    def zelle_search_clients():
        if not can_link():
            abort(403)
        search = request.args.get("q", "").strip()[:100]
        if len(search) < 2:
            return jsonify([])
        clients = Client.query.filter_by(agency_id=current_user.agency_id)
        for token in search.split():
            clients_by_name = or_(*(field.icontains(token, autoescape=True) for field in (
                Client.first_name, Client.middle_name, Client.last_name, Client.email,
            )))
            if token == search:
                clients_by_name = or_(clients_by_name, Client.a_number.icontains(token, autoescape=True),
                                      Client.phone.icontains(token, autoescape=True))
            clients = clients.filter(clients_by_name)
        digits = re.sub(r"[^0-9]", "", search)
        if len(digits) >= 2 and re.fullmatch(r"[Aa0-9 +().-]+", search):
            def normalized_digits(field):
                for separator in "Aa +().-":
                    field = func.replace(field, separator, "")
                return field
            clients = Client.query.filter(
                Client.agency_id == current_user.agency_id,
                or_(normalized_digits(Client.a_number).contains(digits), normalized_digits(Client.phone).contains(digits)),
            )
        response = jsonify([
            {"id": client.id, "name": client.full_name,
             "phone_suffix": re.sub(r"\D", "", client.phone or "")[-4:],
             "alien_suffix": re.sub(r"\D", "", client.a_number or "")[-4:]}
            for client in clients.order_by(Client.last_name, Client.first_name, Client.id).limit(15)
        ])
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/agency/zelle/clients/<int:client_id>/invoices")
    @login_required
    def zelle_client_invoices(client_id):
        if not can_link():
            abort(403)
        Client.query.filter_by(id=client_id, agency_id=current_user.agency_id).first_or_404()
        response = jsonify([
            {"id": invoice.id, "number": invoice.invoice_number, "case": case_title,
             "balance": f"{invoice.balance_due:.2f}"}
            for invoice, case_title in eligible_invoices(client_id).add_columns(CrmCase.title)
            .order_by(CrmInvoice.issue_date.desc(), CrmInvoice.id.desc())
        ])
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.post("/agency/zelle/<int:alert_id>/link")
    @login_required
    def zelle_link_payment(alert_id):
        alert = require_linkable_alert(alert_id)
        back = url_for("zelle_inbox", **filter_values())
        if db.session.get(ZellePaymentLink, alert.id):
            flash("This Zelle alert has already been linked.", "warning")
            return redirect(back, code=303)
        client_id = request.form.get("client_id", type=int)
        invoice_id = request.form.get("invoice_id", type=int)
        if not all(value is not None and 0 < value <= 2_147_483_647 for value in (client_id, invoice_id)):
            abort(400)
        invoice = eligible_invoices(client_id).filter(CrmInvoice.id == invoice_id).with_for_update().first_or_404()
        now = utc_now()
        activity = CrmInvoiceActivity(
            agency_id=alert.agency_id, invoice_id=invoice.id, activity_type="Payment",
            payment_method="Zelle", amount=alert.amount, activity_date=eastern_datetime(now).date(),
            created_at=now, recorded_by_id=current_user.get_id(), recorded_by_label=current_user_label(),
            description=f"Zelle sender: {alert.sender_name}",
        )
        link = ZellePaymentLink(alert_id=alert.id, invoice_number=invoice.invoice_number)
        try:
            db.session.add(link)
            db.session.flush()
            db.session.add(activity)
            db.session.flush()
            link.activity_id = activity.id
            recalc_invoice(invoice)
            log_client(invoice.client, "Invoice payment linked from Zelle",
                       f"{invoice.invoice_number}: ${alert.amount:.2f} from {alert.sender_name} (Zelle alert #{alert.id})")
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            if not db.session.get(ZellePaymentLink, alert_id):
                raise
            flash("This Zelle alert has already been linked.", "warning")
        else:
            flash(f"Zelle payment linked to invoice {link.invoice_number}.", "success")
        return redirect(back, code=303)

    @app.context_processor
    def zelle_sidebar():
        if not can_read_zelle_inbox():
            return {}
        return {"show_zelle_inbox": True, "unread_zelle_count": visible_alerts(utc_now()).filter(~read_marker()).count()}

    @app.get("/agency/zelle")
    @login_required
    def zelle_inbox():
        if not can_read_zelle_inbox():
            abort(403)
        filters = filter_values()
        alerts = visible_alerts(utc_now())
        activity_time = func.coalesce(ZelleDepositAlert.transaction_at, ZelleDepositAlert.received_at)
        try:
            sender = filters.get("sender", "").strip()
            if sender:
                alerts = alerts.filter(ZelleDepositAlert.sender_name.icontains(sender, autoescape=True))
            start = date.fromisoformat(filters["date_from"]) if filters.get("date_from") else None
            end = date.fromisoformat(filters["date_to"]) if filters.get("date_to") else None
            if start and end and start > end:
                raise ValueError
            if start:
                alerts = alerts.filter(activity_time >= eastern_midnight_utc(start))
            if end:
                alerts = alerts.filter(activity_time < eastern_midnight_utc(end + timedelta(days=1)))
            if filters.get("amount"):
                alerts = alerts.filter(ZelleDepositAlert.amount == payment_amount(filters["amount"]))
        except (ValueError, InvalidOperation, OverflowError):
            flash("Enter a valid date range and amount.", "warning")
            alerts = alerts.filter(db.false())
        page = min(1000000, max(1, request.args.get("page", 1, type=int)))
        filters.pop("page", None)
        results = alerts.add_columns(read_marker().label("is_read")).order_by(
            ZelleDepositAlert.received_at.desc(), ZelleDepositAlert.id.desc()
        )
        # SQLAlchemy's model pagination would discard the per-user read column.
        rows = results.offset((page - 1) * 50).limit(51).all()
        rows, has_next = rows[:50], len(rows) > 50
        linked = dict(db.session.query(ZellePaymentLink.alert_id, ZellePaymentLink.invoice_number)
                      .filter(ZellePaymentLink.alert_id.in_([alert.id for alert, _ in rows])).all()) if rows else {}
        return render_template(
            "zelle_inbox.html", rows=rows, has_next=has_next, page=page, filters=filters,
            linked=linked, can_link_payment=can_link(),
            limited=isinstance(current_user, AgencyCaseManager), eastern_datetime=eastern_datetime,
        )

    @app.post("/agency/zelle/sync")
    @login_required
    def zelle_sync_now():
        if not can_read_zelle_inbox():
            abort(403)
        runner = Path(__file__).resolve().parent / "tools" / "run_odaisa_zelle_sync.py"
        python = Path.home() / ".virtualenvs" / "apex-filing" / "bin" / "python"
        try:
            result = subprocess.run(
                [str(python), str(runner), "--apply", "--manual"],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=25,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            current_app.logger.warning("Manual Zelle sync could not finish: %s", type(exc).__name__)
            flash("Zelle sync did not finish. Try again in a minute.", "warning")
        else:
            saved = re.search(r"^Saved (\d+) new incoming Zelle alerts;", result.stdout, re.MULTILINE)
            if result.returncode == 0 and saved:
                count = int(saved.group(1))
                if isinstance(current_user, AgencyCaseManager):
                    message = "Zelle sync completed."
                else:
                    message = ("No new Zelle alerts." if count == 0 else
                               f"Imported {count} new Zelle {'alert' if count == 1 else 'alerts'}.")
                flash(message, "success")
            elif result.returncode == 75:
                flash("A Zelle sync is running or just ran. Try again in a minute.", "info")
            else:
                current_app.logger.warning("Manual Zelle sync returned code %s without a result", result.returncode)
                flash("Zelle sync could not be completed. Try again later.", "warning")
        filters = filter_values()
        filters.pop("page", None)
        return redirect(url_for("zelle_inbox", **filters), code=303)

    def mark_alerts_read(alert_ids):
        for attempt in range(2):
            unread = visible_alerts(utc_now()).filter(
                ZelleDepositAlert.id.in_(alert_ids), ~read_marker()
            ).with_entities(ZelleDepositAlert.id)
            db.session.add_all([
                ZelleDepositRead(alert_id=alert_id, reader=current_user.get_id())
                for (alert_id,) in unread
            ])
            try:
                db.session.commit()
                return
            except IntegrityError:
                db.session.rollback()
                if attempt:
                    raise

    @app.post("/agency/zelle/<int:alert_id>/read")
    @login_required
    def zelle_mark_read(alert_id):
        if not can_read_zelle_inbox():
            abort(403)
        visible_alerts(utc_now()).filter_by(id=alert_id).first_or_404()
        mark_alerts_read([alert_id])
        return redirect(url_for("zelle_inbox", **filter_values()), code=303)

    @app.post("/agency/zelle/read")
    @login_required
    def zelle_mark_selected_read():
        if not can_read_zelle_inbox():
            abort(403)
        selected = request.form.getlist("alert_id")
        if len(selected) > 50 or any(not re.fullmatch(r"[1-9][0-9]{0,9}", value) for value in selected):
            abort(400)
        if selected:
            mark_alerts_read({int(value) for value in selected})
        else:
            flash("Select at least one unread Zelle alert.", "info")
        return redirect(url_for("zelle_inbox", **filter_values()), code=303)
