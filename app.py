import os
import json
import re
import uuid
from datetime import datetime
from decimal import Decimal, InvalidOperation

from flask import (
    Flask,
    abort,
    flash,
    redirect,
    render_template,
    request,
    send_from_directory,
    url_for,
)
from flask_login import LoginManager, current_user, login_required, login_user, logout_user
from flask_wtf import CSRFProtect
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
from werkzeug.utils import secure_filename
from sqlalchemy import inspect, text

from forms import CASE_STATUSES, CASE_TYPES, FORM_TEMPLATES, I485_QUESTIONS, I589_QUESTIONS, SUBSCRIPTION_TOOLS, US_STATES
from models import (
    ActiveSession,
    Agency,
    AgencyDocument,
    AgencyPreparer,
    AgencyTranslator,
    AgencyUser,
    ApexUser,
    Case,
    CaseAnswer,
    CaseDocument,
    CaseQuestion,
    Client,
    FormTemplate,
    GeneratedForm,
    PdfField,
    SubscriptionTool,
    db,
)


BASE_DIR = os.path.abspath(os.path.dirname(__file__))
ALLOWED_EXTENSIONS = {"pdf", "png", "jpg", "jpeg", "doc", "docx", "txt"}


def create_app():
    app = Flask(__name__, instance_relative_config=True)
    app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-change-me")
    app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get(
        "DATABASE_URL", f"sqlite:///{os.path.join(app.instance_path, 'app.db')}"
    )
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.config["UPLOAD_FOLDER"] = os.environ.get("UPLOAD_FOLDER", os.path.join(BASE_DIR, "uploads"))
    app.config["MAX_CONTENT_LENGTH"] = int(os.environ.get("MAX_CONTENT_LENGTH", 16 * 1024 * 1024))

    os.makedirs(app.instance_path, exist_ok=True)
    os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)

    db.init_app(app)
    CSRFProtect(app)

    login_manager = LoginManager(app)
    login_manager.login_view = "home"

    @login_manager.user_loader
    def load_user(user_key):
        try:
            role, raw_id = user_key.split(":", 1)
            user_id = int(raw_id)
        except (ValueError, AttributeError):
            return None
        if role == "apex":
            return db.session.get(ApexUser, user_id)
        if role == "agency":
            return db.session.get(AgencyUser, user_id)
        if role == "client":
            return db.session.get(Client, user_id)
        return None

    @app.before_request
    def refresh_active_session():
        if current_user.is_authenticated:
            row = ActiveSession.query.filter_by(
                user_id=current_user.id,
                role=current_user.role,
                ip_address=request.remote_addr or "unknown",
            ).order_by(ActiveSession.login_time.desc()).first()
            if row:
                row.last_activity = datetime.utcnow()
                db.session.commit()

    @app.context_processor
    def inject_choices():
        return {
            "case_statuses": CASE_STATUSES,
            "case_types": available_case_types(),
            "subscription_tools": SUBSCRIPTION_TOOLS,
            "states": US_STATES,
        }

    register_routes(app)
    return app


def role_required(*roles):
    def decorator(view):
        def wrapped(*args, **kwargs):
            if not current_user.is_authenticated:
                return redirect(url_for("home"))
            if current_user.role not in roles:
                abort(403)
            return view(*args, **kwargs)

        wrapped.__name__ = view.__name__
        return login_required(wrapped)

    return decorator


def agency_for_user():
    if current_user.role == "apex":
        return None
    if current_user.role == "agency":
        return current_user.agency
    if current_user.role == "client":
        return current_user.agency
    return None


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def save_upload(file_storage, subfolder):
    if not file_storage or not file_storage.filename:
        return None
    if not allowed_file(file_storage.filename):
        raise ValueError("Unsupported file type.")
    original = secure_filename(file_storage.filename)
    stored = f"{uuid.uuid4().hex}_{original}"
    target_dir = os.path.join(app.config["UPLOAD_FOLDER"], subfolder)
    os.makedirs(target_dir, exist_ok=True)
    file_storage.save(os.path.join(target_dir, stored))
    return original, os.path.join(subfolder, stored).replace("\\", "/")


def selected_tools_from_form():
    names = request.form.getlist("subscriptions")
    return SubscriptionTool.query.filter(SubscriptionTool.name.in_(names)).all() if names else []


def replace_template_questions(code, question_lines):
    parsed_questions = [parse_question_line(line, code, index) for index, line in enumerate(question_lines.splitlines(), start=1) if line.strip()]
    existing = CaseQuestion.query.filter_by(case_type=code).order_by(CaseQuestion.sort_order).all()
    for index, (field_key, prompt) in enumerate(parsed_questions, start=1):
        input_type = "textarea" if len(prompt) > 80 or prompt.lower().startswith(("describe", "list", "why", "what")) else "text"
        question = existing[index - 1] if index <= len(existing) else CaseQuestion(case_type=code)
        question.prompt = prompt
        question.field_key = field_key
        question.input_type = input_type
        question.sort_order = index
        question.required = True
        db.session.add(question)
    for extra in existing[len(parsed_questions):]:
        if not CaseAnswer.query.filter_by(question_id=extra.id).first():
            db.session.delete(extra)


def parse_question_line(line, code, index):
    raw = line.strip()
    if "|" in raw:
        field_key, prompt = [part.strip() for part in raw.split("|", 1)]
        return field_key or f"{code.lower().replace('-', '')}_question_{index}", prompt
    return f"{code.lower().replace('-', '')}_question_{index}", raw


def reorder_template_questions(code):
    questions = CaseQuestion.query.filter_by(case_type=code).order_by(CaseQuestion.sort_order, CaseQuestion.id).all()
    for index, question in enumerate(questions, start=1):
        question.sort_order = index


def detect_pdf_template(template, uploaded_file):
    saved = save_upload(uploaded_file, f"form_templates/{template.code}")
    if not saved:
        return None
    original, stored = saved
    template.pdf_original_filename = original
    template.pdf_stored_filename = stored
    pdf_path = os.path.join(app.config["UPLOAD_FOLDER"], stored)
    metadata = inspect_uscis_pdf(pdf_path)
    template.pdf_kind = metadata["kind"]
    template.pdf_field_count = len(metadata["fields"])
    template.pdf_generation_strategy = metadata["strategy"]
    PdfField.query.filter_by(template_id=template.id).delete()
    for field in metadata["fields"]:
        db.session.add(
            PdfField(
                template_id=template.id,
                field_name=field["name"][:255],
                field_type=field.get("type"),
                page_number=field.get("page"),
                rect_json=field.get("rect_json"),
            )
        )
    return pdf_path


