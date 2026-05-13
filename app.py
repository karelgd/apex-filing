import os
import importlib.util
import json
import re
import uuid
from io import BytesIO
from datetime import datetime
from decimal import Decimal, InvalidOperation

from flask import (
    Flask,
    abort,
    flash,
    redirect,
    render_template,
    Response,
    request,
    send_from_directory,
    url_for,
)
from flask_login import LoginManager, current_user, login_required, login_user, logout_user
from flask_wtf import CSRFProtect
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.pdfgen import canvas
from werkzeug.utils import secure_filename
from sqlalchemy import inspect, text
import click

from forms import CASE_STATUSES, CASE_TYPES, FORM_TEMPLATES, I485_QUESTIONS, I589_QUESTIONS, SUBSCRIPTION_TOOLS, US_STATES
from models import (
    ActiveSession,
    Agency,
    AgencyDocument,
    AgencyLawFirm,
    AgencyLawyer,
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
    ImmigrationCourt,
    ImmigrationJudge,
    MotionDraft,
    MotionRespondent,
    MotionTemplate,
    OplaOffice,
    PdfField,
    SubscriptionTool,
    db,
)

OPLAOffice = OplaOffice
Judge = ImmigrationJudge


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


def clear_template_questions(code):
    questions = CaseQuestion.query.filter_by(case_type=code).all()
    question_ids = [question.id for question in questions]
    if not question_ids:
        return 0
    CaseQuestion.query.filter(CaseQuestion.show_if_question_id.in_(question_ids)).update(
        {"show_if_question_id": None, "show_if_value": ""},
        synchronize_session=False,
    )
    PdfField.query.filter(PdfField.mapped_question_id.in_(question_ids)).update(
        {"mapped_question_id": None},
        synchronize_session=False,
    )
    CaseAnswer.query.filter(CaseAnswer.question_id.in_(question_ids)).delete(synchronize_session=False)
    for question in questions:
        db.session.delete(question)
    db.session.flush()
    return len(question_ids)


def delete_case_question(question):
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


def readable_pdf_field_name(field_name):
    raw = short_pdf_field_key(field_name)
    replacements = {
        "CCHolder": "Credit Card Holder ",
        "AptSteFlr": "Apartment/Suite/Floor",
        "DOB": "Date of Birth",
        "SSN": "Social Security Number",
        "USCIS": "USCIS",
        "PDF417BarCode": "USCIS Barcode",
    }
    for old, new in replacements.items():
        raw = raw.replace(old, new)
    label = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", raw)
    label = re.sub(r"\s+", " ", label).strip()
    label = label.replace("Given Name", "Given Name (First Name)")
    label = label.replace("Family Name", "Family Name (Last Name)")
    label = label.replace("Middle Name", "Middle Name")
    return label or field_name


def short_pdf_field_key(field_name):
    raw = (field_name or "").split(".")[-1]
    raw = raw.replace("#", "")
    raw = re.sub(r"\[\d+\]", "", raw)
    return raw


def terminal_pdf_field_key(field_name):
    raw = (field_name or "").split(".")[-1]
    return raw.replace("#", "")


def normalized_pdf_field_key(field_name):
    return re.sub(r"[^a-z0-9]", "", (field_name or "").lower())


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


def seed_questions_from_pdf_fields(template):
    if CaseQuestion.query.filter_by(case_type=template.code).first():
        return 0
    fields = PdfField.query.filter_by(template_id=template.id).order_by(PdfField.page_number, PdfField.id).all()
    seeded = 0
    for field in fields:
        if should_skip_pdf_field(field.field_name):
            continue
        seeded += 1
        db.session.add(
            CaseQuestion(
                case_type=template.code,
                field_key=field.field_name,
                prompt=readable_pdf_field_name(field.field_name)[:255],
                input_type=guess_input_type(field.field_name, field.field_type),
                sort_order=seeded,
                required=True,
            )
        )
    return seeded


def should_skip_pdf_field(field_name):
    lower = (field_name or "").lower()
    return any(token in lower for token in ("barcode", "pdf417", "pagecount", "signature"))


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


def guess_input_type(prompt, field_type=None):
    if field_type and "check" in field_type.lower():
        return "checkbox"
    lower = prompt.lower()
    if any(token in lower for token in ("checkbox", "check box", "check if", "select if")):
        return "checkbox"
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
    preparer.title = ""
    preparer.phone = request.form.get("phone", "").strip()
    preparer.email = request.form.get("email", "").strip()
    preparer.address = request.form.get("address", "").strip()


def populate_lawyer_from_form(lawyer):
    lawyer.first_name = request.form["first_name"].strip()
    lawyer.middle_name = request.form.get("middle_name", "").strip()
    lawyer.last_name = request.form["last_name"].strip()
    lawyer.bar_number = request.form["bar_number"].strip()
    lawyer.phone = request.form.get("phone", "").strip()
    lawyer.email = request.form.get("email", "").strip()


