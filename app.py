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

def extract_products(order):
    items = order.get("line_items", [])

    product_names = []
    categories = set()

    for item in items:
        name = item.get("name")
        if name:
            product_names.append(name)

        # If category exists in meta (optional)
        for meta in item.get("meta_data", []):
            if meta.get("key") == "category":
                categories.add(meta.get("value"))

    return {
        "last_product_names": ", ".join(product_names),
        "last_product_category": list(categories)[0] if categories else None
    }

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
            "drop_stage": cart.get("drop_stage"),
            # IMPORTANT: reset coupon flag for every abandoned cart
            "abc_cupon5_sent": False,
            "abc1": False,
            "abc2": False,
            "abc3": False
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

@app.route("/woocommerce", methods=["POST"])
def woocommerce_webhook():
    data = request.json

    try:
        status = data.get("status")

        # Only process paid / completed orders
        if status not in ["processing", "completed"]:
            return jsonify({"ignored_status": status}), 200

        billing = data.get("billing", {})
        email = billing.get("email")
        phone = billing.get("phone")

        if not email:
            raise Exception("No email in WooCommerce payload")

        order_id = data.get("id")
        total = data.get("total")
        order_date = data.get("date_created_gmt")

        products = extract_products(data)

        mautic_payload = {
            "email": email,
            "mobile": phone,

            # Purchase markers
            "has_purchased": True,
            "last_order_date": order_date,

            # Set only once logic handled by Mautic (or optional relay check)
            "first_order_date": order_date,

            # Commerce intelligence
            "last_product_names": products["last_product_names"],
            "last_product_category": products["last_product_category"],

            # Location
            "city": billing.get("city"),
            "state": billing.get("state"),
            "pincode": billing.get("postcode"),

            # Attribution
            "lead_source": "woocommerce"
        }

        mautic_upsert(mautic_payload)

        log_ok(email, "woocommerce")
        store_payload(data, "forwarded")

        return jsonify({"status": "order synced"}), 200

    except Exception as e:
        log_error(str(e), data)
        store_payload(data, "errors")
        return jsonify({"error": str(e)}), 400

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