def inspect_uscis_pdf(pdf_path):
    metadata = {"kind": "unknown", "strategy": "summary_pdf", "fields": []}
    xfa = False
    has_acroform = False
    try:
        from pypdf import PdfReader
    except ImportError:
        metadata["kind"] = "pypdf_missing"
        return inspect_pdf_widgets_with_pymupdf(pdf_path, metadata, has_acroform, xfa)
    try:
        reader = PdfReader(pdf_path)
        root = reader.trailer.get("/Root", {})
        acroform = root.get("/AcroForm")
        has_acroform = bool(acroform)
        fields = []
        if acroform:
            try:
                xfa = bool(acroform.get("/XFA"))
            except AttributeError:
                xfa = False
            raw_fields = reader.get_fields() or {}
            fields = [
                {"name": name, "type": str(field.get("/FT", "")) if hasattr(field, "get") else "", "page": None, "rect_json": None}
                for name, field in raw_fields.items()
            ]
        if acroform and xfa and fields:
            metadata["kind"] = "hybrid_xfa_acroform"
            metadata["strategy"] = "overlay_preserve_original"
        elif xfa:
            metadata["kind"] = "xfa"
            metadata["strategy"] = "overlay_preserve_original"
        elif acroform and fields:
            metadata["kind"] = "acroform"
            metadata["strategy"] = "acroform_fill_need_appearances"
        elif acroform:
            metadata["kind"] = "acroform_no_fields"
            metadata["strategy"] = "overlay_preserve_original"
        else:
            metadata["kind"] = "flat_pdf"
            metadata["strategy"] = "overlay_preserve_original"
        metadata["fields"] = fields
    except Exception:
        metadata["kind"] = "inspection_failed"
        metadata["strategy"] = "summary_pdf"
    return inspect_pdf_widgets_with_pymupdf(pdf_path, metadata, has_acroform, xfa)


def inspect_pdf_widgets_with_pymupdf(pdf_path, metadata, has_acroform=False, xfa=False):
    if metadata.get("fields"):
        return metadata
    try:
        import fitz
    except ImportError:
        return metadata
    fields = []
    seen = set()
    try:
        document = fitz.open(pdf_path)
        for page_index in range(document.page_count):
            page = document.load_page(page_index)
            for widget in page.widgets() or []:
                name = (widget.field_name or "").strip()
                if not name or name in seen:
                    continue
                seen.add(name)
                rect = widget.rect
                fields.append(
                    {
                        "name": name,
                        "type": str(widget.field_type_string or widget.field_type or ""),
                        "page": page_index + 1,
                        "rect_json": json.dumps([round(rect.x0, 2), round(rect.y0, 2), round(rect.x1, 2), round(rect.y1, 2)]),
                    }
                )
        document.close()
    except Exception:
        return metadata
    if not fields:
        return metadata
    metadata["fields"] = fields
    if xfa and has_acroform:
        metadata["kind"] = "hybrid_xfa_acroform"
        metadata["strategy"] = "overlay_preserve_original"
    elif xfa:
        metadata["kind"] = "xfa"
        metadata["strategy"] = "overlay_preserve_original"
    else:
        metadata["kind"] = "acroform_widgets"
        metadata["strategy"] = "acroform_fill_need_appearances"
    return metadata


QUESTION_KEYWORDS = (
    "name",
    "address",
    "city",
    "state",
    "zip",
    "date",
    "number",
    "phone",
    "email",
    "signature",
    "applicant",
    "petitioner",
    "beneficiary",
    "country",
    "birth",
    "alien",
    "a-number",
    "uscis",
    "passport",
    "account",
    "card",
    "expiration",
    "security code",
)


QUESTION_NOISE = (
    "department of homeland security",
    "u.s. citizenship and immigration services",
    "uscis form",
    "page ",
    "for uscis use only",
    "do not write",
    "instructions",
    "privacy act",
    "paperwork reduction",
)


def seed_questions_from_pdf_text(code, pdf_path):
    if not pdf_path or CaseQuestion.query.filter_by(case_type=code).first():
        return 0
    draft_questions = extract_draft_questions_from_pdf(pdf_path, code)
    for index, (field_key, prompt, input_type) in enumerate(draft_questions, start=1):
        db.session.add(
            CaseQuestion(
                case_type=code,
                field_key=field_key,
                prompt=prompt[:255],
                input_type=input_type,
                sort_order=index,
                required=True,
            )
        )
    return len(draft_questions)


def extract_draft_questions_from_pdf(pdf_path, code):
    try:
        import fitz
    except ImportError:
        return []
    prompts = []
    seen = set()
    try:
        document = fitz.open(pdf_path)
        for page_index in range(document.page_count):
            page_text = document.load_page(page_index).get_text("text")
            for raw_line in page_text.splitlines():
                prompt = normalize_pdf_prompt(raw_line)
                if not prompt or not looks_like_form_prompt(prompt):
                    continue
                key = prompt.lower()
                if key in seen:
                    continue
                seen.add(key)
                prompts.append(prompt)
                if len(prompts) >= 80:
                    break
            if len(prompts) >= 80:
                break
        document.close()
    except Exception:
        return []
    prefix = code.lower().replace("-", "")
    return [(f"{prefix}_auto_{index}", prompt, guess_input_type(prompt)) for index, prompt in enumerate(prompts, start=1)]


def normalize_pdf_prompt(raw_line):
    prompt = re.sub(r"\s+", " ", raw_line or "").strip(" .:_")
    prompt = re.sub(r"^\d+[.)]\s*", "", prompt)
    prompt = re.sub(r"^Part\s+\d+[.)]?\s*", "", prompt, flags=re.IGNORECASE)
    return prompt.strip()


def looks_like_form_prompt(prompt):
    if len(prompt) < 4 or len(prompt) > 180:
        return False
    lower = prompt.lower()
    if any(noise in lower for noise in QUESTION_NOISE):
        return False
    if lower.isupper() and len(prompt.split()) > 7:
        return False
    if prompt.endswith("?"):
        return True
    if any(keyword in lower for keyword in QUESTION_KEYWORDS):
        return True
    return bool(re.match(r"^(family|given|middle|last|first)\b", lower))


def guess_input_type(prompt):
    lower = prompt.lower()
    if "date" in lower or "expiration" in lower:
        return "date"
    if "describe" in lower or "explain" in lower or "history" in lower:
        return "textarea"
    if "number" in lower or "amount" in lower:
        return "number"
    return "text"


def decimal_from_form(name):
    try:
        return Decimal(request.form.get(name, "0") or "0")
    except InvalidOperation:
        return Decimal("0")


def populate_agency_from_form(agency):
    agency.agency_name = request.form["agency_name"].strip()
    agency.tax_id = request.form["tax_id"].strip()
    agency.street_address = request.form["street_address"].strip()
    agency.apartment = request.form.get("apartment", "").strip()
    agency.city = request.form["city"].strip()
    agency.state = request.form["state"].strip()
    agency.zip_code = request.form["zip_code"].strip()
    agency.ceo_email = request.form["ceo_email"].strip()
    agency.agency_email = request.form.get("agency_email", "").strip()
    agency.ceo_phone = request.form["ceo_phone"].strip()
    agency.agency_phone = request.form.get("agency_phone", "").strip()
    agency.registered_owners = request.form["registered_owners"].strip()
    agency.registered_operator = request.form.get("registered_operator", "").strip()
    agency.membership_plan_cost = decimal_from_form("membership_plan_cost")
    agency.total_ips_allowed = max(1, int(request.form.get("total_ips_allowed") or 1))
    agency.subscriptions = selected_tools_from_form()


