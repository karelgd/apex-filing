"""Agency-scoped agreements with fixed snapshots and email-link typed signatures."""

import hashlib
import io
import json
import os
import secrets
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from functools import wraps
from html import escape
from urllib.parse import urlsplit

from flask import abort, current_app, flash, redirect, render_template, request, send_file, session, url_for
from flask_login import current_user
from sqlalchemy import func, or_, update

from models import AgencyCaseManager, AgencyUser, Client, CrmCase, CrmInvoice, CrmInvoiceActivity, Notification, db


CEO = "Karel Gonzalez"
CONSENT_VERSION = "2026-11-18-v2-inline"
DISCLOSURE = {
    "en": (
        "Electronic records and signature consent applies only to this agreement and its completion record. "
        "You may decline electronic signing or withdraw consent before signing by choosing Decline, "
        "or contacting the agency at the email/phone shown in this agreement. Request paper signing, "
        "a paper copy at no charge, or updates to your contact email from the same office. "
        "Declining or withdrawing does not sign this agreement or incur an electronic-signing fee; "
        "the office will arrange a paper alternative. Withdrawal after signing does not undo an executed agreement. "
        "You need an internet-connected device, current browser, email access, and a PDF reader "
        "with the ability to save or print PDFs. Review the agreement displayed on this page before consenting. "
        "Save a copy for your records. Contact the office if these requirements change or access fails."
    ),
    "es": (
        "El consentimiento para documentos y firmas electrónicas se aplica solo a este acuerdo y su constancia final. "
        "Puede rechazar la firma electrónica o retirar su consentimiento antes de firmar seleccionando Rechazar, "
        "o contactando a la oficina por el correo/teléfono indicado en el acuerdo. Solicite en esa oficina "
        "firma en papel, una copia en papel sin costo o cambios en su correo de contacto. Rechazar o retirar "
        "no firma este acuerdo ni genera cargos por firma electrónica; la oficina coordinará una alternativa en papel. "
        "Retirar el consentimiento después de firmar no anula un acuerdo ya firmado. "
        "Necesita un dispositivo con internet, navegador actualizado, acceso a su correo y un lector PDF "
        "que permita guardar o imprimir archivos. Revise el acuerdo mostrado en esta página antes de consentir. "
        "Guarde una copia. Contacte a la oficina si cambian estos requisitos o no puede acceder."
    ),
}
COPY = {
    "en": {
        "title": "Agreement signature", "download": "Save / print agreement PDF",
        "first": "First name", "first_hint": "First name only. Do not include middle or last names.",
        "last": "Last name(s)", "last_hint": "Enter both last names if you have two.",
        "consent": "I consent to electronic records and electronic signatures for this agreement.",
        "access": "I can read the agreement displayed above and can save or print a copy.",
        "intent": "I have read this agreement, accept its terms, and intend my typed first and last names to be my binding electronic signature.",
        "sign": "Sign agreement", "decline": "Decline electronic signing", "done": "Agreement signed",
        "declined": "Electronic signing declined. Contact the office for a paper alternative.",
        "error": "Unable to complete this step. Check the information or contact the office.",
        "expired": "This signing invitation has expired. Contact the office.",
    },
    "es": {
        "title": "Firma del acuerdo", "download": "Guardar / imprimir acuerdo PDF",
        "first": "Nombre", "first_hint": "Solo su primer nombre. No incluya segundo nombre ni apellidos.",
        "last": "Apellidos", "last_hint": "Escriba ambos apellidos si tiene dos.",
        "consent": "Acepto usar documentos y firmas electrónicas para este acuerdo.",
        "access": "Puedo leer el acuerdo mostrado arriba y guardar o imprimir una copia.",
        "intent": "He leído este acuerdo, acepto sus términos y deseo que mi nombre y apellidos escritos constituyan mi firma electrónica vinculante.",
        "sign": "Firmar acuerdo", "decline": "Rechazar firma electrónica", "done": "Acuerdo firmado",
        "declined": "Firma electrónica rechazada. Contacte a la oficina para coordinar la firma en papel.",
        "error": "No se pudo completar este paso. Revise la información o contacte a la oficina.",
        "expired": "Esta invitación ha vencido. Contacte a la oficina.",
    },
}


