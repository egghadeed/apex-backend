# apex_backend/database.py
from supabase import create_client, Client
from config import SUPABASE_URL, SUPABASE_SERVICE_KEY

_client: Client | None = None

def get_db() -> Client:
    global _client
    if _client is None:
        _client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
    return _client


# ── User helpers ──────────────────────────────────────────────────────────────

def get_user_by_email(email: str) -> dict | None:
    db = get_db()
    res = db.table("users").select("*").eq("email", email).maybe_single().execute()
    return res.data if res else None

def get_user_by_id(user_id: str) -> dict | None:
    db = get_db()
    res = db.table("users").select("*").eq("id", user_id).maybe_single().execute()
    return res.data if res else None

def create_user(email: str, hashed_password: str) -> dict:
    db = get_db()
    res = db.table("users").insert({
        "email":           email,
        "hashed_password": hashed_password,
        "tier":            "free",
        "message_count":   0,
        "active":          True,
    }).execute()
    return res.data[0]

def update_user(user_id: str, fields: dict) -> dict:
    db = get_db()
    res = db.table("users").update(fields).eq("id", user_id).execute()
    return res.data[0]


# ── Usage helpers ─────────────────────────────────────────────────────────────

def get_usage_this_month(user_id: str) -> int:
    """Return message count for the current calendar month."""
    from datetime import datetime
    db = get_db()
    month_start = datetime.now().replace(day=1, hour=0, minute=0, second=0).isoformat()
    res = (db.table("usage_log")
             .select("id", count="exact")
             .eq("user_id", user_id)
             .gte("created_at", month_start)
             .execute())
    return res.count or 0

def log_usage(user_id: str, input_tokens: int, output_tokens: int, model: str):
    db = get_db()
    db.table("usage_log").insert({
        "user_id":       user_id,
        "input_tokens":  input_tokens,
        "output_tokens": output_tokens,
        "model":         model,
    }).execute()


# ── Supabase schema (run once) ─────────────────────────────────────────────────
# Paste this into Supabase SQL editor:
#
# create table users (
#   id               uuid primary key default gen_random_uuid(),
#   email            text unique not null,
#   hashed_password  text not null,
#   tier             text not null default 'free',
#   stripe_customer_id text,
#   stripe_subscription_id text,
#   active           boolean default true,
#   created_at       timestamptz default now()
# );
#
# create table usage_log (
#   id            uuid primary key default gen_random_uuid(),
#   user_id       uuid references users(id),
#   input_tokens  int,
#   output_tokens int,
#   model         text,
#   created_at    timestamptz default now()
# );
#
# create table refresh_tokens (
#   id         uuid primary key default gen_random_uuid(),
#   user_id    uuid references users(id),
#   token      text unique not null,
#   expires_at timestamptz not null,
#   created_at timestamptz default now()
# );