def populate_client_from_form(client):
    client.first_name = request.form["first_name"].strip()
    client.middle_name = request.form.get("middle_name", "").strip()
    client.last_name = request.form["last_name"].strip()
    client.a_number = request.form.get("a_number", "").strip()
    client.phone = request.form["phone"].strip()
    client.email = request.form["email"].strip()
    client.street_address = request.form["street_address"].strip()
    client.apartment = request.form.get("apartment", "").strip()
    client.city = request.form["city"].strip()
    client.state = request.form["state"].strip()
    client.zip_code = request.form["zip_code"].strip()
    client.username = request.form["username"].strip()


def populate_translator_from_form(translator):
    translator.full_name = request.form["full_name"].strip()
    translator.language = request.form["language"].strip()
    translator.phone = request.form.get("phone", "").strip()
    translator.email = request.form.get("email", "").strip()
    translator.address = request.form.get("address", "").strip()


def populate_preparer_from_form(preparer):
    preparer.full_name = request.form["full_name"].strip()
    preparer.title = request.form.get("title", "").strip()
    preparer.phone = request.form.get("phone", "").strip()
    preparer.email = request.form.get("email", "").strip()
    preparer.address = request.form.get("address", "").strip()


def query_case_for_role(case_id):
    case = db.session.get(Case, case_id) or abort(404)
    if current_user.role == "apex":
        return case
    if current_user.role == "agency" and case.agency_id == current_user.agency_id:
        return case
    if current_user.role == "client" and case.client_id == current_user.id:
        return case
    abort(403)


def assign_case_people_from_form(case):
    translator_id = request.form.get("translator_id")
    preparer_id = request.form.get("preparer_id")
    case.translator_id = int(translator_id) if translator_id else None
    case.preparer_id = int(preparer_id) if preparer_id else None
    if case.translator_id and not AgencyTranslator.query.filter_by(id=case.translator_id, agency_id=case.agency_id).first():
        abort(403)
    if case.preparer_id and not AgencyPreparer.query.filter_by(id=case.preparer_id, agency_id=case.agency_id).first():
        abort(403)


def update_case_progress(case):
    all_questions = CaseQuestion.query.filter_by(case_type=case.case_type).order_by(CaseQuestion.sort_order).all()
    answers_by_question = {answer.question_id: answer.answer_text or "" for answer in case.answers}
    questions = visible_questions_for_answers(all_questions, answers_by_question)
    if not questions:
        case.progress_percentage = 0
        return
    answered = CaseAnswer.query.filter(
        CaseAnswer.case_id == case.id,
        CaseAnswer.answer_text.isnot(None),
        CaseAnswer.answer_text != "",
    ).count()
    case.progress_percentage = int((answered / len(questions)) * 100)
    if answered and case.status == "Created":
        case.status = "Client Questionnaire Started"
    if answered == len(questions) and case.status in ["Created", "Client Questionnaire Started", "Waiting for Client"]:
        case.status = "Ready for Review"


def first_unanswered_question_index(case, questions, skip_question_id=None):
    answered_ids = {
        answer.question_id
        for answer in case.answers
        if answer.question_id != skip_question_id and answer.answer_text
    }
    for index, question in enumerate(questions, start=1):
        if question.id not in answered_ids and question.id != skip_question_id:
            return index
    return None


def visible_questions_for_answers(questions, answers_by_question):
    visible = []
    for question in questions:
        if question_is_visible(question, answers_by_question):
            visible.append(question)
    return visible


def question_is_visible(question, answers_by_question):
    if not question.show_if_question_id:
        return True
    actual = (answers_by_question.get(question.show_if_question_id) or "").strip().lower()
    expected = (question.show_if_value or "").strip().lower()
    if question.show_if_operator == "not_equals":
        return actual != expected
    if question.show_if_operator == "contains":
        return expected in actual
    return actual == expected


def can_use_form_filler(agency):
    return bool(agency and agency.has_tool("Form Filler"))


def available_form_templates(active_only=True):
    query = FormTemplate.query.order_by(FormTemplate.code)
    if active_only:
        query = query.filter_by(is_active=True)
    return query.all()


def available_case_types():
    form_codes = [template.code for template in FormTemplate.query.filter_by(is_active=True).order_by(FormTemplate.code)]
    existing = list(dict.fromkeys(form_codes + CASE_TYPES))
    return existing


def agency_can_create_case_type(agency, case_type):
    form_codes = {template.code for template in FormTemplate.query.filter_by(is_active=True)}
    if case_type in form_codes or case_type in {"I-485", "I-765"}:
        return agency.has_tool("Form Filler")
    if case_type == "Motion":
        return agency.has_tool("Motion Creation")
    return True


