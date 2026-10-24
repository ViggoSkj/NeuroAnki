# NeuroAnki

A .NET API client for a Python **fastanki** bridge, plus a mock HTTP API backed by an **in-memory SQLite Anki collection**. Use the same `IAnkiClient`, DTOs and HTTP routes in production and tests; switch only the bridge URL.

```text
Your app -> Neuro.Networking.Anki -> HTTP bridge -> fastanki -> AnkiWeb <-> AnkiDroid
                                    mock mode -> in-memory Anki database
```

## Projects

| Component | Purpose |
| --- | --- |
| `Neuro.Networking.Anki` | .NET Standard 2.1 typed client, cancellation, optional Waher sniffers and typed API errors |
| `bridge/neuro_anki` | FastAPI service with a dedicated disk collection in real mode and an isolated SQLite `:memory:` collection in mock mode |
| `Neuro.Networking.Anki.Tests` | Offline .NET transport and contract tests (.NET 10) |
| `bridge/tests` | Offline API, real-adapter and in-memory database tests |
| `Neuro.Anki.SmokeTest` | Exercises the .NET client against the running mock HTTP service |

## Setup

Requires Python 3.11+ and the .NET 10 SDK to build/test. The client library itself targets .NET Standard 2.1. From the repository root on Windows:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r bridge/requirements.lock
.\.venv\Scripts\python.exe -m pip install --no-deps -e ./bridge
dotnet build NeuroAnki.sln -c Release
```

On Linux/macOS use `.venv/bin/python` instead. `requirements.lock` records the exact Python dependencies verified for this repository. `fastanki` is pinned to 0.0.3 because the bridge depends on that version's collection schema. Its upstream project labels itself alpha; validate upgrades against the offline suite and a disposable sync account before changing the pin.

## Run the mock API

```powershell
$env:ANKI_BRIDGE_TOKEN = 'local-development-token-change-me'
.\.venv\Scripts\python.exe -m neuro_anki --mock --port 8766
```

Open `http://127.0.0.1:8766/docs` for the OpenAPI explorer. Click **Authorize** and enter the bridge token. The mock starts empty, supports notes/decks/cards/review queries and note CRUD, and generates real Basic/Cloze cards through fastanki. Everything is held in memory and disappears on shutdown. Each backend instance owns a separate database.

Mock sync returns `mock_noop`, makes no network calls, and does not erase the fixture database. It does not simulate FSRS, schedule reviews or record study answers. Python tests can call `InMemoryBackend.seed_review(...)` to add review-history fixtures; this helper is not exposed as an HTTP endpoint.

## Run the real bridge

```powershell
$env:ANKI_BRIDGE_TOKEN = 'use-a-long-random-service-token'
$env:ANKIWEB_USERNAME = 'your-account-email'
$env:ANKIWEB_PASSWORD = 'your-account-password'
.\.venv\Scripts\python.exe -m neuro_anki --collection ./data/collection.anki2 --port 8766
```

Alternatively provide `ANKIWEB_HOST_KEY` from your own secret manager and omit username/password. The service retains its host key only in process memory and does not write an `auth.json`. Credentials stay in the Python bridge and are never sent to your .NET app or accepted through HTTP request bodies. To use a self-hosted Anki sync server, set `ANKI_SYNC_ENDPOINT` to a trusted HTTPS URL, or loopback HTTP URL for local testing.

Use one process/worker per dedicated collection and one account per bridge instance. Never point this service at a collection currently used by Anki Desktop. All requests and synchronization are serialized around that collection. Place the service behind HTTPS and appropriate network access controls before using it remotely. It binds to `127.0.0.1` by default; `/health` and API documentation reveal no collection contents. All data and sync routes require the bearer token. No CORS origins are enabled by default.

## .NET example

```csharp
using Neuro.Networking.Anki;

using IAnkiClient anki = new AnkiClient(
    "http://127.0.0.1:8766/",
    Environment.GetEnvironmentVariable("ANKI_BRIDGE_TOKEN")!);

AnkiNote note = await anki.CreateNoteAsync(new CreateNote
{
    Deck = "Languages::Swedish",
    Fields = new() { ["Front"] = "hej", ["Back"] = "hello" },
    Tags = new() { "vocabulary" }
});

Page<AnkiNote> notes = await anki.GetNotesAsync(Deck: "Languages");
Page<AnkiCard> cards = await anki.GetCardsAsync(NoteId: note.Id);
Page<AnkiReview> reviews = await anki.GetReviewsAsync();
```

