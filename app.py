import os
import logging
import requests
from flask import Flask, request, abort
from supabase import create_client
from flask_cors import CORS

# --------------------------------------------------
# Auth helpers
# --------------------------------------------------

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

# --------------------------------------------------
# App + DB
# --------------------------------------------------

app = Flask(__name__)

CORS(
    app,
    resources={r"/*": {"origins": [
        "http://localhost:5173",
        "http://localhost:5174",
        # later:
        # "https://m.yourdomain.com",
        # "https://c.yourdomain.com",
    ]}},
    allow_headers=[
        "Content-Type",
        "X-M-Key",
        "X-C-Key",
    ],
)

logging.basicConfig(level=logging.INFO)

supabase = create_client(
    os.environ["SUPABASE_URL"],
    os.environ["SUPABASE_SERVICE_KEY"],
)

MAILGUN_DOMAIN = os.environ["MAILGUN_DOMAIN"]
MAILGUN_API_KEY = os.environ["MAILGUN_API_KEY"]
FROM_EMAIL = os.environ.get("FROM_EMAIL", "Campaign <campaign@mg.renewableenergyx.com>")
REPLY_DOMAIN = os.environ.get("REPLY_DOMAIN", "mg.renewableenergyx.com")  # reply+TOKEN@REPLY_DOMAIN

# --------------------------------------------------
# Helpers
# --------------------------------------------------

def clean_body(text: str) -> str:
    for marker in ("\nOn ", "\nFrom:", "\n>"):
        if marker in text:
            text = text.split(marker, 1)[0]
    return text.strip()

def create_token() -> str:
    return os.urandom(8).hex()

def get_campaign(campaign_id: str):
    return (
        supabase
        .table("campaigns")
        .select("id,name,status,subject,body,recipient_count,created_at")
        .eq("id", campaign_id)
        .single()
        .execute()
    )

def send_one_email(to_email: str, subject: str, body: str, token: str):
    # Reply-To contains token (NOT customer email)
    reply_to = f"reply+{token}@{REPLY_DOMAIN}"

    resp = requests.post(
        f"https://api.mailgun.net/v3/{MAILGUN_DOMAIN}/messages",
        auth=("api", MAILGUN_API_KEY),
        data={
            "from": FROM_EMAIL,
            "to": to_email,
            "subject": subject,
            "text": body,
            "h:Reply-To": reply_to,
        },
        timeout=15,
    )
    resp.raise_for_status()

def compute_recipient_count(campaign_id: str) -> int:
    # Count recipients for campaign
    # Supabase python client doesn't have a clean count helper in older versions,
    # so we fetch small projection and use len.
    res = (
        supabase
        .table("recipients")
        .select("id")
        .eq("campaign_id", campaign_id)
        .execute()
    )
    return len(res.data or [])

# --------------------------------------------------
# Routes
# --------------------------------------------------

@app.route("/", methods=["GET"])
def home():
    return "OK", 200

# ------------------ Mailgun webhook ------------------

