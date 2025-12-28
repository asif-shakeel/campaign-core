import logging
from flask import Flask, request
import os
from supabase import create_client


import requests
import os

def send_test_email(to_email: str, token: str):
    response = requests.post(
        f"https://api.mailgun.net/v3/{os.environ['MAILGUN_DOMAIN']}/messages",
        auth=("api", os.environ["MAILGUN_API_KEY"]),
        data={
            "from": "Campaign <campaign@mg.renewableenergyx.com>",
            "to": to_email,
            "subject": "Test campaign email",
            "text": "Hello!\n\nThis is a test campaign email.\n\nReply to this message.",
            "h:Reply-To": f"reply+{token}@mg.renewableenergyx.com",
        },
        timeout=10,
    )

    response.raise_for_status()

app = Flask(__name__)
supabase = create_client(
    os.environ["SUPABASE_URL"],
    os.environ["SUPABASE_SERVICE_KEY"],
)


# Configure logging so Render shows it
logging.basicConfig(level=logging.INFO)

@app.route("/", methods=["GET"])
def home():
    return "OK", 200

@app.route("/mailgun", methods=["POST"])
def mailgun_webhook():
    logging.info("=== Mailgun webhook received ===")

    # Log content type
    logging.info("Content-Type: %s", request.content_type)

    # Log form keys
    logging.info("Form keys: %s", list(request.form.keys()))

    # Log recipient
    logging.info("Recipient: %s", request.form.get("recipient"))

    # Log body preview if present
    recipient = request.form.get("recipient", "")

    token = None
    if recipient.startswith("reply+") and "@" in recipient:
        token = recipient.split("reply+", 1)[1].split("@", 1)[0]

    logging.info("Reply token: %s", token)


    # Log raw payload size (this is the key diagnostic)
    raw = request.get_data()
    logging.info("Raw payload length: %d bytes", len(raw))

    logging.info("=== End webhook ===")


    subject = request.form.get("subject")
    body = request.form.get("body-plain")

    if token and body:
        supabase.table("replies").insert({
            "token": token,
            "body": body,
            "subject": subject,
        }).execute()



    return "OK", 200

@app.route("/replies", methods=["GET"])
def list_replies():
    res = (
        supabase
        .table("replies")
        .select("token, body, subject, received_at")
        .order("received_at", desc=True)
        .limit(50)
        .execute()
    )

    return res.data


# @app.route("/send-test", methods=["POST"])
# def send_test():
#     token = "testtoken123"
#     send_test_email(
#         to_email=os.environ.get("TEST_EMAIL"),
#         token=token,
#     )
#     return {"status": "sent", "token": token}

if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
