# apex_backend/main.py
import sys
import os

# Ensure project root is on path so routers can import database, config, auth_utils
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

load_dotenv()

# ── Validate required env vars before anything else ──────────────────────────
REQUIRED = ["JWT_SECRET", "SUPABASE_URL", "SUPABASE_SERVICE_KEY", "ANTHROPIC_API_KEY"]
missing = [k for k in REQUIRED if not os.getenv(k)]
if missing:
    print(f"STARTUP ERROR: Missing required environment variables: {', '.join(missing)}", flush=True)
    sys.exit(1)

print("Environment OK, loading routers...", flush=True)

from routers import auth, chat, billing, user

print("Routers loaded, starting app...", flush=True)

app = FastAPI(title="Apex API", version="1.0.0")

origins = os.getenv("CORS_ORIGINS", "*").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router,    prefix="/auth",    tags=["auth"])
app.include_router(chat.router,    prefix="/chat",    tags=["chat"])
app.include_router(billing.router, prefix="/billing", tags=["billing"])
app.include_router(user.router,    prefix="/user",    tags=["user"])

@app.get("/health")
def health():
    return {"status": "ok", "version": "1.0.0"}

print("App ready.", flush=True)
