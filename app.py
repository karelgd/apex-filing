import calendar as calendar_lib
import csv
import os
import importlib.util
import json
import re
import uuid
from functools import wraps
from io import BytesIO, StringIO
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation

from flask import (
    Flask,
    abort,
    flash,
    redirect,
    render_template,
    Response,
    request,
    send_file,
    send_from_directory,
    url_for,
)
from flask_login import LoginManager, current_user, login_required, login_user, logout_user
from flask_wtf import CSRFProtect
from reportlab.lib.pagesizes import landscape, letter
from reportlab.lib.units import inch
from reportlab.pdfgen import canvas
from werkzeug.utils import secure_filename
from sqlalchemy import func, inspect, or_, text
import click

from forms import CASE_STATUSES, CASE_TYPES, CRM_CASE_SERVICES, FORM_TEMPLATES, I485_QUESTIONS, I589_QUESTIONS, SUBSCRIPTION_TOOLS, US_STATES
from models import (
    ActiveSession,
    Agency,
    AgencyCaseManager,
    AgencyCrmCaseType,
    AgencyCrmPreparer,
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
    CasePdfFieldValue,
    CasePdfManualValue,
    CaseQuestion,
    Client,
    CrmAppointment,
    CrmAppointmentNote,
    CrmCase,
    CrmCaseNote,
    CrmCaseQuestionnaire,
    CrmCaseStatusHistory,
    CrmCaseTag,
    CrmClientDocument,
    CrmClientNote,
    CrmInvoice,
    CrmInvoiceActivity,
    FormTemplate,
    GeneratedForm,
    ImmigrationCourt,
    ImmigrationJudge,
    JoinderActivityLog,
    JoinderClient,
    JoinderDocument,
    JoinderNote,
    KnowledgeBaseModule,
    KnowledgeBaseTopic,
    MotionDraft,
    MotionRespondent,
    MotionTemplate,
    OplaOffice,
    PdfField,
    PdfManualField,
    PdfQuestionPlacement,
    SubscriptionTool,
    db,
)

OPLAOffice = OplaOffice
Judge = ImmigrationJudge
CRM_KNOWLEDGE_TOPICS = [
    "Como Acceder a la subscripcion CRM",
    "Entendiendo la pagina principal del CRM y sus datos",
    "Como buscar un cliente ya creado en el CRM y ver toda su informacion",
    "Entendiendo la informacion en la pagina general del cliente",
    "Como crear, editar o eliminar un Cliente en CRM.",
    "Como Crear un Caso a un cliente",
    "Como Crear una cita en el calendario a un caso",
    "Como agregar, editar o eliminar pagos, descuentos o refunds a un invoice generado",
    "Como subir, ver o descargar documentos de un cliente",
    "Como ver el calendario general y sus citas",
    "Como acceder a los reportes.",
    "Como usar los filtros en la herramienta de Reportes.",
    "Entendiendo los resultados de los reportes.",
    "Como ver o descargar un reporte",
]


BASE_DIR = os.path.abspath(os.path.dirname(__file__))
ALLOWED_EXTENSIONS = {"pdf", "png", "jpg", "jpeg", "doc", "docx", "txt"}
CLIENT_DOCUMENT_EXTENSIONS = {"pdf", "png", "jpg", "jpeg", "docx"}


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
        if role == "agency_preparer":
            return db.session.get(AgencyPreparer, user_id)
        if role == "agency_case_manager":
            return db.session.get(AgencyCaseManager, user_id)
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
            "crm_case_services": CRM_CASE_SERVICES,
            "subscription_tools": SUBSCRIPTION_TOOLS,
            "states": US_STATES,
            "is_agency_owner": is_agency_owner,
            "is_agency_staff": is_agency_staff,
            "can_generate_forms_for_current_user": can_generate_forms_for_current_user,
            "can_use_form_filler_for_current_user": can_use_form_filler_for_current_user,
            "can_use_motion_creation_for_current_user": can_use_motion_creation_for_current_user,
            "can_use_joinder_for_current_user": can_use_joinder_for_current_user,
            "can_manage_client_users_for_current_user": can_manage_client_users_for_current_user,
            "question_visual_mapping": question_visual_mapping,
            "question_visual_mappings": question_visual_mappings,
            "pdf_field_visual_mapping": pdf_field_visual_mapping,
            "is_pdf_checkbox_field": is_pdf_checkbox_field,
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


def is_agency_owner():
    return current_user.is_authenticated and isinstance(current_user, AgencyUser)


def is_agency_staff():
    return current_user.is_authenticated and current_user.role == "agency" and not is_agency_owner()


def can_generate_forms_for_current_user():
    return current_user.is_authenticated and (
        current_user.role == "apex"
        or is_agency_owner()
        or isinstance(current_user, AgencyPreparer)
    )


def can_use_form_filler_for_current_user():
    return current_user.is_authenticated and (
        current_user.role == "apex"
        or (
            current_user.role == "agency"
            and (is_agency_owner() or isinstance(current_user, AgencyPreparer))
        )
    )


def can_use_motion_creation_for_current_user():
    return current_user.is_authenticated and current_user.role == "agency" and (
        is_agency_owner() or isinstance(current_user, AgencyPreparer)
    )


def can_manage_client_users_for_current_user():
    return current_user.is_authenticated and (
        current_user.role == "apex"
        or (current_user.role == "agency" and is_agency_owner())
    )


def agency_owner_required(view):
    @wraps(view)
    @login_required
    def wrapped(*args, **kwargs):
        if current_user.role != "agency" or not is_agency_owner():
            abort(403)
        return view(*args, **kwargs)

    return wrapped


def agency_motion_required(view):
    @wraps(view)
    @login_required
    def wrapped(*args, **kwargs):
        if not can_use_motion_creation_for_current_user():
            abort(403)
        return view(*args, **kwargs)

    return wrapped


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