class AgreementTemplate(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    agency_id = db.Column(db.Integer, db.ForeignKey("agency.id"), nullable=False, index=True)
    name = db.Column(db.String(180), nullable=False)
    content = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)


class Agreement(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    agency_id = db.Column(db.Integer, db.ForeignKey("agency.id"), nullable=False, index=True)
    client_id = db.Column(db.Integer, db.ForeignKey("client.id"), nullable=False, index=True)
    name = db.Column(db.String(180), nullable=False)
    snapshot = db.Column(db.Text, nullable=False)
    snapshot_hash = db.Column(db.String(64), nullable=False)
    token_hash = db.Column(db.String(64), nullable=False, unique=True)
    status = db.Column(db.String(20), nullable=False, default="Pending")
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    expires_at = db.Column(db.DateTime, nullable=False)
    sent_at = db.Column(db.DateTime)
    signed_at = db.Column(db.DateTime)
    language = db.Column(db.String(2))
    signature = db.Column(db.String(240))
    code_hash = db.Column(db.String(255))
    code_at = db.Column(db.DateTime)
    code_attempts = db.Column(db.Integer, nullable=False, default=0)
    verification_nonce = db.Column(db.String(80))
    pdf = db.Column(db.LargeBinary)
    pdf_hash = db.Column(db.String(64))
    archived_at = db.Column(db.DateTime)
    client = db.relationship("Client")


class AgreementEvent(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    agreement_id = db.Column(db.Integer, db.ForeignKey("agreement.id"), nullable=False, index=True)
    action = db.Column(db.String(80), nullable=False)
    actor = db.Column(db.String(180), nullable=False)
    details = db.Column(db.Text)
    ip = db.Column(db.String(80))
    user_agent = db.Column(db.String(500))
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)


def now_utc():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def digest(value):
    return hashlib.sha256(value.encode("utf-8") if isinstance(value, str) else value).hexdigest()


def event(agreement, action, actor, details=""):
    db.session.add(AgreementEvent(
        agreement_id=agreement.id, action=action, actor=actor, details=details,
        ip=(request.remote_addr or "")[:80], user_agent=request.headers.get("User-Agent", "")[:500],
    ))


def notify_agreement_signed(agreement):
    client = agreement.client
    managers = AgencyCaseManager.query.filter_by(agency_id=agreement.agency_id, is_active=True)
    assigned = (managers.filter_by(id=client.assigned_to_id).first()
                if client.assigned_to_role == "agency_case_manager" else None)
    if assigned:
        recipients = [assigned]
    else:
        case_ids = [service.get("case_id") for service in json.loads(agreement.snapshot)["services"]]
        recipients = managers.join(CrmCase, CrmCase.case_manager_id == AgencyCaseManager.id).filter(
            CrmCase.id.in_(case_ids), CrmCase.client_id == agreement.client_id,
            CrmCase.agency_id == agreement.agency_id,
        ).distinct().all()
    for manager in recipients:
        db.session.add(Notification(
            agency_id=agreement.agency_id, recipient_role="agency_case_manager", recipient_id=manager.id,
            sender_label="System", notification_type="agreement_signed", client_id=client.id,
            message=f"Agreement {agreement.name} (#{agreement.id}) for client {client.full_name} has been signed.",
        ))


def agreement_pdf(agreement):
    from reportlab.lib import colors
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import inch
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak

    snapshot = json.loads(agreement.snapshot)
    styles = getSampleStyleSheet()
    story = []
    def paragraph(text, style="BodyText"):
        story.append(Paragraph(escape(str(text)).replace("\n", "<br/>"), styles[style]))
        story.append(Spacer(1, 7))
    paragraph(agreement.name, "Title")
    paragraph(f"Agreement #{agreement.id} | {snapshot['agency']['name']}", "Heading2")
    for label in ("agency", "client"):
        party = snapshot[label]
        paragraph(f"{label.title()}: {party['name']}\n{party['address']}\n{party['email']} | {party['phone']}")
    paragraph(f"A-number: {snapshot['client'].get('a_number') or 'Not provided'}")
    paragraph("Services / Servicios", "Heading2")
    data = [[Paragraph(escape(t), styles["BodyText"]) for t in
             ("Service / Servicio", "Price", "Discount", "Total")]]
    for service in snapshot["services"]:
        data.append([Paragraph(escape(service["name"]), styles["BodyText"]),
                     *[f"${service[key]}" for key in ("price", "discount", "total")]])
    table = Table(data, colWidths=[3.3 * inch, .8 * inch, .8 * inch, .8 * inch], repeatRows=1)
    table.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#edf0f2")),
                               ("VALIGN", (0, 0), (-1, -1), "TOP"),
                               ("LINEBELOW", (0, 0), (-1, -1), .4, colors.lightgrey),
                               ("BOTTOMPADDING", (0, 0), (-1, -1), 9)]))
    story.append(table)
    paragraph(f"Total / Total: ${snapshot['total']}", "Heading2")
    paragraph(snapshot["content"])
    paragraph("Company electronic signature / Firma electronica de la empresa", "Heading2")
    paragraph(f"{CEO} | CEO | {snapshot['company_signed_at']} UTC\n"
              f"Automatically applied under authority confirmed by {snapshot['authorized_by']}.")
    if agreement.status == "Signed":
        paragraph("Client electronic signature / Firma electronica del cliente", "Heading2")
        paragraph(f"{agreement.signature}\n{agreement.signed_at.isoformat()} UTC")
    story.append(PageBreak())
    paragraph("Electronic record / Registro electronico", "Heading1")
    paragraph(f"Agreement #{agreement.id}\nSnapshot SHA-256: {agreement.snapshot_hash}")
    if agreement.status != "Signed":
        for language in ("en", "es"):
            paragraph(snapshot["disclosures"][language])
    if agreement.status == "Signed":
        signed_event = AgreementEvent.query.filter_by(agreement_id=agreement.id, action="Client signed").first()
        try:
            consent = json.loads(signed_event.details) if signed_event else {}
        except (TypeError, ValueError):
            consent = {}
        paragraph(f"Electronic consent version: {consent.get('consent_version', snapshot['consent_version'])} | Interface: {agreement.language}")
        paragraph(consent.get("disclosure", snapshot["disclosures"][agreement.language]))
        for action in ("consent", "access", "intent"):
            paragraph(consent.get("affirmations", {}).get(action, COPY[agreement.language][action]))
    events = AgreementEvent.query.filter_by(agreement_id=agreement.id).order_by(AgreementEvent.id).all()
    for entry in events:
        paragraph(f"{entry.created_at.isoformat()} UTC | {entry.action} | {entry.actor}\n"
                  f"{entry.details or ''}\nIP: {entry.ip or '-'} | Browser: {entry.user_agent or '-'}")
    output = io.BytesIO()
    SimpleDocTemplate(output, pagesize=(612, 792), rightMargin=42, leftMargin=42,
                      topMargin=42, bottomMargin=42, title=agreement.name, author=snapshot["agency"]["name"]).build(story)
    return output.getvalue()


