import re

import phonenumbers
from flask_wtf import FlaskForm
from wtforms import BooleanField, EmailField, HiddenField, SelectField, StringField, TelField
from wtforms.validators import AnyOf, DataRequired, Email, Length, Optional, Regexp, ValidationError


ODAISA_LEAD_TOPICS = (
    ("work", "Permiso de trabajo"),
    ("residence", "Residencia permanente"),
    ("citizenship", "Ciudadanía"),
    ("family", "Petición familiar"),
    ("asylum", "Formularios de asilo"),
    ("status", "Extensión o cambio de estatus"),
    ("other", "Otro formulario"),
)

ODAISA_INTAKE_STEPS = (
    ("first_name", "middle_name", "last_name"),
    ("a_number",),
    ("phone",),
    ("email",),
    ("street_address", "city", "state", "zip_code"),
)

US_STATES = [
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID", "IL",
    "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS", "MO", "MT",
    "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI",
    "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY", "DC",
]

US_PHONE_ERROR = "Ingresa un teléfono válido de Estados Unidos, por ejemplo, (305) 555-0123."


def normalize_us_phone(value):
    if not isinstance(value, str) or not re.fullmatch(r"[+0-9(). -]{7,32}", value.strip()):
        return None
    try:
        number = phonenumbers.parse(value, "US")
    except phonenumbers.NumberParseException:
        return None
    if phonenumbers.region_code_for_number(number) != "US" or not phonenumbers.is_valid_number_for_region(number, "US"):
        return None
    return phonenumbers.format_number(number, phonenumbers.PhoneNumberFormat.E164)


class OdaisaConsentForm(FlaskForm):
    consent = BooleanField(
        "Acepto los términos de tratamiento de datos y autorizo a Odaisa a contactarme sobre esta solicitud.",
        validators=[DataRequired(message="Debes aceptar los términos para continuar.")],
    )
    topic = HiddenField(validators=[AnyOf(dict(ODAISA_LEAD_TOPICS), message="Selecciona un servicio válido.")])


class OdaisaLeadDetailsForm(FlaskForm):
    first_name = StringField("Nombre", default="", filters=[str.strip], validators=[
        DataRequired(message="Escribe tu nombre."), Length(max=80),
    ])
    middle_name = StringField("Segundo nombre", default="", filters=[str.strip], validators=[
        Optional(), Length(max=80),
    ])
    last_name = StringField("Apellidos", default="", filters=[str.strip], validators=[
        DataRequired(message="Escribe tus apellidos."), Length(max=80),
    ])
    a_number = StringField("Número A", default="", filters=[str.strip], validators=[
        DataRequired(message="Escribe tu número A."),
        Regexp(r"[0-9]{7,9}\Z", message="Escribe entre 7 y 9 dígitos, sin la letra A."),
    ])
    phone = TelField("Teléfono de Estados Unidos", default="", filters=[str.strip], validators=[
        DataRequired(message="Escribe tu teléfono."),
    ])
    email = EmailField("Correo electrónico", default="", filters=[str.strip], validators=[
        DataRequired(message="Escribe tu correo electrónico."), Length(max=160),
        Email(message="Escribe un correo electrónico válido."),
    ])
    street_address = StringField("Dirección de calle", default="", filters=[str.strip], validators=[
        DataRequired(message="Escribe tu dirección de calle."), Length(max=180),
    ])
    city = StringField("Ciudad", default="", filters=[str.strip], validators=[
        DataRequired(message="Escribe tu ciudad."), Length(max=80),
    ])
    state = SelectField("Estado", choices=[("", "Selecciona"), *((state, state) for state in US_STATES)],
                        validators=[DataRequired(message="Selecciona un estado de Estados Unidos.")])
    zip_code = StringField("Código ZIP", default="", filters=[str.strip], validators=[
        DataRequired(message="Escribe tu código ZIP."),
        Regexp(r"[0-9]{5}(?:-[0-9]{4})?\Z", message="Escribe un ZIP de 5 dígitos o ZIP+4."),
    ])
    def validate_phone(self, field):
        normalized = normalize_us_phone(field.data)
        if not normalized:
            raise ValidationError(US_PHONE_ERROR)
        field.data = normalized


class OdaisaLeadForm(OdaisaLeadDetailsForm):
    website = StringField("Sitio web")
    submission_id = HiddenField(validators=[Regexp(r"[0-9a-f]{32}\Z", message="Vuelve a cargar el formulario e inténtalo de nuevo.")])


SUBSCRIPTION_TOOLS = [
    "Form Filler",
    "Motion Creation",
    "CRM",
    "Joinder",
    "Power of Attorney Creator",
    "Contract Generator",
    "Other future tools",
]

CASE_TYPES = ["I-485", "I-765", "Motion"]

