# Which consents each part of an /ai/assess request needs, and the message shown when one is missing.
# Changing what needs consent only requires editing this file (checked in app/orchestrator/consent_check.py).
# Consent types must match the CHECK constraint on ai.consents.consent_type.

# Request part -> consents the user must have given (the newest ai.consents row has granted = true).
REQUIRED_CONSENTS = {
    "symptoms": ["monitoring"],
    "vitals": ["monitoring"],
    "image": ["monitoring", "camera_image"],
}

# Shown to the user by the app when the consent is missing.
CONSENT_MESSAGES = {
    "monitoring": "Please accept monitoring in the app before using health checks.",
    "camera_image": "Please allow photo analysis in the app before sending an image.",
}
