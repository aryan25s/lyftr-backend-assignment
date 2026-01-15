import hmac
from hashlib import sha256
import json
import os
import pathlib

from fastapi.testclient import TestClient


BASE_DIR = pathlib.Path(__file__).resolve().parent.parent
TEST_DB_PATH = BASE_DIR / "test.db"

os.environ["WEBHOOK_SECRET"] = "testsecret"
os.environ["DATABASE_URL"] = str(TEST_DB_PATH)
os.environ["LOG_LEVEL"] = "DEBUG"

from app.main import app  # noqa: E402


client = TestClient(app)


def sign(body: bytes, secret: str) -> str:
    return hmac.new(secret.encode("utf-8"), body, sha256).hexdigest()


def make_message(mid: str, from_: str = "+919876543210", to: str = "+14155550100", ts: str = "2025-01-15T10:00:00Z", text: str = "Hello"):
    return {
        "message_id": mid,
        "from": from_,
        "to": to,
        "ts": ts,
        "text": text,
    }


def test_health_endpoints():
    r1 = client.get("/health/live")
    assert r1.status_code == 200
    r2 = client.get("/health/ready")
    assert r2.status_code == 200


def test_webhook_valid_and_idempotent():
    payload = make_message("m-webhook-1")
    body = json.dumps(payload).encode("utf-8")
    sig = sign(body, "testsecret")

    r1 = client.post("/webhook", data=body, headers={"X-Signature": sig})
    assert r1.status_code == 200
    assert r1.json() == {"status": "ok"}

    # Duplicate should also return ok, but be idempotent
    r2 = client.post("/webhook", data=body, headers={"X-Signature": sig})
    assert r2.status_code == 200
    assert r2.json() == {"status": "ok"}


def test_webhook_invalid_signature():
    payload = make_message("m-bad-signature")
    body = json.dumps(payload).encode("utf-8")
    sig = sign(body, "wrongsecret")

    r = client.post("/webhook", data=body, headers={"X-Signature": sig})
    assert r.status_code == 401


def test_webhook_validation_errors():
    # Invalid from number
    bad_payload = make_message("m-bad-1", from_="12345")
    body = json.dumps(bad_payload).encode("utf-8")
    sig = sign(body, "testsecret")
    r = client.post("/webhook", data=body, headers={"X-Signature": sig})
    assert r.status_code == 422

    # Invalid ts
    bad_payload2 = make_message("m-bad-2", ts="2025-01-15 10:00:00")
    body2 = json.dumps(bad_payload2).encode("utf-8")
    sig2 = sign(body2, "testsecret")
    r2 = client.post("/webhook", data=body2, headers={"X-Signature": sig2})
    assert r2.status_code == 422


def test_messages_pagination_and_filters():
    # Create several messages
    msgs = [
        make_message("m1", from_="+11111111111", text="hello world"),
        make_message("m2", from_="+11111111111", text="another message"),
        make_message("m3", from_="+22222222222", text="search me"),
    ]

    for m in msgs:
        body = json.dumps(m).encode("utf-8")
        sig = sign(body, "testsecret")
        r = client.post("/webhook", data=body, headers={"X-Signature": sig})
        assert r.status_code == 200

    # Pagination: limit 2
    r_page1 = client.get("/messages", params={"limit": 2, "offset": 0})
    assert r_page1.status_code == 200
    data1 = r_page1.json()
    assert len(data1["items"]) <= 2
    assert data1["limit"] == 2
    assert data1["offset"] == 0

    # Filter by from
    r_from = client.get("/messages", params={"from": "+11111111111"})
    assert r_from.status_code == 200
    from_data = r_from.json()
    assert all(item["from"] == "+11111111111" for item in from_data["items"])

    # q search in text
    r_q = client.get("/messages", params={"q": "search"})
    assert r_q.status_code == 200
    q_data = r_q.json()
    assert all("search" in (item.get("text") or "") for item in q_data["items"])


def test_stats_endpoint():
    r = client.get("/stats")
    assert r.status_code == 200
    stats = r.json()
    assert "total_messages" in stats
    assert "senders_count" in stats
    assert "messages_per_sender" in stats
    assert isinstance(stats["total_messages"], int)