@app.route("/mailgun", methods=["POST"])
def mailgun_webhook():
    logging.info("Mailgun webhook received")

    recipient = request.form.get("recipient", "")
    token = None

    # recipient looks like reply+TOKEN@mg.domain.com
    if recipient.startswith("reply+") and "@" in recipient:
        token = recipient.split("reply+", 1)[1].split("@", 1)[0]

    subject = request.form.get("subject")
    body = request.form.get("body-plain")
    message_id = request.form.get("Message-Id")

    if not token or not body or not message_id:
        return "OK", 200

    # Deduplicate by Message-Id
    existing = (
        supabase
        .table("replies")
        .select("id")
        .eq("message_id", message_id)
        .limit(1)
        .execute()
    )
    if existing.data:
        logging.info("Duplicate reply ignored")
        return "OK", 200

    # Map token -> campaign_id
    row = (
        supabase
        .table("campaign_tokens")
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

# ------------------ Replies ------------------

@app.route("/replies", methods=["GET"])
def list_replies():
    require_viewer()
    res = (
        supabase
        .table("replies")
        .select("token, body, subject, campaign_id, received_at")
        .order("received_at", desc=True)
        .limit(200)
        .execute()
    )
    return res.data

# ------------------ Campaigns ------------------

@app.route("/campaigns", methods=["GET"])
def list_campaigns():
    require_viewer()

    res = (
        supabase
        .table("campaigns")
        .select("id,name,created_at,status,subject,body,recipient_count")
        .order("created_at", desc=True)
        .execute()
    )
    return res.data

@app.route("/campaigns", methods=["POST"])
def create_campaign():
    # Campaigns are created by C (operator)
    require_c()

    data = request.get_json(force=True)
    name = (data.get("name") or "").strip()
    if not name:
        return {"error": "name required"}, 400

    res = supabase.table("campaigns").insert({
        "name": name,
        "status": "draft",
        "recipient_count": 0,
    }).execute()

    return res.data[0]

@app.route("/campaigns/<campaign_id>/content", methods=["POST"])
def set_campaign_content(campaign_id):
    # Content is set by C
    require_c()

    data = request.get_json(force=True)
    subject = (data.get("subject") or "").strip()
    body = (data.get("body") or "").strip()

    if not subject or not body:
        return {"error": "subject and body required"}, 400

    # If already sent, do not allow edits
    camp = get_campaign(campaign_id)
    if not camp.data:
        return {"error": "campaign not found"}, 404
    if camp.data.get("status") == "sent":
        return {"error": "campaign already sent"}, 400

    # Mark "ready" if audience not yet uploaded, otherwise "audience"
    next_status = "ready"
    if (camp.data.get("recipient_count") or 0) > 0:
        next_status = "audience"

    supabase.table("campaigns").update({
        "subject": subject,
        "body": body,
        "status": next_status,
    }).eq("id", campaign_id).execute()

    return {"status": "updated", "campaign_id": campaign_id, "campaign_status": next_status}

# ------------------ Audience upload (M) ------------------

@app.route("/campaigns/<campaign_id>/upload-emails", methods=["POST"])
def upload_emails(campaign_id):
    # Email list is uploaded by M (data owner)
    require_m()

    camp = get_campaign(campaign_id)
    if not camp.data:
        return {"error": "campaign not found"}, 404
    if camp.data.get("status") == "sent":
        return {"error": "campaign already sent"}, 400

    data = request.get_json(force=True)
    emails = data.get("emails")

    if not emails or not isinstance(emails, list):
        return {"error": "emails must be a list"}, 400

    # Safety limit (adjust later)
    if len(emails) > 1000:
        return {"error": "max 1000 emails per request"}, 400

    # For simplicity, replace audience for this campaign each upload:
    supabase.table("recipients").delete().eq("campaign_id", campaign_id).execute()
    supabase.table("campaign_tokens").delete().eq("campaign_id", campaign_id).execute()

    sent_map = []
    for email in emails:
        e = (email or "").strip()
        if not e or "@" not in e:
            continue

        token = create_token()

        # store token mapping for webhook lookup
        supabase.table("campaign_tokens").insert({
            "token": token,
            "campaign_id": campaign_id,
        }).execute()

        # store recipient email ONLY for backend sending
        supabase.table("recipients").insert({
            "campaign_id": campaign_id,
            "token": token,
            "email": e,
        }).execute()

        # return mapping to M (but do not expose via any GET to C)
        sent_map.append({"email": e, "token": token})

    count = len(sent_map)

    # Update campaign status based on whether content exists
    has_content = bool((camp.data.get("subject") or "").strip())
    next_status = "audience" if has_content and count > 0 else "draft" if not has_content else "ready"

    supabase.table("campaigns").update({
        "recipient_count": count,
        "status": next_status,
    }).eq("id", campaign_id).execute()

    return {
        "campaign_id": campaign_id,
        "count": count,
        "status": next_status,
        "map": sent_map,  # M-only
    }, 200

@app.route("/campaigns/<campaign_id>/token-map", methods=["GET"])
def get_token_map(campaign_id):
    # M can re-download map later
    require_m()

    res = (
        supabase
        .table("recipients")
        .select("email,token")
        .eq("campaign_id", campaign_id)
        .execute()
    )
    return {
        "campaign_id": campaign_id,
        "map": res.data or [],
    }

# ------------------ Send campaign (C) ------------------

@app.route("/campaigns/<campaign_id>/send", methods=["POST"])
def send_campaign(campaign_id):
    # Sending is done by C
    require_c()

    camp = get_campaign(campaign_id)
    if not camp.data:
        return {"error": "campaign not found"}, 404

    if camp.data.get("status") == "sent":
        return {"error": "campaign already sent"}, 400

    subject = (camp.data.get("subject") or "").strip()
    body = (camp.data.get("body") or "").strip()
    if not subject or not body:
        return {"error": "campaign content not set"}, 400

    # Must have recipients uploaded
    recs = (
        supabase
        .table("recipients")
        .select("email,token")
        .eq("campaign_id", campaign_id)
        .execute()
    )
    recipients = recs.data or []
    if len(recipients) == 0:
        return {"error": "no recipients uploaded"}, 400

    sent = 0
    failed = 0

    for r in recipients:
        try:
            send_one_email(
                to_email=r["email"],
                subject=subject,
                body=body,
                token=r["token"],
            )
            sent += 1
        except Exception as e:
            logging.warning("Send failed for one recipient: %s", str(e))
            failed += 1

    # Mark sent (even if some failed; you can change policy later)
    supabase.table("campaigns").update({
        "status": "sent",
    }).eq("id", campaign_id).execute()

    return {
        "campaign_id": campaign_id,
        "sent": sent,
        "failed": failed,
        "status": "sent",
    }, 200

# Backward-compat alias: old endpoint name, now C-only and ignores emails
@app.route("/campaigns/<campaign_id>/tokenize-and-send", methods=["POST"])
def tokenize_and_send_alias(campaign_id):
    return send_campaign(campaign_id)

# --------------------------------------------------

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
