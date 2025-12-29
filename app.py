# app.py
import os
import logging
import secrets
import requests
from flask import Flask, request, abort, jsonify
from flask_cors import CORS
from supabase import create_client

# ==================================================
# Auth helpers
# ==================================================

def require_m():
    if request.headers.get("X-M-Key") != os.environ.get("M_API_KEY"):
        abort(403)

def require_c():
    if request.headers.get("X-C-Key") != os.environ.get("C_API_KEY"):
        abort(403)

def require_viewer():
    if (
        request.headers.get("X-M-Key") != os.environ.get("M_API_KEY")
        and request.headers.get("X-C-Key") != os.environ.get("C_API_KEY")
    ):
        abort(403)

# ==================================================
# App + CORS + DB
# ==================================================

app = Flask(__name__)

CORS(
    app,
    resources={
        r"/*": {
            "origins": [
                "http://localhost:5173",  # M-UI dev
                "http://localhost:5174",  # C-UI dev
                # add deployed UI origins later
            ]
        }
    },
    allow_headers=["Content-Type", "X-M-Key", "X-C-Key"],
)

logging.basicConfig(level=logging.INFO)

supabase = create_client(
    os.environ["SUPABASE_URL"],
    os.environ["SUPABASE_SERVICE_KEY"],
)

MAILGUN_DOMAIN = os.environ["MAILGUN_DOMAIN"]
MAILGUN_API_KEY = os.environ["MAILGUN_API_KEY"]
FROM_EMAIL = os.environ.get(
    "FROM_EMAIL",
    "Campaign <campaign@mg.renewableenergyx.com>",
)
REPLY_DOMAIN = os.environ.get(
    "REPLY_DOMAIN",
    "mg.renewableenergyx.com",
)

# ==================================================
# Helpers
# ==================================================

def clean_body(text: str) -> str:
    # keep only the top reply (basic heuristics)
    for marker in ("\nOn ", "\nFrom:", "\n>"):
        if marker in text:
            text = text.split(marker, 1)[0]
    return text.strip()

def gen_token() -> str:
    # 16 hex chars
    return secrets.token_hex(8)

def send_one_email(to_email: str, subject: str, body: str, token: str):
    resp = requests.post(
        f"https://api.mailgun.net/v3/{MAILGUN_DOMAIN}/messages",
        auth=("api", MAILGUN_API_KEY),
        data={
            "from": FROM_EMAIL,
            "to": to_email,
            "subject": subject,
            "text": body,
            "h:Reply-To": f"reply+{token}@{REPLY_DOMAIN}",
        },
        timeout=15,
    )
    resp.raise_for_status()

def get_campaign(campaign_id: str):
    return (
        supabase
        .table("campaigns")
        .select("id,name,created_at,status,subject,body")
        .eq("id", campaign_id)
        .single()
        .execute()
    )

def insert_token_mapping(campaign_id: str) -> str:
    """
    Insert token -> campaign_id into campaign_tokens.
    Table schema (per your screenshot):
      campaign_tokens(token text PK, campaign_id uuid, created_at timestamptz)
    """
    for _ in range(8):
        token = gen_token()
        try:
            supabase.table("campaign_tokens").insert({
                "token": token,
                "campaign_id": campaign_id,
            }).execute()
            return token
        except Exception:
            # collision or transient error; retry
            continue
    raise Exception("Failed to generate unique token")

# ==================================================
# Routes
# ==================================================

@app.route("/", methods=["GET"])
def home():
    return "OK", 200

# ------------------ Mailgun webhook ------------------

@app.route("/mailgun", methods=["POST"])
def mailgun_webhook():
    recipient = request.form.get("recipient", "")
    token = None

    # recipient should look like reply+TOKEN@domain
    if recipient.startswith("reply+") and "@" in recipient:
        token = recipient.split("reply+", 1)[1].split("@", 1)[0]

    subject = request.form.get("subject")
    body = request.form.get("body-plain")
    message_id = request.form.get("Message-Id")

    if not token or not body or not message_id:
        return "OK", 200

    # Deduplicate by Message-Id
    existing = (
        supabase.table("replies")
        .select("id")
        .eq("message_id", message_id)
        .limit(1)
        .execute()
    )
    if existing.data:
        return "OK", 200

    # Map token -> campaign_id
    row = (
        supabase.table("campaign_tokens")
        .select("campaign_id")
        .eq("token", token)
        .limit(1)
        .execute()
    )
    campaign_id = row.data[0]["campaign_id"] if row.data else None

    supabase.table("replies").insert({
        "token": token,
        "campaign_id": campaign_id,
        "subject": subject,
        "body": clean_body(body),
        "message_id": message_id,
    }).execute()

    return "OK", 200

