# GoKwik → Mautic Abandoned Cart Relay (ABCRelay)

ABCRelay is a lightweight, Docker-based webhook relay designed to capture **GoKwik abandoned cart events** and forward them into **Mautic** for segmentation and marketing automation.

It acts as a **buffer and translator** between GoKwik and Mautic, without adding load to the production e-commerce server.

---

## Why This Exists

GoKwik:
- Captures abandoned cart data
- Pushes a fixed JSON payload to a webhook
- Payload structure cannot be modified

Mautic:
- Has reserved field names (e.g. `source`)
- Works best with clean, normalized API input
- Should not be directly exposed to third-party webhooks

ABCRelay solves this by:
- Receiving GoKwik payloads unchanged
- Persisting all incoming data for audit/debug
- Normalizing and mapping fields
- Sending clean contact data to Mautic via API

---

## Architecture
```
GoKwik Webhook:
↓
ABCRelay (Docker Container)
↓
• Store raw payload (audit trail)
• Normalize & map fields
• Forward to Mautic API
↓
Mautic (Contacts / Segments)
```

---

## Features

- Stateless, Docker-first design
- Accepts GoKwik payloads without modification
- Stores **all payloads** (incoming / forwarded / errors)
- Creates or updates contacts in Mautic
- Adds lead source and intent tags
- Designed for home server / TrueNAS deployment

---

## Directory Structure

```text
abcrelay/
├── app.py                 # Main webhook handler
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── .env                   # Environment variables (not committed)
├── logs/
│   └── relay.log
└── storage/
    ├── incoming/          # Raw GoKwik payloads
    ├── forwarded/         # Successfully forwarded payloads
    └── errors/            # Failed or invalid payloads
```

---

## Webhook Endpoint

```POST /
Content-Type: application/json
```

## Environment Variables

Create a .env file (do not commit this file):
```
MAUTIC_BASE_URL=https://mautic.example.com
MAUTIC_USERNAME=api_user
MAUTIC_PASSWORD=api_password
```
## Running Locally
```
docker compose up --build
```
Service will be available at:

http://localhost:5555


## Logging & Storage
```
storage/incoming/ → raw webhook payloads
storage/forwarded/ → payloads sent to Mautic
storage/errors/ → invalid or failed payloads
logs/relay.log → processing log
```
## Security Notes

No authentication (intentional for early stage). Designed to run behind an Nginx reverse proxy.
