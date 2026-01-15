Setup Used:
VS Code + Cursor AI + ChatGPT for planning and debugging.

## Webhook Service (FastAPI)

This project is a production-style FastAPI backend for receiving webhook messages, storing them idempotently in SQLite, and exposing APIs for message listing and basic analytics.

It includes HMAC-SHA256 signature validation, structured JSON logging, health checks, optional Prometheus metrics, and full Docker support.

## Message Schema

POST /webhook expects the following JSON body:

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
	-	message_id: must be a non-empty string
	-	from / to: must follow E.164 format (+ followed by digits)
	-	ts: ISO-8601 UTC format with Z suffix
	-	text: optional, maximum 4096 characters

## API Endpoints

- **POST `/webhook`**
  Headers
	-	X-Signature: HMAC-SHA256 hex digest of the raw request body using WEBHOOK_SECRET

Body
	-	Same as the message schema above

Response
	-	200 OK → {"status": "ok"}
	-	The endpoint is idempotent on message_id
(Duplicate messages are ignored at the database level but still return success)

- **GET `/messages`**
 Query Parameters
	-	limit (1–100, default: 50)
  	-	offset (default: 0)
	-	from (optional) – filter by sender
	-	since (optional) – filter by ts >= since
	-	q (optional) – substring search in text
  - **Ordering**
    	-	Messages are always sorted by:
            ts ASC, message_id ASC
  - **Response**
          {
        "items": [...],
        "total": 120,
        "limit": 50,
        "offset": 0
      }

- **GET `/stats`**
      Returns basic analytics:
    	-	total_messages
    	-	senders_count
    	-	messages_per_sender (top 10 senders)
    	-	first_message_ts
    	-	last_message_ts

- **Health Checks**
  	-	/health/live
        Always returns {"status": "ok"} if the service is running.
	-   /health/ready
        Checks that:
	          -	WEBHOOK_SECRET is set
	          -	The SQLite database is reachable
Returns 503 if the service is not ready.

- **Metrics**
 	-	/metrics
        Exposes Prometheus metrics when ENABLE_METRICS=true.
        When disabled, the endpoint returns an empty 404-style response.

    Available counters:
	     -	webhook_requests_total{result="..."}
	     -	messages_stored_total

## Logging

The service uses structured JSON logging (via app/logging_utils.py) and logs all requests to stdout.

Each request log contains:
	-	ts (UTC timestamp)
	-	level
	-	request_id
	-	method
	-	path
	-	status
	-	latency_ms

For /webhook requests, an additional log entry includes:
	-	message_id
	-	dup (true if the message was a duplicate)
	-	result (e.g. ok, invalid_signature, invalid_payload)

## Configuration

All configuration is handled through environment variables (see app/config.py):
	-	WEBHOOK_SECRET – required for HMAC verification
	-	DATABASE_URL – default: /data/app.db
	-	LOG_LEVEL – default: INFO
	-	ENABLE_METRICS – default: true

## SQLite Storage

- Uses the built-in `sqlite3` module with simple SQL, **no ORM**.
- Database file path defaults to `/data/app.db`.
- `messages` table:
  - `id` INTEGER PRIMARY KEY AUTOINCREMENT
  - `message_id` TEXT UNIQUE NOT NULL
  - `from_number` TEXT NOT NULL
  - `to_number` TEXT NOT NULL
  - `ts` TEXT NOT NULL
  - `text` TEXT NULL

## Running Locally (without Docker)

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

## Running with Docker

```bash
export WEBHOOK_SECRET="supersecret"
docker-compose up --build
```

- Service: `http://localhost:8000`.
- SQLite file is stored in the `app-data` volume at `/data/app.db`.

## Example Webhook Call

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

## Design Decisions

Below are some key design decisions and the reasoning behind them:

**HMAC Signature Verification**

I use HMAC-SHA256 to ensure that webhook requests actually come from a trusted sender and haven’t been modified in transit.

The sender generates a signature using a shared secret (WEBHOOK_SECRET) and the raw request body.
My server computes the same signature and compares it using hmac.compare_digest() to prevent timing attacks.

If the signatures match, the request is considered authentic.

**Idempotency Handling**

Webhooks are often retried due to network issues.
To avoid storing duplicate messages, message_id is defined as a UNIQUE field in the database.

If the same message is sent again, SQLite raises an IntegrityError.
I catch this error and return {"status": "ok"} without inserting a new row.

This ensures that duplicate webhooks do not create duplicate records.

**Pagination**

The /messages endpoint uses limit and offset for pagination.

Results are always ordered by:
    ts ASC, message_id ASC

This guarantees consistent ordering, even when multiple messages share the same timestamp.
The total count is also returned so clients can calculate the number of pages.

**Stats Computation**

The /stats endpoint uses simple SQL queries:
	-	COUNT(*) for total messages
	-	COUNT(DISTINCT from_number) for unique senders
	-	GROUP BY from_number for per-sender counts
	-	ORDER BY ts for first and last message timestamps

All queries run in a single database connection.
For large-scale systems, caching could be added later, but this approach is sufficient for the current use case.

## Testing

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pytest -vv
```

Tests cover:
	-	Webhook signature validation
	-	Message storage
	-	API responses
	-	Health checks