def allowed_client_document(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in CLIENT_DOCUMENT_EXTENSIONS


def validate_client_document_upload(file_storage):
    if not file_storage or not file_storage.filename:
        raise ValueError("Choose a document to upload.")
    if not allowed_client_document(file_storage.filename):
        raise ValueError("For security, clients can only upload PDF, image, or DOCX files.")
    extension = file_storage.filename.rsplit(".", 1)[1].lower()
    head = file_storage.stream.read(8)
    file_storage.stream.seek(0)
    signatures = {
        "pdf": [b"%PDF"],
        "png": [b"\x89PNG"],
        "jpg": [b"\xff\xd8\xff"],
        "jpeg": [b"\xff\xd8\xff"],
        "docx": [b"PK\x03\x04"],
    }
    expected = signatures.get(extension, [])
    if expected and not any(head.startswith(signature) for signature in expected):
        raise ValueError("The uploaded file does not match its file type. Please upload the original document file.")


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
        question.prompt_es = question.prompt_es or prompt
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


def question_translation_csv(template):
    output = StringIO()
    writer = csv.DictWriter(
        output,
        fieldnames=["question_id", "sort_order", "field_key", "spanish", "english", "kreyol"],
    )
    writer.writeheader()
    questions = CaseQuestion.query.filter_by(case_type=template.code).order_by(CaseQuestion.sort_order, CaseQuestion.id).all()
    for question in questions:
        writer.writerow(
            {
                "question_id": question.id,
                "sort_order": question.sort_order,
                "field_key": question.field_key,
                "spanish": question.prompt_es or question.prompt,
                "english": question.prompt_en or "",
                "kreyol": question.prompt_ht or "",
            }
        )
    return output.getvalue()


def import_question_translation_csv(template, file_storage):
    if not file_storage:
        flash("Choose a CSV translation file first.", "warning")
        return 0
    raw = file_storage.read()
    try:
        text_value = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text_value = raw.decode("latin-1")
    reader = csv.DictReader(StringIO(text_value))
    questions_by_id = {
        str(question.id): question
        for question in CaseQuestion.query.filter_by(case_type=template.code).all()
    }
    updated = 0
    for row in reader:
        question = questions_by_id.get((row.get("question_id") or "").strip())
        if not question:
            continue
        spanish = (row.get("spanish") or "").strip()
        english = (row.get("english") or "").strip()
        kreyol = (row.get("kreyol") or row.get("creole") or row.get("haitian_creole") or "").strip()
        if spanish:
            question.prompt_es = spanish
            question.prompt = spanish[:255]
        if english:
            question.prompt_en = english
        if kreyol:
            question.prompt_ht = kreyol
        updated += 1
    db.session.commit()
    return updated


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
    PdfQuestionPlacement.query.filter_by(question_id=question.id).delete(synchronize_session=False)
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


def template_pdf_path(template):
    if not template or not template.pdf_stored_filename:
        return None
    return os.path.join(app.config["UPLOAD_FOLDER"], template.pdf_stored_filename)


def template_pdf_pages(template):
    pdf_path = template_pdf_path(template)
    if not pdf_path or not os.path.exists(pdf_path):
        return []
    try:
        import fitz
    except ImportError:
        return []
    document = None
    try:
        document = fitz.open(pdf_path)
        pages = []
        for index in range(document.page_count):
            page = document.load_page(index)
            pages.append(
                {
                    "number": index + 1,
                    "width": round(page.rect.width, 2),
                    "height": round(page.rect.height, 2),
                }
            )
        return pages
    except Exception:
        return []
    finally:
        if document:
            document.close()


def question_visual_mapping(question):
    mappings = question_visual_mappings(question)
    return mappings[0] if mappings else None


def question_visual_mappings(question):
    mappings = [
        {
            "id": placement.id,
            "page": placement.page_number,
            "x": float(placement.x or 0),
            "y": float(placement.y or 0),
            "width": float(placement.width or 0),
            "height": float(placement.height or 0),
        }
        for placement in sorted(question.placements, key=lambda item: (item.page_number, item.y, item.x, item.id))
    ]
    if mappings:
        return mappings
    if question.pdf_page_number and question.pdf_x is not None and question.pdf_y is not None:
        mappings.append(
            {
                "id": None,
                "page": question.pdf_page_number,
                "x": float(question.pdf_x or 0),
                "y": float(question.pdf_y or 0),
                "width": float(question.pdf_width or 0),
                "height": float(question.pdf_height or 0),
            }
        )
        return mappings
    legacy_fields = PdfField.query.filter_by(mapped_question_id=question.id).order_by(PdfField.page_number, PdfField.id).all()
    for field in legacy_fields:
        mapping = pdf_field_visual_mapping(field)
        if mapping:
            mappings.append(mapping)
    return mappings


def manual_field_visual_mapping(field):
    return {
        "id": field.id,
        "page": field.page_number,
        "x": float(field.x or 0),
        "y": float(field.y or 0),
        "width": float(field.width or 120),
        "height": float(field.height or 18),
    }


def pdf_field_rect(field):
    if not field or not field.rect_json:
        return None
    try:
        values = json.loads(field.rect_json)
    except (TypeError, ValueError):
        return None
    if not isinstance(values, list) or len(values) != 4:
        return None
    try:
        x0, y0, x1, y1 = [float(value) for value in values]
    except (TypeError, ValueError):
        return None
    return {
        "x": min(x0, x1),
        "y": min(y0, y1),
        "width": abs(x1 - x0),
        "height": abs(y1 - y0),
    }


def pdf_field_visual_mapping(field):
    rect = pdf_field_rect(field)
    if not rect or not field.page_number:
        return None
    return {
        "id": field.id,
        "page": field.page_number,
        "x": rect["x"],
        "y": rect["y"],
        "width": rect["width"],
        "height": rect["height"],
    }


def is_pdf_checkbox_field(field):
    text = f"{field.field_name or ''} {field.field_type or ''}".lower()
    return any(token in text for token in ("checkbox", "check box", "/btn", "button"))


def reviewable_pdf_fields(template):
    if not template:
        return []
    return [
        field
        for field in PdfField.query.filter_by(template_id=template.id).order_by(PdfField.page_number, PdfField.id).all()
        if pdf_field_visual_mapping(field) and not field.mapped_question_id
    ]


def apply_visual_placement(question, page_number, x, y, width, height, placement=None):
    placement = placement or PdfQuestionPlacement(question=question)
    placement.page_number = page_number
    placement.x = x
    placement.y = y
    placement.width = width
    placement.height = height
    db.session.add(placement)
    question.pdf_page_number = None
    question.pdf_x = None
    question.pdf_y = None
    question.pdf_width = None
    question.pdf_height = None
    return placement


def apply_manual_field_placement(field, page_number, x, y, width, height):
    field.page_number = page_number
    field.x = x
    field.y = y
    field.width = width
    field.height = height
    return field


def visual_placement_from_request(template, payload):
    try:
        page_number = int(payload.get("page"))
        x = Decimal(str(payload.get("x")))
        y = Decimal(str(payload.get("y")))
        width = Decimal(str(payload.get("width") or 140))
        height = Decimal(str(payload.get("height") or 18))
    except (TypeError, ValueError, InvalidOperation):
        abort(400)
    page = next((item for item in template_pdf_pages(template) if item["number"] == page_number), None)
    if not page:
        abort(400)
    page_width = Decimal(str(page["width"]))
    page_height = Decimal(str(page["height"]))
    x = max(Decimal("0"), min(x, max(Decimal("0"), page_width - Decimal("8"))))
    y = max(Decimal("0"), min(y, max(Decimal("0"), page_height - Decimal("8"))))
    width = max(Decimal("8"), min(width, page_width - x))
    height = max(Decimal("8"), min(height, page_height - y))
    return {
        "page_number": page_number,
        "x": x,
        "y": y,
        "width": width,
        "height": height,
    }


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
    try:
        import fitz
    except ImportError:
        return metadata
    fields = []
    seen = set()
    existing_fields = {field["name"]: field for field in metadata.get("fields", []) if field.get("name")}
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
                existing = existing_fields.get(name, {})
                fields.append(
                    {
                        "name": name,
                        "type": str(widget.field_type_string or widget.field_type or existing.get("type") or ""),
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


def populate_crm_person_from_form(person):
    person.full_name = request.form["full_name"].strip()
    person.phone = request.form.get("phone", "").strip()
    person.email = request.form.get("email", "").strip()
    person.address = request.form.get("address", "").strip()


def username_taken(username, current_record=None):
    if not username:
        return False
    for model in (ApexUser, AgencyUser, Client, AgencyPreparer, AgencyCaseManager):
        record = model.query.filter_by(username=username).first()
        if not record:
            continue
        if current_record and isinstance(record, current_record.__class__) and record.id == current_record.id:
            continue
        return True
    return False


def populate_staff_login_from_form(person, label):
    username = request.form.get("username", "").strip()
    password = request.form.get("password", "")
    if not username:
        person.username = None
        person.password_hash = None
        return True
    if username_taken(username, person):
        flash("That username is already in use. Please choose another one.", "danger")
        return False
    if not password and not person.password_hash:
        flash(f"Password is required when creating a {label} login account.", "danger")
        return False
    person.username = username
    if password:
        person.set_password(password)
    return True


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


def date_from_form(name):
    raw = request.form.get(name, "").strip()
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError:
        return None


def datetime_from_form(name):
    raw = request.form.get(name, "").strip()
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%Y-%m-%dT%H:%M")
    except ValueError:
        return None


def next_crm_invoice_number():
    return next_crm_invoice_number_for_agency(current_user.agency_id)


def next_crm_invoice_number_for_agency(agency_id):
    count = CrmInvoice.query.filter_by(agency_id=agency_id).count() + 1
    return f"CRM-{agency_id:03d}-{count:05d}"


def populate_crm_invoice_from_form(invoice):
    invoice.issue_date = date_from_form("issue_date") or invoice.issue_date or datetime.utcnow().date()
    invoice.due_date = date_from_form("due_date")
    invoice.description = request.form.get("description", "").strip()
    invoice.subtotal = decimal_from_form("subtotal")
    invoice.discount = decimal_from_form("discount")
    invoice.total = max(Decimal("0"), invoice.subtotal - invoice.discount)
    invoice.paid_amount = decimal_from_form("paid_amount")
    invoice.status = request.form.get("status") or "Unpaid"
    invoice.notes = request.form.get("notes", "").strip()


def crm_invoice_for_case(case):
    invoice = CrmInvoice.query.filter_by(case_id=case.id, agency_id=case.agency_id).order_by(CrmInvoice.created_at.asc()).first()
    if not invoice:
        invoice = CrmInvoice(
            agency_id=case.agency_id,
            client_id=case.client_id,
            case_id=case.id,
            invoice_number=next_crm_invoice_number_for_agency(case.agency_id),
            issue_date=datetime.utcnow().date(),
            description=case.title,
        )
        db.session.add(invoice)
        db.session.flush()
    return invoice


def sync_case_invoice(case):
    invoice = crm_invoice_for_case(case)
    invoice.client_id = case.client_id
    invoice.description = case.title
    invoice.subtotal = case.price or Decimal("0")
    recalc_crm_invoice(invoice)
    return invoice


def recalc_crm_invoice(invoice):
    activities = CrmInvoiceActivity.query.filter_by(invoice_id=invoice.id).all() if invoice.id else invoice.activities
    discounts = sum((activity.amount or Decimal("0")) for activity in activities if activity.activity_type == "Discount")
    payments = sum((activity.amount or Decimal("0")) for activity in activities if activity.activity_type == "Payment")
    refunds = sum((activity.amount or Decimal("0")) for activity in activities if activity.activity_type == "Refund")
    invoice.subtotal = invoice.case.price or invoice.subtotal or Decimal("0")
    invoice.discount = discounts
    invoice.total = invoice.subtotal - discounts
    invoice.paid_amount = payments - refunds
    balance = invoice.total - invoice.paid_amount
    if invoice.status != "Void":
        if invoice.paid_amount <= 0:
            invoice.status = "Unpaid"
        elif balance <= 0:
            invoice.status = "Paid"
        else:
            invoice.status = "Partial"
    return balance


def crm_client_note_timeline(client):
    entries = []
    for note in CrmClientNote.query.filter_by(client_id=client.id, agency_id=client.agency_id).all():
        entries.append(
            {
                "created_at": note.created_at,
                "source": "General",
                "title": "Client note",
                "text": note.note_text,
                "url": url_for("crm_client_detail", client_id=client.id),
            }
        )
    for note in (
        CrmCaseNote.query.join(CrmCase)
        .filter(CrmCase.client_id == client.id, CrmCase.agency_id == client.agency_id)
        .all()
    ):
        entries.append(
            {
                "created_at": note.created_at,
                "source": "Case",
                "title": note.case.title,
                "text": note.note_text,
                "url": url_for("crm_case_detail", case_id=note.case_id),
            }
        )
    for note in (
        CrmAppointmentNote.query.join(CrmAppointment)
        .filter(CrmAppointment.client_id == client.id, CrmAppointment.agency_id == client.agency_id)
        .all()
    ):
        entries.append(
            {
                "created_at": note.created_at,
                "source": "Appointment",
                "title": note.appointment.title or note.appointment.case.title,
                "text": note.note_text,
                "url": url_for("crm_appointment_detail", appointment_id=note.appointment_id),
            }
        )
    for invoice in CrmInvoice.query.filter_by(client_id=client.id, agency_id=client.agency_id).all():
        if invoice.notes:
            entries.append(
                {
                    "created_at": invoice.updated_at or invoice.created_at,
                    "source": "Invoice",
                    "title": invoice.invoice_number,
                    "text": invoice.notes,
                    "url": url_for("crm_invoice_detail", invoice_id=invoice.id),
                }
            )
    return sorted(entries, key=lambda entry: entry["created_at"] or datetime.min, reverse=True)


def populate_invoice_activity_from_form(activity):
    activity.activity_type = request.form["activity_type"].strip()
    activity.amount = decimal_from_form("amount")
    activity.activity_date = date_from_form("activity_date") or datetime.utcnow().date()
    activity.description = request.form.get("description", "").strip()


def populate_crm_appointment_from_form(appointment):
    start_at = datetime_from_form("start_at")
    if not start_at:
        flash("Appointment date and time is required.", "danger")
        abort(400)
    appointment.title = appointment.case.title if appointment.case else request.form.get("case_title", "").strip()
    appointment.appointment_type = request.form.get("appointment_type", "").strip()
    appointment.start_at = start_at
    try:
        duration_minutes = max(1, int(request.form.get("duration_minutes") or 30))
    except ValueError:
        duration_minutes = 30
    appointment.end_at = start_at + timedelta(minutes=duration_minutes)
    appointment.location = ""
    appointment.status = request.form.get("status") or "Scheduled"
    appointment.notes = request.form.get("notes", "").strip()


def crm_appointment_duration_minutes(appointment):
    if appointment and appointment.start_at and appointment.end_at:
        minutes = int((appointment.end_at - appointment.start_at).total_seconds() / 60)
        return minutes if minutes > 0 else 30
    return 30


def global_crm_case_service_options():
    return [(f"{code} - {purpose}", code, purpose, "global") for code, purpose in CRM_CASE_SERVICES]


def agency_crm_case_service_options(agency_id):
    private_types = AgencyCrmCaseType.query.filter_by(agency_id=agency_id).order_by(AgencyCrmCaseType.name).all()
    options = global_crm_case_service_options()
    existing_labels = {option[0] for option in options}
    for case_type in private_types:
        label = case_type.label
        if label not in existing_labels:
            options.append((label, case_type.name, case_type.purpose or "", "agency"))
            existing_labels.add(label)
    return options


def populate_crm_case_from_form(case):
    case.title = request.form["title"].strip()
    case.status = request.form.get("status") or "Open"
    case.price = decimal_from_form("price")
    case.case_manager_id = int(request.form["case_manager_id"]) if request.form.get("case_manager_id") else None
    case.form_preparer_id = int(request.form["form_preparer_id"]) if request.form.get("form_preparer_id") else None
    case.tag_id = resolve_crm_case_tag(case.agency_id)
    case.notes = request.form.get("notes", "").strip()
    if case.case_manager_id and not AgencyCaseManager.query.filter_by(id=case.case_manager_id, agency_id=case.agency_id).first():
        abort(403)
    if case.form_preparer_id and not AgencyPreparer.query.filter_by(id=case.form_preparer_id, agency_id=case.agency_id).first():
        abort(403)
    if case.tag_id and not CrmCaseTag.query.filter_by(id=case.tag_id, agency_id=case.agency_id).first():
        abort(403)
    if case.status == "Completed" and not case.completed_at:
        case.completed_at = datetime.utcnow()
    if case.status != "Completed":
        case.completed_at = None


def record_crm_case_status(case, status=None):
    status = status or case.status or "Open"
    last_entry = CrmCaseStatusHistory.query.filter_by(case_id=case.id).order_by(CrmCaseStatusHistory.changed_at.desc()).first()
    if last_entry and last_entry.status == status:
        return
    db.session.add(CrmCaseStatusHistory(agency_id=case.agency_id, case_id=case.id, status=status))


def ensure_crm_case_status_history(case):
    if case.status_history:
        return
    db.session.add(
        CrmCaseStatusHistory(
            agency_id=case.agency_id,
            case_id=case.id,
            status=case.status or "Open",
            changed_at=case.opened_at or case.created_at or datetime.utcnow(),
        )
    )
    db.session.flush()


def sync_crm_case_questionnaire(crm_case):
    if not can_use_crm_form_filler(crm_case.agency) or not can_use_form_filler_for_current_user():
        return
    selected_codes = [code.strip() for code in request.form.getlist("form_codes") if code.strip()]
    legacy_code = request.form.get("form_code", "").strip()
    if legacy_code and legacy_code not in selected_codes:
        selected_codes.append(legacy_code)
    if not selected_codes:
        return
    existing_cases = linked_form_filler_cases_for_crm_case(crm_case)
    existing_codes = {case.case_type: case for case in existing_cases}
    for form_code in selected_codes:
        if form_code in existing_codes:
            continue
        template = FormTemplate.query.filter_by(code=form_code, is_active=True).first() or abort(404)
        questionnaire = Case(
            agency_id=crm_case.agency_id,
            client_id=crm_case.client_id,
            case_type=template.code,
            status="Waiting for Client",
            notes=f"Linked to CRM case #{crm_case.id}: {crm_case.title}",
        )
        db.session.add(questionnaire)
        db.session.flush()
        db.session.add(
            CrmCaseQuestionnaire(
                agency_id=crm_case.agency_id,
                crm_case_id=crm_case.id,
                form_filler_case_id=questionnaire.id,
            )
        )
        if not crm_case.form_filler_case_id:
            crm_case.form_filler_case_id = questionnaire.id


def linked_form_filler_cases_for_crm_case(crm_case):
    cases = []
    seen_ids = set()
    if crm_case.form_filler_case:
        cases.append(crm_case.form_filler_case)
        seen_ids.add(crm_case.form_filler_case.id)
    for link in sorted(crm_case.questionnaire_links, key=lambda item: (item.created_at, item.id)):
        questionnaire = link.form_filler_case
        if questionnaire and questionnaire.id not in seen_ids:
            cases.append(questionnaire)
            seen_ids.add(questionnaire.id)
    return cases


def resolve_crm_case_tag(agency_id):
    new_tag_name = request.form.get("new_tag_name", "").strip()
    if new_tag_name:
        existing = CrmCaseTag.query.filter(
            CrmCaseTag.agency_id == agency_id,
            func.lower(CrmCaseTag.name) == new_tag_name.lower(),
        ).first()
        if existing:
            return existing.id
        tag = CrmCaseTag(agency_id=agency_id, name=new_tag_name)
        db.session.add(tag)
        db.session.flush()
        return tag.id
    return int(request.form["tag_id"]) if request.form.get("tag_id") else None


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


def save_case_manual_pdf_values(case, manual_fields, existing_values):
    for field in manual_fields:
        form_key = f"manual_field_{field.id}"
        if form_key not in request.form:
            continue
        value = existing_values.get(field.id) or CasePdfManualValue(case_id=case.id, manual_field_id=field.id)
        value.value_text = request.form.get(form_key, "").strip()
        db.session.add(value)


def save_case_pdf_field_values(case, pdf_fields, existing_values):
    for field in pdf_fields:
        form_key = f"pdf_field_{field.id}"
        if form_key not in request.form:
            continue
        value = existing_values.get(field.id) or CasePdfFieldValue(case_id=case.id, pdf_field_id=field.id)
        value.value_text = request.form.get(form_key, "").strip()
        db.session.add(value)


def review_pdf_field_display_values(case, pdf_fields, saved_values):
    display_values = {}
    field_entries = answer_entries_by_pdf_field(case)
    field_lookup = build_pdf_field_value_lookup(field_entries) if field_entries else {"exact": {}, "loose": {}}
    checkbox_counts = {}
    for field in pdf_fields:
        if is_pdf_checkbox_field(field):
            key = normalized_pdf_field_key(field.field_name)
            checkbox_counts[key] = checkbox_counts.get(key, 0) + 1
    checkbox_occurrences = {}
    for field in pdf_fields:
        saved_value = saved_values.get(field.id)
        if saved_value and saved_value.value_text:
            display_values[field.id] = saved_value.value_text
            continue
        checkbox_field = is_pdf_checkbox_field(field)
        occurrence_key = normalized_pdf_field_key(field.field_name)
        occurrence = checkbox_occurrences.get(occurrence_key, 0)
        if checkbox_field:
            checkbox_occurrences[occurrence_key] = occurrence + 1
        entry = lookup_pdf_field_entry(
            field_lookup,
            field.field_name,
            PdfFieldWidgetProxy(field) if checkbox_field else None,
            strict=checkbox_field,
            widget_occurrence=occurrence if checkbox_field else None,
            widget_count=checkbox_counts.get(occurrence_key, 1),
        )
        if not entry:
            display_values[field.id] = ""
            continue
        display_values[field.id] = "Yes" if checkbox_field else entry.get("value", "")
    return display_values


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


def can_use_crm(agency):
    return bool(agency and agency.has_tool("CRM"))


def can_use_joinder(agency):
    return bool(agency and agency.has_tool("Joinder"))


def can_use_joinder_for_current_user():
    return current_user.is_authenticated and current_user.role == "agency" and (
        is_agency_owner()
        or isinstance(current_user, AgencyCaseManager)
        or isinstance(current_user, AgencyPreparer)
    )


def can_use_crm_form_filler(agency):
    return can_use_crm(agency) and can_use_form_filler(agency)


def active_knowledge_modules():
    return KnowledgeBaseModule.query.filter_by(is_active=True).order_by(
        KnowledgeBaseModule.sort_order,
        KnowledgeBaseModule.name,
    ).all()


def save_knowledge_topic_pdf(topic, file_storage):
    if not file_storage or not file_storage.filename:
        return
    if not file_storage.filename.lower().endswith(".pdf"):
        raise ValueError("Knowledge Base topics require a PDF file.")
    saved = save_upload(file_storage, f"knowledge_base/module_{topic.module_id}")
    if saved:
        topic.pdf_original_filename, topic.pdf_stored_filename = saved


def build_crm_report_data(agency_id, args):
    report_type = args.get("report_type", "cases").strip()
    if report_type not in {"cases", "invoices"}:
        report_type = "cases"
    manager_id = args.get("case_manager_id", "").strip()
    preparer_id = args.get("form_preparer_id", "").strip()
    tag_id = args.get("tag_id", "").strip()
    case_type = args.get("case_type", "").strip()
    case_status = args.get("case_status", "").strip()
    invoice_status = args.get("invoice_status", "").strip()
    created_from = args.get("created_from", "").strip()
    created_to = args.get("created_to", "").strip()
    date_warning = False

    cases_query = CrmCase.query.filter_by(agency_id=agency_id)
    if manager_id.isdigit():
        cases_query = cases_query.filter(CrmCase.case_manager_id == int(manager_id))
    if preparer_id.isdigit():
        cases_query = cases_query.filter(CrmCase.form_preparer_id == int(preparer_id))
    if tag_id.isdigit():
        cases_query = cases_query.filter(CrmCase.tag_id == int(tag_id))
    if case_type:
        cases_query = cases_query.filter(CrmCase.title == case_type)
    if case_status:
        cases_query = cases_query.filter(CrmCase.status == case_status)
    try:
        if created_from and report_type == "cases":
            cases_query = cases_query.filter(CrmCase.created_at >= datetime.strptime(created_from, "%Y-%m-%d"))
        if created_to and report_type == "cases":
            cases_query = cases_query.filter(CrmCase.created_at < datetime.strptime(created_to, "%Y-%m-%d") + timedelta(days=1))
    except ValueError:
        date_warning = True

    cases = cases_query.order_by(CrmCase.created_at.desc()).all()
    case_ids = [case.id for case in cases]
    invoices_query = CrmInvoice.query.filter_by(agency_id=agency_id)
    if case_ids:
        invoices_query = invoices_query.filter(CrmInvoice.case_id.in_(case_ids))
    elif manager_id or preparer_id or tag_id or case_type or case_status:
        invoices_query = invoices_query.filter(False)
    if invoice_status:
        invoices_query = invoices_query.filter(CrmInvoice.status == invoice_status)
    if report_type == "invoices":
        try:
            if created_from:
                invoices_query = invoices_query.filter(CrmInvoice.issue_date >= datetime.strptime(created_from, "%Y-%m-%d").date())
            if created_to:
                invoices_query = invoices_query.filter(CrmInvoice.issue_date <= datetime.strptime(created_to, "%Y-%m-%d").date())
        except ValueError:
            date_warning = True
    invoices = invoices_query.order_by(CrmInvoice.issue_date.desc(), CrmInvoice.updated_at.desc()).all()
    invoice_ids = [invoice.id for invoice in invoices]
    activities = (
        CrmInvoiceActivity.query.filter(CrmInvoiceActivity.agency_id == agency_id, CrmInvoiceActivity.invoice_id.in_(invoice_ids)).all()
        if invoice_ids
        else []
    )

    all_case_types = [option[0] for option in agency_crm_case_service_options(agency_id)]
    existing_case_types = [
        row[0]
        for row in db.session.query(CrmCase.title)
        .filter_by(agency_id=agency_id)
        .distinct()
        .order_by(CrmCase.title)
        .all()
    ]
    case_managers = AgencyCaseManager.query.filter_by(agency_id=agency_id).order_by(AgencyCaseManager.full_name).all()
    form_preparers = AgencyPreparer.query.filter_by(agency_id=agency_id).order_by(AgencyPreparer.full_name).all()
    case_tags = CrmCaseTag.query.filter_by(agency_id=agency_id).order_by(CrmCaseTag.name).all()
    manager_lookup = {manager.id: manager.full_name for manager in case_managers}
    preparer_lookup = {preparer.id: preparer.full_name for preparer in form_preparers}
    tag_lookup = {tag.id: tag.name for tag in case_tags}
    case_status_options = sorted(
        set(
            ["Open", "Documents Received", "Documents Needed", "Documents Ready", "Completed"]
            + [
                row[0]
                for row in db.session.query(CrmCase.status)
                .filter_by(agency_id=agency_id)
                .distinct()
                .order_by(CrmCase.status)
                .all()
                if row[0]
            ]
        )
    )
    invoice_status_options = sorted(
        set(
            ["Unpaid", "Partial", "Paid", "Overpaid"]
            + [
                row[0]
                for row in db.session.query(CrmInvoice.status)
                .filter_by(agency_id=agency_id)
                .distinct()
                .order_by(CrmInvoice.status)
                .all()
                if row[0]
            ]
        )
    )

    active_filters = []
    if case_status:
        active_filters.append(f'status "{case_status}"')
    if preparer_id.isdigit() and int(preparer_id) in preparer_lookup:
        active_filters.append(f'form preparer "{preparer_lookup[int(preparer_id)]}"')
    if tag_id.isdigit() and int(tag_id) in tag_lookup:
        active_filters.append(f'tag "{tag_lookup[int(tag_id)]}"')
    if manager_id.isdigit() and int(manager_id) in manager_lookup:
        active_filters.append(f'case manager "{manager_lookup[int(manager_id)]}"')
    if case_type:
        active_filters.append(f'case type "{case_type}"')
    if invoice_status and report_type == "invoices":
        active_filters.append(f'invoice status "{invoice_status}"')
    if created_from and created_to:
        active_filters.append(f"{'invoice date' if report_type == 'invoices' else 'created'} from {created_from} to {created_to}")
    elif created_from:
        active_filters.append(f"{'invoice date' if report_type == 'invoices' else 'created'} on or after {created_from}")
    elif created_to:
        active_filters.append(f"{'invoice date' if report_type == 'invoices' else 'created'} on or before {created_to}")
    report_answer = f"Showing all CRM {'invoices' if report_type == 'invoices' else 'cases'} for this agency."
    if active_filters:
        report_answer = f"Showing CRM {'invoices' if report_type == 'invoices' else 'cases'} with " + ", ".join(active_filters) + "."

    return {
        "cases": cases,
        "invoices": invoices,
        "case_managers": case_managers,
        "form_preparers": form_preparers,
        "case_tags": case_tags,
        "case_type_options": sorted(set(all_case_types + existing_case_types)),
        "case_status_options": case_status_options,
        "invoice_status_options": invoice_status_options,
        "report_answer": report_answer,
        "date_warning": date_warning,
        "filters": {
            "report_type": report_type,
            "manager_id": manager_id,
            "preparer_id": preparer_id,
            "tag_id": tag_id,
            "case_type": case_type,
            "case_status": case_status,
            "invoice_status": invoice_status,
            "created_from": created_from,
            "created_to": created_to,
        },
        "summary": {
            "case_count": len(cases),
            "invoice_count": len(invoices),
            "total_case_value": sum((case.price or Decimal("0")) for case in cases),
            "total_billed": sum((invoice.total or Decimal("0")) for invoice in invoices),
            "total_paid": sum((invoice.paid_amount or Decimal("0")) for invoice in invoices),
            "total_discounts": sum((invoice.discount or Decimal("0")) for invoice in invoices),
            "total_refunds": sum((activity.amount or Decimal("0")) for activity in activities if activity.activity_type == "Refund"),
            "open_balance": sum((invoice.balance_due or Decimal("0")) for invoice in invoices if invoice.status != "Paid"),
        },
    }


JOINDER_STATUSES = ["New", "Docs Received", "Reviewed", "Rejected", "Approved", "Paid"]
JOINDER_AGENCY_COMMISSION_BY_CONTRACT = {
    Decimal("2500"): Decimal("500"),
    Decimal("1500"): Decimal("350"),
    Decimal("1000"): Decimal("250"),
}
JOINDER_CASE_MANAGER_COMMISSION_RATE = Decimal("0.15")


def joinder_commissions_for_value(contract_value):
    contract = Decimal(str(contract_value or "0")).quantize(Decimal("0.01"))
    agency_commission = JOINDER_AGENCY_COMMISSION_BY_CONTRACT.get(contract, Decimal("0"))
    manager_commission = agency_commission * JOINDER_CASE_MANAGER_COMMISSION_RATE
    return {
        "agency_commission": agency_commission.quantize(Decimal("0.01")),
        "manager_commission": manager_commission.quantize(Decimal("0.01")),
    }


def joinder_search_summary(clients):
    total_contract_value = sum((client.contract_value or Decimal("0")) for client in clients)
    total_agency_commission = Decimal("0")
    total_manager_commission = Decimal("0")
    for client in clients:
        commissions = joinder_commissions_for_value(client.contract_value)
        total_agency_commission += commissions["agency_commission"]
        total_manager_commission += commissions["manager_commission"]
    return {
        "total_clients": len(clients),
        "total_contract_value": total_contract_value,
        "total_agency_commission": total_agency_commission,
        "total_manager_commission": total_manager_commission,
    }


def joinder_user_label():
    if is_agency_owner():
        return current_user.username
    return getattr(current_user, "full_name", None) or getattr(current_user, "username", "Agency user")


def joinder_access_required():
    if not can_use_joinder(current_user.agency):
        flash("This feature is not included in your current membership.", "warning")
        return False
    if not can_use_joinder_for_current_user():
        abort(403)
    return True


def populate_joinder_client_from_form(client, prefix=""):
    client.alien_number = request.form[f"{prefix}alien_number"].strip()
    client.first_name = request.form[f"{prefix}first_name"].strip()
    client.last_name = request.form[f"{prefix}last_name"].strip()
    client.phone = request.form.get(f"{prefix}phone", "").strip()
    client.email = request.form.get(f"{prefix}email", "").strip()
    client.address = request.form.get(f"{prefix}address", "").strip()
    client.city = request.form.get(f"{prefix}city", "").strip()
    client.state = request.form.get(f"{prefix}state", "").strip()
    client.contract_value = decimal_from_form(f"{prefix}contract_value")
    client.status = request.form.get(f"{prefix}status") or "New"
    manager_id = request.form.get(f"{prefix}case_manager_id", "").strip()
    client.case_manager_id = int(manager_id) if manager_id else None
    if client.case_manager_id and not AgencyCaseManager.query.filter_by(id=client.case_manager_id, agency_id=client.agency_id).first():
        abort(403)


def joinder_client_snapshot(client):
    return {
        "Alien Number": client.alien_number or "",
        "First Name": client.first_name or "",
        "Last Name": client.last_name or "",
        "Phone": client.phone or "",
        "Email": client.email or "",
        "Address": client.address or "",
        "City": client.city or "",
        "State": client.state or "",
        "Contract Value": f"{client.contract_value or Decimal('0')}",
        "Status": client.status or "",
        "Case Manager": client.case_manager.full_name if client.case_manager else "",
    }


def joinder_log(client, action, detail=""):
    db.session.add(
        JoinderActivityLog(
            agency_id=client.agency_id,
            client_id=client.id,
            user_label=joinder_user_label(),
            action=action,
            detail=detail,
        )
    )


def joinder_edit_detail(before, client):
    after = joinder_client_snapshot(client)
    changes = []
    for label, old_value in before.items():
        new_value = after.get(label, "")
        if str(old_value or "") != str(new_value or ""):
            changes.append(f"changed {label} from {old_value or 'blank'} to {new_value or 'blank'}")
    return "; ".join(changes)


def query_joinder_client(client_id):
    return JoinderClient.query.filter_by(id=client_id, agency_id=current_user.agency_id).first() or abort(404)


def joinder_related_clients(client):
    if client.primary_client_id:
        root = client.primary_client
        related = [root] + [dependent for dependent in root.dependents if dependent.id != client.id]
        return [item for item in related if item]
    return list(client.dependents)


def build_joinder_search_query(agency_id, args):
    query = JoinderClient.query.filter_by(agency_id=agency_id)
    search = args.get("q", "").strip()
    manager_id = args.get("case_manager_id", "").strip()
    status = args.get("status", "").strip()
    created_from = args.get("created_from", "").strip()
    created_to = args.get("created_to", "").strip()
    if search:
        like = f"%{search.lower()}%"
        query = query.filter(
            or_(
                func.lower(JoinderClient.first_name + " " + JoinderClient.last_name).like(like),
                func.lower(JoinderClient.last_name + " " + JoinderClient.first_name).like(like),
                func.lower(JoinderClient.alien_number).like(like),
                func.lower(JoinderClient.phone).like(like),
                func.lower(JoinderClient.email).like(like),
                func.lower(JoinderClient.address).like(like),
                func.lower(JoinderClient.city).like(like),
            )
        )
    if manager_id.isdigit():
        query = query.filter(JoinderClient.case_manager_id == int(manager_id))
    if status in JOINDER_STATUSES:
        query = query.filter(JoinderClient.status == status)
    try:
        if created_from:
            query = query.filter(JoinderClient.created_at >= datetime.strptime(created_from, "%Y-%m-%d"))
        if created_to:
            query = query.filter(JoinderClient.created_at < datetime.strptime(created_to, "%Y-%m-%d") + timedelta(days=1))
    except ValueError:
        flash("One of the dates was invalid and was ignored.", "warning")
    return query.order_by(JoinderClient.last_name, JoinderClient.first_name)


def save_joinder_document(client, file_storage, description=""):
    saved = save_upload(file_storage, f"joinder/clients/{client.id}")
    if not saved:
        return None
    original, stored = saved
    document = JoinderDocument(
        agency_id=client.agency_id,
        client_id=client.id,
        original_filename=original,
        stored_filename=stored,
        description=description,
    )
    db.session.add(document)
    db.session.flush()
    joinder_log(client, "Document uploaded", original)
    return document


def build_crm_chart(title, counts):
    palette = ["#1f73ff", "#12b8a6", "#f5a623", "#7157e8", "#f45d6c", "#4aa3df", "#38c977", "#ff8a4c"]
    cleaned = []
    for label, value in counts.items():
        numeric_value = float(value or 0)
        if numeric_value > 0:
            cleaned.append((label, numeric_value))
    cleaned.sort(key=lambda item: item[1], reverse=True)
    total = sum(value for _, value in cleaned)
    rows = []
    segments = []
    cursor = 0
    for index, (label, value) in enumerate(cleaned):
        percent = (value / total * 100) if total else 0
        color = palette[index % len(palette)]
        rows.append({"label": label, "value": value, "percent": percent, "color": color})
        segments.append(f"{color} {cursor:.2f}% {cursor + percent:.2f}%")
        cursor += percent
    return {
        "title": title,
        "total": total,
        "rows": rows,
        "gradient": ", ".join(segments) if segments else "#e9eff8 0% 100%",
    }


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
    db.session.commit()
    return 0


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
        (template.motion_title or template.display_name).upper(),
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


def render_motion_form_response(templates, lawyers, law_firms, references, motion=None):
    clients = Client.query.filter_by(agency_id=current_user.agency.id).order_by(
        Client.last_name, Client.first_name, Client.id
    ).all()
    client_options = [
        {
            "label": f"{client.full_name} - A# {client.a_number}" if client.a_number else client.full_name,
            "first_name": client.first_name,
            "middle_name": client.middle_name or "",
            "last_name": client.last_name,
            "alien_number": client.a_number or "",
            "search_text": " ".join(
                part
                for part in [client.full_name, client.a_number, client.email, client.phone, client.username]
                if part
            ),
        }
        for client in clients
    ]
    return render_template(
        "motion_form.html",
        templates=templates,
        lawyers=lawyers,
        law_firms=law_firms,
        motion=motion,
        client_options=client_options,
        **references,
    )


def update_motion_from_request(motion, agency):
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
        return False

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
        return False
    if not detention_status or not next_hearing_date or not next_hearing_type:
        flash("Detention classification, next hearing date, and next hearing type are required.", "danger")
        return False

    lawyer_id = request.form.get("lawyer_id")
    law_firm_id = request.form.get("law_firm_id")
    exhibits = normalize_exhibits(request.form.getlist("exhibit_description[]"))
    lawyer = db.session.get(AgencyLawyer, int(lawyer_id)) if lawyer_id else None
    law_firm = db.session.get(AgencyLawFirm, int(law_firm_id)) if law_firm_id else None
    if lawyer and lawyer.agency_id != agency.id:
        abort(403)
    if law_firm and law_firm.agency_id != agency.id:
        abort(403)

    motion.agency_id = agency.id
    motion.template_id = template.id
    motion.motion_title = template.motion_title
    motion.immigration_court = court
    motion.immigration_court_address = court_address
    motion.immigration_judge = judge
    motion.opla_office = opla
    motion.opla_address = opla_address
    motion.lawyer_id = lawyer.id if lawyer else None
    motion.law_firm_id = law_firm.id if law_firm else None
    motion.lawyer_name = lawyer.full_name if lawyer else ""
    motion.lawyer_bar_number = lawyer.bar_number if lawyer else ""
    motion.law_firm_name = law_firm.name if law_firm else ""
    motion.law_firm_phone = law_firm.phone if law_firm else ""
    motion.law_firm_address = law_firm.address if law_firm else ""
    motion.detention_status = detention_status
    motion.next_hearing_date = next_hearing_date
    motion.next_hearing_type = next_hearing_type
    motion.exhibits_text = "\n".join(exhibits)
    motion.rendered_content = render_motion_content(template, respondents, court, court_address, judge, opla, opla_address, lawyer, law_firm, exhibits, detention_status, next_hearing_date, next_hearing_type)

    if motion.id:
        MotionRespondent.query.filter_by(motion_id=motion.id).delete()
        db.session.flush()
    for index, person in enumerate(respondents, start=1):
        db.session.add(MotionRespondent(motion=motion, sort_order=index, **person))
    return True


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
                user = (
                    AgencyUser.query.filter_by(username=username).first()
                    or AgencyPreparer.query.filter_by(username=username).first()
                    or AgencyCaseManager.query.filter_by(username=username).first()
                )
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

    @app.route("/apex/knowledge-base", methods=["GET", "POST"])
    @role_required("apex")
    def apex_knowledge_base_admin():
        if request.method == "POST":
            module = KnowledgeBaseModule(
                name=request.form["name"].strip(),
                description=request.form.get("description", "").strip(),
                sort_order=KnowledgeBaseModule.query.count() + 1,
                is_active=bool(request.form.get("is_active")),
            )
            db.session.add(module)
            db.session.commit()
            flash("Knowledge Base module created.", "success")
            return redirect(url_for("apex_knowledge_module_detail", module_id=module.id))
        modules = KnowledgeBaseModule.query.order_by(KnowledgeBaseModule.sort_order, KnowledgeBaseModule.name).all()
        return render_template("apex_knowledge_base_admin.html", modules=modules)

    @app.route("/apex/knowledge-base/modules/<int:module_id>", methods=["GET", "POST"])
    @role_required("apex")
    def apex_knowledge_module_detail(module_id):
        module = db.session.get(KnowledgeBaseModule, module_id) or abort(404)
        if request.method == "POST":
            topic = KnowledgeBaseTopic(
                module_id=module.id,
                title=request.form["title"].strip(),
                description=request.form.get("description", "").strip(),
                sort_order=KnowledgeBaseTopic.query.filter_by(module_id=module.id).count() + 1,
                is_active=bool(request.form.get("is_active")),
            )
            db.session.add(topic)
            db.session.flush()
            try:
                save_knowledge_topic_pdf(topic, request.files.get("pdf"))
            except ValueError as exc:
                db.session.rollback()
                flash(str(exc), "danger")
                return redirect(url_for("apex_knowledge_module_detail", module_id=module.id))
            db.session.commit()
            flash("Knowledge Base topic created.", "success")
            return redirect(url_for("apex_knowledge_module_detail", module_id=module.id))
        topics = KnowledgeBaseTopic.query.filter_by(module_id=module.id).order_by(KnowledgeBaseTopic.sort_order, KnowledgeBaseTopic.title).all()
        return render_template("apex_knowledge_module_detail.html", module=module, topics=topics)

    @app.route("/apex/knowledge-base/modules/<int:module_id>/edit", methods=["POST"])
    @role_required("apex")
    def apex_knowledge_module_edit(module_id):
        module = db.session.get(KnowledgeBaseModule, module_id) or abort(404)
        module.name = request.form["name"].strip()
        module.description = request.form.get("description", "").strip()
        module.is_active = bool(request.form.get("is_active"))
        db.session.commit()
        flash("Knowledge Base module updated.", "success")
        return redirect(url_for("apex_knowledge_module_detail", module_id=module.id))

    @app.route("/apex/knowledge-base/topics/<int:topic_id>/edit", methods=["GET", "POST"])
    @role_required("apex")
    def apex_knowledge_topic_edit(topic_id):
        topic = db.session.get(KnowledgeBaseTopic, topic_id) or abort(404)
        if request.method == "POST":
            topic.title = request.form["title"].strip()
            topic.description = request.form.get("description", "").strip()
            topic.is_active = bool(request.form.get("is_active"))
            try:
                save_knowledge_topic_pdf(topic, request.files.get("pdf"))
            except ValueError as exc:
                db.session.rollback()
                flash(str(exc), "danger")
                return redirect(url_for("apex_knowledge_topic_edit", topic_id=topic.id))
            db.session.commit()
            flash("Knowledge Base topic updated.", "success")
            return redirect(url_for("apex_knowledge_module_detail", module_id=topic.module_id))
        return render_template("apex_knowledge_topic_form.html", topic=topic)

    @app.route("/apex/knowledge-base/topics/<int:topic_id>/delete", methods=["POST"])
    @role_required("apex")
    def apex_knowledge_topic_delete(topic_id):
        topic = db.session.get(KnowledgeBaseTopic, topic_id) or abort(404)
        module_id = topic.module_id
        stored_path = os.path.join(app.config["UPLOAD_FOLDER"], topic.pdf_stored_filename) if topic.pdf_stored_filename else None
        db.session.delete(topic)
        db.session.commit()
        if stored_path and os.path.exists(stored_path):
            try:
                os.remove(stored_path)
            except OSError:
                pass
        flash("Knowledge Base topic deleted.", "info")
        return redirect(url_for("apex_knowledge_module_detail", module_id=module_id))

    @app.route("/apex/knowledge-base/modules/<int:module_id>/topics/reorder", methods=["POST"])
    @role_required("apex")
    def apex_knowledge_topics_reorder(module_id):
        module = db.session.get(KnowledgeBaseModule, module_id) or abort(404)
        payload = request.get_json(silent=True) or {}
        try:
            ordered_ids = [int(raw_id) for raw_id in payload.get("topic_ids", [])]
        except (TypeError, ValueError):
            abort(400)
        topics = KnowledgeBaseTopic.query.filter_by(module_id=module.id).all()
        topics_by_id = {topic.id: topic for topic in topics}
        if set(ordered_ids) != set(topics_by_id):
            abort(400)
        for index, topic_id in enumerate(ordered_ids, start=1):
            topics_by_id[topic_id].sort_order = index
        db.session.commit()
        return {"status": "ok"}

    @app.route("/agency/knowledge-base")
    @role_required("agency")
    def agency_knowledge_base():
        modules = active_knowledge_modules()
        selected_module = None
        selected_topic = None
        module_id = request.args.get("module_id", "").strip()
        topic_id = request.args.get("topic_id", "").strip()
        if module_id.isdigit():
            selected_module = KnowledgeBaseModule.query.filter_by(id=int(module_id), is_active=True).first()
        if not selected_module and modules:
            selected_module = modules[0]
        topics = []
        if selected_module:
            topics = KnowledgeBaseTopic.query.filter_by(module_id=selected_module.id, is_active=True).order_by(
                KnowledgeBaseTopic.sort_order,
                KnowledgeBaseTopic.title,
            ).all()
            if topic_id.isdigit():
                selected_topic = next((topic for topic in topics if topic.id == int(topic_id)), None)
            if not selected_topic and topics:
                selected_topic = topics[0]
        return render_template(
            "agency_knowledge_base.html",
            modules=modules,
            selected_module=selected_module,
            topics=topics,
            selected_topic=selected_topic,
        )

    @app.route("/knowledge-base/topics/<int:topic_id>/pdf")
    @role_required("apex", "agency")
    def knowledge_topic_pdf(topic_id):
        topic = db.session.get(KnowledgeBaseTopic, topic_id) or abort(404)
        if current_user.role == "agency" and (not topic.is_active or not topic.module.is_active):
            abort(404)
        if not topic.pdf_stored_filename:
            abort(404)
        authorize_upload_access(topic.pdf_stored_filename)
        return send_from_directory(app.config["UPLOAD_FOLDER"], topic.pdf_stored_filename, as_attachment=False)

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
            save_form_template_from_request()
            flash("Questionnaire created. Open the builder and right-click the PDF to add questions.", "success")
            return redirect(url_for("apex_form_filler_admin"))
        return render_template("apex_form_template_form.html")

    @app.route("/apex/subscriptions/form-filler/<int:template_id>/builder", methods=["GET", "POST"])
    @role_required("apex")
    def apex_form_builder(template_id):
        template = db.session.get(FormTemplate, template_id) or abort(404)
        questions = CaseQuestion.query.filter_by(case_type=template.code).order_by(CaseQuestion.sort_order).all()
        if request.method == "POST":
            if request.form.get("manual_field_mode"):
                if not request.form.get("placement_page"):
                    flash("Right-click the PDF where this agency text box should appear.", "warning")
                    return redirect(url_for("apex_form_builder", template_id=template.id))
                placement_payload = {
                    "page": request.form.get("placement_page"),
                    "x": request.form.get("placement_x"),
                    "y": request.form.get("placement_y"),
                    "width": request.form.get("placement_width"),
                    "height": request.form.get("placement_height"),
                }
                placement = visual_placement_from_request(template, placement_payload)
                manual_field = PdfManualField(
                    template_id=template.id,
                    label=request.form.get("prompt", "").strip() or "Agency PDF text box",
                    render_mode=request.form.get("render_mode") or "normal",
                    page_number=placement["page_number"],
                    x=placement["x"],
                    y=placement["y"],
                    width=placement["width"],
                    height=placement["height"],
                )
                try:
                    manual_field.render_box_count = max(0, int(request.form.get("render_box_count") or 0))
                except ValueError:
                    manual_field.render_box_count = 0
                db.session.add(manual_field)
                db.session.commit()
                flash("Agency-only PDF text box added.", "success")
                return redirect(url_for("apex_form_builder", template_id=template.id))
            question_id = request.form.get("question_id")
            question = db.session.get(CaseQuestion, int(question_id)) if question_id else CaseQuestion(case_type=template.code)
            question.prompt = request.form["prompt"].strip()
            question.prompt_es = request.form.get("prompt_es", "").strip() or question.prompt
            question.prompt_en = request.form.get("prompt_en", "").strip()
            question.prompt_ht = request.form.get("prompt_ht", "").strip()
            field_key = request.form.get("field_key", "").strip()
            if not field_key:
                field_key = f"{template.code.lower().replace('-', '')}_question_{uuid.uuid4().hex[:8]}"
            question.field_key = field_key
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
            db.session.flush()
            PdfField.query.filter_by(template_id=template.id, mapped_question_id=question.id).update(
                {"mapped_question_id": None},
                synchronize_session=False,
            )
            PdfField.query.filter_by(template_id=template.id, field_name=question.field_key).update(
                {"mapped_question_id": question.id},
                synchronize_session=False,
            )
            if request.form.get("placement_page"):
                placement_payload = {
                    "page": request.form.get("placement_page"),
                    "x": request.form.get("placement_x"),
                    "y": request.form.get("placement_y"),
                    "width": request.form.get("placement_width"),
                    "height": request.form.get("placement_height"),
                }
                placement = visual_placement_from_request(template, placement_payload)
                apply_visual_placement(
                    question,
                    placement["page_number"],
                    placement["x"],
                    placement["y"],
                    placement["width"],
                    placement["height"],
                )
            db.session.commit()
            flash("Question saved.", "success")
            return redirect(url_for("apex_form_builder", template_id=template.id))
        pdf_fields = PdfField.query.filter_by(template_id=template.id).order_by(PdfField.field_name).all()
        pdf_field_options = [
            {
                "id": field.id,
                "name": field.field_name,
                "label": readable_pdf_field_name(field.field_name),
                "type": field.field_type,
                "page": field.page_number,
                "mapping": pdf_field_visual_mapping(field),
                "is_checkbox": is_pdf_checkbox_field(field),
                "mapped_question_id": field.mapped_question_id,
            }
            for field in pdf_fields
        ]
        return render_template(
            "apex_form_builder.html",
            template=template,
            questions=questions,
            manual_fields=PdfManualField.query.filter_by(template_id=template.id).order_by(PdfManualField.page_number, PdfManualField.y, PdfManualField.x).all(),
            pdf_fields=pdf_fields,
            pdf_field_options=pdf_field_options,
            pdf_pages=template_pdf_pages(template),
        )

    @app.route("/apex/subscriptions/form-filler/<int:template_id>/builder/translations.csv")
    @role_required("apex")
    def apex_form_translations_export(template_id):
        template = db.session.get(FormTemplate, template_id) or abort(404)
        filename = f"{template.code.lower()}_question_translations.csv"
        return Response(
            question_translation_csv(template),
            mimetype="text/csv",
            headers={"Content-Disposition": f"attachment; filename={filename}"},
        )

    @app.route("/apex/subscriptions/form-filler/<int:template_id>/builder/translations/import", methods=["POST"])
    @role_required("apex")
    def apex_form_translations_import(template_id):
        template = db.session.get(FormTemplate, template_id) or abort(404)
        updated = import_question_translation_csv(template, request.files.get("translation_file"))
        if updated:
            flash(f"{updated} question translations imported.", "success")
        return redirect(url_for("apex_form_builder", template_id=template.id))

    @app.route("/apex/subscriptions/form-filler/<int:template_id>/builder/pdf-page/<int:page_number>.png")
    @role_required("apex")
    def apex_form_builder_pdf_page(template_id, page_number):
        template = db.session.get(FormTemplate, template_id) or abort(404)
        pdf_path = template_pdf_path(template)
        if not pdf_path or not os.path.exists(pdf_path):
            abort(404)
        try:
            import fitz
        except ImportError:
            abort(500)
        document = None
        try:
            document = fitz.open(pdf_path)
            if page_number < 1 or page_number > document.page_count:
                abort(404)
            page = document.load_page(page_number - 1)
            pixmap = page.get_pixmap(matrix=fitz.Matrix(1.6, 1.6), alpha=False)
            data = pixmap.tobytes("png")
        finally:
            if document:
                document.close()
        return send_file(BytesIO(data), mimetype="image/png", download_name=f"{template.code}-page-{page_number}.png")

    @app.route("/apex/subscriptions/form-filler/<int:template_id>/builder/questions/<int:question_id>/visual-placement", methods=["POST", "DELETE"])
    @role_required("apex")
    def apex_form_question_visual_placement(template_id, question_id):
        template = db.session.get(FormTemplate, template_id) or abort(404)
        question = db.session.get(CaseQuestion, question_id) or abort(404)
        if question.case_type != template.code:
            abort(404)
        if request.method == "DELETE":
            placement_id = request.args.get("placement_id", type=int)
            if placement_id:
                placement = PdfQuestionPlacement.query.filter_by(id=placement_id, question_id=question.id).first() or abort(404)
                db.session.delete(placement)
            else:
                PdfQuestionPlacement.query.filter_by(question_id=question.id).delete(synchronize_session=False)
                question.pdf_page_number = None
                question.pdf_x = None
                question.pdf_y = None
                question.pdf_width = None
                question.pdf_height = None
            db.session.commit()
            return {"status": "ok", "mappings": question_visual_mappings(question)}
        payload = request.get_json(silent=True) or {}
        placement_values = visual_placement_from_request(template, payload)
        placement = None
        placement_id = payload.get("placement_id")
        if placement_id:
            try:
                placement_id = int(placement_id)
            except (TypeError, ValueError):
                abort(400)
            placement = PdfQuestionPlacement.query.filter_by(id=placement_id, question_id=question.id).first() or abort(404)
        apply_visual_placement(
            question,
            placement_values["page_number"],
            placement_values["x"],
            placement_values["y"],
            placement_values["width"],
            placement_values["height"],
            placement=placement,
        )
        db.session.commit()
        return {"status": "ok", "mappings": question_visual_mappings(question)}

    @app.route("/apex/subscriptions/form-filler/<int:template_id>/builder/manual-fields/<int:field_id>/visual-placement", methods=["POST", "DELETE"])
    @role_required("apex")
    def apex_form_manual_field_visual_placement(template_id, field_id):
        template = db.session.get(FormTemplate, template_id) or abort(404)
        field = db.session.get(PdfManualField, field_id) or abort(404)
        if field.template_id != template.id:
            abort(404)
        if request.method == "DELETE":
            CasePdfManualValue.query.filter_by(manual_field_id=field.id).delete(synchronize_session=False)
            db.session.delete(field)
            db.session.commit()
            return {"status": "ok"}
        placement_values = visual_placement_from_request(template, request.get_json(silent=True) or {})
        apply_manual_field_placement(
            field,
            placement_values["page_number"],
            placement_values["x"],
            placement_values["y"],
            placement_values["width"],
            placement_values["height"],
        )
        db.session.commit()
        return {"status": "ok", "mapping": manual_field_visual_mapping(field)}

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
        if not can_use_form_filler_for_current_user():
            abort(403)
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
    @agency_motion_required
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
    @agency_motion_required
    def motion_template_new():
        agency = current_user.agency
        if not can_use_motion_creation(agency):
            flash("This feature is not included in your current membership.", "warning")
            return redirect(url_for("agency_dashboard"))
        if request.method == "POST":
            name = request.form["name"].strip()
            motion_title = request.form["motion_title"].strip()
            content = request.form["content"].strip()
            if not name:
                flash("Template name is required.", "danger")
                return render_template("motion_template_form.html", template=None)
            if not motion_title:
                flash("Motion title is required.", "danger")
                return render_template("motion_template_form.html", template=None)
            if not content:
                flash("Motion template content is required.", "danger")
                return render_template("motion_template_form.html", template=None)
            template = MotionTemplate(agency_id=agency.id, name=name, motion_title=motion_title, content=content)
            db.session.add(template)
            db.session.commit()
            flash("Motion template created.", "success")
            return redirect(url_for("agency_motions"))
        return render_template("motion_template_form.html", template=None)

    @app.route("/agency/tools/motions/templates/<int:template_id>/edit", methods=["GET", "POST"])
    @agency_motion_required
    def motion_template_edit(template_id):
        agency = current_user.agency
        if not can_use_motion_creation(agency):
            flash("This feature is not included in your current membership.", "warning")
            return redirect(url_for("agency_dashboard"))
        template = motion_template_for_agency(template_id, agency.id)
        if request.method == "POST":
            template.name = request.form["name"].strip()
            template.motion_title = request.form["motion_title"].strip()
            template.content = request.form["content"].strip()
            if not template.name:
                flash("Template name is required.", "danger")
                return render_template("motion_template_form.html", template=template)
            if not template.motion_title:
                flash("Motion title is required.", "danger")
                return render_template("motion_template_form.html", template=template)
            if not template.content:
                flash("Motion template content is required.", "danger")
                return render_template("motion_template_form.html", template=template)
            db.session.commit()
            flash("Motion template updated.", "success")
            return redirect(url_for("agency_motions"))
        return render_template("motion_template_form.html", template=template)

    @app.route("/agency/tools/motions/templates/<int:template_id>/delete", methods=["POST"])
    @agency_motion_required
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
    @agency_motion_required
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
            motion = MotionDraft()
            if not update_motion_from_request(motion, agency):
                db.session.rollback()
                return render_motion_form_response(templates, lawyers, law_firms, references)
            db.session.add(motion)
            db.session.commit()
            flash("Motion created.", "success")
            return redirect(url_for("motion_detail", motion_id=motion.id))
        return render_motion_form_response(templates, lawyers, law_firms, references)

    @app.route("/agency/tools/motions/<int:motion_id>/edit", methods=["GET", "POST"])
    @agency_motion_required
    def motion_edit(motion_id):
        agency = current_user.agency
        if not can_use_motion_creation(agency):
            flash("This feature is not included in your current membership.", "warning")
            return redirect(url_for("agency_dashboard"))
        motion = motion_for_agency(motion_id, agency.id)
        templates = MotionTemplate.query.filter_by(agency_id=agency.id).order_by(MotionTemplate.updated_at.desc()).all()
        references = motion_reference_lists()
        lawyers = AgencyLawyer.query.filter_by(agency_id=agency.id).order_by(AgencyLawyer.last_name, AgencyLawyer.first_name).all()
        law_firms = AgencyLawFirm.query.filter_by(agency_id=agency.id).order_by(AgencyLawFirm.name).all()
        if request.method == "POST":
            if not update_motion_from_request(motion, agency):
                db.session.rollback()
                return render_motion_form_response(templates, lawyers, law_firms, references, motion=motion)
            db.session.commit()
            flash("Motion updated.", "success")
            return redirect(url_for("motion_detail", motion_id=motion.id))
        return render_motion_form_response(templates, lawyers, law_firms, references, motion=motion)

    @app.route("/agency/tools/motions/<int:motion_id>")
    @agency_motion_required
    def motion_detail(motion_id):
        agency = current_user.agency
        if not can_use_motion_creation(agency):
            flash("This feature is not included in your current membership.", "warning")
            return redirect(url_for("agency_dashboard"))
        motion = motion_for_agency(motion_id, agency.id)
        return render_template("motion_detail.html", motion=motion)

    @app.route("/agency/tools/motions/<int:motion_id>/download")
    @agency_motion_required
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
    @agency_motion_required
    def motion_pdf(motion_id):
        agency = current_user.agency
        if not can_use_motion_creation(agency):
            flash("This feature is not included in your current membership.", "warning")
            return redirect(url_for("agency_dashboard"))
        motion = motion_for_agency(motion_id, agency.id)
        return motion_pdf_response(motion)

    @app.route("/agency/tools/motions/<int:motion_id>/delete", methods=["POST"])
    @agency_motion_required
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
    @agency_owner_required
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
    @agency_owner_required
    def translator_edit(translator_id):
        translator = AgencyTranslator.query.filter_by(id=translator_id, agency_id=current_user.agency_id).first() or abort(404)
        if request.method == "POST":
            populate_translator_from_form(translator)
            db.session.commit()
            flash("Translator updated.", "success")
            return redirect(url_for("translator_list"))
        return render_template("person_form.html", agency=current_user.agency, person=translator, person_type="translator", title="Edit Translator")

    @app.route("/agency/translators/<int:translator_id>/delete", methods=["POST"])
    @agency_owner_required
    def translator_delete(translator_id):
        translator = AgencyTranslator.query.filter_by(id=translator_id, agency_id=current_user.agency_id).first() or abort(404)
        Case.query.filter_by(translator_id=translator.id).update({"translator_id": None})
        db.session.delete(translator)
        db.session.commit()
        flash("Translator deleted.", "info")
        return redirect(url_for("translator_list"))

    @app.route("/agency/preparers", methods=["GET", "POST"])
    @agency_owner_required
    def preparer_list():
        agency = current_user.agency
        if request.method == "POST":
            preparer = AgencyPreparer(agency_id=agency.id)
            populate_preparer_from_form(preparer)
            if not populate_staff_login_from_form(preparer, "form preparer"):
                return redirect(url_for("preparer_list"))
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
    @agency_owner_required
    def preparer_edit(preparer_id):
        preparer = AgencyPreparer.query.filter_by(id=preparer_id, agency_id=current_user.agency_id).first() or abort(404)
        if request.method == "POST":
            populate_preparer_from_form(preparer)
            if not populate_staff_login_from_form(preparer, "form preparer"):
                return render_template("person_form.html", agency=current_user.agency, person=preparer, person_type="preparer", title="Edit Form Preparer")
            db.session.commit()
            flash("Form preparer updated.", "success")
            return redirect(url_for("preparer_list"))
        return render_template("person_form.html", agency=current_user.agency, person=preparer, person_type="preparer", title="Edit Form Preparer")

    @app.route("/agency/preparers/<int:preparer_id>/delete", methods=["POST"])
    @agency_owner_required
    def preparer_delete(preparer_id):
        preparer = AgencyPreparer.query.filter_by(id=preparer_id, agency_id=current_user.agency_id).first() or abort(404)
        Case.query.filter_by(preparer_id=preparer.id).update({"preparer_id": None})
        CrmCase.query.filter_by(form_preparer_id=preparer.id).update({"form_preparer_id": None})
        db.session.delete(preparer)
        db.session.commit()
        flash("Form preparer deleted.", "info")
        return redirect(url_for("preparer_list"))

    @app.route("/agency/case-managers", methods=["GET", "POST"])
    @agency_owner_required
    def case_manager_list():
        agency = current_user.agency
        if request.method == "POST":
            manager = AgencyCaseManager(agency_id=agency.id)
            populate_crm_person_from_form(manager)
            if not populate_staff_login_from_form(manager, "case manager"):
                return redirect(url_for("case_manager_list"))
            db.session.add(manager)
            db.session.commit()
            flash("Case manager saved.", "success")
            return redirect(url_for("case_manager_list"))
        return render_template(
            "people_list.html",
            agency=agency,
            people=AgencyCaseManager.query.filter_by(agency_id=agency.id).order_by(AgencyCaseManager.full_name).all(),
            person_type="case_manager",
            title="Case Managers",
        )

    @app.route("/agency/case-managers/<int:manager_id>/edit", methods=["GET", "POST"])
    @agency_owner_required
    def case_manager_edit(manager_id):
        manager = AgencyCaseManager.query.filter_by(id=manager_id, agency_id=current_user.agency_id).first() or abort(404)
        if request.method == "POST":
            populate_crm_person_from_form(manager)
            if not populate_staff_login_from_form(manager, "case manager"):
                return render_template("person_form.html", agency=current_user.agency, person=manager, person_type="case_manager", title="Edit Case Manager")
            db.session.commit()
            flash("Case manager updated.", "success")
            return redirect(url_for("case_manager_list"))
        return render_template("person_form.html", agency=current_user.agency, person=manager, person_type="case_manager", title="Edit Case Manager")

    @app.route("/agency/case-managers/<int:manager_id>/delete", methods=["POST"])
    @agency_owner_required
    def case_manager_delete(manager_id):
        manager = AgencyCaseManager.query.filter_by(id=manager_id, agency_id=current_user.agency_id).first() or abort(404)
        CrmCase.query.filter_by(case_manager_id=manager.id).update({"case_manager_id": None})
        JoinderClient.query.filter_by(case_manager_id=manager.id).update({"case_manager_id": None})
        db.session.delete(manager)
        db.session.commit()
        flash("Case manager deleted.", "info")
        return redirect(url_for("case_manager_list"))

    @app.route("/agency/crm-preparers", methods=["GET", "POST"])
    @agency_owner_required
    def crm_preparer_list():
        return redirect(url_for("preparer_list"))

    @app.route("/agency/crm-preparers/<int:preparer_id>/edit", methods=["GET", "POST"])
    @agency_owner_required
    def crm_preparer_edit(preparer_id):
        return redirect(url_for("preparer_list"))

    @app.route("/agency/crm-preparers/<int:preparer_id>/delete", methods=["POST"])
    @agency_owner_required
    def crm_preparer_delete(preparer_id):
        return redirect(url_for("preparer_list"))

    @app.route("/agency/lawyers", methods=["GET", "POST"])
    @agency_owner_required
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
    @agency_owner_required
    def lawyer_edit(lawyer_id):
        lawyer = AgencyLawyer.query.filter_by(id=lawyer_id, agency_id=current_user.agency_id).first() or abort(404)
        if request.method == "POST":
            populate_lawyer_from_form(lawyer)
            db.session.commit()
            flash("Lawyer updated.", "success")
            return redirect(url_for("lawyer_list"))
        return render_template("lawyer_form.html", lawyer=lawyer)

    @app.route("/agency/lawyers/<int:lawyer_id>/delete", methods=["POST"])
    @agency_owner_required
    def lawyer_delete(lawyer_id):
        lawyer = AgencyLawyer.query.filter_by(id=lawyer_id, agency_id=current_user.agency_id).first() or abort(404)
        MotionDraft.query.filter_by(lawyer_id=lawyer.id).update({"lawyer_id": None})
        db.session.delete(lawyer)
        db.session.commit()
        flash("Lawyer deleted.", "info")
        return redirect(url_for("lawyer_list"))

    @app.route("/agency/law-firms", methods=["GET", "POST"])
    @agency_owner_required
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
    @agency_owner_required
    def law_firm_edit(firm_id):
        firm = AgencyLawFirm.query.filter_by(id=firm_id, agency_id=current_user.agency_id).first() or abort(404)
        if request.method == "POST":
            populate_law_firm_from_form(firm)
            db.session.commit()
            flash("Law firm updated.", "success")
            return redirect(url_for("law_firm_list"))
        return render_template("law_firm_form.html", firm=firm)

    @app.route("/agency/law-firms/<int:firm_id>/delete", methods=["POST"])
    @agency_owner_required
    def law_firm_delete(firm_id):
        firm = AgencyLawFirm.query.filter_by(id=firm_id, agency_id=current_user.agency_id).first() or abort(404)
        MotionDraft.query.filter_by(law_firm_id=firm.id).update({"law_firm_id": None})
        db.session.delete(firm)
        db.session.commit()
        flash("Law firm deleted.", "info")
        return redirect(url_for("law_firm_list"))

    @app.route("/agency/crm")
    @role_required("agency")
    def agency_crm():
        if not can_use_crm(current_user.agency):
            flash("This feature is not included in your current membership.", "warning")
            return redirect(url_for("agency_dashboard"))
        cases = CrmCase.query.filter_by(agency_id=current_user.agency_id).order_by(CrmCase.updated_at.desc()).all()
        for case in cases:
            sync_case_invoice(case)
        db.session.commit()
        invoices = CrmInvoice.query.filter_by(agency_id=current_user.agency_id).order_by(CrmInvoice.updated_at.desc()).all()
        search = request.args.get("q", "").strip()
        manager_id = request.args.get("case_manager_id", "").strip()
        preparer_id = request.args.get("form_preparer_id", "").strip()
        created_on = request.args.get("created_on", "").strip()
        created_from = request.args.get("created_from", "").strip()
        created_to = request.args.get("created_to", "").strip()
        searched = request.args.get("searched") == "1"
        clients = []
        if searched:
            clients_query = Client.query.filter_by(agency_id=current_user.agency_id)
            if search:
                lowered_search = f"%{search.lower()}%"
                clients_query = clients_query.filter(
                    or_(
                        func.lower(Client.first_name + " " + Client.last_name).like(lowered_search),
                        func.lower(Client.email).like(lowered_search),
                        func.lower(Client.phone).like(lowered_search),
                        func.lower(Client.a_number).like(lowered_search),
                    )
                )
            if created_on:
                date_value = datetime.strptime(created_on, "%Y-%m-%d").date()
                clients_query = clients_query.filter(func.date(Client.created_at) == date_value)
            else:
                if created_from:
                    clients_query = clients_query.filter(func.date(Client.created_at) >= datetime.strptime(created_from, "%Y-%m-%d").date())
                if created_to:
                    clients_query = clients_query.filter(func.date(Client.created_at) <= datetime.strptime(created_to, "%Y-%m-%d").date())
            if manager_id or preparer_id:
                clients_query = clients_query.join(CrmCase, CrmCase.client_id == Client.id)
                if manager_id:
                    clients_query = clients_query.filter(CrmCase.case_manager_id == int(manager_id))
                if preparer_id:
                    clients_query = clients_query.filter(CrmCase.form_preparer_id == int(preparer_id))
                clients_query = clients_query.distinct()
            clients = clients_query.order_by(Client.last_name, Client.first_name).all()
        case_statuses = ["Open", "Documents Received", "Documents Needed", "Documents Ready", "Completed"]
        case_status_counts = {status: len([case for case in cases if case.status == status]) for status in case_statuses}
        open_balance_total = sum((invoice.balance_due or Decimal("0")) for invoice in invoices if invoice.status != "Paid")
        return render_template(
            "agency_crm.html",
            clients=clients,
            cases=cases,
            invoices=invoices,
            case_status_counts=case_status_counts,
            total_clients=Client.query.filter_by(agency_id=current_user.agency_id).count(),
            total_discounts=sum((invoice.discount or Decimal("0")) for invoice in invoices),
            total_refunds=sum((activity.amount or Decimal("0")) for activity in CrmInvoiceActivity.query.filter_by(agency_id=current_user.agency_id, activity_type="Refund").all()),
            open_balance_total=open_balance_total,
            searched=searched,
            case_managers=AgencyCaseManager.query.filter_by(agency_id=current_user.agency_id).order_by(AgencyCaseManager.full_name).all(),
            form_preparers=AgencyPreparer.query.filter_by(agency_id=current_user.agency_id).order_by(AgencyPreparer.full_name).all(),
            search=search,
            manager_id=manager_id,
            preparer_id=preparer_id,
            created_on=created_on,
            created_from=created_from,
            created_to=created_to,
        )

    @app.route("/agency/crm/calendar")
    @role_required("agency")
    def crm_calendar():
        if not can_use_crm(current_user.agency):
            flash("This feature is not included in your current membership.", "warning")
            return redirect(url_for("agency_dashboard"))
        view = request.args.get("view", "month")
        if view not in {"month", "week", "day"}:
            view = "month"
        try:
            selected_date = datetime.strptime(request.args.get("date", ""), "%Y-%m-%d").date()
        except ValueError:
            selected_date = datetime.utcnow().date()

        if view == "day":
            start_date = selected_date
            end_date = selected_date
            title = selected_date.strftime("%B %d, %Y")
            previous_date = selected_date - timedelta(days=1)
            next_date = selected_date + timedelta(days=1)
        elif view == "week":
            start_date = selected_date - timedelta(days=selected_date.weekday())
            end_date = start_date + timedelta(days=6)
            title = f"{start_date.strftime('%b %d')} - {end_date.strftime('%b %d, %Y')}"
            previous_date = selected_date - timedelta(days=7)
            next_date = selected_date + timedelta(days=7)
        else:
            start_date = selected_date.replace(day=1)
            _, last_day = calendar_lib.monthrange(selected_date.year, selected_date.month)
            end_date = selected_date.replace(day=last_day)
            title = selected_date.strftime("%B %Y")
            previous_month = selected_date.month - 1 or 12
            previous_year = selected_date.year - 1 if selected_date.month == 1 else selected_date.year
            next_month = selected_date.month + 1 if selected_date.month < 12 else 1
            next_year = selected_date.year + 1 if selected_date.month == 12 else selected_date.year
            previous_date = selected_date.replace(year=previous_year, month=previous_month, day=1)
            next_date = selected_date.replace(year=next_year, month=next_month, day=1)

        start_dt = datetime.combine(start_date, datetime.min.time())
        end_dt = datetime.combine(end_date + timedelta(days=1), datetime.min.time())
        appointments = (
            CrmAppointment.query.filter(
                CrmAppointment.agency_id == current_user.agency_id,
                CrmAppointment.start_at >= start_dt,
                CrmAppointment.start_at < end_dt,
            )
            .order_by(CrmAppointment.start_at.asc())
            .all()
        )
        appointments_by_date = {}
        for appointment in appointments:
            appointments_by_date.setdefault(appointment.start_at.date(), []).append(appointment)

        calendar_weeks = []
        if view == "month":
            for week in calendar_lib.Calendar(firstweekday=0).monthdatescalendar(selected_date.year, selected_date.month):
                calendar_weeks.append(
                    [
                        {
                            "date": day,
                            "in_month": day.month == selected_date.month,
                            "appointments": appointments_by_date.get(day, []),
                        }
                        for day in week
                    ]
                )
        else:
            day_count = 1 if view == "day" else 7
            calendar_weeks.append(
                [
                    {
                        "date": start_date + timedelta(days=offset),
                        "in_month": True,
                        "appointments": appointments_by_date.get(start_date + timedelta(days=offset), []),
                    }
                    for offset in range(day_count)
                ]
            )

        return render_template(
            "crm_calendar.html",
            view=view,
            title=title,
            selected_date=selected_date,
            previous_date=previous_date,
            next_date=next_date,
            calendar_weeks=calendar_weeks,
            appointment_count=len(appointments),
        )

    @app.route("/agency/crm/reports")
    @role_required("agency")
    def crm_reports():
        if not can_use_crm(current_user.agency):
            flash("This feature is not included in your current membership.", "warning")
            return redirect(url_for("agency_dashboard"))
        report_data = build_crm_report_data(current_user.agency_id, request.args)
        if report_data["date_warning"]:
            flash("One of the report dates was invalid and was ignored.", "warning")
        return render_template("crm_reports.html", **report_data)

    @app.route("/agency/crm/reports/download")
    @role_required("agency")
    def crm_reports_download():
        if not can_use_crm(current_user.agency):
            flash("This feature is not included in your current membership.", "warning")
            return redirect(url_for("agency_dashboard"))
        return generate_crm_report_pdf(build_crm_report_data(current_user.agency_id, request.args))

    @app.route("/agency/crm/case-types", methods=["GET", "POST"])
    @role_required("agency")
    def crm_case_types():
        if not can_use_crm(current_user.agency):
            flash("This feature is not included in your current membership.", "warning")
            return redirect(url_for("agency_dashboard"))
        if not is_agency_owner():
            abort(403)
        if request.method == "POST":
            name = request.form.get("name", "").strip()
            purpose = request.form.get("purpose", "").strip()
            if not name:
                flash("Case type name is required.", "warning")
                return redirect(url_for("crm_case_types"))
            existing_global = {code.lower() for code, _ in CRM_CASE_SERVICES} | {f"{code} - {purpose}".lower() for code, purpose in CRM_CASE_SERVICES}
            if name.lower() in existing_global:
                flash("That case type already exists globally.", "warning")
                return redirect(url_for("crm_case_types"))
            if AgencyCrmCaseType.query.filter(func.lower(AgencyCrmCaseType.name) == name.lower(), AgencyCrmCaseType.agency_id == current_user.agency_id).first():
                flash("That private case type already exists for this agency.", "warning")
                return redirect(url_for("crm_case_types"))
            db.session.add(AgencyCrmCaseType(agency_id=current_user.agency_id, name=name, purpose=purpose))
            db.session.commit()
            flash("Private case type added.", "success")
            return redirect(url_for("crm_case_types"))
        return render_template(
            "crm_case_types.html",
            private_case_types=AgencyCrmCaseType.query.filter_by(agency_id=current_user.agency_id).order_by(AgencyCrmCaseType.name).all(),
            global_case_types=CRM_CASE_SERVICES,
        )

    @app.route("/agency/crm/case-types/<int:case_type_id>/delete", methods=["POST"])
    @role_required("agency")
    def crm_case_type_delete(case_type_id):
        if not can_use_crm(current_user.agency):
            flash("This feature is not included in your current membership.", "warning")
            return redirect(url_for("agency_dashboard"))
        if not is_agency_owner():
            abort(403)
        case_type = AgencyCrmCaseType.query.filter_by(id=case_type_id, agency_id=current_user.agency_id).first() or abort(404)
        db.session.delete(case_type)
        db.session.commit()
        flash("Private case type deleted.", "success")
        return redirect(url_for("crm_case_types"))

    @app.route("/agency/crm/clients/<int:client_id>")
    @role_required("agency")
    def crm_client_detail(client_id):
        if not can_use_crm(current_user.agency):
            flash("This feature is not included in your current membership.", "warning")
            return redirect(url_for("agency_dashboard"))
        client = Client.query.filter_by(id=client_id, agency_id=current_user.agency_id).first() or abort(404)
        for case in client.crm_cases:
            sync_case_invoice(case)
        db.session.commit()
        questionnaire_codes = [template.code for template in available_form_templates()] if can_use_form_filler(current_user.agency) else []
        questionnaires = (
            Case.query.filter(
                Case.agency_id == current_user.agency_id,
                Case.client_id == client.id,
                Case.case_type.in_(questionnaire_codes),
            )
            .order_by(Case.updated_at.desc(), Case.id.desc())
            .all()
            if questionnaire_codes
            else []
        )
        linked_crm_cases = {}
        if questionnaires:
            questionnaire_ids = [questionnaire.id for questionnaire in questionnaires]
            linked_rows = CrmCase.query.filter(
                CrmCase.agency_id == current_user.agency_id,
                CrmCase.client_id == client.id,
                CrmCase.form_filler_case_id.in_(questionnaire_ids),
            ).all()
            linked_crm_cases = {row.form_filler_case_id: row for row in linked_rows}
            linked_rows = CrmCaseQuestionnaire.query.filter(
                CrmCaseQuestionnaire.agency_id == current_user.agency_id,
                CrmCaseQuestionnaire.form_filler_case_id.in_(questionnaire_ids),
            ).all()
            crm_case_ids = [row.crm_case_id for row in linked_rows]
            crm_case_lookup = {
                row.id: row
                for row in CrmCase.query.filter(
                    CrmCase.id.in_(crm_case_ids),
                    CrmCase.client_id == client.id,
                    CrmCase.agency_id == current_user.agency_id,
                ).all()
            } if crm_case_ids else {}
            for row in linked_rows:
                if row.crm_case_id in crm_case_lookup:
                    linked_crm_cases[row.form_filler_case_id] = crm_case_lookup[row.crm_case_id]
        return render_template(
            "crm_client_detail.html",
            client=client,
            cases=CrmCase.query.filter_by(client_id=client.id, agency_id=current_user.agency_id).order_by(CrmCase.updated_at.desc()).all(),
            invoices=CrmInvoice.query.filter_by(client_id=client.id, agency_id=current_user.agency_id).order_by(CrmInvoice.updated_at.desc()).all(),
            appointments=CrmAppointment.query.filter_by(client_id=client.id, agency_id=current_user.agency_id).order_by(CrmAppointment.start_at.desc()).all(),
            documents=CrmClientDocument.query.filter_by(client_id=client.id, agency_id=current_user.agency_id).order_by(CrmClientDocument.uploaded_at.desc()).all(),
            questionnaires=questionnaires,
            linked_crm_cases=linked_crm_cases,
            note_timeline=crm_client_note_timeline(client),
        )

    @app.route("/agency/crm/clients/<int:client_id>/notes", methods=["POST"])
    @role_required("agency")
    def crm_client_note_create(client_id):
        if not can_use_crm(current_user.agency):
            flash("This feature is not included in your current membership.", "warning")
            return redirect(url_for("agency_dashboard"))
        client = Client.query.filter_by(id=client_id, agency_id=current_user.agency_id).first() or abort(404)
        note_text = request.form.get("note_text", "").strip()
        if note_text:
            db.session.add(CrmClientNote(agency_id=current_user.agency_id, client_id=client.id, note_text=note_text))
            db.session.commit()
            flash("Client note added.", "success")
        return redirect(url_for("crm_client_detail", client_id=client.id))

    @app.route("/agency/crm/clients/<int:client_id>/cases/new", methods=["GET", "POST"])
    @role_required("agency")
    def crm_case_create(client_id):
        if not can_use_crm(current_user.agency):
            flash("This feature is not included in your current membership.", "warning")
            return redirect(url_for("agency_dashboard"))
        client = Client.query.filter_by(id=client_id, agency_id=current_user.agency_id).first() or abort(404)
        if request.method == "POST":
            case = CrmCase(
                agency_id=current_user.agency_id,
                client_id=client.id,
            )
            populate_crm_case_from_form(case)
            db.session.add(case)
            db.session.flush()
            record_crm_case_status(case)
            sync_crm_case_questionnaire(case)
            if case.notes:
                db.session.add(CrmCaseNote(agency_id=case.agency_id, case_id=case.id, note_text=case.notes))
                case.notes = ""
            sync_case_invoice(case)
            db.session.commit()
            flash("CRM case created.", "success")
            return redirect(url_for("crm_client_detail", client_id=client.id))
        return render_template(
            "crm_case_form.html",
            client=client,
            case=None,
            crm_case_services=agency_crm_case_service_options(current_user.agency_id),
            case_managers=AgencyCaseManager.query.filter_by(agency_id=current_user.agency_id).order_by(AgencyCaseManager.full_name).all(),
            form_preparers=AgencyPreparer.query.filter_by(agency_id=current_user.agency_id).order_by(AgencyPreparer.full_name).all(),
            case_tags=CrmCaseTag.query.filter_by(agency_id=current_user.agency_id).order_by(CrmCaseTag.name).all(),
            form_templates=available_form_templates() if can_use_crm_form_filler(current_user.agency) and can_use_form_filler_for_current_user() else [],
            can_link_form_filler=can_use_crm_form_filler(current_user.agency) and can_use_form_filler_for_current_user(),
            selected_form_codes=[],
        )

    @app.route("/agency/crm/cases/<int:case_id>/edit", methods=["GET", "POST"])
    @role_required("agency")
    def crm_case_edit(case_id):
        if not can_use_crm(current_user.agency):
            flash("This feature is not included in your current membership.", "warning")
            return redirect(url_for("agency_dashboard"))
        case = CrmCase.query.filter_by(id=case_id, agency_id=current_user.agency_id).first() or abort(404)
        if request.method == "POST":
            ensure_crm_case_status_history(case)
            existing_note = case.notes
            previous_status = case.status
            populate_crm_case_from_form(case)
            if case.status != previous_status:
                record_crm_case_status(case)
            sync_crm_case_questionnaire(case)
            if case.notes and case.notes != existing_note:
                db.session.add(CrmCaseNote(agency_id=case.agency_id, case_id=case.id, note_text=case.notes))
                case.notes = ""
            sync_case_invoice(case)
            db.session.commit()
            flash("CRM case updated.", "success")
            return redirect(url_for("crm_client_detail", client_id=case.client_id))
        return render_template(
            "crm_case_form.html",
            client=case.client,
            case=case,
            crm_case_services=agency_crm_case_service_options(current_user.agency_id),
            case_managers=AgencyCaseManager.query.filter_by(agency_id=current_user.agency_id).order_by(AgencyCaseManager.full_name).all(),
            form_preparers=AgencyPreparer.query.filter_by(agency_id=current_user.agency_id).order_by(AgencyPreparer.full_name).all(),
            case_tags=CrmCaseTag.query.filter_by(agency_id=current_user.agency_id).order_by(CrmCaseTag.name).all(),
            form_templates=available_form_templates() if can_use_crm_form_filler(current_user.agency) and can_use_form_filler_for_current_user() else [],
            can_link_form_filler=can_use_crm_form_filler(current_user.agency) and can_use_form_filler_for_current_user(),
            selected_form_codes=[questionnaire.case_type for questionnaire in linked_form_filler_cases_for_crm_case(case)],
        )

    @app.route("/agency/crm/cases/<int:case_id>")
    @role_required("agency")
    def crm_case_detail(case_id):
        if not can_use_crm(current_user.agency):
            flash("This feature is not included in your current membership.", "warning")
            return redirect(url_for("agency_dashboard"))
        case = CrmCase.query.filter_by(id=case_id, agency_id=current_user.agency_id).first() or abort(404)
        ensure_crm_case_status_history(case)
        sync_case_invoice(case)
        db.session.commit()
        return render_template("crm_case_detail.html", case=case, linked_questionnaires=linked_form_filler_cases_for_crm_case(case))

    @app.route("/agency/crm/cases/<int:case_id>/notes", methods=["POST"])
    @role_required("agency")
    def crm_case_note_create(case_id):
        if not can_use_crm(current_user.agency):
            flash("This feature is not included in your current membership.", "warning")
            return redirect(url_for("agency_dashboard"))
        case = CrmCase.query.filter_by(id=case_id, agency_id=current_user.agency_id).first() or abort(404)
        note_text = request.form.get("note_text", "").strip()
        if note_text:
            db.session.add(CrmCaseNote(agency_id=current_user.agency_id, case_id=case.id, note_text=note_text))
            db.session.commit()
            flash("Case note added.", "success")
        return redirect(url_for("crm_case_detail", case_id=case.id))

    @app.route("/agency/crm/cases/<int:case_id>/delete", methods=["POST"])
    @role_required("agency")
    def crm_case_delete(case_id):
        if not can_use_crm(current_user.agency):
            flash("This feature is not included in your current membership.", "warning")
            return redirect(url_for("agency_dashboard"))
        case = CrmCase.query.filter_by(id=case_id, agency_id=current_user.agency_id).first() or abort(404)
        client_id = case.client_id
        linked_questionnaires = linked_form_filler_cases_for_crm_case(case)
        db.session.delete(case)
        for linked_questionnaire in linked_questionnaires:
            db.session.delete(linked_questionnaire)
        db.session.commit()
        flash("CRM case deleted.", "info")
        return redirect(url_for("crm_client_detail", client_id=client_id))

    @app.route("/agency/crm/clients/<int:client_id>/invoices/new", methods=["GET", "POST"])
    @role_required("agency")
    def crm_invoice_create(client_id):
        if not can_use_crm(current_user.agency):
            flash("This feature is not included in your current membership.", "warning")
            return redirect(url_for("agency_dashboard"))
        client = Client.query.filter_by(id=client_id, agency_id=current_user.agency_id).first() or abort(404)
        cases = CrmCase.query.filter_by(client_id=client.id, agency_id=current_user.agency_id).order_by(CrmCase.updated_at.desc()).all()
        if request.method == "POST":
            case = CrmCase.query.filter_by(id=int(request.form["case_id"]), client_id=client.id, agency_id=current_user.agency_id).first() or abort(404)
            invoice = CrmInvoice(
                agency_id=current_user.agency_id,
                client_id=client.id,
                case_id=case.id,
                invoice_number=request.form.get("invoice_number", "").strip() or next_crm_invoice_number(),
            )
            populate_crm_invoice_from_form(invoice)
            db.session.add(invoice)
            db.session.commit()
            flash("Invoice created.", "success")
            return redirect(url_for("crm_client_detail", client_id=client.id))
        return render_template("crm_invoice_form.html", client=client, invoice=None, cases=cases)

    @app.route("/agency/crm/invoices/<int:invoice_id>/edit", methods=["GET", "POST"])
    @role_required("agency")
    def crm_invoice_edit(invoice_id):
        if not can_use_crm(current_user.agency):
            flash("This feature is not included in your current membership.", "warning")
            return redirect(url_for("agency_dashboard"))
        invoice = CrmInvoice.query.filter_by(id=invoice_id, agency_id=current_user.agency_id).first() or abort(404)
        cases = CrmCase.query.filter_by(client_id=invoice.client_id, agency_id=current_user.agency_id).order_by(CrmCase.updated_at.desc()).all()
        if request.method == "POST":
            case = CrmCase.query.filter_by(id=int(request.form["case_id"]), client_id=invoice.client_id, agency_id=current_user.agency_id).first() or abort(404)
            invoice.case_id = case.id
            invoice.invoice_number = request.form.get("invoice_number", "").strip() or invoice.invoice_number
            populate_crm_invoice_from_form(invoice)
            db.session.commit()
            flash("Invoice updated.", "success")
            return redirect(url_for("crm_client_detail", client_id=invoice.client_id))
        return render_template("crm_invoice_form.html", client=invoice.client, invoice=invoice, cases=cases)

    @app.route("/agency/crm/invoices/<int:invoice_id>")
    @role_required("agency")
    def crm_invoice_detail(invoice_id):
        if not can_use_crm(current_user.agency):
            flash("This feature is not included in your current membership.", "warning")
            return redirect(url_for("agency_dashboard"))
        invoice = CrmInvoice.query.filter_by(id=invoice_id, agency_id=current_user.agency_id).first() or abort(404)
        sync_case_invoice(invoice.case)
        db.session.commit()
        return render_template("crm_invoice_detail.html", invoice=invoice, balance_due=invoice.balance_due)

    @app.route("/agency/crm/invoices/<int:invoice_id>/activities/new", methods=["POST"])
    @role_required("agency")
    def crm_invoice_activity_create(invoice_id):
        if not can_use_crm(current_user.agency):
            flash("This feature is not included in your current membership.", "warning")
            return redirect(url_for("agency_dashboard"))
        invoice = CrmInvoice.query.filter_by(id=invoice_id, agency_id=current_user.agency_id).first() or abort(404)
        if request.form.get("activity_type") == "Refund" and not is_agency_owner():
            abort(403)
        activity = CrmInvoiceActivity(agency_id=current_user.agency_id, invoice_id=invoice.id)
        populate_invoice_activity_from_form(activity)
        db.session.add(activity)
        db.session.flush()
        recalc_crm_invoice(invoice)
        db.session.commit()
        flash(f"{activity.activity_type} added.", "success")
        return redirect(url_for("crm_invoice_detail", invoice_id=invoice.id))

    @app.route("/agency/crm/invoice-activities/<int:activity_id>/edit", methods=["GET", "POST"])
    @role_required("agency")
    def crm_invoice_activity_edit(activity_id):
        if not can_use_crm(current_user.agency):
            flash("This feature is not included in your current membership.", "warning")
            return redirect(url_for("agency_dashboard"))
        if not is_agency_owner():
            abort(403)
        activity = CrmInvoiceActivity.query.filter_by(id=activity_id, agency_id=current_user.agency_id).first() or abort(404)
        if request.method == "POST":
            populate_invoice_activity_from_form(activity)
            recalc_crm_invoice(activity.invoice)
            db.session.commit()
            flash("Invoice activity updated.", "success")
            return redirect(url_for("crm_invoice_detail", invoice_id=activity.invoice_id))
        return render_template("crm_invoice_activity_form.html", activity=activity)

    @app.route("/agency/crm/invoice-activities/<int:activity_id>/delete", methods=["POST"])
    @role_required("agency")
    def crm_invoice_activity_delete(activity_id):
        if not can_use_crm(current_user.agency):
            flash("This feature is not included in your current membership.", "warning")
            return redirect(url_for("agency_dashboard"))
        if not is_agency_owner():
            abort(403)
        activity = CrmInvoiceActivity.query.filter_by(id=activity_id, agency_id=current_user.agency_id).first() or abort(404)
        invoice = activity.invoice
        invoice_id = invoice.id
        db.session.delete(activity)
        db.session.flush()
        recalc_crm_invoice(invoice)
        db.session.commit()
        flash("Invoice activity deleted.", "info")
        return redirect(url_for("crm_invoice_detail", invoice_id=invoice_id))

    @app.route("/agency/crm/invoices/<int:invoice_id>/pdf")
    @role_required("agency")
    def crm_invoice_pdf(invoice_id):
        if not can_use_crm(current_user.agency):
            flash("This feature is not included in your current membership.", "warning")
            return redirect(url_for("agency_dashboard"))
        invoice = CrmInvoice.query.filter_by(id=invoice_id, agency_id=current_user.agency_id).first() or abort(404)
        sync_case_invoice(invoice.case)
        db.session.commit()
        return generate_crm_invoice_pdf(invoice)

    @app.route("/agency/crm/invoices/<int:invoice_id>/delete", methods=["POST"])
    @role_required("agency")
    def crm_invoice_delete(invoice_id):
        if not can_use_crm(current_user.agency):
            flash("This feature is not included in your current membership.", "warning")
            return redirect(url_for("agency_dashboard"))
        invoice = CrmInvoice.query.filter_by(id=invoice_id, agency_id=current_user.agency_id).first() or abort(404)
        client_id = invoice.client_id
        db.session.delete(invoice)
        db.session.commit()
        flash("Invoice deleted.", "info")
        return redirect(url_for("crm_client_detail", client_id=client_id))

    @app.route("/agency/crm/cases/<int:case_id>/appointments/new", methods=["GET", "POST"])
    @role_required("agency")
    def crm_appointment_create(case_id):
        if not can_use_crm(current_user.agency):
            flash("This feature is not included in your current membership.", "warning")
            return redirect(url_for("agency_dashboard"))
        case = CrmCase.query.filter_by(id=case_id, agency_id=current_user.agency_id).first() or abort(404)
        if request.method == "POST":
            appointment = CrmAppointment(
                agency_id=current_user.agency_id,
                client_id=case.client_id,
                case_id=case.id,
            )
            populate_crm_appointment_from_form(appointment)
            db.session.add(appointment)
            db.session.flush()
            if appointment.notes:
                db.session.add(CrmAppointmentNote(agency_id=appointment.agency_id, appointment_id=appointment.id, note_text=appointment.notes))
                appointment.notes = ""
            db.session.commit()
            flash("Appointment created.", "success")
            return redirect(url_for("crm_client_detail", client_id=case.client_id))
        return render_template("crm_appointment_form.html", case=case, appointment=None, duration_minutes=30)

    @app.route("/agency/crm/appointments/<int:appointment_id>/edit", methods=["GET", "POST"])
    @role_required("agency")
    def crm_appointment_edit(appointment_id):
        if not can_use_crm(current_user.agency):
            flash("This feature is not included in your current membership.", "warning")
            return redirect(url_for("agency_dashboard"))
        appointment = CrmAppointment.query.filter_by(id=appointment_id, agency_id=current_user.agency_id).first() or abort(404)
        if request.method == "POST":
            existing_note = appointment.notes
            populate_crm_appointment_from_form(appointment)
            if appointment.notes and appointment.notes != existing_note:
                db.session.add(CrmAppointmentNote(agency_id=appointment.agency_id, appointment_id=appointment.id, note_text=appointment.notes))
                appointment.notes = ""
            db.session.commit()
            flash("Appointment updated.", "success")
            return redirect(url_for("crm_client_detail", client_id=appointment.client_id))
        return render_template(
            "crm_appointment_form.html",
            case=appointment.case,
            appointment=appointment,
            duration_minutes=crm_appointment_duration_minutes(appointment),
        )

    @app.route("/agency/crm/appointments/<int:appointment_id>")
    @role_required("agency")
    def crm_appointment_detail(appointment_id):
        if not can_use_crm(current_user.agency):
            flash("This feature is not included in your current membership.", "warning")
            return redirect(url_for("agency_dashboard"))
        appointment = CrmAppointment.query.filter_by(id=appointment_id, agency_id=current_user.agency_id).first() or abort(404)
        return render_template(
            "crm_appointment_detail.html",
            appointment=appointment,
            duration_minutes=crm_appointment_duration_minutes(appointment),
        )

    @app.route("/agency/crm/appointments/<int:appointment_id>/notes", methods=["POST"])
    @role_required("agency")
    def crm_appointment_note_create(appointment_id):
        if not can_use_crm(current_user.agency):
            flash("This feature is not included in your current membership.", "warning")
            return redirect(url_for("agency_dashboard"))
        appointment = CrmAppointment.query.filter_by(id=appointment_id, agency_id=current_user.agency_id).first() or abort(404)
        note_text = request.form.get("note_text", "").strip()
        if note_text:
            db.session.add(CrmAppointmentNote(agency_id=current_user.agency_id, appointment_id=appointment.id, note_text=note_text))
            db.session.commit()
            flash("Appointment note added.", "success")
        return redirect(url_for("crm_appointment_detail", appointment_id=appointment.id))

    @app.route("/agency/crm/appointments/<int:appointment_id>/delete", methods=["POST"])
    @role_required("agency")
    def crm_appointment_delete(appointment_id):
        if not can_use_crm(current_user.agency):
            flash("This feature is not included in your current membership.", "warning")
            return redirect(url_for("agency_dashboard"))
        appointment = CrmAppointment.query.filter_by(id=appointment_id, agency_id=current_user.agency_id).first() or abort(404)
        client_id = appointment.client_id
        db.session.delete(appointment)
        db.session.commit()
        flash("Appointment deleted.", "info")
        return redirect(url_for("crm_client_detail", client_id=client_id))

    @app.route("/agency/crm/clients/<int:client_id>/documents", methods=["POST"])
    @role_required("agency")
    def crm_document_upload(client_id):
        if not can_use_crm(current_user.agency):
            flash("This feature is not included in your current membership.", "warning")
            return redirect(url_for("agency_dashboard"))
        client = Client.query.filter_by(id=client_id, agency_id=current_user.agency_id).first() or abort(404)
        file_storage = request.files.get("document")
        try:
            saved = save_upload(file_storage, f"crm/clients/{client.id}")
        except ValueError as exc:
            flash(str(exc), "danger")
            return redirect(url_for("crm_client_detail", client_id=client.id))
        if not saved:
            flash("Choose a document to upload.", "warning")
            return redirect(url_for("crm_client_detail", client_id=client.id))
        original, stored = saved
        case_id = request.form.get("case_id")
        linked_case = None
        if case_id:
            linked_case = CrmCase.query.filter_by(id=int(case_id), client_id=client.id, agency_id=current_user.agency_id).first() or abort(404)
        db.session.add(
            CrmClientDocument(
                agency_id=current_user.agency_id,
                client_id=client.id,
                case_id=linked_case.id if linked_case else None,
                original_filename=original,
                stored_filename=stored,
                document_type=request.form.get("document_type", "").strip() or "Client document",
                description=request.form.get("description", "").strip(),
            )
        )
        db.session.commit()
        flash("Document uploaded.", "success")
        return redirect(url_for("crm_client_detail", client_id=client.id))

    @app.route("/agency/crm/documents/<int:document_id>/delete", methods=["POST"])
    @role_required("agency")
    def crm_document_delete(document_id):
        if not can_use_crm(current_user.agency):
            flash("This feature is not included in your current membership.", "warning")
            return redirect(url_for("agency_dashboard"))
        document = CrmClientDocument.query.filter_by(id=document_id, agency_id=current_user.agency_id).first() or abort(404)
        client_id = document.client_id
        stored_path = os.path.join(app.config["UPLOAD_FOLDER"], document.stored_filename)
        db.session.delete(document)
        db.session.commit()
        if os.path.exists(stored_path):
            try:
                os.remove(stored_path)
            except OSError:
                pass
        flash("Document deleted.", "info")
        return redirect(url_for("crm_client_detail", client_id=client_id))

    @app.route("/agency/crm/documents/<int:document_id>/view")
    @role_required("agency")
    def crm_document_view(document_id):
        if not can_use_crm(current_user.agency):
            flash("This feature is not included in your current membership.", "warning")
            return redirect(url_for("agency_dashboard"))
        document = CrmClientDocument.query.filter_by(id=document_id, agency_id=current_user.agency_id).first() or abort(404)
        authorize_upload_access(document.stored_filename)
        return send_from_directory(app.config["UPLOAD_FOLDER"], document.stored_filename, as_attachment=False)

    @app.route("/agency/crm/documents/<int:document_id>/download")
    @role_required("agency")
    def crm_document_download(document_id):
        if not can_use_crm(current_user.agency):
            flash("This feature is not included in your current membership.", "warning")
            return redirect(url_for("agency_dashboard"))
        document = CrmClientDocument.query.filter_by(id=document_id, agency_id=current_user.agency_id).first() or abort(404)
        authorize_upload_access(document.stored_filename)
        return send_from_directory(
            app.config["UPLOAD_FOLDER"],
            document.stored_filename,
            as_attachment=True,
            download_name=document.original_filename,
        )

    @app.route("/agency/joinder")
    @role_required("agency")
    def agency_joinder():
        if not joinder_access_required():
            return redirect(url_for("agency_dashboard"))
        searched = request.args.get("searched") == "1"
        clients = build_joinder_search_query(current_user.agency_id, request.args).all() if searched else []
        show_admin_commissions = is_agency_owner()
        total_clients = JoinderClient.query.filter_by(agency_id=current_user.agency_id).count()
        total_value = (
            db.session.query(func.coalesce(func.sum(JoinderClient.contract_value), 0))
            .filter(JoinderClient.agency_id == current_user.agency_id)
            .scalar()
            or Decimal("0")
        )
        return render_template(
            "joinder_dashboard.html",
            clients=clients,
            searched=searched,
            show_admin_commissions=show_admin_commissions,
            result_summary=joinder_search_summary(clients) if searched else None,
            joinder_commissions_for_value=joinder_commissions_for_value,
            total_clients=total_clients,
            total_value=total_value,
            case_managers=AgencyCaseManager.query.filter_by(agency_id=current_user.agency_id).order_by(AgencyCaseManager.full_name).all(),
            filters={
                "q": request.args.get("q", ""),
                "case_manager_id": request.args.get("case_manager_id", ""),
                "status": request.args.get("status", ""),
                "created_from": request.args.get("created_from", ""),
                "created_to": request.args.get("created_to", ""),
            },
            statuses=JOINDER_STATUSES,
            related_clients=joinder_related_clients,
        )

    @app.route("/agency/joinder/search.pdf")
    @role_required("agency")
    def joinder_search_pdf():
        if not joinder_access_required():
            return redirect(url_for("agency_dashboard"))
        clients = build_joinder_search_query(current_user.agency_id, request.args).all()
        return generate_joinder_search_pdf(clients, request.args, show_admin_commissions=is_agency_owner())

    @app.route("/agency/joinder/clients/new", methods=["GET", "POST"])
    @role_required("agency")
    def joinder_client_create():
        if not joinder_access_required():
            return redirect(url_for("agency_dashboard"))
        case_managers = AgencyCaseManager.query.filter_by(agency_id=current_user.agency_id).order_by(AgencyCaseManager.full_name).all()
        if request.method == "POST":
            client = JoinderClient(agency_id=current_user.agency_id)
            populate_joinder_client_from_form(client)
            db.session.add(client)
            db.session.flush()
            joinder_log(client, "Client created", f"Created {client.full_name}")
            try:
                save_joinder_document(client, request.files.get("document"), request.form.get("document_description", "").strip())
            except ValueError as exc:
                db.session.rollback()
                flash(str(exc), "danger")
                return render_template("joinder_client_form.html", client=None, case_managers=case_managers, statuses=JOINDER_STATUSES, existing_dependents=[])
            try:
                dependent_count = int(request.form.get("dependent_count") or 0)
            except ValueError:
                dependent_count = 0
            for index in range(dependent_count):
                prefix = f"dependent_{index}_"
                if not request.form.get(f"{prefix}first_name", "").strip() and not request.form.get(f"{prefix}last_name", "").strip():
                    continue
                dependent = JoinderClient(agency_id=current_user.agency_id, primary_client_id=client.id)
                populate_joinder_client_from_form(dependent, prefix=prefix)
                db.session.add(dependent)
                db.session.flush()
                joinder_log(dependent, "Client created", f"Created dependent for {client.full_name}")
                try:
                    save_joinder_document(dependent, request.files.get(f"{prefix}document"), request.form.get(f"{prefix}document_description", "").strip())
                except ValueError as exc:
                    db.session.rollback()
                    flash(str(exc), "danger")
                    return render_template("joinder_client_form.html", client=None, case_managers=case_managers, statuses=JOINDER_STATUSES, existing_dependents=[])
            db.session.commit()
            flash("Joinder client saved.", "success")
            return redirect(url_for("joinder_client_detail", client_id=client.id))
        return render_template("joinder_client_form.html", client=None, case_managers=case_managers, statuses=JOINDER_STATUSES, existing_dependents=[])

    @app.route("/agency/joinder/clients/<int:client_id>")
    @role_required("agency")
    def joinder_client_detail(client_id):
        if not joinder_access_required():
            return redirect(url_for("agency_dashboard"))
        client = query_joinder_client(client_id)
        return render_template(
            "joinder_client_detail.html",
            client=client,
            documents=JoinderDocument.query.filter_by(client_id=client.id, agency_id=current_user.agency_id).order_by(JoinderDocument.uploaded_at.desc()).all(),
            related_clients=joinder_related_clients(client),
            statuses=JOINDER_STATUSES,
        )

    @app.route("/agency/joinder/clients/<int:client_id>/edit", methods=["GET", "POST"])
    @role_required("agency")
    def joinder_client_edit(client_id):
        if not joinder_access_required():
            return redirect(url_for("agency_dashboard"))
        client = query_joinder_client(client_id)
        case_managers = AgencyCaseManager.query.filter_by(agency_id=current_user.agency_id).order_by(AgencyCaseManager.full_name).all()
        existing_dependents = sorted(client.dependents, key=lambda dependent: dependent.full_name.lower()) if not client.primary_client_id else []
        if request.method == "POST":
            before = joinder_client_snapshot(client)
            populate_joinder_client_from_form(client)
            detail = joinder_edit_detail(before, client)
            if detail:
                joinder_log(client, "Client edited", detail)
            if not client.primary_client_id:
                remove_dependent_ids = {
                    int(dependent_id)
                    for dependent_id in request.form.getlist("remove_dependent_ids")
                    if dependent_id.isdigit()
                }
                if remove_dependent_ids:
                    dependents_to_remove = JoinderClient.query.filter(
                        JoinderClient.agency_id == current_user.agency_id,
                        JoinderClient.primary_client_id == client.id,
                        JoinderClient.id.in_(remove_dependent_ids),
                    ).all()
                    for dependent in dependents_to_remove:
                        dependent.primary_client_id = None
                        joinder_log(dependent, "Related case updated", f"Removed from related case with {client.full_name}")
                        joinder_log(client, "Dependent removed", f"Removed {dependent.full_name} from this related case")
                try:
                    dependent_count = int(request.form.get("dependent_count") or 0)
                except ValueError:
                    dependent_count = 0
                for index in range(dependent_count):
                    prefix = f"dependent_{index}_"
                    if not request.form.get(f"{prefix}first_name", "").strip() and not request.form.get(f"{prefix}last_name", "").strip():
                        continue
                    dependent = JoinderClient(agency_id=current_user.agency_id, primary_client_id=client.id)
                    populate_joinder_client_from_form(dependent, prefix=prefix)
                    db.session.add(dependent)
                    db.session.flush()
                    joinder_log(dependent, "Client created", f"Created dependent for {client.full_name}")
                    joinder_log(client, "Dependent added", f"Added {dependent.full_name} to this related case")
                    try:
                        save_joinder_document(dependent, request.files.get(f"{prefix}document"), request.form.get(f"{prefix}document_description", "").strip())
                    except ValueError as exc:
                        db.session.rollback()
                        flash(str(exc), "danger")
                        return render_template("joinder_client_form.html", client=client, case_managers=case_managers, statuses=JOINDER_STATUSES, existing_dependents=existing_dependents)
            db.session.commit()
            flash("Joinder client updated.", "success")
            return redirect(url_for("joinder_client_detail", client_id=client.id))
        return render_template("joinder_client_form.html", client=client, case_managers=case_managers, statuses=JOINDER_STATUSES, existing_dependents=existing_dependents)

    @app.route("/agency/joinder/clients/<int:client_id>/delete", methods=["POST"])
    @role_required("agency")
    def joinder_client_delete(client_id):
        if not joinder_access_required():
            return redirect(url_for("agency_dashboard"))
        client = query_joinder_client(client_id)
        stored_paths = [os.path.join(app.config["UPLOAD_FOLDER"], document.stored_filename) for document in client.documents]
        if client.primary_client_id:
            JoinderClient.query.filter_by(primary_client_id=client.id, agency_id=current_user.agency_id).update(
                {"primary_client_id": client.primary_client_id},
                synchronize_session=False,
            )
        else:
            JoinderClient.query.filter_by(primary_client_id=client.id, agency_id=current_user.agency_id).update(
                {"primary_client_id": None},
                synchronize_session=False,
            )
        db.session.delete(client)
        db.session.commit()
        for stored_path in stored_paths:
            if os.path.exists(stored_path):
                try:
                    os.remove(stored_path)
                except OSError:
                    pass
        flash("Joinder client deleted.", "info")
        return redirect(url_for("agency_joinder"))

    @app.route("/agency/joinder/clients/<int:client_id>/documents", methods=["POST"])
    @role_required("agency")
    def joinder_document_upload(client_id):
        if not joinder_access_required():
            return redirect(url_for("agency_dashboard"))
        client = query_joinder_client(client_id)
        try:
            document = save_joinder_document(client, request.files.get("document"), request.form.get("description", "").strip())
        except ValueError as exc:
            flash(str(exc), "danger")
            return redirect(request.form.get("next") or url_for("joinder_client_detail", client_id=client.id))
        if not document:
            flash("Choose a document to upload.", "warning")
        else:
            db.session.commit()
            flash("Document uploaded.", "success")
        return redirect(request.form.get("next") or url_for("joinder_client_detail", client_id=client.id))

    @app.route("/agency/joinder/documents/<int:document_id>/view")
    @role_required("agency")
    def joinder_document_view(document_id):
        if not joinder_access_required():
            return redirect(url_for("agency_dashboard"))
        document = JoinderDocument.query.filter_by(id=document_id, agency_id=current_user.agency_id).first() or abort(404)
        joinder_log(document.client, "Document viewed", document.original_filename)
        db.session.commit()
        authorize_upload_access(document.stored_filename)
        return send_from_directory(app.config["UPLOAD_FOLDER"], document.stored_filename, as_attachment=False)

    @app.route("/agency/joinder/documents/<int:document_id>/download")
    @role_required("agency")
    def joinder_document_download(document_id):
        if not joinder_access_required():
            return redirect(url_for("agency_dashboard"))
        document = JoinderDocument.query.filter_by(id=document_id, agency_id=current_user.agency_id).first() or abort(404)
        joinder_log(document.client, "Document downloaded", document.original_filename)
        db.session.commit()
        authorize_upload_access(document.stored_filename)
        return send_from_directory(
            app.config["UPLOAD_FOLDER"],
            document.stored_filename,
            as_attachment=True,
            download_name=document.original_filename,
        )

    @app.route("/agency/joinder/documents/<int:document_id>/delete", methods=["POST"])
    @role_required("agency")
    def joinder_document_delete(document_id):
        if not joinder_access_required():
            return redirect(url_for("agency_dashboard"))
        document = JoinderDocument.query.filter_by(id=document_id, agency_id=current_user.agency_id).first() or abort(404)
        client = document.client
        stored_path = os.path.join(app.config["UPLOAD_FOLDER"], document.stored_filename)
        joinder_log(client, "Document deleted", document.original_filename)
        db.session.delete(document)
        db.session.commit()
        if os.path.exists(stored_path):
            try:
                os.remove(stored_path)
            except OSError:
                pass
        flash("Document deleted.", "info")
        return redirect(url_for("joinder_client_detail", client_id=client.id))

    @app.route("/agency/joinder/clients/<int:client_id>/notes", methods=["POST"])
    @role_required("agency")
    def joinder_note_create(client_id):
        if not joinder_access_required():
            return redirect(url_for("agency_dashboard"))
        client = query_joinder_client(client_id)
        note_text = request.form.get("note_text", "").strip()
        if note_text:
            db.session.add(
                JoinderNote(
                    agency_id=current_user.agency_id,
                    client_id=client.id,
                    note_text=note_text,
                    created_by=joinder_user_label(),
                )
            )
            joinder_log(client, "Note added", note_text[:180])
            db.session.commit()
            flash("Note added.", "success")
        return redirect(url_for("joinder_client_detail", client_id=client.id))

    @app.route("/clients")
    @role_required("apex", "agency")
    def client_list():
        if not can_manage_client_users_for_current_user():
            abort(403)
        if current_user.role == "apex":
            clients = Client.query.order_by(Client.last_name).all()
        else:
            clients = Client.query.filter_by(agency_id=current_user.agency_id).order_by(Client.last_name).all()
        return render_template("client_list.html", clients=clients, apex_view=current_user.role == "apex")

    @app.route("/clients/new", methods=["GET", "POST"])
    @role_required("apex", "agency")
    def client_create():
        if not can_manage_client_users_for_current_user():
            abort(403)
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
        if not can_manage_client_users_for_current_user():
            abort(403)
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
        if not can_manage_client_users_for_current_user():
            abort(403)
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
        if not can_use_form_filler_for_current_user():
            abort(403)
        if current_user.role == "apex":
            cases = Case.query.order_by(Case.updated_at.desc()).all()
        else:
            cases = Case.query.filter_by(agency_id=current_user.agency_id).order_by(Case.updated_at.desc()).all()
        return render_template("case_list.html", cases=cases)

    @app.route("/cases/new", methods=["GET", "POST"])
    @role_required("apex", "agency")
    def case_create():
        if not can_use_form_filler_for_current_user():
            abort(403)
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
        if not can_use_form_filler_for_current_user():
            abort(403)
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
        if not can_use_form_filler_for_current_user():
            abort(403)
        case = query_case_for_role(case_id)
        questions = CaseQuestion.query.filter_by(case_type=case.case_type).order_by(CaseQuestion.sort_order).all()
        answers = {answer.question_id: answer for answer in case.answers}
        template = FormTemplate.query.filter_by(code=case.case_type, is_active=True).first()
        manual_fields = PdfManualField.query.filter_by(template_id=template.id).order_by(PdfManualField.page_number, PdfManualField.y, PdfManualField.x).all() if template else []
        manual_values = {value.manual_field_id: value for value in CasePdfManualValue.query.filter_by(case_id=case.id).all()}
        pdf_fields = reviewable_pdf_fields(template)
        pdf_field_values = {value.pdf_field_id: value for value in CasePdfFieldValue.query.filter_by(case_id=case.id).all()}
        if request.method == "POST":
            for question in questions:
                if f"question_{question.id}" not in request.form:
                    continue
                answer = answers.get(question.id) or CaseAnswer(case_id=case.id, question_id=question.id)
                answer.answer_text = request.form.get(f"question_{question.id}", "").strip()
                db.session.add(answer)
            save_case_manual_pdf_values(case, manual_fields, manual_values)
            save_case_pdf_field_values(case, pdf_fields, pdf_field_values)
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
            template=template,
            pdf_pages=template_pdf_pages(template) if template else [],
            manual_fields=manual_fields,
            manual_values=manual_values,
            pdf_fields=pdf_fields,
            pdf_field_values=pdf_field_values,
            pdf_field_display_values=review_pdf_field_display_values(case, pdf_fields, pdf_field_values),
            translators=AgencyTranslator.query.filter_by(agency_id=case.agency_id).order_by(AgencyTranslator.full_name).all(),
            preparers=AgencyPreparer.query.filter_by(agency_id=case.agency_id).order_by(AgencyPreparer.full_name).all(),
        )

    @app.route("/cases/<int:case_id>/review/pdf-page/<int:page_number>.png")
    @role_required("apex", "agency")
    def case_review_pdf_page(case_id, page_number):
        if not can_use_form_filler_for_current_user():
            abort(403)
        case = query_case_for_role(case_id)
        template = FormTemplate.query.filter_by(code=case.case_type, is_active=True).first() or abort(404)
        pdf_path = template_pdf_path(template)
        if not pdf_path or not os.path.exists(pdf_path):
            abort(404)
        try:
            import fitz
        except ImportError:
            abort(500)
        document = None
        try:
            document = fitz.open(pdf_path)
            if page_number < 1 or page_number > document.page_count:
                abort(404)
            page = document.load_page(page_number - 1)
            pixmap = page.get_pixmap(matrix=fitz.Matrix(1.6, 1.6), alpha=False)
            data = pixmap.tobytes("png")
        finally:
            if document:
                document.close()
        return send_file(BytesIO(data), mimetype="image/png", download_name=f"{template.code}-review-page-{page_number}.png")

    @app.route("/cases/<int:case_id>/generate", methods=["POST"])
    @role_required("apex", "agency")
    def generate_form(case_id):
        if not can_use_form_filler_for_current_user():
            abort(403)
        case = query_case_for_role(case_id)
        if not can_generate_forms_for_current_user():
            abort(403)
        if not can_use_form_filler(case.agency):
            flash("This feature is not included in your current membership.", "warning")
            return redirect(url_for("case_review", case_id=case.id))
        questions = CaseQuestion.query.filter_by(case_type=case.case_type).order_by(CaseQuestion.sort_order).all()
        answers = {answer.question_id: answer for answer in case.answers}
        template = FormTemplate.query.filter_by(code=case.case_type, is_active=True).first()
        manual_fields = PdfManualField.query.filter_by(template_id=template.id).all() if template else []
        manual_values = {value.manual_field_id: value for value in CasePdfManualValue.query.filter_by(case_id=case.id).all()}
        pdf_fields = reviewable_pdf_fields(template)
        pdf_field_values = {value.pdf_field_id: value for value in CasePdfFieldValue.query.filter_by(case_id=case.id).all()}
        for question in questions:
            form_key = f"question_{question.id}"
            if form_key in request.form:
                answer = answers.get(question.id) or CaseAnswer(case_id=case.id, question_id=question.id)
                answer.answer_text = request.form.get(form_key, "").strip()
                db.session.add(answer)
        save_case_manual_pdf_values(case, manual_fields, manual_values)
        save_case_pdf_field_values(case, pdf_fields, pdf_field_values)
        assign_case_people_from_form(case)
        update_case_progress(case)
        db.session.flush()
        db.session.expire(case, ["answers"])
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
        if not can_use_form_filler_for_current_user():
            abort(403)
        case = query_case_for_role(case_id)
        return render_template("generated_documents.html", case=case)

    @app.route("/client")
    @role_required("client")
    def client_dashboard():
        crm_cases = CrmCase.query.filter_by(client_id=current_user.id).order_by(
            CrmCase.created_at.desc(), CrmCase.id.desc()
        ).all()
        linked_questionnaire_ids = {
            crm_case.form_filler_case_id
            for crm_case in crm_cases
            if crm_case.form_filler_case_id
        }
        linked_questionnaire_ids.update(
            link.form_filler_case_id
            for crm_case in crm_cases
            for link in crm_case.questionnaire_links
            if link.form_filler_case_id
        )
        standalone_questionnaires = [
            case for case in current_user.cases if case.id not in linked_questionnaire_ids
        ]
        return render_template(
            "client_dashboard.html",
            client=current_user,
            crm_cases=crm_cases,
            standalone_questionnaires=standalone_questionnaires,
        )

    @app.route("/client/crm-cases/<int:case_id>")
    @role_required("client")
    def client_crm_case_detail(case_id):
        case = CrmCase.query.filter_by(id=case_id, client_id=current_user.id).first() or abort(404)
        ensure_crm_case_status_history(case)
        db.session.commit()
        documents = CrmClientDocument.query.filter_by(
            client_id=current_user.id,
            case_id=case.id,
            agency_id=case.agency_id,
        ).order_by(CrmClientDocument.uploaded_at.desc()).all()
        return render_template(
            "client_crm_case_detail.html",
            case=case,
            documents=documents,
            linked_questionnaires=linked_form_filler_cases_for_crm_case(case),
        )

    @app.route("/client/crm-cases/<int:case_id>/documents", methods=["POST"])
    @role_required("client")
    def client_crm_document_upload(case_id):
        case = CrmCase.query.filter_by(id=case_id, client_id=current_user.id).first() or abort(404)
        file_storage = request.files.get("document")
        try:
            validate_client_document_upload(file_storage)
            original, stored = save_upload(file_storage, f"crm/clients/{current_user.id}")
        except ValueError as exc:
            flash(str(exc), "danger")
            return redirect(url_for("client_crm_case_detail", case_id=case.id))
        db.session.add(
            CrmClientDocument(
                agency_id=case.agency_id,
                client_id=current_user.id,
                case_id=case.id,
                original_filename=original,
                stored_filename=stored,
                document_type="Client upload",
                description=request.form.get("description", "").strip(),
            )
        )
        db.session.commit()
        flash("Document uploaded.", "success")
        return redirect(url_for("client_crm_case_detail", case_id=case.id))

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
    if parts[0] == "crm" and len(parts) >= 3 and parts[1] == "clients":
        try:
            client_id = int(parts[2])
        except ValueError:
            abort(403)
        client = db.session.get(Client, client_id) or abort(403)
        if current_user.role == "apex":
            return
        if current_user.role == "agency" and client.agency_id == current_user.agency_id:
            return
        if current_user.role == "client" and client.id == current_user.id:
            return
        abort(403)
    if parts[0] == "knowledge_base":
        if current_user.role == "apex":
            return
        if current_user.role == "agency":
            topic = KnowledgeBaseTopic.query.filter_by(pdf_stored_filename=filename, is_active=True).first()
            if topic and topic.module.is_active:
                return
        abort(403)
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


def generate_crm_invoice_pdf(invoice):
    buffer = BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=letter)
    width, height = letter
    y = height - 55
    pdf.setFont("Helvetica-Bold", 16)
    pdf.drawString(50, y, f"Invoice {invoice.invoice_number}")
    pdf.setFont("Helvetica", 10)
    if invoice.issue_date:
        pdf.drawRightString(width - 50, y, invoice.issue_date.strftime("%m/%d/%Y"))
    y -= 32
    pdf.setFont("Helvetica-Bold", 11)
    pdf.drawString(50, y, invoice.agency.agency_name)
    y -= 14
    pdf.setFont("Helvetica", 10)
    for line in [invoice.agency.display_address, invoice.agency.agency_phone or invoice.agency.ceo_phone, invoice.agency.agency_email or invoice.agency.ceo_email]:
        if line:
            pdf.drawString(50, y, line[:95])
            y -= 13
    y -= 12
    pdf.setFont("Helvetica-Bold", 11)
    pdf.drawString(50, y, "Bill To")
    y -= 14
    pdf.setFont("Helvetica", 10)
    for line in [invoice.client.full_name, invoice.client.display_address, invoice.client.phone, invoice.client.email]:
        if line:
            pdf.drawString(50, y, line[:95])
            y -= 13
    y -= 10
    pdf.setFont("Helvetica-Bold", 11)
    pdf.drawString(50, y, "Case")
    y -= 16
    pdf.setFont("Helvetica", 10)
    pdf.drawString(50, y, invoice.case.title[:70])
    pdf.drawRightString(width - 50, y, f"Service price: ${float(invoice.subtotal or 0):,.2f}")
    y -= 28
    pdf.setFont("Helvetica-Bold", 11)
    pdf.drawString(50, y, "Activity")
    y -= 16
    pdf.setFont("Helvetica-Bold", 9)
    pdf.drawString(50, y, "Date")
    pdf.drawString(125, y, "Type")
    pdf.drawString(225, y, "Description")
    pdf.drawRightString(width - 50, y, "Amount")
    y -= 12
    pdf.line(50, y, width - 50, y)
    y -= 14
    pdf.setFont("Helvetica", 9)
    for activity in sorted(invoice.activities, key=lambda row: (row.activity_date, row.created_at), reverse=True):
        if y < 90:
            pdf.showPage()
            y = height - 55
            pdf.setFont("Helvetica", 9)
        pdf.drawString(50, y, activity.activity_date.strftime("%m/%d/%Y") if activity.activity_date else "")
        pdf.drawString(125, y, activity.activity_type)
        pdf.drawString(225, y, (activity.description or "")[:46])
        pdf.drawRightString(width - 50, y, f"${float(activity.amount or 0):,.2f}")
        y -= 14
    y -= 12
    pdf.setFont("Helvetica-Bold", 10)
    for label, amount in [
        ("Subtotal", invoice.subtotal or 0),
        ("Discounts", invoice.discount or 0),
        ("Total", invoice.total or 0),
        ("Paid minus refunds", invoice.paid_amount or 0),
        ("Balance due", invoice.balance_due),
    ]:
        pdf.drawRightString(width - 140, y, label)
        pdf.drawRightString(width - 50, y, f"${float(amount):,.2f}")
        y -= 15
    pdf.save()
    buffer.seek(0)
    return Response(
        buffer.getvalue(),
        mimetype="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={invoice.invoice_number}.pdf"},
    )