# ------------------ Replies (M or C) ------------------

@app.route("/replies", methods=["GET"])
def list_replies():
    require_viewer()
    res = (
        supabase.table("replies")
        .select("token,body,subject,campaign_id,received_at")
        .order("received_at", desc=True)
        .limit(500)
        .execute()
    )
    return jsonify(res.data or [])

# ------------------ Campaigns (both can view) ------------------

@app.route("/campaigns", methods=["GET"])
def list_campaigns():
    require_viewer()
    res = (
        supabase.table("campaigns")
        .select("id,name,created_at,status,subject,body")
        .order("created_at", desc=True)
        .execute()
    )
    return jsonify(res.data or [])

# ------------------ M: create campaign ------------------

@app.route("/campaigns", methods=["POST"])
def create_campaign():
    require_m()
    data = request.get_json(force=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name required"}), 400

    res = supabase.table("campaigns").insert({
        "name": name,
        "status": "draft",
        "subject": None,
        "body": None,
    }).execute()

    return jsonify(res.data[0]), 200

# ------------------ C: set content ------------------

@app.route("/campaigns/<campaign_id>/content", methods=["POST"])
def set_campaign_content(campaign_id):
    require_c()

    data = request.get_json(force=True) or {}
    subject = (data.get("subject") or "").strip()
    body = (data.get("body") or "").strip()

    if not subject or not body:
        return jsonify({"error": "subject and body required"}), 400

    camp = get_campaign(campaign_id)
    if not camp.data:
        return jsonify({"error": "campaign not found"}), 404
    if camp.data.get("status") == "sent":
        return jsonify({"error": "campaign already sent"}), 400

    supabase.table("campaigns").update({
        "subject": subject,
        "body": body,
        "status": "ready",
    }).eq("id", campaign_id).execute()

    return jsonify({"status": "updated", "campaign_id": campaign_id, "campaign_status": "ready"}), 200

# ------------------ M: tokenize + send (single step, NO email storage) ------------------

@app.route("/campaigns/<campaign_id>/tokenize-and-send", methods=["POST"])
def tokenize_and_send(campaign_id):
    require_m()

    camp = get_campaign(campaign_id)
    if not camp.data:
        return jsonify({"error": "campaign not found"}), 404

    if camp.data.get("status") == "sent":
        return jsonify({"error": "campaign already sent"}), 400

    if camp.data.get("status") != "ready":
        return jsonify({"error": f"campaign not ready (status={camp.data.get('status')})"}), 400

    subject = (camp.data.get("subject") or "").strip()
    body = (camp.data.get("body") or "").strip()
    if not subject or not body:
        return jsonify({"error": "campaign content not set"}), 400

    payload = request.get_json(force=True) or {}
    emails = payload.get("emails")

    if not isinstance(emails, list) or not emails:
        return jsonify({"error": "emails must be a non-empty list"}), 400

    if len(emails) > 1000:
        return jsonify({"error": "max 1000 emails per request"}), 400

    results = {"campaign_id": campaign_id, "sent": [], "failed": []}

    for email in emails:
        e = (email or "").strip()
        if not e or "@" not in e:
            results["failed"].append({"email": email, "error": "invalid_email"})
            continue

        try:
            token = insert_token_mapping(campaign_id)
            send_one_email(to_email=e, subject=subject, body=body, token=token)
            results["sent"].append({"email": e, "token": token})
        except Exception as ex:
            logging.warning("Failed to send to %s: %s", e, str(ex))
            results["failed"].append({"email": e, "error": "send_failed"})

    # Mark campaign sent (even if partial failures; adjust policy later if desired)
    supabase.table("campaigns").update({"status": "sent"}).eq("id", campaign_id).execute()

    return jsonify(results), 200

# ==================================================
# Main
# ==================================================

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
