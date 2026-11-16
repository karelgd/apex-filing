import os
import re
import subprocess
from datetime import date, datetime, time, timedelta, timezone
from decimal import InvalidOperation
from pathlib import Path

from flask import abort, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import exists, func
from sqlalchemy.exc import IntegrityError

from employee_payments import payment_amount
from models import AgencyCaseManager, AgencyUser, db


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


def register_zelle_inbox(app, eastern_datetime):
    from zelle_gmail import register_zelle_sync

    register_zelle_sync(app)
    app.config.setdefault("ODAISA_ZELLE_INBOX_ENABLED", os.environ.get("ODAISA_ZELLE_INBOX_ENABLED") == "1")

    def eastern_midnight_utc(day):
        midnight = datetime.combine(day, time.min)
        return midnight + (midnight - eastern_datetime(midnight))

    def filter_values():
        return {key: request.args[key] for key in ("sender", "date_from", "date_to", "amount", "page")
                if key in request.args}

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
        return render_template(
            "zelle_inbox.html", rows=rows[:50], has_next=len(rows) > 50, page=page, filters=filters,
            limited=isinstance(current_user, AgencyCaseManager), eastern_datetime=eastern_datetime,
        )

    @app.post("/agency/zelle/sync")
    @login_required
    def zelle_sync_now():
        if not can_read_zelle_inbox() or not isinstance(current_user, AgencyUser):
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

    @app.post("/agency/zelle/<int:alert_id>/read")
    @login_required
    def zelle_mark_read(alert_id):
        if not can_read_zelle_inbox():
            abort(403)
        visible_alerts(utc_now()).filter_by(id=alert_id).first_or_404()
        if not db.session.get(ZelleDepositRead, (alert_id, current_user.get_id())):
            db.session.add(ZelleDepositRead(alert_id=alert_id, reader=current_user.get_id()))
            try:
                db.session.commit()
            except IntegrityError:
                db.session.rollback()
                if not db.session.get(ZelleDepositRead, (alert_id, current_user.get_id())):
                    raise
        return redirect(url_for("zelle_inbox", **filter_values()), code=303)