def register_agreements(app, *, can_use_crm, is_owner, user_label, send_email, log_client):
    def staff(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if (not current_user.is_authenticated or not current_user.is_active
                    or not isinstance(current_user, (AgencyUser, AgencyCaseManager))):
                abort(403)
            if not can_use_crm(current_user.agency):
                abort(403)
            return view(*args, **kwargs)
        return wrapped

    def client_for(client_id):
        return Client.query.filter_by(id=client_id, agency_id=current_user.agency_id).first_or_404()

    def owned(agreement_id):
        agreement = Agreement.query.filter_by(id=agreement_id, agency_id=current_user.agency_id).first_or_404()
        if agreement.archived_at and not is_owner():
            abort(404)
        return agreement

    def public(token):
        if current_user.is_authenticated and isinstance(current_user, AgencyCaseManager):
            abort(403)
        return Agreement.query.filter_by(token_hash=digest(token), archived_at=None).first_or_404()

    def signing_base():
        base = os.environ.get("AGREEMENT_PUBLIC_BASE_URL", "").rstrip("/")
        if (not base or not urlsplit(base).netloc or urlsplit(base).scheme != "https"
                or urlsplit(base).query or urlsplit(base).fragment
                or current_app.secret_key in (None, "", "dev-change-me")
                or (not current_app.testing and not current_app.config.get("SESSION_COOKIE_SECURE"))):
            return None
        return base

    def send_invitation(agreement, link):
        snapshot = json.loads(agreement.snapshot)
        return send_email(
            snapshot["client"]["email"], "Agreement signature requested / Solicitud de firma",
            f"{snapshot['agency']['name']}\nHello / Hola {snapshot['client']['name']},\n"
            f"Agreement / Acuerdo: {agreement.name}\nReview and sign / Revise y firme: {link}\n"
            "Choose English or Spanish on the signing page. Elija ingles o espanol en la pagina.\n"
            f"Sign by / Firme antes del: {agreement.expires_at.strftime('%Y-%m-%d')} UTC.",
            html_body=render_template("agreement_email.html", agreement=agreement, agency=snapshot["agency"],
                                      client=snapshot["client"], link=link, notice="invitation"),
        )

    @app.after_request
    def protect_agreement_responses(response):
        if request.endpoint and request.endpoint.startswith("agreement"):
            response.headers["Cache-Control"] = "no-store"
            # HTTPS CSRF validation requires a referrer on same-origin form posts.
            response.headers["Referrer-Policy"] = "same-origin"
            response.headers["X-Robots-Tag"] = "noindex, nofollow"
            response.headers["X-Frame-Options"] = "DENY"
        return response

    @app.get("/agency/agreements")
    @staff
    def agreements():
        rows = Agreement.query.join(Agreement.client).filter(
            Agreement.agency_id == current_user.agency_id, Agreement.archived_at.is_(None),
        )
        query = request.args.get("q", "").strip()[:150]
        for token in query.split():
            rows = rows.filter(or_(*(field.icontains(token, autoescape=True)
                                     for field in (Client.first_name, Client.middle_name, Client.last_name,
                                                   Client.a_number, Client.phone, Client.email))))
        try:
            start = date.fromisoformat(request.args["from"]) if request.args.get("from") else None
            end = date.fromisoformat(request.args["to"]) if request.args.get("to") else None
            if start and end and start > end:
                raise ValueError
            if start:
                rows = rows.filter(Agreement.created_at >= datetime.combine(start, datetime.min.time()))
            if end:
                rows = rows.filter(Agreement.created_at < datetime.combine(end + timedelta(days=1), datetime.min.time()))
        except (ValueError, OverflowError):
            abort(400)
        page = max(1, request.args.get("page", 1, type=int))
        return render_template("agreements.html", rows=rows.order_by(Agreement.created_at.desc()).paginate(
            page=page, per_page=30, error_out=False))

    @app.get("/agency/agreements/templates")
    @staff
    def agreement_templates():
        if not is_owner():
            abort(403)
        templates = AgreementTemplate.query.filter_by(agency_id=current_user.agency_id).order_by(
            AgreementTemplate.created_at.desc(), AgreementTemplate.id.desc()
        ).paginate(page=max(1, request.args.get("page", 1, type=int)), per_page=30, error_out=False)
        return render_template("agreement_templates.html", templates=templates)

    @app.route("/agency/agreements/templates/new", methods=["GET", "POST"])
    @staff
    def agreement_template_new():
        if not is_owner():
            abort(403)
        if request.method == "POST":
            name, content = request.form.get("name", "").strip(), request.form.get("content", "").strip()
            if not 1 <= len(name) <= 180 or not 1 <= len(content) <= 60000:
                abort(400)
            db.session.add(AgreementTemplate(agency_id=current_user.agency_id, name=name, content=content))
            db.session.commit()
            flash("Agreement template created.", "success")
            return redirect(url_for("agreement_templates"))
        return render_template("agreement_template.html")

    @app.route("/agency/agreements/templates/<int:template_id>/edit", methods=["GET", "POST"])
    @staff
    def agreement_template_edit(template_id):
        if not is_owner():
            abort(403)
        template = AgreementTemplate.query.filter_by(
            id=template_id, agency_id=current_user.agency_id
        ).first_or_404()
        if request.method == "POST":
            name, content = request.form.get("name", "").strip(), request.form.get("content", "").strip()
            if not 1 <= len(name) <= 180 or not 1 <= len(content) <= 60000:
                abort(400)
            template.name, template.content = name, content
            db.session.commit()
            flash("Agreement template updated. Previously sent agreements are unchanged.", "success")
            return redirect(url_for("agreement_templates"))
        return render_template("agreement_template.html", template=template)

    @app.route("/agency/crm/clients/<int:client_id>/agreements/new", methods=["GET", "POST"])
    @staff
    def agreement_new(client_id):
        client = client_for(client_id)
        templates = AgreementTemplate.query.filter_by(agency_id=client.agency_id).order_by(AgreementTemplate.created_at.desc()).all()
        cases = CrmCase.query.filter_by(client_id=client.id, agency_id=client.agency_id).order_by(CrmCase.title).all()
        if request.method == "POST":
            if request.form.get("authorize") != "yes":
                return render_template("agreement_new.html", client=client, templates=templates, cases=cases,
                                       authorization_error=True), 400
            template = AgreementTemplate.query.filter_by(
                id=request.form.get("template_id", type=int), agency_id=client.agency_id).first_or_404()
            try:
                ids = {int(value) for value in request.form.getlist("case_id")}
            except ValueError:
                abort(400)
            selected = [case for case in cases if case.id in ids]
            if not ids or len(ids) > 50 or len(selected) != len(ids):
                abort(400)
            services = []
            for case in selected:
                invoices = CrmInvoice.query.filter_by(case_id=case.id, agency_id=client.agency_id, client_id=client.id).filter(
                    CrmInvoice.status != "Void").all()
                if len(invoices) > 1:
                    flash("A selected case has multiple active invoices. Resolve this before sending an agreement.", "warning")
                    return redirect(request.url)
                price = case.price or Decimal("0")
                discount = Decimal("0")
                if invoices:
                    discount = db.session.query(func.sum(CrmInvoiceActivity.amount)).filter_by(
                        invoice_id=invoices[0].id, activity_type="Discount").scalar() or Decimal("0")
                if price < 0 or discount < 0 or discount > price:
                    abort(400, description="Review the selected case pricing and discounts.")
                services.append({"name": case.title, "case_id": case.id, "price": f"{price:.2f}",
                                 "discount": f"{discount:.2f}", "total": f"{price - discount:.2f}"})
            agency = client.agency
            now = now_utc()
            snapshot = json.dumps({
                "agency": {"name": agency.agency_name, "address": agency.display_address,
                           "email": agency.agency_email or agency.ceo_email, "phone": agency.agency_phone or agency.ceo_phone},
                "client": {"name": client.full_name, "address": client.display_address,
                           "email": client.email, "phone": client.phone, "a_number": client.a_number},
                "content": template.content, "template_id": template.id, "services": services,
                "disclosures": DISCLOSURE, "consent_version": CONSENT_VERSION,
                "total": f"{sum(Decimal(item['total']) for item in services):.2f}",
                "authorized_by": user_label(), "company_signed_at": now.isoformat(),
            }, ensure_ascii=True, sort_keys=True)
            token = secrets.token_urlsafe(32)
            agreement = Agreement(agency_id=client.agency_id, client_id=client.id, name=template.name,
                                  snapshot=snapshot, snapshot_hash=digest(snapshot), token_hash=digest(token),
                                  created_at=now, expires_at=now + timedelta(days=30))
            db.session.add(agreement)
            db.session.flush()
            event(agreement, "Company signature authorized", user_label(), f"{CEO}; template #{template.id}")
            base = signing_base()
            if not base:
                db.session.rollback()
                flash("Configure the HTTPS signing URL, a strong secret key, and secure session cookies before sending.", "warning")
                return redirect(request.url)
            link = base + url_for("agreement_sign", token=token)
            ok, _ = send_invitation(agreement, link)
            agreement.status = "Pending" if ok else "Delivery failed"
            agreement.sent_at = now if ok else None
            event(agreement, "Invitation sent" if ok else "Delivery failed", user_label())
            log_client(client, "Agreement sent" if ok else "Agreement email failed", f"{agreement.name} (#{agreement.id})")
            db.session.commit()
            flash("Agreement sent for signature." if ok else "Email delivery failed. Create a new invitation to retry.",
                  "success" if ok else "warning")
            return redirect(url_for("crm_client_detail", client_id=client.id, tab="agreements"))
        return render_template("agreement_new.html", client=client, templates=templates, cases=cases)

    @app.get("/agency/agreements/<int:agreement_id>")
    @staff
    def agreement_detail(agreement_id):
        if not is_owner():
            abort(403)
        agreement = owned(agreement_id)
        return render_template("agreement_detail.html", agreement=agreement, snapshot=json.loads(agreement.snapshot),
                               events=AgreementEvent.query.filter_by(agreement_id=agreement.id).order_by(AgreementEvent.id).all())

    @app.post("/agency/agreements/<int:agreement_id>/delete")
    @staff
    def agreement_delete(agreement_id):
        if not is_owner():
            abort(403)
        agreement = owned(agreement_id)
        agreement.archived_at = now_utc()
        event(agreement, "Archived", user_label())
        log_client(agreement.client, "Agreement archived", f"{agreement.name} (#{agreement.id})")
        db.session.commit()
        return redirect(url_for("crm_client_detail", client_id=agreement.client_id, tab="agreements"))

    @app.get("/agency/agreements/<int:agreement_id>/pdf")
    @staff
    def agreement_staff_pdf(agreement_id):
        if not is_owner():
            abort(403)
        return serve_pdf(owned(agreement_id))

    @app.post("/agency/agreements/<int:agreement_id>/resend")
    @staff
    def agreement_resend(agreement_id):
        agreement = owned(agreement_id)
        db.session.execute(update(Agreement).where(Agreement.id == agreement.id).values(id=Agreement.id))
        db.session.refresh(agreement)
        if agreement.archived_at or agreement.status != "Pending":
            abort(409)
        back = (url_for("crm_client_detail", client_id=agreement.client_id, tab="agreements")
                if request.form.get("return_to") == "client" else url_for("agreements"))
        if agreement.sent_at and now_utc() - agreement.sent_at < timedelta(minutes=1):
            flash("Please wait one minute before resending this agreement.", "warning")
            return redirect(back, code=303)
        base = signing_base()
        if not base:
            flash("The signing email configuration is not ready. Contact your administrator.", "warning")
            return redirect(back, code=303)
        token = secrets.token_urlsafe(32)
        previous_expiry = agreement.expires_at
        agreement.expires_at = now_utc() + timedelta(days=30)
        ok, _ = send_invitation(agreement, base + url_for("agreement_sign", token=token))
        if ok:
            agreement.token_hash = digest(token)
            agreement.sent_at = now_utc()
            event(agreement, "Invitation resent", user_label(), "Previous signing link replaced; terms unchanged.")
            log_client(agreement.client, "Agreement invitation resent", f"{agreement.name} (#{agreement.id})")
        else:
            agreement.expires_at = previous_expiry
            event(agreement, "Invitation resend failed", user_label())
        db.session.commit()
        flash("Agreement email resent." if ok else "Email delivery failed. The previous link remains valid.",
              "success" if ok else "warning")
        return redirect(back, code=303)

    def serve_pdf(agreement):
        data = agreement.pdf if agreement.status == "Signed" else agreement_pdf(agreement)
        if agreement.status == "Signed" and (not data or digest(data) != agreement.pdf_hash):
            abort(409, description="Document integrity check failed. Contact the agency.")
        return send_file(io.BytesIO(data), mimetype="application/pdf",
                         as_attachment=request.args.get("download") == "1", download_name=f"agreement-{agreement.id}.pdf")

    @app.route("/sign/agreements/<token>", methods=["GET", "POST"])
    def agreement_sign(token):
        agreement = public(token)
        lang = request.values.get("lang", "en")
        lang = lang if lang in COPY else "en"
        text = COPY[lang]
        if request.method == "POST":
            db.session.execute(update(Agreement).where(Agreement.id == agreement.id).values(id=Agreement.id))
            db.session.refresh(agreement)
            if agreement.archived_at or agreement.token_hash != digest(token):
                abort(404)
            action = request.form.get("action")
            if agreement.status != "Pending" or agreement.expires_at < now_utc():
                abort(409)
            if action in ("sign", "decline"):
                if session.get(f"agreement-view:{agreement.id}") != agreement.snapshot_hash:
                    abort(403)
                if action == "decline":
                    agreement.status = "Declined"
                    event(agreement, "Electronic signing declined", "Recipient", f"Interface: {lang}")
                else:
                    first, last = request.form.get("first", "").strip(), request.form.get("last", "").strip()
                    if (not 1 <= len(first) <= 80 or not 1 <= len(last) <= 150
                            or not any(char.isalpha() for char in first) or not any(char.isalpha() for char in last)
                            or any(not (char.isalpha() or char in " '-") for char in first + last)
                            or any(request.form.get(key) != "yes" for key in ("consent", "access", "intent"))):
                        abort(400)
                    if digest(agreement.snapshot) != agreement.snapshot_hash:
                        abort(409)
                    agreement.signature = f"{first} {last}"
                    agreement.signed_at, agreement.language, agreement.status = now_utc(), lang, "Signed"
                    event(agreement, "Client signed", agreement.signature,
                          json.dumps({"consent_version": CONSENT_VERSION, "language": lang,
                                      "disclosure": DISCLOSURE[lang], "snapshot_hash": agreement.snapshot_hash,
                                      "access_method": "Email invitation link; no separate identity verification",
                                      "affirmations": {key: COPY[lang][key] for key in ("consent", "access", "intent")}},
                                     ensure_ascii=True))
                    notify_agreement_signed(agreement)
                    db.session.flush()
                    agreement.pdf = agreement_pdf(agreement)
                    agreement.pdf_hash = digest(agreement.pdf)
                    log_client(agreement.client, "Agreement signed", f"{agreement.name} (#{agreement.id})")
            else:
                abort(400)
            db.session.commit()
            if action in ("sign", "decline") and agreement.status in ("Signed", "Declined"):
                snapshot = json.loads(agreement.snapshot)
                link = os.environ.get("AGREEMENT_PUBLIC_BASE_URL", "").rstrip("/") + url_for("agreement_sign", token=token, lang=lang)
                ok, _ = send_email(snapshot["client"]["email"], f"Agreement {agreement.status} / Acuerdo #{agreement.id}",
                                   f"{snapshot['agency']['name']}\n{agreement.name}\nStatus / Estado: {agreement.status}\n{link}",
                                   html_body=render_template("agreement_email.html", agreement=agreement,
                                                             agency=snapshot["agency"], client=snapshot["client"],
                                                             link=link, notice=agreement.status))
                event(agreement, "Completion email sent" if ok else "Completion email failed", "System",
                      "Client confirmation only; agency notification is an internal CRM Alert.")
                db.session.commit()
            return redirect(url_for("agreement_sign", token=token, lang=lang), code=303)
        if agreement.status not in ("Pending", "Signed", "Declined"):
            abort(410)
        expired = agreement.status == "Pending" and agreement.expires_at < now_utc()
        if not expired:
            if session.get(f"agreement-view:{agreement.id}") != agreement.snapshot_hash:
                event(agreement, "Agreement page accessed", "Invitation link holder",
                      "Bearer-link access; no independent email or identity verification.")
                db.session.commit()
            session[f"agreement-view:{agreement.id}"] = agreement.snapshot_hash
        return render_template("agreement_sign.html", agreement=agreement, lang=lang, t=text,
                               disclosure=DISCLOSURE[lang], expired=expired, snapshot=json.loads(agreement.snapshot))

    @app.get("/sign/agreements/<token>/pdf")
    def agreement_public_pdf(token):
        agreement = public(token)
        if agreement.status not in ("Pending", "Signed", "Declined"):
            abort(410)
        if agreement.status != "Signed" and agreement.expires_at < now_utc():
            abort(410)
        return serve_pdf(agreement)
