# Importing "collection" loads Base and every model, so Base.metadata knows all tables.
# Alembic autogenerate needs this: a model that is not imported here is invisible to it.
# New table? Add its file to collection/ and import its class below.

from collection.base import Base
from collection.disclaimer import Disclaimer
from collection.vitals_reading import VitalsReading
from collection.assessment import Assessment
from collection.motor_result import MotorResult
from collection.ai_usage import AiUsage
from collection.review import Review
from collection.audit_log import AuditLog
from collection.consent import Consent
from collection.medical_source import MedicalSource
from collection.critical_alert import CriticalAlert

__all__ = [
    "Base",
    "Disclaimer",
    "VitalsReading",
    "Assessment",
    "MotorResult",
    "AiUsage",
    "Review",
    "AuditLog",
    "Consent",
    "MedicalSource",
    "CriticalAlert",
]
