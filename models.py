from datetime import datetime, timedelta

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
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    agency = db.relationship("Agency", back_populates="cases")
    client = db.relationship("Client", back_populates="cases")
    answers = db.relationship("CaseAnswer", back_populates="case", cascade="all, delete-orphan")
    documents = db.relationship("CaseDocument", back_populates="case", cascade="all, delete-orphan")
    generated_forms = db.relationship("GeneratedForm", back_populates="case", cascade="all, delete-orphan")


class CaseQuestion(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    case_type = db.Column(db.String(40), nullable=False)
    prompt = db.Column(db.String(255), nullable=False)
    field_key = db.Column(db.String(80), nullable=False)
    input_type = db.Column(db.String(30), default="text", nullable=False)
    sort_order = db.Column(db.Integer, default=0, nullable=False)
    required = db.Column(db.Boolean, default=True, nullable=False)

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
