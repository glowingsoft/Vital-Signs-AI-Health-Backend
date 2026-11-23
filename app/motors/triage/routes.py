# HTTP endpoint for the triage motor: POST /motor/triage/analyze.
# It only receives the request and hands the data to logic.py, like a thin Django view.

from fastapi import APIRouter

from app.motors.triage.logic import analyze
from app.shared.contracts import MotorInput, MotorOutput

# APIRouter = like a Django urls.py for one app. main.py "includes" it when this motor should run.
router = APIRouter(prefix="/motor/triage", tags=["triage motor"])


# response_model = FastAPI checks our answer matches MotorOutput before sending it.
@router.post("/analyze", response_model=MotorOutput)
def analyze_endpoint(motor_input: MotorInput) -> MotorOutput:
    # FastAPI has already validated the JSON body into a MotorInput (no serializer.is_valid() needed).
    return analyze(motor_input.data, country=motor_input.country)