def register_routes(app):
    @app.cli.command("init-db")
    def init_db_command():
        init_database()
        print("Database initialized.")

    @app.route("/")
    def home():
        if current_user.is_authenticated:
            return redirect(url_for(f"{current_user.role}_dashboard"))
        return render_template("home.html")

    @app.route("/login/<role>", methods=["GET", "POST"])
    def login(role):
        if role not in {"apex", "agency", "client"}:
            abort(404)
        if request.method == "POST":
            username = request.form["username"].strip()
            password = request.form["password"]
            user = None
            if role == "apex":
                user = ApexUser.query.filter_by(username=username).first()
            elif role == "agency":
                user = AgencyUser.query.filter_by(username=username).first()
            else:
                user = Client.query.filter_by(username=username).first()
            if not user or not user.check_password(password):
                flash("Invalid username or password.", "danger")
                return render_template("login.html", role=role)
            if role == "agency" and not agency_ip_allowed(user.agency, request.remote_addr or "unknown"):
                flash("Agency IP login limit reached. Contact Apex to increase your plan.", "danger")
                return render_template("login.html", role=role)
            login_user(user)
            record_login(user)
            return redirect(url_for(f"{role}_dashboard"))
        return render_template("login.html", role=role)

    @app.route("/logout")
    @login_required
    def logout():
        logout_user()
        flash("You have been logged out.", "info")
        return redirect(url_for("home"))

    @app.route("/apex")
    @role_required("apex")
    def apex_dashboard():
        return render_template(
            "apex_dashboard.html",
            agency_count=Agency.query.count(),
            client_count=Client.query.count(),
            case_count=Case.query.count(),
            agencies=Agency.query.order_by(Agency.created_at.desc()).limit(5).all(),
        )

    @app.route("/apex/agencies")
    @role_required("apex")
    def agency_list():
        return render_template("agency_list.html", agencies=Agency.query.order_by(Agency.agency_name).all())

    @app.route("/apex/subscriptions/form-filler", methods=["GET", "POST"])
    @role_required("apex")
    def apex_form_filler_admin():
        if request.method == "POST":
            code = request.form["code"].strip().upper()
            template = FormTemplate.query.filter_by(code=code).first() or FormTemplate(code=code)
            template.name = request.form["name"].strip()
            template.description = request.form.get("description", "").strip()
            template.is_active = bool(request.form.get("is_active"))
            db.session.add(template)
            db.session.flush()
            question_lines = request.form.get("questions", "")
            if question_lines.strip():
                replace_template_questions(code, question_lines)
            pdf_path = detect_pdf_template(template, request.files.get("pdf_template"))
            if not pdf_path and template.pdf_stored_filename:
                existing_pdf_path = os.path.join(app.config["UPLOAD_FOLDER"], template.pdf_stored_filename)
                pdf_path = existing_pdf_path if os.path.exists(existing_pdf_path) else None
            seeded_count = seed_questions_from_pdf_text(code, pdf_path) if pdf_path and not question_lines.strip() else 0
            db.session.commit()
            if seeded_count:
                flash(f"Form Filler questionnaire saved with {seeded_count} draft questions extracted from the PDF.", "success")
            else:
                flash("Form Filler questionnaire saved.", "success")
            return redirect(url_for("apex_form_filler_admin"))
        templates = available_form_templates(active_only=False)
        questions_by_code = {
            template.code: CaseQuestion.query.filter_by(case_type=template.code).order_by(CaseQuestion.sort_order).all()
            for template in templates
        }
        return render_template(
            "apex_form_filler_admin.html",
            templates=templates,
            questions_by_code=questions_by_code,
        )

    @app.route("/apex/subscriptions/form-filler/<int:template_id>/builder", methods=["GET", "POST"])
    @role_required("apex")
    def apex_form_builder(template_id):
        template = db.session.get(FormTemplate, template_id) or abort(404)
        questions = CaseQuestion.query.filter_by(case_type=template.code).order_by(CaseQuestion.sort_order).all()
        if request.method == "POST":
            question_id = request.form.get("question_id")
            question = db.session.get(CaseQuestion, int(question_id)) if question_id else CaseQuestion(case_type=template.code)
            question.prompt = request.form["prompt"].strip()
            question.field_key = request.form["field_key"].strip()
            question.input_type = request.form["input_type"]
            question.sort_order = int(request.form.get("sort_order") or len(questions) + 1)
            question.required = bool(request.form.get("required"))
            show_if_question_id = request.form.get("show_if_question_id")
            question.show_if_question_id = int(show_if_question_id) if show_if_question_id else None
            question.show_if_operator = request.form.get("show_if_operator") or "equals"
            question.show_if_value = request.form.get("show_if_value", "").strip()
            db.session.add(question)
            db.session.commit()
            flash("Question saved.", "success")
            return redirect(url_for("apex_form_builder", template_id=template.id))
        pdf_fields = PdfField.query.filter_by(template_id=template.id).order_by(PdfField.field_name).all()
        return render_template(
            "apex_form_builder.html",
            template=template,
            questions=questions,
            pdf_fields=pdf_fields,
        )

    @app.route("/apex/subscriptions/form-filler/<int:template_id>/builder/questions/<int:question_id>/delete", methods=["POST"])
    @role_required("apex")
    def apex_form_question_delete(template_id, question_id):
        template = db.session.get(FormTemplate, template_id) or abort(404)
        question = db.session.get(CaseQuestion, question_id) or abort(404)
        if question.case_type != template.code:
            abort(404)
        CaseQuestion.query.filter_by(show_if_question_id=question.id).update(
            {"show_if_question_id": None, "show_if_value": ""},
            synchronize_session=False,
        )
        PdfField.query.filter_by(mapped_question_id=question.id).update(
            {"mapped_question_id": None},
            synchronize_session=False,
        )
        CaseAnswer.query.filter_by(question_id=question.id).delete(synchronize_session=False)
        db.session.delete(question)
        db.session.flush()
        reorder_template_questions(template.code)
        db.session.commit()
        flash("Question deleted from the questionnaire.", "info")
        return redirect(url_for("apex_form_builder", template_id=template.id))

    @app.route("/apex/subscriptions/form-filler/<int:template_id>/delete", methods=["POST"])
    @role_required("apex")
    def apex_form_template_delete(template_id):
        template = db.session.get(FormTemplate, template_id) or abort(404)
        existing_cases = Case.query.filter_by(case_type=template.code).count()
        if existing_cases:
            template.is_active = False
            db.session.commit()
            flash(
                f"{template.code} has existing cases, so it was deactivated instead of deleted to preserve historical records.",
                "warning",
            )
            return redirect(url_for("apex_form_filler_admin"))
        questions = CaseQuestion.query.filter_by(case_type=template.code).all()
        for question in questions:
            if CaseAnswer.query.filter_by(question_id=question.id).first():
                template.is_active = False
                db.session.commit()
                flash(f"{template.code} has answer history, so it was deactivated instead of deleted.", "warning")
                return redirect(url_for("apex_form_filler_admin"))
            db.session.delete(question)
        PdfField.query.filter_by(template_id=template.id).delete()
        db.session.delete(template)
        db.session.commit()
        flash("Questionnaire deleted.", "info")
        return redirect(url_for("apex_form_filler_admin"))

    @app.route("/apex/agencies/new", methods=["GET", "POST"])
    @role_required("apex")
    def agency_create():
        if request.method == "POST":
            agency = Agency()
            populate_agency_from_form(agency)
            user = AgencyUser(username=request.form["username"].strip(), agency=agency)
            user.set_password(request.form["password"])
            db.session.add(agency)
            db.session.add(user)
            db.session.commit()
            save_agency_documents(agency)
            flash("Agency created.", "success")
            return redirect(url_for("agency_list"))
        return render_template("agency_form.html", agency=None, user=None)

    @app.route("/apex/agencies/<int:agency_id>/edit", methods=["GET", "POST"])
    @role_required("apex")
    def agency_edit(agency_id):
        agency = db.session.get(Agency, agency_id) or abort(404)
        if request.method == "POST":
            populate_agency_from_form(agency)
            agency.user.username = request.form["username"].strip()
            if request.form.get("password"):
                agency.user.set_password(request.form["password"])
            db.session.commit()
            save_agency_documents(agency)
            flash("Agency updated.", "success")
            return redirect(url_for("agency_list"))
        return render_template("agency_form.html", agency=agency, user=agency.user)

    @app.route("/apex/agencies/<int:agency_id>/delete", methods=["POST"])
    @role_required("apex")
    def agency_delete(agency_id):
        agency = db.session.get(Agency, agency_id) or abort(404)
        db.session.delete(agency)
        db.session.commit()
        flash("Agency deleted.", "info")
        return redirect(url_for("agency_list"))

    @app.route("/apex/clients")
    @role_required("apex")
    def all_clients():
        return render_template("client_list.html", clients=Client.query.order_by(Client.last_name).all(), apex_view=True)

    @app.route("/agency")
    @role_required("agency")
    def agency_dashboard():
        agency = current_user.agency
        return render_template("agency_dashboard.html", agency=agency)

    @app.route("/agency/tools/form-filler", methods=["GET", "POST"])
    @role_required("agency")
    def agency_form_filler():
        agency = current_user.agency
        if not can_use_form_filler(agency):
            flash("This feature is not included in your current membership.", "warning")
            return redirect(url_for("agency_dashboard"))
        if request.method == "POST":
            client = db.session.get(Client, int(request.form["client_id"])) or abort(404)
            if client.agency_id != agency.id:
                abort(403)
            template = FormTemplate.query.filter_by(code=request.form["form_code"], is_active=True).first() or abort(404)
            case = Case(
                agency_id=agency.id,
                client_id=client.id,
                case_type=template.code,
                status="Waiting for Client",
                notes=request.form.get("notes", "").strip(),
            )
            db.session.add(case)
            db.session.commit()
            flash(f"{template.code} questionnaire assigned to {client.full_name}.", "success")
            return redirect(url_for("agency_form_filler"))
        templates = available_form_templates()
        template_codes = [template.code for template in templates]
        cases = (
            Case.query.filter(Case.agency_id == agency.id, Case.case_type.in_(template_codes))
            .order_by(Case.updated_at.desc())
            .all()
            if template_codes
            else []
        )
        return render_template(
            "agency_form_filler.html",
            agency=agency,
            clients=Client.query.filter_by(agency_id=agency.id).order_by(Client.last_name).all(),
            templates=templates,
            cases=cases,
        )

    @app.route("/agency/translators", methods=["GET", "POST"])
    @role_required("agency")
    def translator_list():
        agency = current_user.agency
        if request.method == "POST":
            translator = AgencyTranslator(agency_id=agency.id)
            populate_translator_from_form(translator)
            db.session.add(translator)
            db.session.commit()
            flash("Translator saved.", "success")
            return redirect(url_for("translator_list"))
        return render_template(
            "people_list.html",
            agency=agency,
            people=AgencyTranslator.query.filter_by(agency_id=agency.id).order_by(AgencyTranslator.full_name).all(),
            person_type="translator",
            title="Translators",
        )

    @app.route("/agency/translators/<int:translator_id>/edit", methods=["GET", "POST"])
    @role_required("agency")
    def translator_edit(translator_id):
        translator = AgencyTranslator.query.filter_by(id=translator_id, agency_id=current_user.agency_id).first() or abort(404)
        if request.method == "POST":
            populate_translator_from_form(translator)
            db.session.commit()
            flash("Translator updated.", "success")
            return redirect(url_for("translator_list"))
        return render_template("person_form.html", agency=current_user.agency, person=translator, person_type="translator", title="Edit Translator")

    @app.route("/agency/translators/<int:translator_id>/delete", methods=["POST"])
    @role_required("agency")
    def translator_delete(translator_id):
        translator = AgencyTranslator.query.filter_by(id=translator_id, agency_id=current_user.agency_id).first() or abort(404)
        Case.query.filter_by(translator_id=translator.id).update({"translator_id": None})
        db.session.delete(translator)
        db.session.commit()
        flash("Translator deleted.", "info")
        return redirect(url_for("translator_list"))

    @app.route("/agency/preparers", methods=["GET", "POST"])
    @role_required("agency")
    def preparer_list():
        agency = current_user.agency
        if request.method == "POST":
            preparer = AgencyPreparer(agency_id=agency.id)
            populate_preparer_from_form(preparer)
            db.session.add(preparer)
            db.session.commit()
            flash("Form preparer saved.", "success")
            return redirect(url_for("preparer_list"))
        return render_template(
            "people_list.html",
            agency=agency,
            people=AgencyPreparer.query.filter_by(agency_id=agency.id).order_by(AgencyPreparer.full_name).all(),
            person_type="preparer",
            title="Form Preparers",
        )

    @app.route("/agency/preparers/<int:preparer_id>/edit", methods=["GET", "POST"])
    @role_required("agency")
    def preparer_edit(preparer_id):
        preparer = AgencyPreparer.query.filter_by(id=preparer_id, agency_id=current_user.agency_id).first() or abort(404)
        if request.method == "POST":
            populate_preparer_from_form(preparer)
            db.session.commit()
            flash("Form preparer updated.", "success")
            return redirect(url_for("preparer_list"))
        return render_template("person_form.html", agency=current_user.agency, person=preparer, person_type="preparer", title="Edit Form Preparer")

    @app.route("/agency/preparers/<int:preparer_id>/delete", methods=["POST"])
    @role_required("agency")
    def preparer_delete(preparer_id):
        preparer = AgencyPreparer.query.filter_by(id=preparer_id, agency_id=current_user.agency_id).first() or abort(404)
        Case.query.filter_by(preparer_id=preparer.id).update({"preparer_id": None})
        db.session.delete(preparer)
        db.session.commit()
        flash("Form preparer deleted.", "info")
        return redirect(url_for("preparer_list"))

    @app.route("/clients")
    @role_required("apex", "agency")
    def client_list():
        if current_user.role == "apex":
            clients = Client.query.order_by(Client.last_name).all()
        else:
            clients = Client.query.filter_by(agency_id=current_user.agency_id).order_by(Client.last_name).all()
        return render_template("client_list.html", clients=clients, apex_view=current_user.role == "apex")

    @app.route("/clients/new", methods=["GET", "POST"])
    @role_required("apex", "agency")
    def client_create():
        agencies = Agency.query.order_by(Agency.agency_name).all() if current_user.role == "apex" else [current_user.agency]
        if request.method == "POST":
            agency_id = int(request.form.get("agency_id") or current_user.agency_id)
            if current_user.role == "agency" and agency_id != current_user.agency_id:
                abort(403)
            client = Client(agency_id=agency_id)
            populate_client_from_form(client)
            client.set_password(request.form["password"])
            db.session.add(client)
            db.session.commit()
            flash("Client created.", "success")
            return redirect(url_for("client_list"))
        return render_template("client_form.html", client=None, agencies=agencies)

    @app.route("/clients/<int:client_id>/edit", methods=["GET", "POST"])
    @role_required("apex", "agency")
    def client_edit(client_id):
        client = db.session.get(Client, client_id) or abort(404)
        if current_user.role == "agency" and client.agency_id != current_user.agency_id:
            abort(403)
        agencies = Agency.query.order_by(Agency.agency_name).all() if current_user.role == "apex" else [current_user.agency]
        if request.method == "POST":
            agency_id = int(request.form.get("agency_id") or client.agency_id)
            if current_user.role == "agency" and agency_id != current_user.agency_id:
                abort(403)
            client.agency_id = agency_id
            populate_client_from_form(client)
            if request.form.get("password"):
                client.set_password(request.form["password"])
            db.session.commit()
            flash("Client updated.", "success")
            return redirect(url_for("client_list"))
        return render_template("client_form.html", client=client, agencies=agencies)

    @app.route("/clients/<int:client_id>/delete", methods=["POST"])
    @role_required("apex", "agency")
    def client_delete(client_id):
        client = db.session.get(Client, client_id) or abort(404)
        if current_user.role == "agency" and client.agency_id != current_user.agency_id:
            abort(403)
        db.session.delete(client)
        db.session.commit()
        flash("Client deleted.", "info")
        return redirect(url_for("client_list"))

    @app.route("/cases")
    @role_required("apex", "agency")
    def case_list():
        if current_user.role == "apex":
            cases = Case.query.order_by(Case.updated_at.desc()).all()
        else:
            cases = Case.query.filter_by(agency_id=current_user.agency_id).order_by(Case.updated_at.desc()).all()
        return render_template("case_list.html", cases=cases)

    @app.route("/cases/new", methods=["GET", "POST"])
    @role_required("apex", "agency")
    def case_create():
        clients = visible_clients()
        if request.method == "POST":
            client = db.session.get(Client, int(request.form["client_id"])) or abort(404)
            if current_user.role == "agency" and client.agency_id != current_user.agency_id:
                abort(403)
            case_type = request.form["case_type"]
            if not agency_can_create_case_type(client.agency, case_type):
                flash("This feature is not included in your current membership.", "warning")
                return redirect(url_for("case_list"))
            case = Case(
                agency_id=client.agency_id,
                client_id=client.id,
                case_type=case_type,
                status=request.form.get("status") or "Created",
                notes=request.form.get("notes", "").strip(),
            )
            db.session.add(case)
            db.session.commit()
            save_case_documents(case, "agency")
            flash("Case created.", "success")
            return redirect(url_for("case_list"))
        return render_template("case_form.html", case=None, clients=clients)

    @app.route("/cases/<int:case_id>/edit", methods=["GET", "POST"])
    @role_required("apex", "agency")
    def case_edit(case_id):
        case = query_case_for_role(case_id)
        clients = visible_clients()
        if request.method == "POST":
            client = db.session.get(Client, int(request.form["client_id"])) or abort(404)
            if current_user.role == "agency" and client.agency_id != current_user.agency_id:
                abort(403)
            case.client_id = client.id
            case.agency_id = client.agency_id
            new_case_type = request.form["case_type"]
            if not agency_can_create_case_type(client.agency, new_case_type):
                flash("This feature is not included in your current membership.", "warning")
                return redirect(url_for("case_edit", case_id=case.id))
            case.case_type = new_case_type
            case.status = request.form["status"]
            case.notes = request.form.get("notes", "").strip()
            db.session.commit()
            save_case_documents(case, current_user.role)
            flash("Case updated.", "success")
            return redirect(url_for("case_list"))
        return render_template("case_form.html", case=case, clients=clients)

    @app.route("/cases/<int:case_id>/review", methods=["GET", "POST"])
    @role_required("apex", "agency")
    def case_review(case_id):
        case = query_case_for_role(case_id)
        questions = CaseQuestion.query.filter_by(case_type=case.case_type).order_by(CaseQuestion.sort_order).all()
        answers = {answer.question_id: answer for answer in case.answers}
        if request.method == "POST":
            for question in questions:
                answer = answers.get(question.id) or CaseAnswer(case_id=case.id, question_id=question.id)
                answer.answer_text = request.form.get(f"question_{question.id}", "").strip()
                db.session.add(answer)
            case.status = request.form.get("status", case.status)
            assign_case_people_from_form(case)
            update_case_progress(case)
            db.session.commit()
            flash("Case answers updated.", "success")
            return redirect(url_for("case_review", case_id=case.id))
        return render_template(
            "case_review.html",
            case=case,
            questions=questions,
            answers=answers,
            translators=AgencyTranslator.query.filter_by(agency_id=case.agency_id).order_by(AgencyTranslator.full_name).all(),
            preparers=AgencyPreparer.query.filter_by(agency_id=case.agency_id).order_by(AgencyPreparer.full_name).all(),
        )

    @app.route("/cases/<int:case_id>/generate", methods=["POST"])
    @role_required("apex", "agency")
    def generate_form(case_id):
        case = query_case_for_role(case_id)
        if not can_use_form_filler(case.agency):
            flash("This feature is not included in your current membership.", "warning")
            return redirect(url_for("case_review", case_id=case.id))
        questions = CaseQuestion.query.filter_by(case_type=case.case_type).order_by(CaseQuestion.sort_order).all()
        answers = {answer.question_id: answer for answer in case.answers}
        for question in questions:
            form_key = f"question_{question.id}"
            if form_key in request.form:
                answer = answers.get(question.id) or CaseAnswer(case_id=case.id, question_id=question.id)
                answer.answer_text = request.form.get(form_key, "").strip()
                db.session.add(answer)
        assign_case_people_from_form(case)
        update_case_progress(case)
        if case.progress_percentage < 100:
            db.session.commit()
            flash("The case is not ready yet. All questionnaire answers are required before generation.", "warning")
            return redirect(url_for("case_review", case_id=case.id))
        filename = generate_case_pdf(case)
        generated = GeneratedForm(case_id=case.id, filename=filename)
        doc = CaseDocument(
            case_id=case.id,
            uploaded_by_role=current_user.role,
            original_filename=os.path.basename(filename),
            stored_filename=filename,
            document_type="Generated Form Summary",
        )
        case.status = "Generated"
        db.session.add_all([generated, doc])
        db.session.commit()
        flash("PDF answer summary generated.", "success")
        return redirect(url_for("generated_documents", case_id=case.id))

    @app.route("/cases/<int:case_id>/generated")
    @role_required("apex", "agency")
    def generated_documents(case_id):
        case = query_case_for_role(case_id)
        return render_template("generated_documents.html", case=case)

    @app.route("/client")
    @role_required("client")
    def client_dashboard():
        return render_template("client_dashboard.html", client=current_user)

    @app.route("/client/cases/<int:case_id>/questionnaire", methods=["GET", "POST"])
    @role_required("client")
    def questionnaire(case_id):
        case = query_case_for_role(case_id)
        answers = {answer.question_id: answer for answer in case.answers}
        answers_by_question = {answer.question_id: answer.answer_text or "" for answer in case.answers}
        all_questions = CaseQuestion.query.filter_by(case_type=case.case_type).order_by(CaseQuestion.sort_order).all()
        questions = visible_questions_for_answers(all_questions, answers_by_question)
        if not questions:
            return render_template("questionnaire.html", case=case, questions=questions, answers=answers)
        raw_index = request.args.get("q", 1, type=int)
        current_index = min(max(raw_index, 1), len(questions))
        current_question = questions[current_index - 1]
        if request.method == "POST":
            answer = answers.get(current_question.id) or CaseAnswer(case_id=case.id, question_id=current_question.id)
            answer.answer_text = request.form.get(f"question_{current_question.id}", "").strip()
            db.session.add(answer)
            update_case_progress(case)
            db.session.commit()
            save_case_documents(case, "client")
            answers = {answer.question_id: answer for answer in case.answers}
            answers_by_question = {answer.question_id: answer.answer_text or "" for answer in case.answers}
            questions = visible_questions_for_answers(all_questions, answers_by_question)
            current_index = next((idx for idx, question in enumerate(questions, start=1) if question.id == current_question.id), current_index)
            action = request.form.get("action", "save")
            if action == "next" and current_index < len(questions):
                return redirect(url_for("questionnaire", case_id=case.id, q=current_index + 1))
            if action == "later" and current_index < len(questions):
                unanswered = first_unanswered_question_index(case, questions, skip_question_id=current_question.id)
                return redirect(url_for("questionnaire", case_id=case.id, q=unanswered or current_index + 1))
            flash("Progress saved.", "success")
            return redirect(url_for("client_dashboard"))
        return render_template(
            "questionnaire.html",
            case=case,
            questions=questions,
            answers=answers,
            current_question=current_question,
            current_index=current_index,
        )

    @app.route("/uploads/<path:filename>")
    @login_required
    def uploaded_file(filename):
        authorize_upload_access(filename)
        return send_from_directory(app.config["UPLOAD_FOLDER"], filename, as_attachment=True)