All operations accept cancellation tokens. An injected `HttpClient` remains caller-owned. The built-in handler disables redirects and uses a ten-minute timeout; cancel long operations explicitly when appropriate. Cancellation stops the HTTP wait, but a sync already running in the Python process may still finish. The client never retries writes or sync automatically.

Anki field names retain their original casing (`Front`, `Back`, custom fields). Updates patch supplied fields only; omitted/null tags preserve existing tags and `[]` clears them. Some custom field names collide with fastanki's Python method arguments and are rejected with HTTP 422. Card `due` is a raw Anki value whose units depend on its queue/type. Review intervals preserve Anki's signed representation: positive values are days and negative values are seconds.

## HTTP API

All collection routes are under `/v1`. JSON uses snake_case and list endpoints return `{ "items": [...], "next_cursor": 123 }`.

| Method and path | Behavior |
| --- | --- |
| `GET /health` | Backend mode and liveness; no authentication required |
| `GET /v1/decks` | Deck IDs and names |
| `GET /v1/notes` | Notes, optional exact tag and deck/subdeck filters |
| `GET /v1/notes/{id}` | One note |
| `POST /v1/notes` | Create a note and its generated cards |
| `PATCH /v1/notes/{id}` | Patch fields and optionally replace tags |
| `DELETE /v1/notes/{id}` | Delete a note/cards and record Anki deletion markers |
| `GET /v1/cards` | Scheduling metadata, optional note ID or deck/subdeck filter |
| `GET /v1/reviews` | Review history, optional card ID filter |
| `POST /v1/sync` | Explicit incremental sync or explicit full download |

Paging parameters are `after_id` (exclusive, default 0) and `limit` (1–1000, default 100). Use `next_cursor` as the next `after_id` until it is null. The server requests one extra row so an exact-size final page does not imply a nonexistent next page. This is ID pagination, **not a change feed or a transactionally consistent multi-page snapshot**. Notes/cards edited later keep their IDs, so rescan them to detect updates. Reconcile deletions only after a complete successful scan with writes/sync paused if consistency matters. Review IDs can serve as a cursor for new historical entries; reset it after replacing the underlying collection.

## Sync semantics

- `await anki.SyncAsync()` sends `{ "mode": "incremental" }`. Pending local edits and deletions can be uploaded, and remote changes are downloaded. This is bidirectional sync, not a read-only import.
- Schema mismatch/sanity failures return HTTP **409** with error code `full_sync_required`. They never trigger an implicit full upload or download.
- To initialize from an existing account, explicitly call `await anki.DownloadCollectionAsync()`. It sends `{ "mode": "download" }`, backs up the local collection, then replaces it with the remote collection. A failed download restores the backup and reopens the local collection. Review local edits before doing this. Backup files contain private collection data and are not automatically deleted.
- There is no full-upload route. Resolve schema conflicts in Anki or explicitly download after deciding which data to retain.
- Mock mode returns `{ "status": "mock_noop", "mode": "..." }` for both supported modes.

This version covers collection data, not media transfer, card HTML rendering or review submission/scheduling. Sounds/images referenced in fields are preserved as field content; their files are not downloaded. It uses fastanki's sync protocol implementation rather than AnkiWeb's unofficial website `/svc/*` endpoints.

`AnkiApiException` contains the HTTP status and structured error response. Sniffers record method/path and status only, excluding tokens, filter queries, note contents and response bodies. Access logs are disabled in the bundled launcher. Reverse proxies must follow the same policy if you want queries kept out of logs.

## Verify

```powershell
dotnet test NeuroAnki.sln -c Release
.\.venv\Scripts\python.exe -m pytest bridge/tests -q
.\.venv\Scripts\python.exe scripts/verify_mock.py
```

The final command launches a temporary mock on a loopback port with a generated token, runs the .NET HTTP smoke test, and stops the service in a `finally` block. Tests do not require AnkiWeb credentials and never contact a live account. Real AnkiWeb authentication/synchronization must be validated separately against a disposable account before deployment.

## Upstream

- [fastanki repository](https://github.com/AnswerDotAI/fastanki) (Apache-2.0)
- [Collection API](https://answerdotai.github.io/fastanki/collection.html)
- [Sync protocol implementation](https://answerdotai.github.io/fastanki/syncer.html)

The bridge wraps the installed fastanki package; it does not vendor upstream source. A small Windows timezone compatibility shim is applied only when `time.localtime()` lacks `tm_gmtoff` in fastanki 0.0.3.
