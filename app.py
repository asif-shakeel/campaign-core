from flask import Flask, request

app = Flask(__name__)

@app.route("/", methods=["GET"])
def home():
    return "OK", 200

@app.route("/mailgun", methods=["POST"])
def mailgun_webhook():
    print("=== Mailgun webhook received ===")

    # Show what keys Mailgun sends (safe)
    keys = list(request.form.keys())
    print("Form keys:", keys)

    # Show where it was sent to (important for routing)
    recipient = request.form.get("recipient")
    print("Recipient:", recipient)

    # Show a short preview of the message body
    body = request.form.get("body-plain", "")
    preview = body[:200]  # first 200 chars only
    print("Body preview:", repr(preview))

    print("=== End webhook ===")
    return "OK", 200

if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
