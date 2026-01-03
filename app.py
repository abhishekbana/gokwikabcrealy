from flask import Flask, request, jsonify
import os, json, uuid, datetime, requests

app = Flask(__name__)

# -------------------------------------------------------------------
# Environment / Paths
# -------------------------------------------------------------------
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

# -------------------------------------------------------------------
# Helpers
# -------------------------------------------------------------------
def log(msg):
    with open(LOG_FILE, "a") as f:
        f.write(f"{datetime.datetime.utcnow().isoformat()} | {msg}\n")

def log_ok(email, source):
    log(f"OK | {source} | {email}")

def log_error(error):
    log(f"ERROR | {error}")

def store_payload(payload, folder):
    ts = datetime.datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    fid = f"{ts}-{uuid.uuid4().hex}"
    path = f"{DATA_DIR}/{folder}/{fid}.json"
    with open(path, "w") as f:
        json.dump(payload, f, indent=2)

def mautic_upsert(payload):
    resp = requests.post(
        f"{MAUTIC_URL}/api/contacts/new",
        auth=(MAUTIC_USER, MAUTIC_PASS),
        json=payload,
        timeout=10
    )
    if resp.status_code not in (200, 201):
        raise Exception(resp.text)

def extract_products(order):
    items = order.get("line_items", [])
    names = []

    for item in items:
        name = item.get("name")
        if name:
            names.append(name)

    return ", ".join(names)

# -------------------------------------------------------------------
# GoKwik – Abandoned Cart Endpoint
# -------------------------------------------------------------------
@app.route("/", methods=["POST"])
def gokwik_ingest():
    payload = request.json
    store_payload(payload, "incoming")

    try:
        carts = payload.get("carts", [])
        if not carts:
            raise Exception("No carts in payload")

        cart = carts[0]
        customer = cart.get("customer", {})

        email = customer.get("email")
        phone = customer.get("phone")

        if not email:
            raise Exception("Missing email")

        contact = {
            "email": email,
            "firstname": customer.get("firstname", ""),
            "lastname": customer.get("lastname", ""),
            "mobile": phone,

            "lead_source": "gokwik",

            "cart_url": cart.get("abc_url"),
            "cart_value": cart.get("total_price"),
            "tags": ["source:gokwik", "intent:abandoned-cart"],
            "drop_stage": cart.get("drop_stage"),

            "last_abandoned_cart_date": datetime.datetime.utcnow().isoformat(),

            # IMPORTANT: reset coupon flag on every abandon
            "abc_cupon5_sent": False,
            "abc1": False,
            "abc2": False,
            "abc3": False
        }

        mautic_upsert(contact)
        store_payload(contact, "forwarded")
        log_ok(email, "gokwik")

        return jsonify({"status": "ok"}), 200

    except Exception as e:
        store_payload(payload, "errors")
        log_error(str(e))
        return jsonify({"error": str(e)}), 400

# -------------------------------------------------------------------
# WooCommerce – Order Update Endpoint
# -------------------------------------------------------------------
@app.route("/woocommerce", methods=["POST"])
def woocommerce_webhook():
    data = request.json
    store_payload(data, "incoming")

    try:
        status = data.get("status")
        if status not in ["processing", "completed"]:
            return jsonify({"ignored_status": status}), 200

        billing = data.get("billing", {})
        email = billing.get("email")
        phone = billing.get("phone")

        if not email:
            raise Exception("No email in WooCommerce payload")

        order_date = data.get("date_created_gmt")

        mautic_payload = {
            "last_order_id": str(data.get("id")),
            "email": email,
            "mobile": phone,

            "has_purchased": True,
            "last_order_date": order_date,

            "last_product_names": extract_products(data),

            "city": billing.get("city"),
            "state": billing.get("state"),
            "pincode": billing.get("postcode"),

            "lead_source": "woocommerce",
            "tags": ["source:website", "type:website-customer"],

            # Safe to send — Mautic will keep first value
            "first_order_date": order_date,
            "abc_cupon5_sent": True,
            "abc1": True,
            "abc2": True,
            "abc3": True
        }

        mautic_upsert(mautic_payload)
        store_payload(mautic_payload, "forwarded")
        log_ok(email, "woocommerce")

        return jsonify({"status": "order synced"}), 200

    except Exception as e:
        store_payload(data, "errors")
        log_error(str(e))
        return jsonify({"error": str(e)}), 400

# -------------------------------------------------------------------
# Run
# -------------------------------------------------------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