def agency_ip_allowed(agency, ip_address):
    cutoff = ActiveSession.active_window()
    active_ips = (
        db.session.query(ActiveSession.ip_address)
        .filter(
            ActiveSession.role == "agency",
            ActiveSession.agency_id == agency.id,
            ActiveSession.last_activity >= cutoff,
        )
        .distinct()
        .all()
    )
    active = {row[0] for row in active_ips}
    return ip_address in active or len(active) < agency.total_ips_allowed


def record_login(user):
    db.session.add(
        ActiveSession(
            user_id=user.id,
            role=user.role,
            agency_id=getattr(user, "agency_id", None),
            ip_address=request.remote_addr or "unknown",
        )
    )
    db.session.commit()


def authorize_upload_access(filename):
    parts = filename.replace("\\", "/").split("/")
    if len(parts) < 2:
        abort(403)
    if parts[0] == "agencies":
        try:
            agency_id = int(parts[1])
        except ValueError:
            abort(403)
        if current_user.role == "apex":
            return
        if current_user.role == "agency" and current_user.agency_id == agency_id:
            return
        abort(403)
    if parts[0] == "cases":
        try:
            case_id = int(parts[1])
        except ValueError:
            abort(403)
        query_case_for_role(case_id)
        return
    abort(403)


def visible_clients():
    if current_user.role == "apex":
        return Client.query.order_by(Client.last_name).all()
    return Client.query.filter_by(agency_id=current_user.agency_id).order_by(Client.last_name).all()


