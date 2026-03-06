# apex_backend/routers/auth.py
from fastapi import APIRouter, HTTPException, status, Depends
from pydantic import BaseModel, EmailStr
from database import get_user_by_email, create_user, get_db
from auth_utils import (
    hash_password, verify_password,
    create_access_token, create_refresh_token,
    get_current_user,
)
from datetime import datetime, timezone

router = APIRouter()


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str

class LoginRequest(BaseModel):
    email: EmailStr
    password: str

class RefreshRequest(BaseModel):
    refresh_token: str


@router.post("/register", status_code=201)
def register(body: RegisterRequest):
    if len(body.password) < 8:
        raise HTTPException(400, "Password must be at least 8 characters")

    existing = get_user_by_email(body.email)
    if existing:
        raise HTTPException(409, "An account with that email already exists")

    hashed = hash_password(body.password)
    user   = create_user(body.email, hashed)

    access_token  = create_access_token(user["id"], user["email"], user["tier"])
    refresh_token = create_refresh_token(user["id"])

    return {
        "access_token":  access_token,
        "refresh_token": refresh_token,
        "token_type":    "bearer",
        "user": {
            "id":    user["id"],
            "email": user["email"],
            "tier":  user["tier"],
        }
    }


@router.post("/login")
def login(body: LoginRequest):
    user = get_user_by_email(body.email)
    if not user or not verify_password(body.password, user["hashed_password"]):
        raise HTTPException(401, "Invalid email or password")

    if not user.get("active"):
        raise HTTPException(403, "Account is deactivated")

    access_token  = create_access_token(user["id"], user["email"], user["tier"])
    refresh_token = create_refresh_token(user["id"])

    return {
        "access_token":  access_token,
        "refresh_token": refresh_token,
        "token_type":    "bearer",
        "user": {
            "id":    user["id"],
            "email": user["email"],
            "tier":  user["tier"],
        }
    }


@router.post("/refresh")
def refresh(body: RefreshRequest):
    db  = get_db()
    now = datetime.now(timezone.utc).isoformat()

    res = (db.table("refresh_tokens")
             .select("*, users(*)")
             .eq("token", body.refresh_token)
             .gt("expires_at", now)
             .single()
             .execute())

    if not res.data:
        raise HTTPException(401, "Invalid or expired refresh token")

    user = res.data["users"]
    if not user.get("active"):
        raise HTTPException(403, "Account is deactivated")

    # Rotate refresh token
    db.table("refresh_tokens").delete().eq("token", body.refresh_token).execute()
    new_refresh = create_refresh_token(user["id"])
    new_access  = create_access_token(user["id"], user["email"], user["tier"])

    return {
        "access_token":  new_access,
        "refresh_token": new_refresh,
        "token_type":    "bearer",
    }


@router.post("/logout")
def logout(body: RefreshRequest):
    db = get_db()
    db.table("refresh_tokens").delete().eq("token", body.refresh_token).execute()
    return {"detail": "Logged out"}


@router.get("/me")
def me(user: dict = Depends(get_current_user)):
    return {
        "id":    user["id"],
        "email": user["email"],
        "tier":  user["tier"],
    }
