import logging
from flask import Flask, request

app = Flask(__name__)

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
    body = request.form.get("body-plain")
    if body:
        logging.info("Body preview: %r", body[:200])
    else:
        logging.info("No body-plain field found")

    # Log raw payload size (this is the key diagnostic)
    raw = request.get_data()
    logging.info("Raw payload length: %d bytes", len(raw))

    logging.info("=== End webhook ===")
    return "OK", 200

if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