def render_person_details(pdf, x, y, label, person):
    pdf.setFont("Helvetica-Bold", 9)
    pdf.drawString(x, y, label)
    y -= 12
    pdf.setFont("Helvetica", 9)
    if not person:
        pdf.drawString(x + 12, y, "None selected")
        return y - 14
    lines = [person.full_name]
    if isinstance(person, AgencyTranslator):
        lines.append(f"Language: {person.language}")
    if isinstance(person, AgencyPreparer) and person.title:
        lines.append(f"Title: {person.title}")
    if person.phone:
        lines.append(f"Phone: {person.phone}")
    if person.email:
        lines.append(f"Email: {person.email}")
    if person.address:
        lines.append(f"Address: {person.address}")
    for line in lines:
        pdf.drawString(x + 12, y, line[:100])
        y -= 12
    return y - 6


def save_agency_documents(agency):
    for file_storage in request.files.getlist("documents"):
        try:
            saved = save_upload(file_storage, f"agencies/{agency.id}")
        except ValueError as exc:
            flash(str(exc), "warning")
            continue
        if saved:
            original, stored = saved
            db.session.add(AgencyDocument(agency_id=agency.id, original_filename=original, stored_filename=stored))
    db.session.commit()


def save_case_documents(case, uploaded_by_role):
    for file_storage in request.files.getlist("documents"):
        try:
            saved = save_upload(file_storage, f"cases/{case.id}")
        except ValueError as exc:
            flash(str(exc), "warning")
            continue
        if saved:
            original, stored = saved
            db.session.add(
                CaseDocument(
                    case_id=case.id,
                    uploaded_by_role=uploaded_by_role,
                    original_filename=original,
                    stored_filename=stored,
                )
            )
    db.session.commit()


