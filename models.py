from datetime import date, datetime, timedelta

from flask_login import UserMixin
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import check_password_hash, generate_password_hash


db = SQLAlchemy()


agency_subscriptions = db.Table(
    "agency_subscriptions",
    db.Column("agency_id", db.Integer, db.ForeignKey("agency.id"), primary_key=True),
    db.Column("tool_id", db.Integer, db.ForeignKey("subscription_tool.id"), primary_key=True),
)


class PasswordMixin:
    password_hash = db.Column(db.String(255), nullable=False)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)


class ApexUser(UserMixin, PasswordMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    @property
    def role(self):
        return "apex"

    def get_id(self):
        return f"apex:{self.id}"


class SubscriptionTool(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), unique=True, nullable=False)


class FormTemplate(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(40), unique=True, nullable=False)
    name = db.Column(db.String(180), nullable=False)
    description = db.Column(db.Text)
    subscription_tool = db.Column(db.String(80), default="Form Filler", nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    pdf_original_filename = db.Column(db.String(255))
    pdf_stored_filename = db.Column(db.String(255))
    pdf_kind = db.Column(db.String(40), default="not_uploaded", nullable=False)
    pdf_field_count = db.Column(db.Integer, default=0, nullable=False)
    pdf_generation_strategy = db.Column(db.String(80), default="summary_pdf", nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    @property
    def label(self):
        return f"{self.code} - {self.name}"


class PdfField(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    template_id = db.Column(db.Integer, db.ForeignKey("form_template.id"), nullable=False)
    field_name = db.Column(db.String(255), nullable=False)
    field_type = db.Column(db.String(80))
    page_number = db.Column(db.Integer)
    rect_json = db.Column(db.Text)
    mapped_question_id = db.Column(db.Integer, db.ForeignKey("case_question.id"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    template = db.relationship("FormTemplate", backref=db.backref("pdf_fields", cascade="all, delete-orphan"))
    mapped_question = db.relationship("CaseQuestion")

    __table_args__ = (db.UniqueConstraint("template_id", "field_name", name="uq_template_pdf_field"),)


class Agency(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    agency_name = db.Column(db.String(160), nullable=False)
    tax_id = db.Column(db.String(80), nullable=False)
    street_address = db.Column(db.String(180), nullable=False)
    apartment = db.Column(db.String(80))
    city = db.Column(db.String(80), nullable=False)
    state = db.Column(db.String(2), nullable=False)
    zip_code = db.Column(db.String(12), nullable=False)
    ceo_email = db.Column(db.String(160), nullable=False)
    agency_email = db.Column(db.String(160))
    ceo_phone = db.Column(db.String(40), nullable=False)
    agency_phone = db.Column(db.String(40))
    registered_owners = db.Column(db.String(240), nullable=False)
    registered_operator = db.Column(db.String(160))
    membership_plan_cost = db.Column(db.Numeric(10, 2), default=0)
    total_ips_allowed = db.Column(db.Integer, default=1, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    user = db.relationship("AgencyUser", back_populates="agency", uselist=False, cascade="all, delete-orphan")
    clients = db.relationship("Client", back_populates="agency", cascade="all, delete-orphan")
    cases = db.relationship("Case", back_populates="agency", cascade="all, delete-orphan")
    documents = db.relationship("AgencyDocument", back_populates="agency", cascade="all, delete-orphan")
    subscriptions = db.relationship("SubscriptionTool", secondary=agency_subscriptions, lazy="subquery")

    def has_tool(self, name):
        return any(tool.name == name for tool in self.subscriptions)

    @property
    def display_address(self):
        unit = f", {self.apartment}" if self.apartment else ""
        return f"{self.street_address}{unit}, {self.city}, {self.state} {self.zip_code}"


class AgencyUser(UserMixin, PasswordMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    agency_id = db.Column(db.Integer, db.ForeignKey("agency.id"), nullable=False, unique=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    agency = db.relationship("Agency", back_populates="user")

    @property
    def role(self):
        return "agency"

    def get_id(self):
        return f"agency:{self.id}"


class AgencyTranslator(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    agency_id = db.Column(db.Integer, db.ForeignKey("agency.id"), nullable=False)
    full_name = db.Column(db.String(160), nullable=False)
    language = db.Column(db.String(80), nullable=False)
    phone = db.Column(db.String(40))
    email = db.Column(db.String(160))
    address = db.Column(db.String(240))
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    agency = db.relationship("Agency", backref=db.backref("translators", cascade="all, delete-orphan"))


class AgencyPreparer(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    agency_id = db.Column(db.Integer, db.ForeignKey("agency.id"), nullable=False)
    full_name = db.Column(db.String(160), nullable=False)
    title = db.Column(db.String(100))
    phone = db.Column(db.String(40))
    email = db.Column(db.String(160))
    address = db.Column(db.String(240))
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    agency = db.relationship("Agency", backref=db.backref("preparers", cascade="all, delete-orphan"))


class AgencyCaseManager(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    agency_id = db.Column(db.Integer, db.ForeignKey("agency.id"), nullable=False)
    full_name = db.Column(db.String(160), nullable=False)
    phone = db.Column(db.String(40))
    email = db.Column(db.String(160))
    address = db.Column(db.String(240))
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    agency = db.relationship("Agency", backref=db.backref("case_managers", cascade="all, delete-orphan"))


class AgencyCrmPreparer(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    agency_id = db.Column(db.Integer, db.ForeignKey("agency.id"), nullable=False)
    full_name = db.Column(db.String(160), nullable=False)
    phone = db.Column(db.String(40))
    email = db.Column(db.String(160))
    address = db.Column(db.String(240))
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    agency = db.relationship("Agency", backref=db.backref("crm_preparers", cascade="all, delete-orphan"))


class AgencyLawyer(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    agency_id = db.Column(db.Integer, db.ForeignKey("agency.id"), nullable=False)
    first_name = db.Column(db.String(80), nullable=False)
    middle_name = db.Column(db.String(80))
    last_name = db.Column(db.String(80), nullable=False)
    bar_number = db.Column(db.String(80), nullable=False)
    phone = db.Column(db.String(40))
    email = db.Column(db.String(160))
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    agency = db.relationship("Agency", backref=db.backref("lawyers", cascade="all, delete-orphan"))

    @property
    def full_name(self):
        parts = [self.first_name, self.middle_name, self.last_name]
        return " ".join(part for part in parts if part)


class AgencyLawFirm(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    agency_id = db.Column(db.Integer, db.ForeignKey("agency.id"), nullable=False)
    name = db.Column(db.String(180), nullable=False)
    phone = db.Column(db.String(40))
    address = db.Column(db.String(240), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    agency = db.relationship("Agency", backref=db.backref("law_firms", cascade="all, delete-orphan"))


class MotionTemplate(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    agency_id = db.Column(db.Integer, db.ForeignKey("agency.id"), nullable=False)
    name = db.Column(db.String(180), nullable=False, default="Untitled Motion Template")
    motion_title = db.Column(db.String(220), nullable=False, default="MOTION")
    content = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    agency = db.relationship("Agency", backref=db.backref("motion_templates", cascade="all, delete-orphan"))

    @property
    def display_name(self):
        return self.name or f"Motion Template #{self.id}"


class ImmigrationCourt(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(180), nullable=False)
    address_line1 = db.Column(db.String(180))
    address_line2 = db.Column(db.String(180))
    city = db.Column(db.String(100))
    state = db.Column(db.String(2))
    zip_code = db.Column(db.String(20))
    postal_code = db.Column(db.String(20))
    address = db.Column(db.String(240))
    is_active = db.Column(db.Boolean, default=True, nullable=False)

    @property
    def label(self):
        location = ", ".join(part for part in [self.city, self.state] if part)
        return f"{self.name} - {location}" if location else self.name

    @property
    def address_text(self):
        parts = [self.address_line1, self.address_line2]
        city_line = " ".join(part for part in [", ".join(part for part in [self.city, self.state] if part), self.postal_code or self.zip_code] if part)
        parts.append(city_line)
        return "\n".join(part for part in parts if part) or (self.address or "")


class ImmigrationJudge(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(180), nullable=False)
    court_name = db.Column(db.String(180))
    is_active = db.Column(db.Boolean, default=True, nullable=False)

    @property
    def label(self):
        return f"{self.name} - {self.court_name}" if self.court_name else self.name


class OplaOffice(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(180), nullable=False)
    address_line1 = db.Column(db.String(180))
    address_line2 = db.Column(db.String(180))
    city = db.Column(db.String(100))
    state = db.Column(db.String(2))
    postal_code = db.Column(db.String(20))
    address = db.Column(db.String(240))
    phone = db.Column(db.String(40))
    is_active = db.Column(db.Boolean, default=True, nullable=False)

    @property
    def label(self):
        location = ", ".join(part for part in [self.city, self.state] if part)
        return f"{self.name} - {location}" if location else self.name

    @property
    def address_text(self):
        parts = [self.address_line1, self.address_line2]
        city_line = " ".join(part for part in [", ".join(part for part in [self.city, self.state] if part), self.postal_code] if part)
        parts.append(city_line)
        return "\n".join(part for part in parts if part) or (self.address or "")


class MotionDraft(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    agency_id = db.Column(db.Integer, db.ForeignKey("agency.id"), nullable=False)
    template_id = db.Column(db.Integer, db.ForeignKey("motion_template.id"), nullable=False)
    immigration_court = db.Column(db.String(240), nullable=False)
    immigration_court_address = db.Column(db.Text)
    immigration_judge = db.Column(db.String(240), nullable=False)
    opla_office = db.Column(db.String(240), nullable=False)
    opla_address = db.Column(db.Text)
    lawyer_id = db.Column(db.Integer, db.ForeignKey("agency_lawyer.id"))
    law_firm_id = db.Column(db.Integer, db.ForeignKey("agency_law_firm.id"))
    lawyer_name = db.Column(db.String(160))
    lawyer_bar_number = db.Column(db.String(80))
    law_firm_name = db.Column(db.String(180))
    law_firm_phone = db.Column(db.String(40))
    law_firm_address = db.Column(db.Text)
    motion_title = db.Column(db.String(220))
    detention_status = db.Column(db.String(30))
    next_hearing_date = db.Column(db.String(20))
    next_hearing_type = db.Column(db.String(30))
    exhibits_text = db.Column(db.Text)
    rendered_content = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    agency = db.relationship("Agency", backref=db.backref("motion_drafts", cascade="all, delete-orphan"))
    template = db.relationship("MotionTemplate")
    lawyer = db.relationship("AgencyLawyer")
    law_firm = db.relationship("AgencyLawFirm")
    respondents = db.relationship("MotionRespondent", back_populates="motion", cascade="all, delete-orphan", order_by="MotionRespondent.sort_order")

    @property
    def title(self):
        if self.motion_title:
            return self.motion_title.upper()
        if self.template and self.template.motion_title:
            return self.template.motion_title.upper()
        return f"Motion #{self.id}"


class MotionRespondent(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    motion_id = db.Column(db.Integer, db.ForeignKey("motion_draft.id"), nullable=False)
    first_name = db.Column(db.String(80), nullable=False)
    middle_name = db.Column(db.String(80))
    last_name = db.Column(db.String(80), nullable=False)
    alien_number = db.Column(db.String(40), nullable=False)
    sort_order = db.Column(db.Integer, default=1, nullable=False)

    motion = db.relationship("MotionDraft", back_populates="respondents")

    @property
    def full_name(self):
        parts = [self.first_name, self.middle_name, self.last_name]
        return " ".join(part for part in parts if part)


class Client(UserMixin, PasswordMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    agency_id = db.Column(db.Integer, db.ForeignKey("agency.id"), nullable=False)
    first_name = db.Column(db.String(80), nullable=False)
    middle_name = db.Column(db.String(80))
    last_name = db.Column(db.String(80), nullable=False)
    a_number = db.Column(db.String(40))
    phone = db.Column(db.String(40), nullable=False)
    email = db.Column(db.String(160), nullable=False)
    street_address = db.Column(db.String(180), nullable=False)
    apartment = db.Column(db.String(80))
    city = db.Column(db.String(80), nullable=False)
    state = db.Column(db.String(2), nullable=False)
    zip_code = db.Column(db.String(12), nullable=False)
    username = db.Column(db.String(80), unique=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    agency = db.relationship("Agency", back_populates="clients")
    cases = db.relationship("Case", back_populates="client", cascade="all, delete-orphan")
    crm_cases = db.relationship("CrmCase", back_populates="client", cascade="all, delete-orphan")
    crm_invoices = db.relationship("CrmInvoice", back_populates="client", cascade="all, delete-orphan")
    crm_appointments = db.relationship("CrmAppointment", back_populates="client", cascade="all, delete-orphan")
    crm_documents = db.relationship("CrmClientDocument", back_populates="client", cascade="all, delete-orphan")

    @property
    def role(self):
        return "client"

    def get_id(self):
        return f"client:{self.id}"

    @property
    def full_name(self):
        parts = [self.first_name, self.middle_name, self.last_name]
        return " ".join(part for part in parts if part)

    @property
    def display_address(self):
        unit = f", {self.apartment}" if self.apartment else ""
        return f"{self.street_address}{unit}, {self.city}, {self.state} {self.zip_code}"


class Case(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    agency_id = db.Column(db.Integer, db.ForeignKey("agency.id"), nullable=False)
    client_id = db.Column(db.Integer, db.ForeignKey("client.id"), nullable=False)
    case_type = db.Column(db.String(40), nullable=False)
    status = db.Column(db.String(80), default="Created", nullable=False)
    progress_percentage = db.Column(db.Integer, default=0, nullable=False)
    translator_id = db.Column(db.Integer, db.ForeignKey("agency_translator.id"))
    preparer_id = db.Column(db.Integer, db.ForeignKey("agency_preparer.id"))
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    agency = db.relationship("Agency", back_populates="cases")
    client = db.relationship("Client", back_populates="cases")
    answers = db.relationship("CaseAnswer", back_populates="case", cascade="all, delete-orphan")
    documents = db.relationship("CaseDocument", back_populates="case", cascade="all, delete-orphan")
    generated_forms = db.relationship("GeneratedForm", back_populates="case", cascade="all, delete-orphan")
    translator = db.relationship("AgencyTranslator")
    preparer = db.relationship("AgencyPreparer")


class CaseQuestion(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    case_type = db.Column(db.String(40), nullable=False)
    prompt = db.Column(db.String(255), nullable=False)
    field_key = db.Column(db.String(80), nullable=False)
    input_type = db.Column(db.String(30), default="text", nullable=False)
    render_mode = db.Column(db.String(30), default="normal", nullable=False)
    render_box_count = db.Column(db.Integer, default=0, nullable=False)
    sort_order = db.Column(db.Integer, default=0, nullable=False)
    required = db.Column(db.Boolean, default=True, nullable=False)
    client_visible = db.Column(db.Boolean, default=True, nullable=False)
    show_if_question_id = db.Column(db.Integer, db.ForeignKey("case_question.id"))
    show_if_operator = db.Column(db.String(30), default="equals", nullable=False)
    show_if_value = db.Column(db.String(255))

    show_if_question = db.relationship("CaseQuestion", remote_side=[id])

    __table_args__ = (db.UniqueConstraint("case_type", "field_key", name="uq_case_question_key"),)


class CaseAnswer(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    case_id = db.Column(db.Integer, db.ForeignKey("case.id"), nullable=False)
    question_id = db.Column(db.Integer, db.ForeignKey("case_question.id"), nullable=False)
    answer_text = db.Column(db.Text)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    case = db.relationship("Case", back_populates="answers")
    question = db.relationship("CaseQuestion")

    __table_args__ = (db.UniqueConstraint("case_id", "question_id", name="uq_case_answer_question"),)


class AgencyDocument(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    agency_id = db.Column(db.Integer, db.ForeignKey("agency.id"), nullable=False)
    original_filename = db.Column(db.String(255), nullable=False)
    stored_filename = db.Column(db.String(255), nullable=False)
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    agency = db.relationship("Agency", back_populates="documents")


class CaseDocument(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    case_id = db.Column(db.Integer, db.ForeignKey("case.id"), nullable=False)
    uploaded_by_role = db.Column(db.String(20), nullable=False)
    original_filename = db.Column(db.String(255), nullable=False)
    stored_filename = db.Column(db.String(255), nullable=False)
    document_type = db.Column(db.String(80), default="Upload", nullable=False)
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    case = db.relationship("Case", back_populates="documents")


class GeneratedForm(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    case_id = db.Column(db.Integer, db.ForeignKey("case.id"), nullable=False)
    filename = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    case = db.relationship("Case", back_populates="generated_forms")


class CrmCase(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    agency_id = db.Column(db.Integer, db.ForeignKey("agency.id"), nullable=False)
    client_id = db.Column(db.Integer, db.ForeignKey("client.id"), nullable=False)
    title = db.Column(db.String(140), nullable=False)
    status = db.Column(db.String(60), default="Open", nullable=False)
    price = db.Column(db.Numeric(10, 2), default=0, nullable=False)
    case_manager_id = db.Column(db.Integer, db.ForeignKey("agency_case_manager.id"))
    form_preparer_id = db.Column(db.Integer, db.ForeignKey("agency_preparer.id"))
    tag_id = db.Column(db.Integer, db.ForeignKey("crm_case_tag.id"))
    opened_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    completed_at = db.Column(db.DateTime)
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    agency = db.relationship("Agency", backref=db.backref("crm_cases", cascade="all, delete-orphan"))
    client = db.relationship("Client", back_populates="crm_cases")
    case_manager = db.relationship("AgencyCaseManager")
    form_preparer = db.relationship("AgencyPreparer")
    tag = db.relationship("CrmCaseTag")
    invoices = db.relationship("CrmInvoice", back_populates="case", cascade="all, delete-orphan")
    appointments = db.relationship("CrmAppointment", back_populates="case", cascade="all, delete-orphan")
    documents = db.relationship("CrmClientDocument", back_populates="case")
    note_entries = db.relationship("CrmCaseNote", back_populates="case", cascade="all, delete-orphan", order_by="CrmCaseNote.created_at.desc()")


class CrmCaseTag(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    agency_id = db.Column(db.Integer, db.ForeignKey("agency.id"), nullable=False)
    name = db.Column(db.String(80), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    agency = db.relationship("Agency", backref=db.backref("crm_case_tags", cascade="all, delete-orphan"))


class CrmInvoice(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    agency_id = db.Column(db.Integer, db.ForeignKey("agency.id"), nullable=False)
    client_id = db.Column(db.Integer, db.ForeignKey("client.id"), nullable=False)
    case_id = db.Column(db.Integer, db.ForeignKey("crm_case.id"), nullable=False)
    invoice_number = db.Column(db.String(32), nullable=False, index=True)
    issue_date = db.Column(db.Date, default=date.today, nullable=False)
    due_date = db.Column(db.Date)
    description = db.Column(db.Text)
    subtotal = db.Column(db.Numeric(10, 2), default=0, nullable=False)
    discount = db.Column(db.Numeric(10, 2), default=0, nullable=False)
    total = db.Column(db.Numeric(10, 2), default=0, nullable=False)
    paid_amount = db.Column(db.Numeric(10, 2), default=0, nullable=False)
    status = db.Column(db.String(30), default="Unpaid", nullable=False)
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    agency = db.relationship("Agency", backref=db.backref("crm_invoices", cascade="all, delete-orphan"))
    client = db.relationship("Client", back_populates="crm_invoices")
    case = db.relationship("CrmCase", back_populates="invoices")
    activities = db.relationship("CrmInvoiceActivity", back_populates="invoice", cascade="all, delete-orphan", order_by="CrmInvoiceActivity.activity_date.desc(), CrmInvoiceActivity.created_at.desc()")

    @property
    def balance_due(self):
        return (self.total or 0) - (self.paid_amount or 0)


class CrmAppointment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    agency_id = db.Column(db.Integer, db.ForeignKey("agency.id"), nullable=False)
    client_id = db.Column(db.Integer, db.ForeignKey("client.id"), nullable=False)
    case_id = db.Column(db.Integer, db.ForeignKey("crm_case.id"), nullable=False)
    title = db.Column(db.String(180), nullable=False)
    appointment_type = db.Column(db.String(80))
    start_at = db.Column(db.DateTime, nullable=False)
    end_at = db.Column(db.DateTime)
    location = db.Column(db.String(240))
    status = db.Column(db.String(40), default="Scheduled", nullable=False)
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    agency = db.relationship("Agency", backref=db.backref("crm_appointments", cascade="all, delete-orphan"))
    client = db.relationship("Client", back_populates="crm_appointments")
    case = db.relationship("CrmCase", back_populates="appointments")
    note_entries = db.relationship("CrmAppointmentNote", back_populates="appointment", cascade="all, delete-orphan", order_by="CrmAppointmentNote.created_at.desc()")


class CrmCaseNote(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    agency_id = db.Column(db.Integer, db.ForeignKey("agency.id"), nullable=False)
    case_id = db.Column(db.Integer, db.ForeignKey("crm_case.id"), nullable=False)
    note_text = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    agency = db.relationship("Agency")
    case = db.relationship("CrmCase", back_populates="note_entries")


class CrmAppointmentNote(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    agency_id = db.Column(db.Integer, db.ForeignKey("agency.id"), nullable=False)
    appointment_id = db.Column(db.Integer, db.ForeignKey("crm_appointment.id"), nullable=False)
    note_text = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    agency = db.relationship("Agency")
    appointment = db.relationship("CrmAppointment", back_populates="note_entries")


class CrmInvoiceActivity(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    agency_id = db.Column(db.Integer, db.ForeignKey("agency.id"), nullable=False)
    invoice_id = db.Column(db.Integer, db.ForeignKey("crm_invoice.id"), nullable=False)
    activity_type = db.Column(db.String(30), nullable=False)
    amount = db.Column(db.Numeric(10, 2), default=0, nullable=False)
    activity_date = db.Column(db.Date, default=date.today, nullable=False)
    description = db.Column(db.String(240))
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    agency = db.relationship("Agency")
    invoice = db.relationship("CrmInvoice", back_populates="activities")


class CrmClientDocument(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    agency_id = db.Column(db.Integer, db.ForeignKey("agency.id"), nullable=False)
    client_id = db.Column(db.Integer, db.ForeignKey("client.id"), nullable=False)
    case_id = db.Column(db.Integer, db.ForeignKey("crm_case.id"))
    original_filename = db.Column(db.String(255), nullable=False)
    stored_filename = db.Column(db.String(255), nullable=False)
    document_type = db.Column(db.String(80), default="Client document", nullable=False)
    description = db.Column(db.String(240))
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    agency = db.relationship("Agency", backref=db.backref("crm_documents", cascade="all, delete-orphan"))
    client = db.relationship("Client", back_populates="crm_documents")
    case = db.relationship("CrmCase", back_populates="documents")


class ActiveSession(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, nullable=False)
    role = db.Column(db.String(20), nullable=False)
    agency_id = db.Column(db.Integer, db.ForeignKey("agency.id"))
    ip_address = db.Column(db.String(80), nullable=False)
    login_time = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    last_activity = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    @classmethod
    def active_window(cls):
        return datetime.utcnow() - timedelta(hours=8)
