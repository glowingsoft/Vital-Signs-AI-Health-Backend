# Which consents each part of an /ai/assess request needs, and the message shown when one is missing.
# Changing what needs consent only requires editing this file (checked in app/orchestrator/consent_check.py).
# Consent types must match the CHECK constraint on ai.consents.consent_type.

# Request part -> consents the user must have given (the newest ai.consents row has granted = true).
REQUIRED_CONSENTS = {
    "symptoms": ["monitoring_fda", "triage"],
    "vitals": ["monitoring_fda"],
    "image": ["monitoring_fda", "image_processing"],
    "audio_key": ["voice_recording"],  # a voice recording of the symptoms is stored only with recording consent
}
# The other types (research_participation, parental_consent, age_13_reconsent, adult_terms)
# are stored by NestJS but not checked here: the age consents depend on the user's age, which only NestJS knows.

# The "message" of the 403 answer; each missing consent also has its own message below.
CONSENT_REQUIRED_MESSAGE = "Please give the missing consent in the app before using health checks."

# Shown to the user by the app when the consent is missing.
CONSENT_MESSAGES = {
    "monitoring_fda": "Please accept 24/7 monitoring in the app before using health checks.",
    "triage": "Please allow symptom checks in the app before describing your symptoms.",
    "image_processing": "Please allow photo analysis in the app before sending an image.",
    "voice_recording": "Please allow voice recording in the app, or type your symptoms instead.",
}
