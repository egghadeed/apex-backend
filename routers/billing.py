# apex_backend/routers/billing.py
import stripe
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from auth_utils import get_current_user
from database import get_db, update_user
from config import (
    STRIPE_SECRET_KEY, STRIPE_WEBHOOK_SECRET,
    STRIPE_PRICE_BASIC, STRIPE_PRICE_PRO, STRIPE_PRICE_POWER
)

stripe.api_key = STRIPE_SECRET_KEY
router = APIRouter()

PRICE_TO_TIER = {
    STRIPE_PRICE_BASIC: "basic",
    STRIPE_PRICE_PRO:   "pro",
    STRIPE_PRICE_POWER: "power",
}

TIER_TO_PRICE = {
    "basic": STRIPE_PRICE_BASIC,
    "pro":   STRIPE_PRICE_PRO,
    "power": STRIPE_PRICE_POWER,
}


class CheckoutRequest(BaseModel):
    tier: str            # "basic" | "pro" | "power"
    success_url: str     # where to redirect after payment
    cancel_url:  str


@router.post("/checkout")
def create_checkout(body: CheckoutRequest, user: dict = Depends(get_current_user)):
    price_id = TIER_TO_PRICE.get(body.tier)
    if not price_id:
        raise HTTPException(400, f"Unknown tier: {body.tier}")

    # Create or reuse Stripe customer
    customer_id = user.get("stripe_customer_id")
    if not customer_id:
        customer    = stripe.Customer.create(email=user["email"], metadata={"user_id": user["id"]})
        customer_id = customer.id
        update_user(user["id"], {"stripe_customer_id": customer_id})

    session = stripe.checkout.Session.create(
        customer=customer_id,
        payment_method_types=["card"],
        line_items=[{"price": price_id, "quantity": 1}],
        mode="subscription",
        success_url=body.success_url + "?session_id={CHECKOUT_SESSION_ID}",
        cancel_url=body.cancel_url,
        metadata={"user_id": user["id"], "tier": body.tier},
    )
    return {"checkout_url": session.url}


@router.post("/portal")
def billing_portal(user: dict = Depends(get_current_user)):
    """Opens Stripe customer portal so users can manage/cancel their subscription."""
    customer_id = user.get("stripe_customer_id")
    if not customer_id:
        raise HTTPException(400, "No billing account found")

    session = stripe.billing_portal.Session.create(
        customer=customer_id,
        return_url="https://yourdomain.com/dashboard",
    )
    return {"portal_url": session.url}


@router.post("/webhook")
async def stripe_webhook(request: Request):
    """Stripe calls this when subscription events happen."""
    payload    = await request.body()
    sig_header = request.headers.get("stripe-signature", "")

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)
    except stripe.error.SignatureVerificationError:
        raise HTTPException(400, "Invalid webhook signature")

    db = get_db()

    if event["type"] == "checkout.session.completed":
        session = event["data"]["object"]
        user_id = session["metadata"].get("user_id")
        tier    = session["metadata"].get("tier")
        sub_id  = session.get("subscription")
        if user_id and tier:
            update_user(user_id, {
                "tier":                    tier,
                "stripe_subscription_id":  sub_id,
            })

    elif event["type"] in ("customer.subscription.updated", "customer.subscription.resumed"):
        sub = event["data"]["object"]
        # Find user by stripe customer id
        customer_id = sub["customer"]
        res = db.table("users").select("id").eq("stripe_customer_id", customer_id).execute()
        if res.data:
            # Map price back to tier
            price_id = sub["items"]["data"][0]["price"]["id"]
            tier     = PRICE_TO_TIER.get(price_id, "basic")
            update_user(res.data[0]["id"], {"tier": tier, "active": True})

    elif event["type"] in ("customer.subscription.deleted", "customer.subscription.paused"):
        sub         = event["data"]["object"]
        customer_id = sub["customer"]
        res = db.table("users").select("id").eq("stripe_customer_id", customer_id).execute()
        if res.data:
            update_user(res.data[0]["id"], {"tier": "free"})

    elif event["type"] == "invoice.payment_failed":
        invoice     = event["data"]["object"]
        customer_id = invoice["customer"]
        res = db.table("users").select("id").eq("stripe_customer_id", customer_id).execute()
        if res.data:
            # Grace period — don't immediately downgrade, just log
            print(f"Payment failed for user {res.data[0]["id"]}")

    return {"received": True}
