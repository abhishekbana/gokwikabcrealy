from flask import Flask, request, jsonify
import os
import json
import uuid
import logging
import requests
from datetime import datetime

# -------------------------------------------------------------------
# App & Logging Setup (TOP LEVEL)
# -------------------------------------------------------------------
app = Flask(__name__)

LOG_FILE = os.getenv("LOG_FILE", "/data/logs/relay.log")
os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)

logger = logging.getLogger("webhook-relay")

file_handler = logging.FileHandler(LOG_FILE)
file_handler.setFormatter(
    logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s")
)
logger.addHandler(file_handler)

# -------------------------------------------------------------------
# Environment / Configuration
# -------------------------------------------------------------------
DATA_DIR = os.getenv("DATA_DIR", "/data/storage")

MAUTIC_URL = os.getenv("MAUTIC_URL")
MAUTIC_USER = os.getenv("MAUTIC_USER")
MAUTIC_PASS = os.getenv("MAUTIC_PASS")
FAST2SMS_API_KEY = os.getenv("FAST2SMS_API_KEY")

FAST2SMS_WHATSAPP_URL = "https://www.fast2sms.com/dev/whatsapp"
MESSAGE_ID = "10360"
PHONE_NUMBER_ID = "978701858655665"

INCOMING = f"{DATA_DIR}/incoming"
FORWARDED = f"{DATA_DIR}/forwarded"
ERRORS = f"{DATA_DIR}/errors"
WHATSAPP_SENT = f"{DATA_DIR}/whatsapp_sent"

for d in (INCOMING, FORWARDED, ERRORS, WHATSAPP_SENT):
    os.makedirs(d, exist_ok=True)

# -------------------------------------------------------------------
# Helper Functions
# -------------------------------------------------------------------
def store_payload(payload, folder):
    ts = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    fid = f"{ts}-{uuid.uuid4().hex}"
    path = f"{DATA_DIR}/{folder}/{fid}.json"
    with open(path, "w") as f:
        json.dump(payload, f, indent=2)


def mautic_upsert(payload):
    resp = requests.post(
        f"{MAUTIC_URL}/api/contacts/new",
        auth=(MAUTIC_USER, MAUTIC_PASS),
        json=payload,
        timeout=10,
    )
    if resp.status_code not in (200, 201):
        raise Exception(resp.text)


def extract_products(order):
    items = order.get("line_items", [])
    names = [i.get("name") for i in items if i.get("name")]
    return ", ".join(names)


def whatsapp_already_sent(order_id):
    return os.path.exists(f"{WHATSAPP_SENT}/order_{order_id}.flag")


def mark_whatsapp_sent(order_id):
    with open(f"{WHATSAPP_SENT}/order_{order_id}.flag", "w") as f:
        f.write(datetime.utcnow().isoformat())


# -------------------------------------------------------------------
# WhatsApp – Order Processing Utility Message
# -------------------------------------------------------------------
def send_whatsapp_order_processing(order):
    order_id = str(order.get("id"))

    if whatsapp_already_sent(order_id):
        logger.info(f"WhatsApp skipped | Already sent | Order {order_id}")
        return {"status": "skipped", "reason": "duplicate"}

    try:
        billing = order.get("billing", {})

        customer_name = billing.get("first_name", "").strip()
        order_date_raw = order.get("date_created")
        order_date = datetime.fromisoformat(
            order_date_raw.replace("Z", "")
        ).strftime("%d/%m/%Y")

        order_value = f"Rs. {int(float(order.get('total', 0))):,}/-"
        payment_type = "COD" if order.get("payment_method") == "cod" else "Prepaid"

        mobile = billing.get("phone", "")
        mobile = "".join(filter(str.isdigit, mobile))[-10:]

        if len(mobile) != 10:
            logger.warning(
                f"WhatsApp NOT sent | Invalid mobile | Order {order_id} | {mobile}"
            )
            return {"status": "skipped", "reason": "invalid_mobile"}

        variables = "|".join(
            [customer_name, order_id, order_date, order_value, payment_type]
        )

        payload = {
            "authorization": FAST2SMS_API_KEY,
            "message_id": MESSAGE_ID,
            "phone_number_id": PHONE_NUMBER_ID,
            "numbers": mobile,
            "variables_values": variables,
        }

        from urllib.parse import urlencode
        raw_url = f"{FAST2SMS_WHATSAPP_URL}?{urlencode(payload)}"
        logger.info("WhatsApp RAW URL | %s", raw_url)

        # response = requests.post(
        #     FAST2SMS_WHATSAPP_URL, data=payload, timeout=10
        # )
        headers = {"authorization": FAST2SMS_API_KEY}

        payload_no_auth = payload.copy()
        payload_no_auth.pop("authorization", None)
        response = requests.post(
            FAST2SMS_WHATSAPP_URL,
            headers=headers,
            data=payload_no_auth,
            timeout=10
        )

        if response.status_code == 200:
            logger.info(f"WhatsApp sent | Order {order_id} | {mobile}")
            mark_whatsapp_sent(order_id)
        else:
            logger.error(
                f"WhatsApp FAILED | Order {order_id} | "
                f"{response.status_code} | {response.text}"
            )

        return response.json()

    except Exception as e:
        logger.exception(f"WhatsApp ERROR | Order {order_id} | {str(e)}")
        return {"status": "error", "message": str(e)}


# -------------------------------------------------------------------
# GoKwik – Abandoned Cart Webhook
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

        if not email:
            raise Exception("Missing email")

        contact = {
            "email": email,
            "firstname": customer.get("firstname", ""),
            "lastname": customer.get("lastname", ""),
            "mobile": customer.get("phone"),
            "lead_source": "gokwik",
            "cart_url": cart.get("abc_url"),
            "cart_value": cart.get("total_price"),
            "drop_stage": cart.get("drop_stage"),
            "last_abandoned_cart_date": datetime.utcnow().isoformat(),
            "tags": ["source:gokwik", "intent:abandoned-cart"],
            "abc_cupon5_sent": False,
            "abc1": False,
            "abc2": False,
            "abc3": False,
        }

        mautic_upsert(contact)
        store_payload(contact, "forwarded")
        logger.info(f"Mautic OK | gokwik | {email}")

        return jsonify({"status": "ok"}), 200

    except Exception as e:
        store_payload(payload, "errors")
        logger.error(f"GoKwik ERROR | {str(e)}")
        return jsonify({"error": str(e)}), 400


# -------------------------------------------------------------------
# WooCommerce – Order Webhook
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
            "email": email,
            "mobile": phone,
            "last_order_id": str(data.get("id")),
            "last_order_date": order_date,
            "first_order_date": order_date,
            "has_purchased": True,
            "last_product_names": extract_products(data),
            "city": billing.get("city"),
            "pincode": billing.get("postcode"),
            "lead_source": "woocommerce",
            "tags": ["source:website", "type:website-customer"],
            "abc_cupon5_sent": True,
            "abc1": True,
            "abc2": True,
            "abc3": True,
        }

        mautic_upsert(mautic_payload)
        store_payload(mautic_payload, "forwarded")
        logger.info(f"Mautic OK | woocommerce | {email}")

        # WhatsApp should NEVER break order sync
        try:
            send_whatsapp_order_processing(data)
        except Exception:
            logger.error("WhatsApp failed but order sync succeeded")

        return jsonify({"status": "order synced"}), 200

    except Exception as e:
        store_payload(data, "errors")
        logger.error(f"WooCommerce ERROR | {str(e)}")
        return jsonify({"error": str(e)}), 400


# -------------------------------------------------------------------
# Run
# -------------------------------------------------------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
