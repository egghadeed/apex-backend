import hashlib
import hmac
import json
import os
import resend
from fastapi import APIRouter, HTTPException, Request, Header

from database import get_db
from config import GITHUB_WEBHOOK_SECRET, RESEND_API_KEY, DASHBOARD_URL, FROM_EMAIL

router = APIRouter()

resend.api_key = RESEND_API_KEY


@router.get("/version")
async def get_version():
    """Public endpoint — no auth required."""
    db = get_db()
    result = (db.table("app_versions")
               .select("version, download_url, release_notes")
               .order("created_at", desc=True)
               .limit(1)
               .execute())
    if not result.data:
        raise HTTPException(status_code=404, detail="No version found")
    return result.data[0]


@router.post("/webhook/github")
async def github_webhook(
    request: Request,
    x_hub_signature_256: str = Header(None),
    x_github_event: str = Header(None),
):
    body = await request.body()

    if not x_hub_signature_256:
        raise HTTPException(status_code=401, detail="Missing signature")

    expected = "sha256=" + hmac.new(
        GITHUB_WEBHOOK_SECRET.encode(),
        body,
        hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(expected, x_hub_signature_256):
        raise HTTPException(status_code=401, detail="Invalid signature")

    if x_github_event != "release":
        return {"status": "ignored"}

    payload = json.loads(body)
    if payload.get("action") != "published":
        return {"status": "ignored"}

    release = payload["release"]
    version = release["tag_name"].lstrip("v")
    release_notes = release.get("body", "")

    assets = release.get("assets", [])
    exe_asset = next((a for a in assets if a["name"].endswith(".exe")), None)
    if not exe_asset:
        return {"status": "no exe asset found"}

    download_url = exe_asset["browser_download_url"]

    db = get_db()
    db.table("app_versions").insert({
        "version": version,
        "download_url": download_url,
        "release_notes": release_notes,
    }).execute()

    await send_update_emails(version, download_url)

    return {"status": "ok", "version": version}


async def send_update_emails(version: str, download_url: str):
    db = get_db()
    result = db.table("users").select("email").execute()
    emails = [row["email"] for row in result.data if row.get("email")]

    if not emails:
        return

    for email in emails:
        resend.Emails.send({
            "from": FROM_EMAIL,
            "to": email,
            "subject": f"Apex Assistant v{version} is available",
            "html": f"""
            <div style="font-family: monospace; background: #0a0a0b; color: #f0f0f2; padding: 32px; max-width: 480px;">
                <p style="color: #00D4FF; font-size: 11px; letter-spacing: 0.2em;">// UPDATE AVAILABLE</p>
                <h2 style="color: #f0f0f2; font-size: 24px; margin: 8px 0;">Apex Assistant v{version}</h2>
                <p style="color: #70707a; font-size: 13px; line-height: 1.8;">
                    A new version of Apex Assistant is ready to download.
                </p>
                <a href="{DASHBOARD_URL}"
                   style="display: inline-block; margin-top: 24px; padding: 12px 28px;
                          background: #00D4FF; color: #0a0a0b; text-decoration: none;
                          font-size: 11px; font-weight: 600; letter-spacing: 0.1em;">
                    DOWNLOAD FROM DASHBOARD →
                </a>
                <p style="color: #3a3a42; font-size: 10px; margin-top: 32px;">
                    You're receiving this because you have an Apex Assistant account.
                </p>
            </div>
            """,
        })
