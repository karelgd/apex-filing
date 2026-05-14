US_STATES = [
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID", "IL",
    "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS", "MO", "MT",
    "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI",
    "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY", "DC",
]

SUBSCRIPTION_TOOLS = [
    "Form Filler",
    "Motion Creation",
    "CRM",
    "Power of Attorney Creator",
    "Contract Generator",
    "Other future tools",
]

CASE_TYPES = ["I-485", "I-765", "Motion"]

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
