# apex_backend/main.py
# FastAPI backend for Apex — handles auth, subscriptions, and Claude proxying

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
import os

load_dotenv()

from routers import auth, chat, billing, user

app = FastAPI(title="Apex API", version="1.0.0")

# CORS — allow the desktop app and website
origins = os.getenv("CORS_ORIGINS", "http://localhost:3000").split(",")
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