def create_answer_summary_pdf(case):
    folder = f"cases/{case.id}/generated"
    os.makedirs(os.path.join(app.config["UPLOAD_FOLDER"], folder), exist_ok=True)
    filename = f"{folder}/answer_summary_{uuid.uuid4().hex[:8]}.pdf"
    output_path = os.path.join(app.config["UPLOAD_FOLDER"], filename)
    pdf = canvas.Canvas(output_path, pagesize=letter)
    width, height = letter
    y = height - 50
    pdf.setFont("Helvetica-Bold", 14)
    pdf.drawString(50, y, f"{case.case_type} Answer Summary")
    y -= 24
    pdf.setFont("Helvetica", 10)
    pdf.drawString(50, y, f"Client: {case.client.full_name}")
    y -= 16
    pdf.drawString(50, y, f"Agency: {case.agency.agency_name}")
    y -= 22
    y = render_person_details(pdf, 50, y, "Translator", case.translator)
    y = render_person_details(pdf, 50, y, "Form Preparer", case.preparer)
    y -= 8
    pdf.setFont("Helvetica", 9)
    questions = CaseQuestion.query.filter_by(case_type=case.case_type).order_by(CaseQuestion.sort_order).all()
    answers = {answer.question_id: answer.answer_text or "" for answer in case.answers}
    for question in questions:
        if y < 80:
            pdf.showPage()
            y = height - 50
            pdf.setFont("Helvetica", 9)
        pdf.setFont("Helvetica-Bold", 9)
        pdf.drawString(50, y, question.prompt[:90])
        y -= 14
        pdf.setFont("Helvetica", 9)
        text = answers.get(question.id, "")
        for line in split_pdf_lines(text, 95):
            pdf.drawString(65, y, line)
            y -= 12
        y -= 8
    pdf.save()
    return filename


def generate_case_pdf(case):
    template = FormTemplate.query.filter_by(code=case.case_type, is_active=True).first()
    if not template or not template.pdf_stored_filename:
        return create_answer_summary_pdf(case)
    if template.pdf_generation_strategy == "acroform_fill_need_appearances":
        filled = fill_acroform_pdf(case, template)
        if filled:
            return filled
    # USCIS XFA/hybrid fallback: preserve the original PDF; do not mutate XFA/XML.
    return create_preserved_template_answer_packet(case, template)


