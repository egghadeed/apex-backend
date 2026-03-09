# apex_backend/routers/user.py
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from auth_utils import get_current_user
from database import update_user, get_usage_this_month, get_db
from config import TIER_LIMITS, TIER_DEFAULT_MODEL, TIER_SELECTABLE_MODELS, VISION_CAPABLE

router = APIRouter()


@router.get("/profile")
def profile(user: dict = Depends(get_current_user)):
    tier  = user.get("tier", "free")
    used  = get_usage_this_month(user["id"])
    limit = TIER_LIMITS.get(tier, 50)
    selectable = TIER_SELECTABLE_MODELS.get(tier, [TIER_DEFAULT_MODEL.get(tier)])
    return {
        "id":    user["id"],
        "email": user["email"],
        "tier":  tier,
        "model": TIER_DEFAULT_MODEL.get(tier),
        "available_models": [
            {"id": m, "vision": VISION_CAPABLE.get(m, True)}
            for m in selectable
        ],
        "usage": {
            "used":      used,
            "limit":     limit,
            "unlimited": limit == -1,
        },
        "has_billing": bool(user.get("stripe_customer_id")),
    }


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


@router.post("/change-password")
def change_password(body: ChangePasswordRequest, user: dict = Depends(get_current_user)):
    from auth_utils import verify_password, hash_password
    if not verify_password(body.current_password, user["hashed_password"]):
        raise HTTPException(401, "Current password is incorrect")
    if len(body.new_password) < 8:
        raise HTTPException(400, "New password must be at least 8 characters")
    update_user(user["id"], {"hashed_password": hash_password(body.new_password)})
    return {"detail": "Password updated"}


@router.delete("/account")
def delete_account(user: dict = Depends(get_current_user)):
    """Soft delete — marks account inactive, keeps data for billing records."""
    update_user(user["id"], {"active": False})
    # Cancel Stripe subscription if exists
    if user.get("stripe_subscription_id"):
        try:
            import stripe
            from config import STRIPE_SECRET_KEY
            stripe.api_key = STRIPE_SECRET_KEY
            stripe.Subscription.cancel(user["stripe_subscription_id"])
        except Exception:
            pass
    return {"detail": "Account deactivated"}
