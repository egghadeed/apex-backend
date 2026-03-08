# apex_backend/auth_utils.py
from datetime import datetime, timedelta, timezone
from jose import JWTError, jwt
import bcrypt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from config import JWT_SECRET, JWT_ALGORITHM, ACCESS_TOKEN_EXPIRE_MINUTES
from database import get_user_by_id

bearer_scheme = HTTPBearer()


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))


def create_access_token(user_id: str, email: str, tier: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    return jwt.encode(
        {"sub": user_id, "email": email, "tier": tier, "exp": expire},
        JWT_SECRET, algorithm=JWT_ALGORITHM
    )

def create_refresh_token(user_id: str) -> str:
    from config import REFRESH_TOKEN_EXPIRE_DAYS
    import secrets
    token = secrets.token_urlsafe(48)
    expire = datetime.now(timezone.utc) + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
    from database import get_db
    db = get_db()
    db.table("refresh_tokens").insert({
        "user_id":    user_id,
        "token":      token,
        "expires_at": expire.isoformat(),
    }).execute()
    return token

def decode_access_token(token: str) -> dict:
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme)
) -> dict:
    payload = decode_access_token(credentials.credentials)
    user = get_user_by_id(payload["sub"])
    if not user or not user.get("active"):
        raise HTTPException(status_code=401, detail="User not found or deactivated")
    return user


def require_active_subscription(user: dict = Depends(get_current_user)) -> dict:
    from config import TIER_LIMITS
    from database import get_usage_this_month

    tier = user.get("tier", "free")
    limit = TIER_LIMITS.get(tier, 50)

    if limit != -1:
        used = get_usage_this_month(user["id"])
        if used >= limit:
            raise HTTPException(
                status_code=402,
                detail=f"Monthly limit reached ({used}/{limit}). Please upgrade your plan."
            )
    return user
