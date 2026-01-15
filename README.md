## Webhook Service (FastAPI)

Production-style FastAPI backend for receiving webhook messages, storing them idempotently in SQLite, and exposing message listing and analytics APIs. Includes HMAC-SHA256 validation, structured JSON logging, health checks, optional Prometheus metrics, and Docker support.

### Message Schema

`POST /webhook` expects the following JSON body:

```json
{
  "message_id": "m1",
  "from": "+919876543210",
  "to": "+14155550100",
  "ts": "2025-01-15T10:00:00Z",
  "text": "Hello"
}
```

**Validations:**
- `message_id`: non-empty string.
- `from` / `to`: E.164 format (`+` followed by digits).
- `ts`: ISO-8601 UTC with `Z` suffix.
- `text`: optional, maximum 4096 characters.

### Endpoints

- **POST `/webhook`**
  - **Headers**
    - `X-Signature`: HMAC-SHA256 hex digest of the raw request body using `WEBHOOK_SECRET`.
  - **Body**
    - As per the message schema above.
  - **Response**
    - `200 OK` with `{"status": "ok"}`.
    - Endpoint is **idempotent** on `message_id` (duplicates are ignored at the DB level but still return `{"status": "ok"}`).

- **GET `/messages`**
  - **Query params**
    - `limit` (int, 1–100; default 50).
    - `offset` (int, default 0).
    - `from` (string, optional): filter by sender.
    - `since` (string, optional): filter by `ts >= since` (ISO-8601 UTC with `Z`).
    - `q` (string, optional): substring search in `text`.
  - **Ordering**
    - Fixed ordering: `ts` ASC, then `message_id` ASC.
  - **Response**
    - `200 OK` with:
      - `items`: list of messages.
      - `total`: total count matching the filters.
      - `limit`, `offset`.

- **GET `/stats`**
  - **Response**
    - `200 OK` with:
      - `total_messages`
      - `senders_count`
      - `messages_per_sender` (top 10), each with `sender` and `count`.
      - `first_message_ts`
      - `last_message_ts`

- **Health**
  - `/health/live`: always returns `{"status": "ok"}` if the process is running.
  - `/health/ready`: checks that `WEBHOOK_SECRET` is set and the SQLite database is reachable; returns `503` if not ready.

- **Metrics**
  - `/metrics`: Prometheus metrics if `ENABLE_METRICS=true`. Returns an empty 404-style response when disabled.
  - Exposes counters:
    - `webhook_requests_total{result="..."}`.
    - `messages_stored_total`.

### Logging

Structured JSON logging to stdout via `app/logging_utils.py`.

Each **request log** includes:
- `ts`: log timestamp (UTC).
- `level`
- `request_id`
- `method`
- `path`
- `status`
- `latency_ms`

For `/webhook`, an additional log entry includes:
- `message_id`
- `dup` (boolean, true if duplicate)
- `result` (e.g. `ok`, `invalid_signature`, `invalid_payload`)

### Configuration

Configuration is via environment variables (see `app/config.py`):

- `WEBHOOK_SECRET` (required for readiness + HMAC).
- `DATABASE_URL` (default: `/data/app.db`, interpreted as a file path).
- `LOG_LEVEL` (default: `INFO`).
- `ENABLE_METRICS` (default: `true`).

### SQLite Storage

- Uses the built-in `sqlite3` module with simple SQL, **no ORM**.
- Database file path defaults to `/data/app.db`.
- `messages` table:
  - `id` INTEGER PRIMARY KEY AUTOINCREMENT
  - `message_id` TEXT UNIQUE NOT NULL
  - `from_number` TEXT NOT NULL
  - `to_number` TEXT NOT NULL
  - `ts` TEXT NOT NULL
  - `text` TEXT NULL

### Running Locally (without Docker)

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

export WEBHOOK_SECRET="supersecret"
export DATABASE_URL="./app.db"
export LOG_LEVEL="INFO"

make run
```

Service runs at `http://localhost:8000`.

### Running with Docker

```bash
export WEBHOOK_SECRET="supersecret"
docker-compose up --build
```

- Service: `http://localhost:8000`.
- SQLite file is stored in the `app-data` volume at `/data/app.db`.

### Example Webhook Call

```bash
SECRET="supersecret"
BODY='{"message_id":"m1","from":"+919876543210","to":"+14155550100","ts":"2025-01-15T10:00:00Z","text":"Hello"}'
SIG=$(python - <<EOF
import hmac, hashlib, os
secret = os.environ.get("SECRET", "supersecret")
body = os.environ.get("BODY").encode("utf-8")
print(hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest())
EOF
)

curl -X POST http://localhost:8000/webhook \
  -H "Content-Type: application/json" \
  -H "X-Signature: $SIG" \
  -d "$BODY"
```

### Design Decisions

Here's why I made some of the key choices in this project:

**HMAC Signature Verification**

I use HMAC-SHA256 to verify webhook requests because it's a standard way to ensure the data actually came from the expected sender and hasn't been tampered with. When someone sends a webhook, they compute a signature by hashing the raw request body with a shared secret (the `WEBHOOK_SECRET`). I do the same computation on my end and compare the two signatures. If they match, I know the request is authentic. This prevents attackers from just sending fake webhooks to my endpoint. I use `hmac.compare_digest()` instead of regular string comparison to avoid timing attacks.

**Idempotency Handling**

The webhook endpoint needs to be idempotent because networks are unreliable - the same webhook might get sent multiple times if there's a retry. I handle this at the database level by making `message_id` a UNIQUE constraint. When I try to insert a new message, if that `message_id` already exists, SQLite throws an `IntegrityError`. I catch that error and treat it as "this message was already processed" - so I just return `{"status": "ok"}` without actually creating a duplicate. This means if someone sends the same webhook twice (maybe because their first request timed out), I won't store it twice, but I'll still respond successfully both times.

**Pagination**

For the `/messages` endpoint, I use `limit` and `offset` because it's simple and works well for most use cases. The client tells me how many results they want (`limit`, capped at 100) and where to start (`offset`). I always order results by `ts ASC, message_id ASC` to make sure the ordering is deterministic - even if two messages have the same timestamp, they'll always come back in the same order. I also return the `total` count so the client knows how many pages there are. The downside is that if new messages are added while someone is paginating, they might see duplicates or miss items, but for this use case that's acceptable.

**Stats Computation**

The `/stats` endpoint runs a few SQL queries to gather analytics. For `total_messages` and `senders_count`, I just use `COUNT(*)` and `COUNT(DISTINCT from_number)` - straightforward aggregations. For `messages_per_sender`, I group by `from_number`, count messages per sender, order by count descending, and limit to the top 10. For the first and last message timestamps, I use `ORDER BY ts ASC, message_id ASC` (and the reverse for last) to get deterministic results. All of this happens in a single database connection, so it's reasonably fast. If this were a high-traffic system, I might want to cache these stats or compute them incrementally, but for now this simple approach works fine.

### Testing

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pytest -vv
```


