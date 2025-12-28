from flask import Flask, request

app = Flask(__name__)

@app.route("/", methods=["GET"])
def home():
    return "OK", 200

@app.route("/mailgun", methods=["POST"])
def mailgun_webhook():
    print("Received a POST from Mailgun")
    return "OK", 200

if __name__ == "__main__":
    # Render sets the PORT environment variable
    import os
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