CRM_CASE_SERVICES = [
    ("I-130", "Petition for Alien Relative (family petition)"),
    ("I-485", "Application to Register Permanent Residence / Adjustment of Status (Green Card)"),
    ("I-765", "Application for Employment Authorization (work permit)"),
    ("I-131", "Travel Document / Advance Parole"),
    ("N-400", "Application for Naturalization (U.S. citizenship)"),
    ("I-90", "Replace or renew Green Card"),
    ("I-589", "Application for Asylum and Withholding of Removal"),
    ("I-864", "Affidavit of Support"),
    ("I-129F", "Fiance(e) Visa Petition (K-1 visa)"),
    ("I-751", "Remove Conditions on Residence (2-year green card cases)"),
    ("I-601", "Waiver of Grounds of Inadmissibility"),
    ("I-821D", "DACA request"),
    ("I-821", "Temporary Protected Status (TPS)"),
    ("I-290B", "Motion to Reopen or Appeal"),
    ("N-600", "Certificate of Citizenship"),
    ("I-539", "Extend or Change Nonimmigrant Status"),
    ("I-129", "Petition for Nonimmigrant Worker (H-1B, etc.)"),
    ("I-140", "Employment-Based Immigrant Petition"),
    ("I-918", "U Visa Petition"),
    ("I-914", "T Visa Application"),
    ("I-134", "Declaration of Financial Support"),
    ("I-912", "Fee Waiver Request"),
    ("AR-11", "Change of Address"),
    ("G-28", "Notice of Attorney/Representative"),
    ("G-1145", "E-Notification for USCIS filings"),
    ("G-639", "FOIA Request for Immigration Records"),
    ("Consultation", "Consultation"),
    ("Motion to Terminate Proceedings", "Motion to Terminate Proceedings"),
    ("Motion to Change Venue", "Motion to Change Venue"),
    ("Motion to Continue Proceedings", "Motion to Continue Proceedings"),
    ("Motion to Dismiss Proceedings", "Motion to Dismiss Proceedings"),
    ("EOIR Change of Address", "EOIR Change of Address"),
    ("USCIS Change of Address", "USCIS Change of Address"),
    ("ICE Change of Address", "ICE Change of Address"),
    ("EOIR+USCIS+ICE Changes of Address", "EOIR+USCIS+ICE Changes of Address"),
    ("USCIS+ICE Changes of Address", "USCIS+ICE Changes of Address"),
    ("Documents Review", "Documents Review"),
    ("USCIS RFE", "USCIS Request for Evidence"),
    ("USCIS Unsolicited RFE", "USCIS Unsolicited Evidence Response"),
    ("Passport Photo Service", "Passport Photo Service"),
    ("USCIS Request for Application Withdrawal", "USCIS Request for Application Withdrawal"),
    ("EOIR Notice of Filing Form", "EOIR Notice of Filing Form"),
    ("I-693", "Report of Immigration Medical Examination and Vaccination Record"),
]

FORM_TEMPLATES = [
    ("I-589", "Application for Asylum and for Withholding of Removal", "Asylum intake questionnaire foundation."),
    ("I-485", "Application to Register Permanent Residence or Adjust Status", "Adjustment of status intake questionnaire foundation."),
]

CASE_STATUSES = [
    "Created",
    "Client Questionnaire Started",
    "Waiting for Client",
    "Ready for Review",
    "In Preparation",
    "Generated",
    "Completed",
    "Cancelled",
]

I485_QUESTIONS = [
    ("full_legal_name", "Full legal name", "text"),
    ("other_names_used", "Other names used", "textarea"),
    ("date_of_birth", "Date of birth", "date"),
    ("country_of_birth", "Country of birth", "text"),
    ("current_address", "Current address", "textarea"),
    ("address_history_5_years", "Address history for past 5 years", "textarea"),
    ("passport_number", "Passport number", "text"),
    ("passport_country", "Passport country of issuance", "text"),
    ("passport_expiration_date", "Passport expiration date", "date"),
    ("i94_number", "I-94 number if any", "text"),
    ("last_entry_date", "Last entry date", "date"),
    ("last_entry_place", "Last entry place", "text"),
    ("current_immigration_status", "Current immigration status", "text"),
    ("parents_names", "Parents' names", "textarea"),
    ("employment_history", "Employment history", "textarea"),
    ("marital_status", "Marital status", "text"),
]

I589_QUESTIONS = [
    ("full_legal_name", "Full legal name", "text"),
    ("a_number", "A-Number if any", "text"),
    ("date_of_birth", "Date of birth", "date"),
    ("country_of_birth", "Country of birth", "text"),
    ("nationality", "Nationality", "text"),
    ("current_address", "Current U.S. address", "textarea"),
    ("phone_email", "Current phone number and email", "textarea"),
    ("last_arrival_date", "Date of last arrival in the United States", "date"),
    ("last_arrival_place", "Place of last arrival in the United States", "text"),
    ("current_status", "Current immigration status", "text"),
    ("basis_of_claim", "Why are you seeking asylum or withholding of removal?", "textarea"),
    ("harm_experienced", "Describe any harm, threats, or mistreatment you experienced.", "textarea"),
    ("fear_if_return", "What do you fear would happen if you return to your country?", "textarea"),
    ("family_in_application", "List spouse/children included in this application, if any.", "textarea"),
    ("prior_applications", "Have you previously applied for asylum or related protection?", "textarea"),
]
