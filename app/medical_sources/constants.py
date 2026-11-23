# The allowed values of ai.medical_sources, shared by the admin API, the seed and the CHECK constraints.

import re

from collection.base import MOTORS
from collection.medical_source import SOURCE_COUNTRIES, SOURCE_STATUSES

COUNTRIES = SOURCE_COUNTRIES  # ("US", "MX")
STATUSES = SOURCE_STATUSES  # ("draft", "proposed_addition", "active", "inactive", "broken")
SOURCE_MOTORS = MOTORS  # ("triage", "visual", "vitals")

# Only "active" sources are given to the AI by default (config.MEDICAL_SOURCE_STATUSES).
ACTIVE = "active"
INACTIVE = "inactive"
NEW_SOURCE_STATUS = "draft"  # every source added through the API starts here, until the client approves it

LANGUAGES = ("en", "es")

# Shown to the patient in POST /ai/assess "sources" when a motor's answer has no matched library source.
# Keyed by country code (library.normalize_country); a US or unknown country gets the default.
PENDING_REVIEW_LABELS = {
    "US": "Pending medical review",
    "MX": "Pendiente de revisión médica",
}
DEFAULT_PENDING_REVIEW_COUNTRY = "US"

EVIDENCE_LEVELS = ("clinical_guideline", "peer_reviewed_study", "expert_consensus", "public_health_guidance")

CLIENT_DECISIONS = ("approve", "approve_with_changes", "reject")

# Topic key -> what it covers. A source's topics must be keys of this list.
TOPICS = {
    "blood_pressure": "High or low blood pressure",
    "heart_rate": "Fast, slow or irregular heart rate",
    "respiratory_rate": "Breathing rate",
    "oxygen_saturation": "SpO2 / oxygen level",
    "breathing_difficulty": "Shortness of breath",
    "chest_pain": "Chest pain or possible heart attack",
    "stroke_symptoms": "Signs of stroke",
    "sepsis_infection": "Signs of serious infection or sepsis",
    "shock": "Signs of shock or poor circulation",
    "altered_mental_status": "Confusion or reduced consciousness",
    "diabetes_glucose": "Diabetes and blood glucose",
    "wounds_skin": "Wounds, ulcers and skin lesions",
    "diabetic_foot": "Diabetic foot",
    "burns_trauma": "Burns and injuries",
    "cardiovascular_risk": "Overall cardiovascular risk",
    "heart_failure": "Heart failure",
}

# Source ids: the country, then capital letters, digits and dashes, e.g. US-AHA-HBP-2025, MX-OPS-BEC.
ID_PATTERN = re.compile(r"^(US|MX)-[A-Z0-9][A-Z0-9-]{1,60}$")