def generate_crm_report_pdf(report_data):
    buffer = BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=letter)
    width, height = letter
    left = 50
    y = height - 52
    filters = report_data["filters"]
    summary = report_data["summary"]
    report_label = "Invoices Report" if filters["report_type"] == "invoices" else "Cases Report"

    pdf.setFont("Helvetica-Bold", 16)
    pdf.drawString(left, y, f"CRM {report_label}")
    pdf.setFont("Helvetica", 9)
    pdf.drawRightString(width - 50, y, datetime.utcnow().strftime("%m/%d/%Y"))
    y -= 24
    pdf.setFont("Helvetica", 10)
    for line in split_pdf_lines(report_data["report_answer"], 95):
        pdf.drawString(left, y, line)
        y -= 13
    y -= 8

    pdf.setFont("Helvetica-Bold", 10)
    if filters["report_type"] == "invoices":
        summary_lines = [
            f"Matching invoices: {summary['invoice_count']}",
            f"Total value: ${float(summary['total_billed'] or 0):,.2f}",
            f"Open balance: ${float(summary['open_balance'] or 0):,.2f}",
            f"Paid: ${float(summary['total_paid'] or 0):,.2f}",
        ]
    else:
        summary_lines = [
            f"Matching cases: {summary['case_count']}",
            f"Total value: ${float(summary['total_case_value'] or 0):,.2f}",
        ]
    for line in summary_lines:
        pdf.drawString(left, y, line)
        y -= 14
    y -= 10

    if filters["report_type"] == "invoices":
        rows = [
            (
                invoice.invoice_number,
                invoice.client.full_name,
                invoice.case.title,
                invoice.status,
                f"${float(invoice.total or 0):,.2f}",
                f"${float(invoice.balance_due or 0):,.2f}",
            )
            for invoice in report_data["invoices"]
        ]
        headers = ("Invoice", "Client", "Case", "Status", "Total", "Balance")
        widths = (70, 110, 150, 65, 65, 65)
    else:
        rows = [
            (
                case.title,
                case.client.full_name,
                case.status,
                case.tag.name if case.tag else "No tag",
                case.case_manager.full_name if case.case_manager else "Not assigned",
                f"${float(case.price or 0):,.2f}",
            )
            for case in report_data["cases"]
        ]
        headers = ("Case", "Client", "Status", "Tag", "Manager", "Value")
        widths = (155, 115, 75, 75, 80, 55)

    def draw_header(current_y):
        pdf.setFont("Helvetica-Bold", 8)
        x = left
        for header, col_width in zip(headers, widths):
            pdf.drawString(x, current_y, header)
            x += col_width
        current_y -= 8
        pdf.line(left, current_y, width - 50, current_y)
        return current_y - 12

    y = draw_header(y)
    pdf.setFont("Helvetica", 8)
    for row in rows:
        if y < 70:
            pdf.showPage()
            y = height - 52
            y = draw_header(y)
            pdf.setFont("Helvetica", 8)
        x = left
        for value, col_width in zip(row, widths):
            pdf.drawString(x, y, str(value or "")[: max(8, int(col_width / 5))])
            x += col_width
        y -= 14
    if not rows:
        pdf.drawString(left, y, "No results matched those criteria.")

    pdf.save()
    buffer.seek(0)
    filename = f"crm-{filters['report_type']}-report.pdf"
    return Response(
        buffer.getvalue(),
        mimetype="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


def generate_joinder_search_pdf(clients, args, show_admin_commissions=False):
    pdf_clients = sorted(
        clients,
        key=lambda client: (
            client.case_manager.full_name.lower() if client.case_manager else "zzzzzz not assigned",
            client.last_name.lower(),
            client.first_name.lower(),
            client.alien_number or "",
        ),
    )
    buffer = BytesIO()
    page_size = landscape(letter) if show_admin_commissions else letter
    pdf = canvas.Canvas(buffer, pagesize=page_size)
    width, height = page_size
    left = 45
    y = height - 45
    pdf.setFont("Helvetica-Bold", 15)
    pdf.drawString(left, y, "Joinder Client Search Results")
    pdf.setFont("Helvetica", 9)
    pdf.drawRightString(width - left, y, datetime.utcnow().strftime("%m/%d/%Y"))
    y -= 20
    filters = []
    if args.get("q"):
        filters.append(f"Search: {args.get('q')}")
    if args.get("created_from") or args.get("created_to"):
        filters.append(f"Created: {args.get('created_from') or 'any'} to {args.get('created_to') or 'any'}")
    if args.get("status") in JOINDER_STATUSES:
        filters.append(f"Status: {args.get('status')}")
    if args.get("case_manager_id", "").isdigit():
        manager = db.session.get(AgencyCaseManager, int(args.get("case_manager_id")))
        if manager:
            filters.append(f"Case manager: {manager.full_name}")
    pdf.drawString(left, y, (" | ".join(filters) if filters else "All matching Joinder clients")[:120])
    y -= 22
    summary = joinder_search_summary(clients)
    pdf.setFont("Helvetica-Bold", 10)
    pdf.drawString(left, y, f"Total clients: {summary['total_clients']}")
    if show_admin_commissions:
        pdf.drawString(left + 110, y, f"Contract value: ${float(summary['total_contract_value']):,.2f}")
        pdf.drawString(left + 275, y, f"Agency commission: ${float(summary['total_agency_commission']):,.2f}")
        pdf.drawRightString(width - left, y, f"Case manager commission: ${float(summary['total_manager_commission']):,.2f}")
    else:
        pdf.drawRightString(width - left, y, f"Total contract value: ${float(summary['total_contract_value']):,.2f}")
    y -= 22
    if show_admin_commissions:
        headers = ["#", "Created", "Name", "Alien #", "Status", "Manager", "Contract", "Agency Comm.", "CM Comm."]
        col_x = [left, 70, 120, 238, 308, 365, 490, 558, 638]
    else:
        headers = ["#", "Created", "Name", "Alien #", "Status", "Manager", "Contract"]
        col_x = [left, 68, 125, 240, 320, 380, 505]
    pdf.setFont("Helvetica-Bold", 8)
    for index, header in enumerate(headers):
        pdf.drawString(col_x[index], y, header)
    y -= 8
    pdf.line(left, y, width - left, y)
    y -= 13
    pdf.setFont("Helvetica", 8)
    for line_number, client in enumerate(pdf_clients, start=1):
        if y < 60:
            pdf.showPage()
            y = height - 45
            pdf.setFont("Helvetica", 8)
        commissions = joinder_commissions_for_value(client.contract_value)
        row = [
            str(line_number),
            client.created_at.strftime("%m/%d/%Y") if client.created_at else "",
            client.full_name[:22] if show_admin_commissions else client.full_name[:24],
            client.alien_number or "",
            client.status or "",
            (client.case_manager.full_name[:18] if client.case_manager and show_admin_commissions else client.case_manager.full_name[:20] if client.case_manager else "Not assigned"),
            f"${float(client.contract_value or 0):,.2f}",
        ]
        if show_admin_commissions:
            row.extend(
                [
                    f"${float(commissions['agency_commission']):,.2f}",
                    f"${float(commissions['manager_commission']):,.2f}",
                ]
            )
        for index, value in enumerate(row):
            pdf.drawString(col_x[index], y, str(value))
        y -= 14
    if not clients:
        pdf.drawString(left, y, "No clients matched those criteria.")
    pdf.save()
    buffer.seek(0)
    return Response(
        buffer.getvalue(),
        mimetype="application/pdf",
        headers={"Content-Disposition": "attachment; filename=joinder-search-results.pdf"},
    )


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
    visual = fill_pdf_with_visual_mappings(case, template)
    if visual:
        return visual
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


def is_affirmative_pdf_value(value):
    return str(value or "").strip().lower() in {"yes", "y", "true", "1", "x", "checked"}


def fill_pdf_with_visual_mappings(case, template):
    try:
        import fitz
    except ImportError:
        return None
    source_path = template_pdf_path(template)
    if not source_path or not os.path.exists(source_path):
        return None
    answers = {answer.question_id: answer.answer_text or "" for answer in case.answers}
    questions = CaseQuestion.query.filter_by(case_type=case.case_type).order_by(CaseQuestion.sort_order).all()
    mapped_questions = [question for question in questions if question_visual_mappings(question)]
    manual_fields = PdfManualField.query.filter_by(template_id=template.id).all()
    manual_values = {value.manual_field_id: value.value_text or "" for value in CasePdfManualValue.query.filter_by(case_id=case.id).all()}
    pdf_fields = reviewable_pdf_fields(template)
    pdf_field_values = {value.pdf_field_id: value.value_text or "" for value in CasePdfFieldValue.query.filter_by(case_id=case.id).all()}
    mapped_pdf_fields = [
        field
        for field in PdfField.query.filter(
            PdfField.template_id == template.id,
            PdfField.mapped_question_id.isnot(None),
        ).order_by(PdfField.page_number, PdfField.id).all()
        if pdf_field_visual_mapping(field) and field.mapped_question and not question_visual_mappings(field.mapped_question)
    ]
    if not mapped_questions and not manual_fields and not pdf_fields and not mapped_pdf_fields:
        return None
    folder = f"cases/{case.id}/generated"
    os.makedirs(os.path.join(app.config["UPLOAD_FOLDER"], folder), exist_ok=True)
    filename = f"{folder}/{case.case_type.lower()}_visual_filled_{uuid.uuid4().hex[:8]}.pdf"
    output_path = os.path.join(app.config["UPLOAD_FOLDER"], filename)
    document = None
    try:
        document = fitz.open(source_path)
        placed_count = 0
        placed_rects = set()
        for question in mapped_questions:
            answer_text = (answers.get(question.id) or "").strip()
            if not answer_text:
                continue
            if question.input_type == "checkbox" and not is_affirmative_pdf_value(answer_text):
                continue
            for mapping in question_visual_mappings(question):
                page_index = (mapping["page"] or 1) - 1
                if page_index < 0 or page_index >= document.page_count:
                    continue
                page = document.load_page(page_index)
                x = float(mapping["x"] or 0)
                y = float(mapping["y"] or 0)
                width = float(mapping["width"] or 120)
                height = float(mapping["height"] or 16)
                rect = fitz.Rect(x, y, x + width, y + height)
                placement_key = pdf_overlay_rect_key(page_index, rect)
                if placement_key in placed_rects:
                    continue
                placed_rects.add(placement_key)
                if question.input_type == "checkbox":
                    overlay_checkbox_mark_on_rect(page, rect)
                elif question.render_mode == "split_boxes":
                    overlay_split_box_text_on_rect(page, rect, answer_text, question.render_box_count)
                else:
                    overlay_text_on_rect(page, rect, answer_text)
                placed_count += 1
        for field in manual_fields:
            value_text = (manual_values.get(field.id) or "").strip()
            if not value_text:
                continue
            page_index = (field.page_number or 1) - 1
            if page_index < 0 or page_index >= document.page_count:
                continue
            page = document.load_page(page_index)
            rect = fitz.Rect(
                float(field.x or 0),
                float(field.y or 0),
                float(field.x or 0) + float(field.width or 120),
                float(field.y or 0) + float(field.height or 16),
            )
            placement_key = pdf_overlay_rect_key(page_index, rect)
            if placement_key in placed_rects:
                continue
            placed_rects.add(placement_key)
            if field.render_mode == "split_boxes":
                overlay_split_box_text_on_rect(page, rect, value_text, field.render_box_count)
            else:
                overlay_text_on_rect(page, rect, value_text)
            placed_count += 1
        for field in pdf_fields:
            value_text = (pdf_field_values.get(field.id) or "").strip()
            if not value_text:
                continue
            mapping = pdf_field_visual_mapping(field)
            if not mapping:
                continue
            page_index = (mapping["page"] or 1) - 1
            if page_index < 0 or page_index >= document.page_count:
                continue
            page = document.load_page(page_index)
            rect = fitz.Rect(
                mapping["x"],
                mapping["y"],
                mapping["x"] + mapping["width"],
                mapping["y"] + mapping["height"],
            )
            placement_key = pdf_overlay_rect_key(page_index, rect)
            if placement_key in placed_rects:
                continue
            placed_rects.add(placement_key)
            if is_pdf_checkbox_field(field):
                if is_affirmative_pdf_value(value_text):
                    overlay_checkbox_mark_on_rect(page, rect)
                    placed_count += 1
            else:
                overlay_text_on_rect(page, rect, value_text)
                placed_count += 1
        for field in mapped_pdf_fields:
            question = field.mapped_question
            if not question:
                continue
            value_text = (answers.get(question.id) or "").strip()
            if not value_text:
                continue
            mapping = pdf_field_visual_mapping(field)
            if not mapping:
                continue
            page_index = (mapping["page"] or 1) - 1
            if page_index < 0 or page_index >= document.page_count:
                continue
            page = document.load_page(page_index)
            rect = fitz.Rect(
                mapping["x"],
                mapping["y"],
                mapping["x"] + mapping["width"],
                mapping["y"] + mapping["height"],
            )
            placement_key = pdf_overlay_rect_key(page_index, rect)
            if placement_key in placed_rects:
                continue
            placed_rects.add(placement_key)
            if is_pdf_checkbox_field(field) or question.input_type == "checkbox":
                if is_affirmative_pdf_value(value_text):
                    overlay_checkbox_mark_on_rect(page, rect)
                    placed_count += 1
            elif question.render_mode == "split_boxes":
                overlay_split_box_text_on_rect(page, rect, value_text, question.render_box_count)
                placed_count += 1
            else:
                overlay_text_on_rect(page, rect, value_text)
                placed_count += 1
        if not placed_count:
            document.close()
            return None
        document.save(output_path, garbage=4, deflate=True, clean=True)
        document.close()
        return filename
    except Exception:
        if document:
            document.close()
        return None


def overlay_text_on_rect(page, rect, value, font_size=9, align=0):
    text = str(value or "")
    if not text:
        return False
    safe_rect = rect.__class__(
        rect.x0 + 1,
        rect.y0 + 0.5,
        max(rect.x0 + 2, rect.x1 - 1),
        max(rect.y0 + 2, rect.y1 - 0.5),
    )
    size = min(font_size, max(5, rect.height * 0.72))
    minimum_size = 4.5
    while size >= minimum_size:
        try:
            written = page.insert_textbox(safe_rect, text, fontsize=size, fontname="helv", color=(0, 0, 0), align=align)
            if written >= 0:
                return True
        except Exception:
            pass
        size -= 0.5
    try:
        size = max(minimum_size, min(font_size, max(5, rect.height * 0.72)))
        baseline_y = rect.y0 + min(max(size + 1, rect.height * 0.72), max(size + 1, rect.height - 1))
        page.insert_text((rect.x0 + 1, baseline_y), text[:140], fontsize=size, fontname="helv", color=(0, 0, 0))
        return True
    except Exception:
        return False


def pdf_overlay_rect_key(page_index, rect):
    return (
        page_index,
        round(float(rect.x0), 1),
        round(float(rect.y0), 1),
        round(float(rect.x1), 1),
        round(float(rect.y1), 1),
    )


def overlay_checkbox_mark_on_rect(page, rect):
    size = min(11, max(6, rect.height * 0.95))
    if overlay_text_on_rect(page, rect, "X", font_size=size, align=1):
        return True
    try:
        page.draw_line((rect.x0 + 1, rect.y0 + 1), (rect.x1 - 1, rect.y1 - 1), color=(0, 0, 0), width=0.8)
        page.draw_line((rect.x0 + 1, rect.y1 - 1), (rect.x1 - 1, rect.y0 + 1), color=(0, 0, 0), width=0.8)
        return True
    except Exception:
        return False


def overlay_split_box_text_on_rect(page, rect, value, box_count=0):
    try:
        import fitz
    except ImportError:
        return
    text = str(value or "")
    if not text:
        return
    count = int(box_count or 0) or len(text)
    count = max(1, count)
    cell_width = rect.width / count
    font_size = min(9, max(5, rect.height * 0.72))
    baseline_y = rect.y0 + min(max(font_size + 1, rect.height * 0.75), max(font_size + 1, rect.height - 1))
    for index, character in enumerate(text[:count]):
        x = rect.x0 + (cell_width * index) + (cell_width * 0.35)
        page.insert_text(fitz.Point(x, baseline_y), character, fontsize=font_size, fontname="helv", color=(0, 0, 0))


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


class PdfFieldWidgetProxy:
    def __init__(self, field):
        self.field_name = field.field_name
        self.field_label = readable_pdf_field_name(field.field_name)

    def button_states(self):
        return {"normal": ["Yes"]}


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


def split_pdf_lines_by_width(pdf, text, max_width, font_name="Times-Roman", font_size=11):
    text = text or "(No answer)"
    wrapped = []
    for paragraph in text.splitlines() or [text]:
        words = paragraph.split()
        if not words:
            wrapped.append("")
            continue
        line = ""
        for word in words:
            candidate = f"{line} {word}".strip()
            if line and pdf.stringWidth(candidate, font_name, font_size) > max_width:
                wrapped.append(line)
                line = word
            else:
                line = candidate
        wrapped.append(line)
    return wrapped


def draw_pdf_lines(pdf, lines, x, y, max_chars=90, font_name="Times-Roman", font_size=11, leading=14, bottom_margin=inch, right_margin=inch):
    width, height = letter
    pdf.setFont(font_name, font_size)
    max_width = width - x - right_margin
    for paragraph in lines:
        if not paragraph:
            y -= leading
            continue
        for line in split_pdf_lines_by_width(pdf, paragraph, max_width, font_name, font_size):
            if y < bottom_margin:
                pdf.showPage()
                y = height - inch
                pdf.setFont(font_name, font_size)
            pdf.drawString(x, y, line)
            y -= leading
    return y


def principal_respondent(motion):
    return motion.respondents[0] if motion.respondents else None


def signature_lines(motion):
    if motion.lawyer_name:
        lines = [
            "Respectfully submitted,",
            "",
            "________________________________________",
            motion.lawyer_name,
        ]
        if motion.lawyer_bar_number:
            lines.append(f"Bar No.: {motion.lawyer_bar_number}")
        if motion.law_firm_name:
            lines.append(motion.law_firm_name)
        if motion.law_firm_address:
            lines.extend(motion.law_firm_address.splitlines())
        if motion.law_firm_phone:
            lines.append(motion.law_firm_phone)
        return lines
    lead = principal_respondent(motion)
    respondent_name = lead.full_name if lead else "Respondent"
    alien_number = f"A# {lead.alien_number}" if lead and lead.alien_number else ""
    return [
        "Respectfully submitted,",
        "",
        "________________________________________",
        respondent_name,
        alien_number,
        "Respondent, Pro Se",
    ]


def signer_name_lines(motion):
    if motion.lawyer_name:
        lines = [motion.lawyer_name]
        if motion.law_firm_name:
            lines.append(motion.law_firm_name)
        return lines
    lead = principal_respondent(motion)
    if lead:
        return [lead.full_name, f"A# {lead.alien_number}"]
    return ["Respondent"]


def draw_motion_page_intro(pdf, motion, title, y):
    y = draw_motion_header(pdf, motion, y)
    y = draw_motion_caption(pdf, motion, y)
    y = draw_centered_pdf_line(pdf, title, y, "Times-Bold", 12)
    return y - 8


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
    body_max_chars = 88
    y = height - inch
    pdf.setTitle(motion.title)

    y = draw_motion_page_intro(pdf, motion, motion.title, y)
    y = draw_pdf_lines(pdf, motion_body_text(motion).splitlines(), left, y, max_chars=body_max_chars, font_size=11, leading=15)
    y -= 20
    if y < 2.1 * inch:
        pdf.showPage()
        y = height - inch
    y = draw_pdf_lines(pdf, signature_lines(motion), left, y, max_chars=body_max_chars, font_size=11, leading=15)

    pdf.showPage()
    y = height - inch
    y = draw_motion_page_intro(pdf, motion, "EXHIBITS", y)
    exhibits = motion_exhibits(motion)
    exhibit_lines = [f"Exhibit {exhibit_label(index)}: {description}" for index, description in enumerate(exhibits)] or ["No exhibits listed."]
    draw_pdf_lines(pdf, exhibit_lines, left, y, max_chars=body_max_chars, font_size=11, leading=16)

    pdf.showPage()
    y = height - inch
    y = draw_centered_pdf_line(pdf, "CERTIFICATE OF SERVICE", y, "Times-Bold", 12)
    y -= 18
    service_lines = [
        f"I certify that on {datetime.utcnow().strftime('%B %d, %Y')}, a true and correct copy of the foregoing motion was served on:",
        "",
        motion.opla_office,
        *[line for line in (motion.opla_address or "").splitlines() if line.strip()],
        "",
        "Delivery method:",
        "[x] by regular mail",
        "",
        "I declare under penalty of perjury that the foregoing is true and correct.",
        "",
        "________________________________________",
        *signer_name_lines(motion),
    ]
    y = draw_pdf_lines(pdf, service_lines, left, y, max_chars=body_max_chars, font_size=11, leading=15)

    pdf.showPage()
    y = height - inch
    y = draw_motion_page_intro(pdf, motion, "PROPOSED ORDER", y)
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
    draw_pdf_lines(pdf, order_lines, left, y, max_chars=body_max_chars, font_size=11, leading=17)
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
    migrate_crm_preparers_to_unified_preparers()
    for name in SUBSCRIPTION_TOOLS:
        if not SubscriptionTool.query.filter_by(name=name).first():
            db.session.add(SubscriptionTool(name=name))
    if os.environ.get("SEED_SAMPLE_FORMS") == "1" and FormTemplate.query.count() == 0:
        seed_sample_form_templates()
    seed_default_knowledge_base()
    if not ApexUser.query.filter_by(username="apexadmin").first():
        apex = ApexUser(username="apexadmin")
        apex.set_password("ChangeMe123!")
        db.session.add(apex)
    db.session.commit()


def seed_default_knowledge_base():
    module = KnowledgeBaseModule.query.filter_by(name="CRM").first()
    if not module:
        module = KnowledgeBaseModule(name="CRM", description="Guias de uso para la subscripcion CRM.", sort_order=1, is_active=True)
        db.session.add(module)
        db.session.flush()
    for index, title in enumerate(CRM_KNOWLEDGE_TOPICS, start=1):
        if not KnowledgeBaseTopic.query.filter_by(module_id=module.id, title=title).first():
            db.session.add(
                KnowledgeBaseTopic(
                    module_id=module.id,
                    title=title,
                    description="Documento guia pendiente de cargar en PDF.",
                    sort_order=index,
                    is_active=True,
                )
            )


def migrate_crm_preparers_to_unified_preparers():
    db.session.execute(text("CREATE TABLE IF NOT EXISTS schema_migration (name VARCHAR(120) PRIMARY KEY)"))
    already_done = db.session.execute(
        text("SELECT name FROM schema_migration WHERE name = 'crm_preparers_unified'")
    ).first()
    if already_done:
        return

    mapping = {}
    old_preparers = AgencyCrmPreparer.query.order_by(AgencyCrmPreparer.id).all()
    for old_preparer in old_preparers:
        unified = AgencyPreparer.query.filter_by(
            agency_id=old_preparer.agency_id,
            full_name=old_preparer.full_name,
            email=old_preparer.email,
            phone=old_preparer.phone,
            address=old_preparer.address,
        ).first()
        if not unified:
            unified = AgencyPreparer(
                agency_id=old_preparer.agency_id,
                full_name=old_preparer.full_name,
                title="",
                phone=old_preparer.phone,
                email=old_preparer.email,
                address=old_preparer.address,
            )
            db.session.add(unified)
            db.session.flush()
        mapping[old_preparer.id] = unified.id

    for old_id, unified_id in mapping.items():
        CrmCase.query.filter_by(form_preparer_id=old_id).update({"form_preparer_id": -unified_id})
    for unified_id in set(mapping.values()):
        CrmCase.query.filter_by(form_preparer_id=-unified_id).update({"form_preparer_id": unified_id})

    db.session.execute(text("INSERT INTO schema_migration (name) VALUES ('crm_preparers_unified')"))
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
                    prompt_es=prompt,
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
                    prompt_es=prompt,
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
            "prompt_es": "TEXT",
            "prompt_en": "TEXT",
            "prompt_ht": "TEXT",
            "pdf_page_number": "INTEGER",
            "pdf_x": "NUMERIC(10, 2)",
            "pdf_y": "NUMERIC(10, 2)",
            "pdf_width": "NUMERIC(10, 2)",
            "pdf_height": "NUMERIC(10, 2)",
        }
        for column, ddl in question_additions.items():
            if column not in existing_question:
                db.session.execute(text(f"ALTER TABLE case_question ADD COLUMN {column} {ddl}"))
    if "case_question" in inspector.get_table_names() and "pdf_question_placement" not in inspector.get_table_names():
        db.session.execute(
            text(
                "CREATE TABLE pdf_question_placement ("
                "id INTEGER NOT NULL PRIMARY KEY, "
                "question_id INTEGER NOT NULL, "
                "page_number INTEGER NOT NULL, "
                "x NUMERIC(10, 2) NOT NULL, "
                "y NUMERIC(10, 2) NOT NULL, "
                "width NUMERIC(10, 2) NOT NULL, "
                "height NUMERIC(10, 2) NOT NULL, "
                "created_at DATETIME NOT NULL, "
                "FOREIGN KEY(question_id) REFERENCES case_question (id)"
                ")"
            )
        )
    if "case" in inspector.get_table_names() and "pdf_field" in inspector.get_table_names() and "case_pdf_field_value" not in inspector.get_table_names():
        db.session.execute(
            text(
                "CREATE TABLE case_pdf_field_value ("
                "id INTEGER NOT NULL PRIMARY KEY, "
                "case_id INTEGER NOT NULL, "
                "pdf_field_id INTEGER NOT NULL, "
                "value_text TEXT, "
                "updated_at DATETIME NOT NULL, "
                "FOREIGN KEY(case_id) REFERENCES 'case' (id), "
                "FOREIGN KEY(pdf_field_id) REFERENCES pdf_field (id), "
                "CONSTRAINT uq_case_pdf_field_value UNIQUE (case_id, pdf_field_id)"
                ")"
            )
        )
    if "client" in inspector.get_table_names() and "crm_client_note" not in inspector.get_table_names():
        db.session.execute(
            text(
                "CREATE TABLE crm_client_note ("
                "id INTEGER NOT NULL PRIMARY KEY, "
                "agency_id INTEGER NOT NULL, "
                "client_id INTEGER NOT NULL, "
                "note_text TEXT NOT NULL, "
                "created_at DATETIME NOT NULL, "
                "FOREIGN KEY(agency_id) REFERENCES agency (id), "
                "FOREIGN KEY(client_id) REFERENCES client (id)"
                ")"
            )
        )
    if "agency" in inspector.get_table_names() and "agency_crm_case_type" not in inspector.get_table_names():
        db.session.execute(
            text(
                "CREATE TABLE agency_crm_case_type ("
                "id INTEGER NOT NULL PRIMARY KEY, "
                "agency_id INTEGER NOT NULL, "
                "name VARCHAR(140) NOT NULL, "
                "purpose VARCHAR(220), "
                "created_at DATETIME NOT NULL, "
                "FOREIGN KEY(agency_id) REFERENCES agency (id), "
                "CONSTRAINT uq_agency_crm_case_type_name UNIQUE (agency_id, name)"
                ")"
            )
        )
    if "crm_case" in inspector.get_table_names() and "case" in inspector.get_table_names() and "crm_case_questionnaire" not in inspector.get_table_names():
        db.session.execute(
            text(
                "CREATE TABLE crm_case_questionnaire ("
                "id INTEGER NOT NULL PRIMARY KEY, "
                "agency_id INTEGER NOT NULL, "
                "crm_case_id INTEGER NOT NULL, "
                "form_filler_case_id INTEGER NOT NULL, "
                "created_at DATETIME NOT NULL, "
                "FOREIGN KEY(agency_id) REFERENCES agency (id), "
                "FOREIGN KEY(crm_case_id) REFERENCES crm_case (id), "
                "FOREIGN KEY(form_filler_case_id) REFERENCES 'case' (id), "
                "CONSTRAINT uq_crm_case_questionnaire UNIQUE (crm_case_id, form_filler_case_id)"
                ")"
            )
        )
        db.session.execute(
            text(
                "INSERT OR IGNORE INTO crm_case_questionnaire "
                "(agency_id, crm_case_id, form_filler_case_id, created_at) "
                "SELECT agency_id, id, form_filler_case_id, CURRENT_TIMESTAMP "
                "FROM crm_case WHERE form_filler_case_id IS NOT NULL"
            )
        )
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
            "motion_title": "VARCHAR(220) DEFAULT 'MOTION' NOT NULL",
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
            "motion_title": "VARCHAR(220)",
            "exhibits_text": "TEXT",
            "detention_status": "VARCHAR(30)",
            "next_hearing_date": "VARCHAR(20)",
            "next_hearing_type": "VARCHAR(30)",
        }
        for column, ddl in motion_additions.items():
            if column not in existing_motion:
                db.session.execute(text(f"ALTER TABLE motion_draft ADD COLUMN {column} {ddl}"))
    if "crm_case" in inspector.get_table_names():
        existing_crm_case = {column["name"] for column in inspector.get_columns("crm_case")}
        crm_case_additions = {
            "price": "NUMERIC(10, 2) DEFAULT 0 NOT NULL",
            "case_manager_id": "INTEGER",
            "form_preparer_id": "INTEGER",
            "tag_id": "INTEGER",
            "form_filler_case_id": "INTEGER",
        }
        for column, ddl in crm_case_additions.items():
            if column not in existing_crm_case:
                db.session.execute(text(f"ALTER TABLE crm_case ADD COLUMN {column} {ddl}"))
    for table_name in ("agency_preparer", "agency_case_manager"):
        if table_name in inspector.get_table_names():
            existing_staff = {column["name"] for column in inspector.get_columns(table_name)}
            staff_additions = {
                "username": "VARCHAR(80)",
                "password_hash": "VARCHAR(255)",
            }
            for column, ddl in staff_additions.items():
                if column not in existing_staff:
                    db.session.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {column} {ddl}"))
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
