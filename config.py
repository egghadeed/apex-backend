# apex_backend/config.py
import os
from dotenv import load_dotenv

load_dotenv()

JWT_SECRET                  = os.getenv("JWT_SECRET")
if not JWT_SECRET:
    raise ValueError("JWT_SECRET environment variable must be set")
JWT_ALGORITHM               = os.getenv("JWT_ALGORITHM", "HS256")
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "60"))
REFRESH_TOKEN_EXPIRE_DAYS   = int(os.getenv("REFRESH_TOKEN_EXPIRE_DAYS", "30"))

SUPABASE_URL         = os.getenv("SUPABASE_URL", "")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

STRIPE_SECRET_KEY      = os.getenv("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET  = os.getenv("STRIPE_WEBHOOK_SECRET", "")
STRIPE_PRICE_BASIC     = os.getenv("STRIPE_PRICE_BASIC", "")
STRIPE_PRICE_PRO       = os.getenv("STRIPE_PRICE_PRO", "")
STRIPE_PRICE_POWER     = os.getenv("STRIPE_PRICE_POWER", "")

GITHUB_WEBHOOK_SECRET = os.getenv("GITHUB_WEBHOOK_SECRET", "")
RESEND_API_KEY        = os.getenv("RESEND_API_KEY", "")
DASHBOARD_URL         = os.getenv("DASHBOARD_URL", "https://apex-assistant.vercel.app/dashboard")
FROM_EMAIL            = os.getenv("FROM_EMAIL", "Apex Assistant <updates@apexassistant.app>")

# Per-tier message limits (per month). -1 = unlimited
TIER_LIMITS = {
    "free":  50,
    "basic": 500,
    "pro":   -1,
    "power": -1,
}

# Claude model per tier
TIER_MODELS = {
    "free":  "claude-haiku-4-5-20251001",
    "basic": "claude-haiku-4-5-20251001",
    "pro":   "claude-sonnet-4-20250514",
    "power": "claude-opus-4-20250514",
}