def fill_acroform_pdf(case, template):
    try:
        from pypdf import PdfReader, PdfWriter
        from pypdf.generic import BooleanObject, NameObject
    except ImportError:
        return None
    source_path = os.path.join(app.config["UPLOAD_FOLDER"], template.pdf_stored_filename)
    folder = f"cases/{case.id}/generated"
    os.makedirs(os.path.join(app.config["UPLOAD_FOLDER"], folder), exist_ok=True)
    filename = f"{folder}/{case.case_type.lower()}_filled_{uuid.uuid4().hex[:8]}.pdf"
    output_path = os.path.join(app.config["UPLOAD_FOLDER"], filename)
    try:
        reader = PdfReader(source_path)
        writer = PdfWriter()
        for page in reader.pages:
            writer.add_page(page)
        if "/AcroForm" in reader.trailer["/Root"]:
            writer._root_object.update({NameObject("/AcroForm"): reader.trailer["/Root"]["/AcroForm"]})
            writer._root_object["/AcroForm"].update({NameObject("/NeedAppearances"): BooleanObject(True)})
        field_values = answer_values_by_pdf_field(case)
        for page in writer.pages:
            writer.update_page_form_field_values(page, field_values)
        with open(output_path, "wb") as output:
            writer.write(output)
        return filename
    except Exception:
        return None


def answer_values_by_pdf_field(case):
    values = {}
    for answer in case.answers:
        if answer.question and answer.answer_text:
            values[answer.question.field_key] = answer.answer_text
    return values


def create_preserved_template_answer_packet(case, template):
    folder = f"cases/{case.id}/generated"
    os.makedirs(os.path.join(app.config["UPLOAD_FOLDER"], folder), exist_ok=True)
    filename = f"{folder}/{case.case_type.lower()}_preserved_packet_{uuid.uuid4().hex[:8]}.pdf"
    output_path = os.path.join(app.config["UPLOAD_FOLDER"], filename)
    packet = canvas.Canvas(output_path, pagesize=letter)
    width, height = letter
    packet.setFont("Helvetica-Bold", 13)
    packet.drawString(50, height - 50, f"{case.case_type} Preserved USCIS PDF Packet")
    packet.setFont("Helvetica", 9)
    packet.drawString(50, height - 68, "The original USCIS PDF template is preserved. XFA/XML was not modified.")
    packet.drawString(50, height - 82, "Coordinate-based overlay mapping will place answers on exact fields once approved in Apex.")
    y = height - 112
    y = render_person_details(packet, 50, y, "Translator", case.translator)
    y = render_person_details(packet, 50, y, "Form Preparer", case.preparer)
    y -= 8
    questions = CaseQuestion.query.filter_by(case_type=case.case_type).order_by(CaseQuestion.sort_order).all()
    answers = {answer.question_id: answer.answer_text or "" for answer in case.answers}
    for question in questions:
        if y < 80:
            packet.showPage()
            y = height - 50
            packet.setFont("Helvetica", 9)
        packet.setFont("Helvetica-Bold", 9)
        packet.drawString(50, y, question.prompt[:95])
        y -= 13
        packet.setFont("Helvetica", 9)
        for line in split_pdf_lines(answers.get(question.id, ""), 95):
            packet.drawString(65, y, line)
            y -= 11
        y -= 7
    packet.save()
    return filename


def split_pdf_lines(text, max_chars):
    text = text or "(No answer)"
    lines = []
    for paragraph in text.splitlines() or [text]:
        words = paragraph.split()
        line = ""
        for word in words:
            if len(f"{line} {word}".strip()) > max_chars:
                lines.append(line)
                line = word
            else:
                line = f"{line} {word}".strip()
        lines.append(line)
    return lines


def init_database():
    db.create_all()
    ensure_sqlite_schema()
    for name in SUBSCRIPTION_TOOLS:
        if not SubscriptionTool.query.filter_by(name=name).first():
            db.session.add(SubscriptionTool(name=name))
    for code, name, description in FORM_TEMPLATES:
        template = FormTemplate.query.filter_by(code=code).first()
        if not template:
            db.session.add(FormTemplate(code=code, name=name, description=description))
    if not ApexUser.query.filter_by(username="apexadmin").first():
        apex = ApexUser(username="apexadmin")
        apex.set_password("ChangeMe123!")
        db.session.add(apex)
    for index, (field_key, prompt, input_type) in enumerate(I589_QUESTIONS, start=1):
        if not CaseQuestion.query.filter_by(case_type="I-589", field_key=field_key).first():
            db.session.add(
                CaseQuestion(
                    case_type="I-589",
                    field_key=field_key,
                    prompt=prompt,
                    input_type=input_type,
                    sort_order=index,
                )
            )
    for index, (field_key, prompt, input_type) in enumerate(I485_QUESTIONS, start=1):
        if not CaseQuestion.query.filter_by(case_type="I-485", field_key=field_key).first():
            db.session.add(
                CaseQuestion(
                    case_type="I-485",
                    field_key=field_key,
                    prompt=prompt,
                    input_type=input_type,
                    sort_order=index,
                )
            )
    db.session.commit()


def ensure_sqlite_schema():
    inspector = inspect(db.engine)
    if "form_template" not in inspector.get_table_names():
        return
    existing = {column["name"] for column in inspector.get_columns("form_template")}
    additions = {
        "pdf_original_filename": "VARCHAR(255)",
        "pdf_stored_filename": "VARCHAR(255)",
        "pdf_kind": "VARCHAR(40) DEFAULT 'not_uploaded' NOT NULL",
        "pdf_field_count": "INTEGER DEFAULT 0 NOT NULL",
        "pdf_generation_strategy": "VARCHAR(80) DEFAULT 'summary_pdf' NOT NULL",
    }
    for column, ddl in additions.items():
        if column not in existing:
            db.session.execute(text(f"ALTER TABLE form_template ADD COLUMN {column} {ddl}"))
    if "case_question" in inspector.get_table_names():
        existing_question = {column["name"] for column in inspector.get_columns("case_question")}
        question_additions = {
            "show_if_question_id": "INTEGER",
            "show_if_operator": "VARCHAR(30) DEFAULT 'equals' NOT NULL",
            "show_if_value": "VARCHAR(255)",
        }
        for column, ddl in question_additions.items():
            if column not in existing_question:
                db.session.execute(text(f"ALTER TABLE case_question ADD COLUMN {column} {ddl}"))
    if "case" in inspector.get_table_names():
        existing_case = {column["name"] for column in inspector.get_columns("case")}
        case_additions = {
            "translator_id": "INTEGER",
            "preparer_id": "INTEGER",
        }
        for column, ddl in case_additions.items():
            if column not in existing_case:
                db.session.execute(text(f"ALTER TABLE 'case' ADD COLUMN {column} {ddl}"))
    db.session.commit()


app = create_app()


with app.app_context():
    if os.environ.get("AUTO_INIT_DB", "1") == "1":
        init_database()


if __name__ == "__main__":
    app.run(debug=os.environ.get("FLASK_DEBUG", "1") == "1")
