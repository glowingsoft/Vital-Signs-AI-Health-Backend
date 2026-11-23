# HTTP endpoint for the visual motor: POST /motor/visual/analyze.
# It only receives the request and hands the data to logic.py, like a thin Django view.

from fastapi import APIRouter

from app.motors.visual.logic import analyze
from app.shared.contracts import MotorInput, MotorOutput

# APIRouter = like a Django urls.py for one app. main.py "includes" it when this motor should run.
router = APIRouter(prefix="/motor/visual", tags=["visual motor"])


# response_model = FastAPI checks our answer matches MotorOutput before sending it.
@router.post("/analyze", response_model=MotorOutput)
def analyze_endpoint(motor_input: MotorInput) -> MotorOutput:
    # FastAPI has already validated the JSON body into a MotorInput (no serializer.is_valid() needed).
<<<<<<< Updated upstream
    return analyze(motor_input.data)
=======
    try:
        return analyze(motor_input.data, country=motor_input.country)
    except InvalidImage as error:
        # Same answer as POST /ai/assess for a bad image (AssessRequest checks it there).
        raise HTTPException(status_code=422, detail=str(error)) from None
>>>>>>> Stashed changes