def populate_law_firm_from_form(firm):
    firm.name = request.form["name"].strip()
    firm.phone = request.form.get("phone", "").strip()
    firm.address = request.form["address"].strip()


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
    all_questions = CaseQuestion.query.filter_by(case_type=case.case_type, client_visible=True).order_by(CaseQuestion.sort_order).all()
    answers_by_question = {answer.question_id: answer.answer_text or "" for answer in case.answers}
    questions = visible_questions_for_answers(all_questions, answers_by_question)
    if not questions:
        case.progress_percentage = 0
        return
    visible_question_ids = [question.id for question in questions]
    answered = CaseAnswer.query.filter(
        CaseAnswer.case_id == case.id,
        CaseAnswer.question_id.in_(visible_question_ids),
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


def can_use_motion_creation(agency):
    return bool(agency and agency.has_tool("Motion Creation"))


def available_form_templates(active_only=True):
    query = FormTemplate.query.order_by(FormTemplate.code)
    if active_only:
        query = query.filter_by(is_active=True)
    return query.all()


def available_case_types():
    form_codes = [template.code for template in FormTemplate.query.filter_by(is_active=True).order_by(FormTemplate.code)]
    existing = list(dict.fromkeys(form_codes + CASE_TYPES))
    return existing


def template_matches_search(template, lowered_search):
    haystack = " ".join(
        [
            template.code or "",
            template.name or "",
            template.description or "",
            template.pdf_original_filename or "",
            template.pdf_kind or "",
            template.pdf_generation_strategy or "",
        ]
    ).lower()
    return lowered_search in haystack


def save_form_template_from_request():
    code = request.form["code"].strip().upper()
    template = FormTemplate.query.filter_by(code=code).first()
    rebuilding_existing_template = bool(template)
    template = template or FormTemplate(code=code)
    template.name = request.form["name"].strip()
    template.description = request.form.get("description", "").strip()
    template.is_active = bool(request.form.get("is_active"))
    db.session.add(template)
    db.session.flush()
    question_lines = request.form.get("questions", "")
    if rebuilding_existing_template:
        clear_template_questions(code)
    if question_lines.strip():
        replace_template_questions(code, question_lines)
    pdf_path = detect_pdf_template(template, request.files.get("pdf_template"))
    if not pdf_path and template.pdf_stored_filename:
        existing_pdf_path = os.path.join(app.config["UPLOAD_FOLDER"], template.pdf_stored_filename)
        pdf_path = existing_pdf_path if os.path.exists(existing_pdf_path) else None
    seeded_count = 0
    if not question_lines.strip():
        seeded_count = seed_questions_from_pdf_fields(template)
        if not seeded_count and pdf_path:
            seeded_count = seed_questions_from_pdf_text(code, pdf_path)
    db.session.commit()
    return seeded_count


def motion_profile_block(motion):
    return {
        "court_name": motion.immigration_court,
        "court_address": motion.immigration_court_address or "",
        "judge_name": motion.immigration_judge,
        "opla_name": motion.opla_office,
        "opla_address": motion.opla_address or "",
        "lawyer_name": motion.lawyer_name or "Pro Se",
        "lawyer_bar_number": motion.lawyer_bar_number or "",
        "law_firm_name": motion.law_firm_name or "",
        "law_firm_phone": motion.law_firm_phone or "",
        "law_firm_address": motion.law_firm_address or "",
    }


def exhibit_label(index):
    letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    if index < len(letters):
        return letters[index]
    first = letters[(index // len(letters)) - 1]
    second = letters[index % len(letters)]
    return f"{first}{second}"


def normalize_exhibits(raw_exhibits):
    exhibits = []
    for value in raw_exhibits:
        text_value = (value or "").strip()
        if text_value:
            exhibits.append(text_value)
    return exhibits


def render_exhibits_text(exhibits):
    return "\n".join(f"Exhibit {exhibit_label(index)} - {description}" for index, description in enumerate(exhibits))


def render_motion_content(template, respondents, court, court_address, judge, opla, opla_address, lawyer, law_firm, exhibits=None, detention_status="", next_hearing_date="", next_hearing_type=""):
    exhibits = exhibits or []
    lead = respondents[0] if respondents else {}
    respondent_lines = [
        f"{person.get('last_name', '').upper()}, {person.get('first_name', '')} {person.get('middle_name', '')}".strip()
        + f" (A# {person.get('alien_number', '')})"
        for person in respondents
    ]
    lawyer_name = lawyer.full_name if lawyer else "Pro Se"
    lawyer_bar_number = lawyer.bar_number if lawyer else ""
    law_firm_name = law_firm.name if law_firm else ""
    law_firm_phone = law_firm.phone if law_firm else ""
    law_firm_address = law_firm.address if law_firm else ""
    variables = {
        "alien_first_name": lead.get("first_name", ""),
        "alien_middle_name": lead.get("middle_name", ""),
        "alien_last_name": lead.get("last_name", ""),
        "alien_full_name": " ".join(part for part in [lead.get("first_name", ""), lead.get("middle_name", ""), lead.get("last_name", "")] if part),
        "alien_number": lead.get("alien_number", ""),
        "respondents": "\n".join(respondent_lines),
        "immigration_court": court,
        "immigration_court_address": court_address,
        "immigration_judge": judge,
        "opla_office": opla,
        "opla_address": opla_address,
        "lawyer_name": lawyer_name,
        "lawyer_bar_number": lawyer_bar_number,
        "law_firm_name": law_firm_name,
        "law_firm_phone": law_firm_phone,
        "law_firm_address": law_firm_address,
        "exhibits": render_exhibits_text(exhibits),
        "detention_status": detention_status,
        "next_hearing_date": next_hearing_date,
        "next_hearing_type": next_hearing_type,
        "today": datetime.utcnow().strftime("%B %d, %Y"),
    }
    rendered = template.content or ""
    for key, value in variables.items():
        rendered = rendered.replace(f"{{{{ {key} }}}}", value).replace(f"{{{{{key}}}}}", value)
    caption = "\n".join(
        [
            "UNITED STATES DEPARTMENT OF JUSTICE",
            "EXECUTIVE OFFICE FOR IMMIGRATION REVIEW",
            court.upper(),
            court_address,
            "",
            "In the Matter of:",
            variables["respondents"],
            "",
            "Respondent(s).",
            "",
            f"Before: {judge}",
        ]
    )
    representative = "\n".join(
        [
            lawyer_name,
            f"Bar No.: {lawyer_bar_number}" if lawyer_bar_number else "",
            law_firm_name,
            law_firm_address,
            law_firm_phone,
        ]
    )
    proof = "\n".join(
        [
            "CERTIFICATE OF SERVICE",
            "",
            f"I certify that on {variables['today']}, a true and correct copy of the foregoing motion was served on:",
            "",
            opla,
            opla_address,
            "",
            "by the method required by the Immigration Court practice rules.",
            "",
            "Respectfully submitted,",
            representative,
            "",
            "Signature: ______________________________",
        ]
    )
    order = "\n".join(
        [
            "PROPOSED ORDER",
            "",
            "Upon consideration of the foregoing motion, it is hereby:",
            "",
            "[  ] GRANTED",
            "[  ] DENIED",
            "",
            "Date: ____________________",
            "",
            "________________________________________",
            "Immigration Judge",
        ]
    )
    sections = [
        "PRESENTATION",
        caption,
        template.display_name.upper(),
        rendered,
        "EXHIBITS",
        render_exhibits_text(exhibits) or "No exhibits listed.",
        "PROOF OF SERVICE",
        proof,
        "PROPOSED ORDER",
        order,
    ]
    return "\n\n".join(section.strip() for section in sections if section.strip()).strip()


def motion_reference_lists():
    return {
        "courts": ImmigrationCourt.query.filter_by(is_active=True).order_by(ImmigrationCourt.name).all(),
        "judges": ImmigrationJudge.query.filter_by(is_active=True).order_by(ImmigrationJudge.name).all(),
        "opla_offices": OplaOffice.query.filter_by(is_active=True).order_by(OplaOffice.name).all(),
    }


def motion_for_agency(motion_id, agency_id):
    return MotionDraft.query.filter_by(id=motion_id, agency_id=agency_id).first() or abort(404)


def motion_template_for_agency(template_id, agency_id):
    return MotionTemplate.query.filter_by(id=template_id, agency_id=agency_id).first() or abort(404)


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

    @app.cli.command("import-motion-references")
    @click.argument("source_path", required=False)
    @click.option("--kind", "kinds", multiple=True, type=click.Choice(["courts", "opla", "judges"]), help="Reference type to import. Repeat for more than one.")
    def import_motion_references_command(source_path, kinds):
        source_path = source_path or os.path.join(BASE_DIR, "import_courts_and_opla.py")
        if not os.path.exists(source_path):
            raise click.ClickException(f"Import source not found: {source_path}")
        selected_kinds = kinds or ("courts", "opla", "judges")
        spec = importlib.util.spec_from_file_location("motion_reference_importer", source_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        imported = []
        for kind in selected_kinds:
            function_name = f"import_{kind}"
            importer = getattr(module, function_name, None)
            if not importer:
                raise click.ClickException(f"{source_path} does not define {function_name}().")
            importer()
            imported.append(kind)
        print(f"Imported motion references: {', '.join(imported)}")

    @app.cli.command("motion-reference-counts")
    def motion_reference_counts_command():
        print(f"Courts: {ImmigrationCourt.query.count()}")
        print(f"Judges: {ImmigrationJudge.query.count()}")
        print(f"OPLA offices: {OplaOffice.query.count()}")

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

    @app.route("/apex/subscriptions/form-filler")
    @role_required("apex")
    def apex_form_filler_admin():
        status = request.args.get("status", "active")
        if status not in {"active", "inactive"}:
            status = "active"
        search = request.args.get("q", "").strip()
        templates = FormTemplate.query.filter_by(is_active=(status == "active")).order_by(FormTemplate.code).all()
        if search:
            lowered = search.lower()
            templates = [template for template in templates if template_matches_search(template, lowered)]
        return render_template(
            "apex_form_filler_admin.html",
            templates=templates,
            status=status,
            search=search,
            active_count=FormTemplate.query.filter_by(is_active=True).count(),
            inactive_count=FormTemplate.query.filter_by(is_active=False).count(),
        )

    @app.route("/apex/subscriptions/form-filler/new", methods=["GET", "POST"])
    @role_required("apex")
    def apex_form_template_new():
        if request.method == "POST":
            seeded_count = save_form_template_from_request()
            if seeded_count:
                flash(f"Questionnaire created with {seeded_count} draft questions extracted from the PDF.", "success")
            else:
                flash("Questionnaire created.", "success")
            return redirect(url_for("apex_form_filler_admin"))
        return render_template("apex_form_template_form.html")

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
            question.render_mode = request.form.get("render_mode") or "normal"
            try:
                question.render_box_count = max(0, int(request.form.get("render_box_count") or 0))
            except ValueError:
                question.render_box_count = 0
            question.sort_order = int(request.form.get("sort_order") or len(questions) + 1)
            question.required = bool(request.form.get("required"))
            question.client_visible = bool(request.form.get("client_visible"))
            show_if_question_id = request.form.get("show_if_question_id")
            question.show_if_question_id = int(show_if_question_id) if show_if_question_id else None
            question.show_if_operator = request.form.get("show_if_operator") or "equals"
            question.show_if_value = request.form.get("show_if_value", "").strip()
            db.session.add(question)
            db.session.commit()
            flash("Question saved.", "success")
            return redirect(url_for("apex_form_builder", template_id=template.id))
        pdf_fields = PdfField.query.filter_by(template_id=template.id).order_by(PdfField.field_name).all()
        pdf_field_options = [
            {
                "name": field.field_name,
                "label": readable_pdf_field_name(field.field_name),
                "type": field.field_type,
                "page": field.page_number,
            }
            for field in pdf_fields
        ]
        return render_template(
            "apex_form_builder.html",
            template=template,
            questions=questions,
            pdf_fields=pdf_fields,
            pdf_field_options=pdf_field_options,
        )

    @app.route("/apex/subscriptions/form-filler/<int:template_id>/builder/questions/<int:question_id>/delete", methods=["POST"])
    @role_required("apex")
    def apex_form_question_delete(template_id, question_id):
        template = db.session.get(FormTemplate, template_id) or abort(404)
        question = db.session.get(CaseQuestion, question_id) or abort(404)
        if question.case_type != template.code:
            abort(404)
        delete_case_question(question)
        db.session.flush()
        reorder_template_questions(template.code)
        db.session.commit()
        flash("Question deleted from the questionnaire.", "info")
        return redirect(url_for("apex_form_builder", template_id=template.id))

    @app.route("/apex/subscriptions/form-filler/<int:template_id>/builder/questions/delete-marked", methods=["POST"])
    @role_required("apex")
    def apex_form_questions_bulk_delete(template_id):
        template = db.session.get(FormTemplate, template_id) or abort(404)
        question_ids = []
        for raw_id in request.form.getlist("question_ids"):
            try:
                question_ids.append(int(raw_id))
            except (TypeError, ValueError):
                continue
        if not question_ids:
            flash("Select at least one question to delete.", "warning")
            return redirect(url_for("apex_form_builder", template_id=template.id))

        questions = CaseQuestion.query.filter(
            CaseQuestion.case_type == template.code,
            CaseQuestion.id.in_(question_ids),
        ).all()
        for question in questions:
            delete_case_question(question)
        db.session.flush()
        reorder_template_questions(template.code)
        db.session.commit()
        flash(f"{len(questions)} marked question{'s' if len(questions) != 1 else ''} deleted.", "info")
        return redirect(url_for("apex_form_builder", template_id=template.id))

    @app.route("/apex/subscriptions/form-filler/<int:template_id>/builder/questions/client-visibility", methods=["POST"])
    @role_required("apex")
    def apex_form_questions_client_visibility(template_id):
        template = db.session.get(FormTemplate, template_id) or abort(404)
        question_ids = []
        for raw_id in request.form.getlist("question_ids"):
            try:
                question_ids.append(int(raw_id))
            except (TypeError, ValueError):
                continue
        if not question_ids:
            flash("Select at least one question first.", "warning")
            return redirect(url_for("apex_form_builder", template_id=template.id))
        visible = request.form.get("visibility_action") == "show"
        updated = CaseQuestion.query.filter(
            CaseQuestion.case_type == template.code,
            CaseQuestion.id.in_(question_ids),
        ).update({"client_visible": visible}, synchronize_session=False)
        db.session.commit()
        label = "visible to clients" if visible else "agency review only"
        flash(f"{updated} marked question{'s' if updated != 1 else ''} set to {label}.", "success")
        return redirect(url_for("apex_form_builder", template_id=template.id))

    @app.route("/apex/subscriptions/form-filler/<int:template_id>/builder/questions/reorder", methods=["POST"])
    @role_required("apex")
    def apex_form_questions_reorder(template_id):
        template = db.session.get(FormTemplate, template_id) or abort(404)
        payload = request.get_json(silent=True) or {}
        try:
            ordered_ids = [int(raw_id) for raw_id in payload.get("question_ids", [])]
        except (TypeError, ValueError):
            abort(400)
        questions = CaseQuestion.query.filter_by(case_type=template.code).all()
        questions_by_id = {question.id: question for question in questions}
        if set(ordered_ids) != set(questions_by_id):
            abort(400)
        for index, question_id in enumerate(ordered_ids, start=1):
            questions_by_id[question_id].sort_order = index
        db.session.commit()
        return {"status": "ok"}

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
            clients=Client.query.filter_by(agency_id=agency.id).order_by(Client.last_name, Client.first_name).all(),
            templates=templates,
            cases=cases,
        )

    @app.route("/agency/tools/motions")
    @role_required("agency")
    def agency_motions():
        agency = current_user.agency
        if not can_use_motion_creation(agency):
            flash("This feature is not included in your current membership.", "warning")
            return redirect(url_for("agency_dashboard"))
        return render_template(
            "agency_motions.html",
            agency=agency,
            templates=MotionTemplate.query.filter_by(agency_id=agency.id).order_by(MotionTemplate.updated_at.desc()).all(),
            motions=MotionDraft.query.filter_by(agency_id=agency.id).order_by(MotionDraft.updated_at.desc()).all(),
        )

    @app.route("/agency/tools/motions/templates/new", methods=["GET", "POST"])
    @role_required("agency")
    def motion_template_new():
        agency = current_user.agency
        if not can_use_motion_creation(agency):
            flash("This feature is not included in your current membership.", "warning")
            return redirect(url_for("agency_dashboard"))
        if request.method == "POST":
            name = request.form["name"].strip()
            content = request.form["content"].strip()
            if not name:
                flash("Template name is required.", "danger")
                return render_template("motion_template_form.html", template=None)
            if not content:
                flash("Motion template content is required.", "danger")
                return render_template("motion_template_form.html", template=None)
            template = MotionTemplate(agency_id=agency.id, name=name, content=content)
            db.session.add(template)
            db.session.commit()
            flash("Motion template created.", "success")
            return redirect(url_for("agency_motions"))
        return render_template("motion_template_form.html", template=None)

    @app.route("/agency/tools/motions/templates/<int:template_id>/edit", methods=["GET", "POST"])
    @role_required("agency")
    def motion_template_edit(template_id):
        agency = current_user.agency
        if not can_use_motion_creation(agency):
            flash("This feature is not included in your current membership.", "warning")
            return redirect(url_for("agency_dashboard"))
        template = motion_template_for_agency(template_id, agency.id)
        if request.method == "POST":
            template.name = request.form["name"].strip()
            template.content = request.form["content"].strip()
            if not template.name:
                flash("Template name is required.", "danger")
                return render_template("motion_template_form.html", template=template)
            if not template.content:
                flash("Motion template content is required.", "danger")
                return render_template("motion_template_form.html", template=template)
            db.session.commit()
            flash("Motion template updated.", "success")
            return redirect(url_for("agency_motions"))
        return render_template("motion_template_form.html", template=template)

    @app.route("/agency/tools/motions/templates/<int:template_id>/delete", methods=["POST"])
    @role_required("agency")
    def motion_template_delete(template_id):
        agency = current_user.agency
        if not can_use_motion_creation(agency):
            flash("This feature is not included in your current membership.", "warning")
            return redirect(url_for("agency_dashboard"))
        template = motion_template_for_agency(template_id, agency.id)
        if MotionDraft.query.filter_by(template_id=template.id).first():
            flash("This template already has created motions, so it cannot be deleted yet.", "warning")
            return redirect(url_for("agency_motions"))
        db.session.delete(template)
        db.session.commit()
        flash("Motion template deleted.", "info")
        return redirect(url_for("agency_motions"))

    @app.route("/agency/tools/motions/new", methods=["GET", "POST"])
    @role_required("agency")
    def motion_create():
        agency = current_user.agency
        if not can_use_motion_creation(agency):
            flash("This feature is not included in your current membership.", "warning")
            return redirect(url_for("agency_dashboard"))
        templates = MotionTemplate.query.filter_by(agency_id=agency.id).order_by(MotionTemplate.updated_at.desc()).all()
        references = motion_reference_lists()
        lawyers = AgencyLawyer.query.filter_by(agency_id=agency.id).order_by(AgencyLawyer.last_name, AgencyLawyer.first_name).all()
        law_firms = AgencyLawFirm.query.filter_by(agency_id=agency.id).order_by(AgencyLawFirm.name).all()
        if request.method == "POST":
            if not templates:
                flash("Create a motion template before creating a motion.", "warning")
                return redirect(url_for("motion_template_new"))
            template = motion_template_for_agency(int(request.form["template_id"]), agency.id)
            first_names = request.form.getlist("respondent_first_name[]")
            middle_names = request.form.getlist("respondent_middle_name[]")
            last_names = request.form.getlist("respondent_last_name[]")
            alien_numbers = request.form.getlist("respondent_alien_number[]")
            respondents = []
            for index, first_name in enumerate(first_names):
                person = {
                    "first_name": first_name.strip(),
                    "middle_name": middle_names[index].strip() if index < len(middle_names) else "",
                    "last_name": last_names[index].strip() if index < len(last_names) else "",
                    "alien_number": alien_numbers[index].strip() if index < len(alien_numbers) else "",
                }
                if person["first_name"] or person["last_name"] or person["alien_number"]:
                    respondents.append(person)
            if not respondents or any(not person["first_name"] or not person["last_name"] or not person["alien_number"] for person in respondents):
                flash("Each respondent needs first name, last name, and alien number.", "danger")
                return render_template("motion_form.html", templates=templates, lawyers=lawyers, law_firms=law_firms, **references)
            court = request.form["immigration_court"].strip()
            court_address = request.form.get("immigration_court_address", "").strip()
            judge = request.form["immigration_judge"].strip()
            detention_status = request.form.get("detention_status", "").strip()
            next_hearing_date = request.form.get("next_hearing_date", "").strip()
            next_hearing_type = request.form.get("next_hearing_type", "").strip()
            opla = request.form.get("opla_office", "Office of the Principal Legal Advisor").strip() or "Office of the Principal Legal Advisor"
            opla_address = request.form.get("opla_address", "").strip()
            if not court or not court_address or not judge or not opla or not opla_address:
                flash("Immigration Court, Court Address, Immigration Judge, OPLA Office, and OPLA Address are required.", "danger")
                return render_template("motion_form.html", templates=templates, lawyers=lawyers, law_firms=law_firms, **references)
            if not detention_status or not next_hearing_date or not next_hearing_type:
                flash("Detention classification, next hearing date, and next hearing type are required.", "danger")
                return render_template("motion_form.html", templates=templates, lawyers=lawyers, law_firms=law_firms, **references)
            lawyer_id = request.form.get("lawyer_id")
            law_firm_id = request.form.get("law_firm_id")
            exhibits = normalize_exhibits(request.form.getlist("exhibit_description[]"))
            lawyer = db.session.get(AgencyLawyer, int(lawyer_id)) if lawyer_id else None
            law_firm = db.session.get(AgencyLawFirm, int(law_firm_id)) if law_firm_id else None
            if lawyer and lawyer.agency_id != agency.id:
                abort(403)
            if law_firm and law_firm.agency_id != agency.id:
                abort(403)
            motion = MotionDraft(
                agency_id=agency.id,
                template_id=template.id,
                immigration_court=court,
                immigration_court_address=court_address,
                immigration_judge=judge,
                opla_office=opla,
                opla_address=opla_address,
                lawyer_id=lawyer.id if lawyer else None,
                law_firm_id=law_firm.id if law_firm else None,
                lawyer_name=lawyer.full_name if lawyer else "",
                lawyer_bar_number=lawyer.bar_number if lawyer else "",
                law_firm_name=law_firm.name if law_firm else "",
                law_firm_phone=law_firm.phone if law_firm else "",
                law_firm_address=law_firm.address if law_firm else "",
                detention_status=detention_status,
                next_hearing_date=next_hearing_date,
                next_hearing_type=next_hearing_type,
                exhibits_text="\n".join(exhibits),
                rendered_content=render_motion_content(template, respondents, court, court_address, judge, opla, opla_address, lawyer, law_firm, exhibits, detention_status, next_hearing_date, next_hearing_type),
            )
            db.session.add(motion)
            db.session.flush()
            for index, person in enumerate(respondents, start=1):
                db.session.add(MotionRespondent(motion_id=motion.id, sort_order=index, **person))
            db.session.commit()
            flash("Motion created.", "success")
            return redirect(url_for("motion_detail", motion_id=motion.id))
        return render_template("motion_form.html", templates=templates, lawyers=lawyers, law_firms=law_firms, **references)

    @app.route("/agency/tools/motions/<int:motion_id>")
    @role_required("agency")
    def motion_detail(motion_id):
        agency = current_user.agency
        if not can_use_motion_creation(agency):
            flash("This feature is not included in your current membership.", "warning")
            return redirect(url_for("agency_dashboard"))
        motion = motion_for_agency(motion_id, agency.id)
        return render_template("motion_detail.html", motion=motion)

    @app.route("/agency/tools/motions/<int:motion_id>/download")
    @role_required("agency")
    def motion_download(motion_id):
        agency = current_user.agency
        if not can_use_motion_creation(agency):
            flash("This feature is not included in your current membership.", "warning")
            return redirect(url_for("agency_dashboard"))
        motion = motion_for_agency(motion_id, agency.id)
        filename = f"motion-{motion.id}.txt"
        return Response(
            motion.rendered_content,
            mimetype="text/plain",
            headers={"Content-Disposition": f"attachment; filename={filename}"},
        )

    @app.route("/agency/tools/motions/<int:motion_id>/pdf")
    @role_required("agency")
    def motion_pdf(motion_id):
        agency = current_user.agency
        if not can_use_motion_creation(agency):
            flash("This feature is not included in your current membership.", "warning")
            return redirect(url_for("agency_dashboard"))
        motion = motion_for_agency(motion_id, agency.id)
        return motion_pdf_response(motion)

    @app.route("/agency/tools/motions/<int:motion_id>/delete", methods=["POST"])
    @role_required("agency")
    def motion_delete(motion_id):
        agency = current_user.agency
        if not can_use_motion_creation(agency):
            flash("This feature is not included in your current membership.", "warning")
            return redirect(url_for("agency_dashboard"))
        motion = motion_for_agency(motion_id, agency.id)
        db.session.delete(motion)
        db.session.commit()
        flash("Motion deleted.", "info")
        return redirect(url_for("agency_motions"))

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

    @app.route("/agency/lawyers", methods=["GET", "POST"])
    @role_required("agency")
    def lawyer_list():
        agency = current_user.agency
        if request.method == "POST":
            lawyer = AgencyLawyer(agency_id=agency.id)
            populate_lawyer_from_form(lawyer)
            db.session.add(lawyer)
            db.session.commit()
            flash("Lawyer saved.", "success")
            return redirect(url_for("lawyer_list"))
        return render_template(
            "lawyer_list.html",
            lawyers=AgencyLawyer.query.filter_by(agency_id=agency.id).order_by(AgencyLawyer.last_name, AgencyLawyer.first_name).all(),
        )

    @app.route("/agency/lawyers/<int:lawyer_id>/edit", methods=["GET", "POST"])
    @role_required("agency")
    def lawyer_edit(lawyer_id):
        lawyer = AgencyLawyer.query.filter_by(id=lawyer_id, agency_id=current_user.agency_id).first() or abort(404)
        if request.method == "POST":
            populate_lawyer_from_form(lawyer)
            db.session.commit()
            flash("Lawyer updated.", "success")
            return redirect(url_for("lawyer_list"))
        return render_template("lawyer_form.html", lawyer=lawyer)

    @app.route("/agency/lawyers/<int:lawyer_id>/delete", methods=["POST"])
    @role_required("agency")
    def lawyer_delete(lawyer_id):
        lawyer = AgencyLawyer.query.filter_by(id=lawyer_id, agency_id=current_user.agency_id).first() or abort(404)
        MotionDraft.query.filter_by(lawyer_id=lawyer.id).update({"lawyer_id": None})
        db.session.delete(lawyer)
        db.session.commit()
        flash("Lawyer deleted.", "info")
        return redirect(url_for("lawyer_list"))

    @app.route("/agency/law-firms", methods=["GET", "POST"])
    @role_required("agency")
    def law_firm_list():
        agency = current_user.agency
        if request.method == "POST":
            firm = AgencyLawFirm(agency_id=agency.id)
            populate_law_firm_from_form(firm)
            db.session.add(firm)
            db.session.commit()
            flash("Law firm saved.", "success")
            return redirect(url_for("law_firm_list"))
        return render_template(
            "law_firm_list.html",
            firms=AgencyLawFirm.query.filter_by(agency_id=agency.id).order_by(AgencyLawFirm.name).all(),
        )

    @app.route("/agency/law-firms/<int:firm_id>/edit", methods=["GET", "POST"])
    @role_required("agency")
    def law_firm_edit(firm_id):
        firm = AgencyLawFirm.query.filter_by(id=firm_id, agency_id=current_user.agency_id).first() or abort(404)
        if request.method == "POST":
            populate_law_firm_from_form(firm)
            db.session.commit()
            flash("Law firm updated.", "success")
            return redirect(url_for("law_firm_list"))
        return render_template("law_firm_form.html", firm=firm)

    @app.route("/agency/law-firms/<int:firm_id>/delete", methods=["POST"])
    @role_required("agency")
    def law_firm_delete(firm_id):
        firm = AgencyLawFirm.query.filter_by(id=firm_id, agency_id=current_user.agency_id).first() or abort(404)
        MotionDraft.query.filter_by(law_firm_id=firm.id).update({"law_firm_id": None})
        db.session.delete(firm)
        db.session.commit()
        flash("Law firm deleted.", "info")
        return redirect(url_for("law_firm_list"))

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
            document_type="Generated Form",
        )
        case.status = "Generated"
        db.session.add_all([generated, doc])
        db.session.commit()
        flash("PDF form generated.", "success")
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
        all_questions = CaseQuestion.query.filter_by(case_type=case.case_type, client_visible=True).order_by(CaseQuestion.sort_order).all()
        questions = visible_questions_for_answers(all_questions, answers_by_question)
        if not questions:
            return render_template("questionnaire.html", case=case, questions=questions, answers=answers)
        step = request.args.get("q", "intro")
        if step == "docs":
            if request.method == "POST":
                save_case_documents(case, "client")
                db.session.commit()
                flash("Documents uploaded.", "success")
                if request.form.get("action") == "save":
                    return redirect(url_for("client_dashboard"))
                return redirect(url_for("questionnaire", case_id=case.id, q="docs"))
            return render_template(
                "questionnaire.html",
                case=case,
                questions=questions,
                answers=answers,
                current_question=None,
                current_index=len(questions) + 1,
                document_step=True,
                first_unanswered_index=first_unanswered_question_index(case, questions),
            )
        if step == "intro":
            return render_template(
                "questionnaire.html",
                case=case,
                questions=questions,
                answers=answers,
                current_question=None,
                current_index=0,
                welcome_step=True,
                first_unanswered_index=first_unanswered_question_index(case, questions),
            )
        raw_index = request.args.get("q", 1, type=int)
        current_index = min(max(raw_index, 1), len(questions))
        current_question = questions[current_index - 1]
        if request.method == "POST":
            answer = answers.get(current_question.id) or CaseAnswer(case_id=case.id, question_id=current_question.id)
            answer.answer_text = request.form.get(f"question_{current_question.id}", "").strip()
            db.session.add(answer)
            update_case_progress(case)
            db.session.commit()
            answers = {answer.question_id: answer for answer in case.answers}
            answers_by_question = {answer.question_id: answer.answer_text or "" for answer in case.answers}
            questions = visible_questions_for_answers(all_questions, answers_by_question)
            current_index = next((idx for idx, question in enumerate(questions, start=1) if question.id == current_question.id), current_index)
            action = request.form.get("action", "save")
            if action == "back":
                if current_index > 1:
                    return redirect(url_for("questionnaire", case_id=case.id, q=current_index - 1))
                return redirect(url_for("questionnaire", case_id=case.id, q="intro"))
            if action == "next" and current_index < len(questions):
                return redirect(url_for("questionnaire", case_id=case.id, q=current_index + 1))
            if action == "next":
                return redirect(url_for("questionnaire", case_id=case.id, q="docs"))
            if action == "later":
                unanswered = first_unanswered_question_index(case, questions, skip_question_id=current_question.id)
                return redirect(url_for("questionnaire", case_id=case.id, q=unanswered or "docs"))
            flash("Progress saved.", "success")
            return redirect(url_for("client_dashboard"))
        return render_template(
            "questionnaire.html",
            case=case,
            questions=questions,
            answers=answers,
            current_question=current_question,
            current_index=current_index,
            first_unanswered_index=first_unanswered_question_index(case, questions),
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
    if template.pdf_generation_strategy in ("acroform_fill_need_appearances", "acroform_widgets"):
        filled = fill_pdf_widgets_with_pymupdf(case, template)
        if filled:
            return filled
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


def fill_pdf_widgets_with_pymupdf(case, template):
    try:
        import fitz
    except ImportError:
        return None
    source_path = os.path.join(app.config["UPLOAD_FOLDER"], template.pdf_stored_filename)
    folder = f"cases/{case.id}/generated"
    os.makedirs(os.path.join(app.config["UPLOAD_FOLDER"], folder), exist_ok=True)
    filename = f"{folder}/{case.case_type.lower()}_official_filled_{uuid.uuid4().hex[:8]}.pdf"
    output_path = os.path.join(app.config["UPLOAD_FOLDER"], filename)
    field_entries = answer_entries_by_pdf_field(case)
    if not field_entries:
        return None
    field_lookup = build_pdf_field_value_lookup(field_entries)
    document = None
    try:
        document = fitz.open(source_path)
        filled_count = 0
        checkbox_widget_counts = count_checkbox_widgets_by_field(document)
        widget_occurrences = {}
        for page_index in range(document.page_count):
            page = document.load_page(page_index)
            matched_widgets = []
            widgets = sorted(page.widgets() or [], key=visual_widget_sort_key)
            for widget in widgets:
                field_name = (widget.field_name or "").strip()
                checkbox_widget = is_checkbox_widget(widget)
                occurrence_key = normalized_pdf_field_key(field_name)
                widget_occurrence = widget_occurrences.get(occurrence_key, 0)
                widget_occurrences[occurrence_key] = widget_occurrence + 1
                field_entry = lookup_pdf_field_entry(
                    field_lookup,
                    field_name,
                    widget if checkbox_widget else None,
                    strict=checkbox_widget,
                    widget_occurrence=widget_occurrence,
                    widget_count=checkbox_widget_counts.get(occurrence_key, 1),
                )
                if not field_entry:
                    continue
                field_value = field_entry["value"]
                if checkbox_widget:
                    overlay_widget_text(page, widget, "X")
                else:
                    if field_entry.get("render_mode") != "split_boxes":
                        widget.field_value = field_value
                        widget.update()
                    overlay_widget_text(page, widget, field_value, field_entry)
                matched_widgets.append(widget)
                filled_count += 1
            flatten_matched_widgets(page, matched_widgets)
        if not filled_count:
            document.close()
            return None
        document.save(output_path, garbage=4, deflate=True, clean=True)
        document.close()
        return filename
    except Exception:
        if document:
            document.close()
        return None


def build_pdf_field_value_lookup(field_entries):
    lookup = {"exact": {}, "loose": {}}
    for entry in field_entries:
        field_name = pdf_field_selector_parts(entry["field_name"])["field_name"]
        exact_keys = {
            field_name,
            terminal_pdf_field_key(field_name),
            normalized_pdf_field_key(field_name),
            normalized_pdf_field_key(terminal_pdf_field_key(field_name)),
        }
        loose_keys = {
            short_pdf_field_key(field_name),
            normalized_pdf_field_key(short_pdf_field_key(field_name)),
        }
        for key in exact_keys:
            if key:
                lookup["exact"].setdefault(key, []).append(entry)
        for key in loose_keys:
            if key:
                lookup["loose"].setdefault(key, []).append(entry)
    return lookup


def lookup_pdf_field_entry(field_lookup, pdf_field_name, widget=None, strict=False, widget_occurrence=None, widget_count=1):
    candidates = (
        pdf_field_name,
        terminal_pdf_field_key(pdf_field_name),
        normalized_pdf_field_key(pdf_field_name),
        normalized_pdf_field_key(terminal_pdf_field_key(pdf_field_name)),
    )
    for candidate in candidates:
        if candidate in field_lookup["exact"]:
            return choose_pdf_field_entry(field_lookup["exact"][candidate], widget, widget_occurrence, widget_count)
    loose_candidates = (
        short_pdf_field_key(pdf_field_name),
        normalized_pdf_field_key(short_pdf_field_key(pdf_field_name)),
    )
    for candidate in loose_candidates:
        if candidate in field_lookup["loose"]:
            return choose_pdf_field_entry(field_lookup["loose"][candidate], widget, widget_occurrence, widget_count)
    normalized_widget = normalized_pdf_field_key(pdf_field_name)
    for key, entries in field_lookup["loose"].items():
        if key and key in normalized_widget:
            return choose_pdf_field_entry(entries, widget, widget_occurrence, widget_count)
    if strict:
        return None
    return None


def choose_pdf_field_entry(entries, widget=None, widget_occurrence=None, widget_count=1):
    if not widget:
        return entries[0] if entries else None
    for entry in entries:
        if checkbox_entry_matches_widget(entry, widget, widget_occurrence, widget_count):
            return entry
    return None


def overlay_widget_text(page, widget, value, field_entry=None):
    if field_entry and field_entry.get("render_mode") == "split_boxes":
        overlay_split_box_text(page, widget, value, field_entry)
        return
    rect = widget.rect
    inset_rect = rect.__class__(rect.x0 + 2, rect.y0 + 1, rect.x1 - 2, rect.y1 - 1)
    text = str(value)
    font_size = 8 if len(text) > 30 else 9
    try:
        written = page.insert_textbox(inset_rect, text, fontsize=font_size, fontname="helv", color=(0, 0, 0), align=0)
        if written < 0:
            page.insert_text((rect.x0 + 2, rect.y1 - 4), text[:80], fontsize=font_size, fontname="helv", color=(0, 0, 0))
    except Exception:
        page.insert_text((rect.x0 + 2, rect.y1 - 4), text[:80], fontsize=font_size, fontname="helv", color=(0, 0, 0))


def overlay_split_box_text(page, widget, value, field_entry):
    rect = widget.rect
    text = re.sub(r"\s+", "", str(value or ""))
    if not text:
        return
    box_count = max(int(field_entry.get("render_box_count") or 0), len(text), 1)
    cell_width = rect.width / box_count
    font_size = min(9, max(6, rect.height * 0.62))
    y = rect.y0 + (rect.height * 0.72)
    for index, character in enumerate(text[:80]):
        x = rect.x0 + (cell_width * index) + (cell_width * 0.34)
        page.insert_text((x, y), character, fontsize=font_size, fontname="helv", color=(0, 0, 0))


def is_checkbox_widget(widget):
    widget_type = str(getattr(widget, "field_type_string", "") or getattr(widget, "field_type", "")).lower()
    return "check" in widget_type


def checkbox_on_value(widget):
    try:
        states = widget.button_states()
        on_states = states.get("normal") or states.get("down") or []
        for state in on_states:
            if state and state != "Off":
                return state
    except Exception:
        pass
    return "Yes"


def checkbox_entry_matches_widget(entry, widget, widget_occurrence=None, widget_count=1):
    selector = pdf_field_selector_parts(entry["field_name"])
    source_key = selector["field_name"] or ""
    widget_name = (widget.field_name or "").strip()
    source_text = f"{source_key} {entry.get('prompt', '')}"
    widget_text = f"{widget_name} {terminal_pdf_field_key(widget_name)} {checkbox_on_value(widget)} {getattr(widget, 'field_label', '') or ''}"
    source_tokens = semantic_tokens_from_pdf_text(source_text)
    widget_tokens = semantic_tokens_from_pdf_text(widget_text)
    if selector["choice_index"] is not None and widget_occurrence is not None:
        return selector["choice_index"] == widget_occurrence
    if selector["choice_label"]:
        return selector["choice_label"] in widget_tokens
    categories = (
        ("male", "female"),
        ("single", "married", "divorced", "widowed"),
        ("initial", "replacement", "renewal"),
    )
    for category in categories:
        category_set = set(category)
        source_token = next((token for token in category if token in source_tokens), None)
        widget_matches = widget_tokens & category_set
        if source_token and widget_matches:
            return source_token in widget_matches
        if source_token and not widget_matches and widget_occurrence is not None and widget_count > 1:
            return category.index(source_token) == widget_occurrence

    answer_token = (entry.get("answer_text") or "").strip().lower()
    if answer_token in {"yes", "no"}:
        widget_yes_no = widget_tokens & {"yes", "no"}
        if widget_yes_no:
            return answer_token in widget_yes_no
        if widget_occurrence is not None and widget_count == 2:
            return ("yes", "no").index(answer_token) == widget_occurrence

    if widget_count == 1 and (source_key == widget_name or terminal_pdf_field_key(source_key) == terminal_pdf_field_key(widget_name)):
        return True
    return False


def pdf_field_selector_parts(field_name):
    raw = field_name or ""
    if "::" not in raw:
        return {"field_name": raw, "choice_index": None, "choice_label": ""}
    base, choice = raw.rsplit("::", 1)
    choice = choice.strip()
    if choice.isdigit():
        return {"field_name": base.strip(), "choice_index": int(choice), "choice_label": ""}
    return {"field_name": base.strip(), "choice_index": None, "choice_label": choice.lower()}


def visual_widget_sort_key(widget):
    rect = widget.rect
    return (round(rect.y0, 1), round(rect.x0, 1), round(rect.y1, 1), round(rect.x1, 1))


def count_checkbox_widgets_by_field(document):
    counts = {}
    for page_index in range(document.page_count):
        page = document.load_page(page_index)
        for widget in page.widgets() or []:
            if not is_checkbox_widget(widget):
                continue
            key = normalized_pdf_field_key((widget.field_name or "").strip())
            counts[key] = counts.get(key, 0) + 1
    return counts


def semantic_tokens_from_pdf_text(value):
    spaced = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", value or "")
    return {token for token in re.split(r"[^a-z0-9]+", spaced.lower()) if token}


def flatten_matched_widgets(page, widgets):
    for widget in widgets:
        try:
            if hasattr(page, "delete_widget"):
                page.delete_widget(widget)
            else:
                page.delete_annot(widget)
        except Exception:
            continue


def answer_entries_by_pdf_field(case):
    entries = []
    for answer in case.answers:
        if answer.question and answer.answer_text:
            if answer.question.input_type == "checkbox":
                field_key = checkbox_field_key_for_answer(answer.question.field_key, answer.answer_text)
                if field_key:
                    entries.append(
                        {
                            "field_name": field_key,
                            "value": "X",
                            "input_type": "checkbox",
                            "render_mode": answer.question.render_mode,
                            "render_box_count": answer.question.render_box_count,
                            "answer_text": answer.answer_text,
                            "prompt": answer.question.prompt,
                        }
                    )
            else:
                entries.append(
                    {
                        "field_name": answer.question.field_key,
                        "value": answer.answer_text,
                        "input_type": answer.question.input_type,
                        "render_mode": answer.question.render_mode,
                        "render_box_count": answer.question.render_box_count,
                        "prompt": answer.question.prompt,
                    }
                )
    return entries


def checkbox_field_key_for_answer(field_key, answer_text):
    mapping = parse_checkbox_field_mapping(field_key)
    if mapping:
        return mapping.get(answer_text.lower())
    return field_key if answer_text == "Yes" else None


def parse_checkbox_field_mapping(field_key):
    if "=" not in (field_key or ""):
        return {}
    mapping = {}
    for part in re.split(r"[;\n]", field_key):
        if "=" not in part:
            continue
        label, value = part.split("=", 1)
        label = label.strip().lower()
        value = value.strip()
        if label and value:
            mapping[label] = value
    return mapping


def answer_values_by_pdf_field(case):
    values = {}
    for entry in answer_entries_by_pdf_field(case):
        values[entry["field_name"]] = entry["value"]
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


def draw_pdf_lines(pdf, lines, x, y, max_chars=90, font_name="Times-Roman", font_size=11, leading=14, bottom_margin=inch):
    width, height = letter
    pdf.setFont(font_name, font_size)
    for paragraph in lines:
        if not paragraph:
            y -= leading
            continue
        for line in split_pdf_lines(paragraph, max_chars):
            if y < bottom_margin:
                pdf.showPage()
                y = height - inch
                pdf.setFont(font_name, font_size)
            pdf.drawString(x, y, line)
            y -= leading
    return y


def draw_centered_pdf_line(pdf, text_value, y, font_name="Times-Bold", font_size=11):
    width, _ = letter
    pdf.setFont(font_name, font_size)
    pdf.drawCentredString(width / 2, y, text_value)
    return y - (font_size + 4)


def motion_body_text(motion):
    lines = (motion.rendered_content or "").splitlines()
    title = (motion.title or "").strip()
    collecting = False
    body_lines = []
    for line in lines:
        stripped = line.strip()
        if stripped == title:
            collecting = True
            continue
        if collecting and stripped in {"EXHIBITS", "PROOF OF SERVICE", "CERTIFICATE OF SERVICE", "PROPOSED ORDER"}:
            break
        if collecting:
            body_lines.append(line)
    return "\n".join(body_lines).strip() if body_lines else (motion.rendered_content or "")


def motion_exhibits(motion):
    return normalize_exhibits((motion.exhibits_text or "").splitlines())


def respondent_caption_lines(motion):
    lines = []
    for person in motion.respondents:
        lines.append(f"{person.last_name.upper()}, {person.first_name} {person.middle_name or ''}".strip())
    return lines or ["Respondent(s)"]


def respondent_file_numbers(motion):
    numbers = [f"A{person.alien_number}" for person in motion.respondents if person.alien_number]
    return ", ".join(numbers) or "A"


def display_motion_date(value):
    if not value:
        return ""
    try:
        return datetime.strptime(value, "%Y-%m-%d").strftime("%m/%d/%Y")
    except ValueError:
        return value


def draw_motion_caption(pdf, motion, y):
    width, _ = letter
    left = inch
    right = width - inch
    paren_x = left + 320
    file_x = left + 385
    top = y
    bottom = y - 138
    pdf.setLineWidth(.8)
    pdf.line(left, top, paren_x - 7, top)
    pdf.line(left, bottom, paren_x - 7, bottom)

    paren_y = top - 17
    pdf.setFont("Times-Roman", 18)
    while paren_y > bottom + 4:
        pdf.drawString(paren_x, paren_y, ")")
        paren_y -= 19

    y_left = top - 17
    pdf.setFont("Times-Roman", 11)
    pdf.drawString(left, y_left, "In the Matter of:")
    y_left -= 18
    for line in respondent_caption_lines(motion):
        pdf.drawString(left, y_left, line)
        y_left -= 17
    y_left -= 6
    pdf.drawString(left + 24, y_left, "Respondent,")
    y_left -= 30
    pdf.drawString(left, y_left, "In Removal Proceedings.")

    pdf.setFont("Times-Roman", 11)
    pdf.drawString(file_x, top - 17, f"File No.: {respondent_file_numbers(motion)}")
    y_detail = bottom - 24
    pdf.drawString(left, y_detail, f"Before: {motion.immigration_judge}")
    pdf.drawRightString(right, y_detail, f"Next {motion.next_hearing_type or ''} Hearing Date: {display_motion_date(motion.next_hearing_date)}")
    return y_detail - 28


def draw_motion_header(pdf, motion, y):
    width, _ = letter
    representative_lines = []
    if motion.lawyer_name:
        representative_lines.append(motion.lawyer_name)
    if motion.lawyer_bar_number:
        representative_lines.append(f"Bar No.: {motion.lawyer_bar_number}")
    if motion.law_firm_name:
        representative_lines.append(motion.law_firm_name)
    if motion.law_firm_address:
        representative_lines.extend(motion.law_firm_address.splitlines())
    if motion.law_firm_phone:
        representative_lines.append(motion.law_firm_phone)
    if not representative_lines:
        representative_lines = ["Pro Se"]
    pdf.setFont("Times-Bold", 11)
    pdf.drawRightString(width - inch, y, (motion.detention_status or "").upper())
    y = draw_pdf_lines(pdf, representative_lines, inch, y, max_chars=52, font_size=10, leading=12)
    y -= 18
    y = draw_centered_pdf_line(pdf, "UNITED STATES DEPARTMENT OF JUSTICE", y, "Times-Bold", 11)
    y = draw_centered_pdf_line(pdf, "EXECUTIVE OFFICE FOR IMMIGRATION REVIEW", y, "Times-Bold", 11)
    y = draw_centered_pdf_line(pdf, (motion.immigration_court or "").upper(), y, "Times-Bold", 11)
    if motion.immigration_court_address:
        for line in motion.immigration_court_address.splitlines():
            y = draw_centered_pdf_line(pdf, line, y, "Times-Roman", 10)
    return y - 10


def motion_pdf_response(motion):
    buffer = BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=letter)
    width, height = letter
    left = inch
    y = height - inch
    pdf.setTitle(motion.title)

    y = draw_motion_header(pdf, motion, y)
    y = draw_motion_caption(pdf, motion, y)
    y = draw_centered_pdf_line(pdf, motion.title, y, "Times-Bold", 12)
    y -= 8
    y = draw_pdf_lines(pdf, motion_body_text(motion).splitlines(), left, y, max_chars=88, font_size=11, leading=15)

    pdf.showPage()
    y = height - inch
    y = draw_centered_pdf_line(pdf, "EXHIBITS", y, "Times-Bold", 12)
    y -= 10
    exhibits = motion_exhibits(motion)
    exhibit_lines = [f"Exhibit {exhibit_label(index)}: {description}" for index, description in enumerate(exhibits)] or ["No exhibits listed."]
    y = draw_pdf_lines(pdf, exhibit_lines, left, y, max_chars=88, font_size=11, leading=16)

    y -= 22
    if y < 2 * inch:
        pdf.showPage()
        y = height - inch
    y = draw_centered_pdf_line(pdf, "CERTIFICATE OF SERVICE", y, "Times-Bold", 12)
    y -= 10
    service_lines = [
        f"I certify that on {datetime.utcnow().strftime('%B %d, %Y')}, a true and correct copy of the foregoing motion was served on:",
        "",
        motion.opla_office,
        *[line for line in (motion.opla_address or "").splitlines() if line.strip()],
        "",
        "by the method required by the Immigration Court practice rules.",
        "",
        "Respectfully submitted,",
        "",
        "________________________________________",
        motion.lawyer_name or "Pro Se",
    ]
    y = draw_pdf_lines(pdf, service_lines, left, y, max_chars=88, font_size=11, leading=15)

    pdf.showPage()
    y = height - inch
    y = draw_motion_header(pdf, motion, y)
    y = draw_motion_caption(pdf, motion, y)
    y = draw_centered_pdf_line(pdf, "PROPOSED ORDER", y, "Times-Bold", 12)
    y -= 16
    order_lines = [
        f"Upon consideration of Respondent's {motion.title}, it is hereby:",
        "",
        "[  ] GRANTED",
        "",
        "[  ] DENIED",
        "",
        "[  ] OTHER: ________________________________________________",
        "",
        "Date: ______________________________",
        "",
        "____________________________________________",
        "Immigration Judge",
    ]
    draw_pdf_lines(pdf, order_lines, left, y, max_chars=88, font_size=11, leading=17)
    pdf.save()
    buffer.seek(0)
    filename = f"motion-{motion.id}.pdf"
    return Response(
        buffer.getvalue(),
        mimetype="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


def init_database():
    db.create_all()
    ensure_sqlite_schema()
    for name in SUBSCRIPTION_TOOLS:
        if not SubscriptionTool.query.filter_by(name=name).first():
            db.session.add(SubscriptionTool(name=name))
    if os.environ.get("SEED_SAMPLE_FORMS") == "1" and FormTemplate.query.count() == 0:
        seed_sample_form_templates()
    if not ApexUser.query.filter_by(username="apexadmin").first():
        apex = ApexUser(username="apexadmin")
        apex.set_password("ChangeMe123!")
        db.session.add(apex)
    db.session.commit()


def seed_sample_form_templates():
    for code, name, description in FORM_TEMPLATES:
        template = FormTemplate.query.filter_by(code=code).first()
        if not template:
            db.session.add(FormTemplate(code=code, name=name, description=description))
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
            "client_visible": "BOOLEAN DEFAULT 1 NOT NULL",
            "render_mode": "VARCHAR(30) DEFAULT 'normal' NOT NULL",
            "render_box_count": "INTEGER DEFAULT 0 NOT NULL",
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
    if "motion_template" in inspector.get_table_names():
        existing_motion_template = {column["name"] for column in inspector.get_columns("motion_template")}
        motion_template_additions = {
            "name": "VARCHAR(180) DEFAULT 'Untitled Motion Template' NOT NULL",
        }
        for column, ddl in motion_template_additions.items():
            if column not in existing_motion_template:
                db.session.execute(text(f"ALTER TABLE motion_template ADD COLUMN {column} {ddl}"))
    if "motion_draft" in inspector.get_table_names():
        existing_motion = {column["name"] for column in inspector.get_columns("motion_draft")}
        motion_additions = {
            "immigration_court_address": "TEXT",
            "opla_address": "TEXT",
            "lawyer_id": "INTEGER",
            "law_firm_id": "INTEGER",
            "lawyer_bar_number": "VARCHAR(80)",
            "law_firm_phone": "VARCHAR(40)",
            "law_firm_address": "TEXT",
            "exhibits_text": "TEXT",
            "detention_status": "VARCHAR(30)",
            "next_hearing_date": "VARCHAR(20)",
            "next_hearing_type": "VARCHAR(30)",
        }
        for column, ddl in motion_additions.items():
            if column not in existing_motion:
                db.session.execute(text(f"ALTER TABLE motion_draft ADD COLUMN {column} {ddl}"))
    reference_table_additions = {
        "immigration_court": {
            "address_line1": "VARCHAR(180)",
            "address_line2": "VARCHAR(180)",
            "zip_code": "VARCHAR(20)",
            "postal_code": "VARCHAR(20)",
        },
        "opla_office": {
            "address_line1": "VARCHAR(180)",
            "address_line2": "VARCHAR(180)",
            "postal_code": "VARCHAR(20)",
            "phone": "VARCHAR(40)",
        },
    }
    for table_name, additions in reference_table_additions.items():
        if table_name in inspector.get_table_names():
            existing_reference = {column["name"] for column in inspector.get_columns(table_name)}
            for column, ddl in additions.items():
                if column not in existing_reference:
                    db.session.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {column} {ddl}"))
    db.session.commit()


app = create_app()


with app.app_context():
    if os.environ.get("AUTO_INIT_DB", "1") == "1":
        init_database()


if __name__ == "__main__":
    app.run(debug=os.environ.get("FLASK_DEBUG", "1") == "1")
