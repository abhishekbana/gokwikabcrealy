from flask import Flask, request, jsonify
import os, json, uuid, datetime, requests

app = Flask(__name__)

DATA_DIR = os.getenv("DATA_DIR", "/data/storage")
LOG_FILE = os.getenv("LOG_FILE", "/data/logs/relay.log")

MAUTIC_URL = os.getenv("MAUTIC_URL")
MAUTIC_USER = os.getenv("MAUTIC_USER")
MAUTIC_PASS = os.getenv("MAUTIC_PASS")

INCOMING = f"{DATA_DIR}/incoming"
FORWARDED = f"{DATA_DIR}/forwarded"
ERRORS = f"{DATA_DIR}/errors"

os.makedirs(INCOMING, exist_ok=True)
os.makedirs(FORWARDED, exist_ok=True)
os.makedirs(ERRORS, exist_ok=True)

def log(msg):
    with open(LOG_FILE, "a") as f:
        f.write(f"{datetime.datetime.utcnow().isoformat()} | {msg}\n")

@app.route("/", methods=["POST"])
def ingest():
    payload = request.json
    ts = datetime.datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    fid = f"{ts}-{uuid.uuid4().hex}"

    raw_path = f"{INCOMING}/{fid}.json"
    with open(raw_path, "w") as f:
        json.dump(payload, f, indent=2)

    try:
        carts = payload.get("carts", [])
        if not carts:
            raise ValueError("No carts in payload")

        cart = carts[0]
        customer = cart.get("customer", {})

        email = customer.get("email")
        phone = customer.get("phone")

        if not email:
            raise ValueError("Missing email")

        contact = {
            "email": email,
            "firstname": customer.get("firstname", ""),
            "lastname": customer.get("lastname", ""),
            "mobile": phone,
            "lead_source": "gokwik",
            "tags": ["source:gokwik", "intent:abandoned-cart"],
            "cart_url": cart.get("abc_url"),
            "cart_value": cart.get("total_price"),
            "drop_stage": cart.get("drop_stage")
        }

        mautic_resp = requests.post(
            f"{MAUTIC_URL}/api/contacts/new",
            auth=(MAUTIC_USER, MAUTIC_PASS),
            json=contact,
            timeout=10
        )

        if mautic_resp.status_code not in (200, 201):
            raise Exception(mautic_resp.text)

        with open(f"{FORWARDED}/{fid}.json", "w") as f:
            json.dump(contact, f, indent=2)

        log(f"OK | {email}")
        return jsonify({"status": "ok"}), 200

    except Exception as e:
        with open(f"{ERRORS}/{fid}.json", "w") as f:
            json.dump(payload, f, indent=2)
        log(f"ERROR | {str(e)}")
        return jsonify({"error": str(e)}), 400

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
