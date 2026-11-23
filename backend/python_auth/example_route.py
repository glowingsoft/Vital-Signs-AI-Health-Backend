"""Minimal example. Run from the project root with: JWT_SECRET=... uvicorn python_auth.example_route:app --reload"""

from fastapi import Depends, FastAPI

from python_auth.auth import AuthUser, current_user, require_roles
from python_auth.roles import UserType

app = FastAPI()


@app.get("/ai/me")
def me(user: AuthUser = Depends(current_user)):
    # Any logged-in user (any role).
    return {"user_id": user.user_id, "user_type": user.user_type}


@app.post("/ai/analyze")
def analyze(user: AuthUser = Depends(require_roles(UserType.User))):
    # Patients only; doctors/admins get 403.
    return {"status": "queued", "for_user": user.user_id}
