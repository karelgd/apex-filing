US_STATES = [
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID", "IL",
    "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS", "MO", "MT",
    "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI",
    "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY", "DC",
]

SUBSCRIPTION_TOOLS = [
    "Form Filler",
    "Motion Creation",
    "Power of Attorney Creator",
    "Contract Generator",
    "Other future tools",
]

CASE_TYPES = ["I-485", "I-765", "Motion"]

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
