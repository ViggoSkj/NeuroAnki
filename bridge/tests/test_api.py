from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient
import pytest

from neuro_anki.app import create_app
from neuro_anki.backend import InMemoryBackend

TOKEN = "test-token-not-a-real-secret"


@pytest.fixture
def api():
    backend = InMemoryBackend()
    with TestClient(create_app(backend, TOKEN), headers={"Authorization": "Bearer " + TOKEN}) as client:
        yield client, backend


def add(client, front="hej", deck="Swedish", tags=None, model="Basic", fields=None):
    response = client.post("/v1/notes", json={"model": model, "deck": deck,
                           "fields": fields or {"Front": front, "Back": "hello"}, "tags": tags or []})
    assert response.status_code == 201, response.text
    return response.json()


def test_health_and_authentication(api):
    client, _ = api
    assert client.get("/health").json() == {"status": "ok", "mode": "mock"}
    assert client.get("/v1/notes", headers={"Authorization": "Bearer wrong"}).status_code == 401
    with TestClient(create_app(InMemoryBackend(), TOKEN)) as anonymous:
        assert anonymous.get("/v1/decks").status_code == 401
    assert client.get("/v1/notes").headers["cache-control"] == "no-store"


def test_create_read_update_delete_and_cards(api):
    client, _ = api
    note = add(client, tags=["vocab"])
    assert client.get(f"/v1/notes/{note['id']}").json() == note
    cards = client.get("/v1/cards", params={"note_id": note["id"]}).json()["items"]
    assert len(cards) == 1 and cards[0]["deck"] == "Swedish"
    response = client.patch(f"/v1/notes/{note['id']}", json={"fields": {"Back": "hi"}})
    assert response.json()["fields"]["Back"] == "hi"
    assert response.json()["tags"] == ["vocab"]
    response = client.patch(f"/v1/notes/{note['id']}", json={"fields": {"Back": ""}, "tags": []})
    assert response.json()["fields"]["Back"] == "" and response.json()["tags"] == []
    assert client.delete(f"/v1/notes/{note['id']}").status_code == 204
    assert client.get(f"/v1/notes/{note['id']}").status_code == 404
    assert client.get("/v1/cards").json()["items"] == []


def test_pagination_filters_and_literal_deck_names(api):
    client, _ = api
    for text in ["a", "b", "c"]:
        add(client, text, deck="100%_Study::Words", tags=["word"])
    add(client, "other", deck="100xyStudy", tags=["wordy"])
    first = client.get("/v1/notes", params={"limit": 2, "deck": "100%_Study", "tag": "word"}).json()
    assert len(first["items"]) == 2 and first["next_cursor"]
    second = client.get("/v1/notes", params={"limit": 2, "after_id": first["next_cursor"], "deck": "100%_Study", "tag": "word"}).json()
    assert len(second["items"]) == 1 and second["next_cursor"] is None
    assert client.get("/v1/notes", params={"tag": "wor"}).json()["items"] == []
    assert client.get("/v1/notes", params={"deck": "x' OR 1=1 --"}).json()["items"] == []


def test_cloze_generates_multiple_cards_and_notes_are_not_duplicated(api):
    client, _ = api
    note = add(client, model="Cloze", fields={"Text": "{{c1::hej}} {{c2::då}}", "Back Extra": ""})
    cards = client.get("/v1/cards", params={"note_id": note["id"], "limit": 1}).json()
    assert len(cards["items"]) == 1 and cards["next_cursor"]
    more = client.get("/v1/cards", params={"after_id": cards["next_cursor"]}).json()
    assert len(more["items"]) == 1
    assert len(client.get("/v1/notes", params={"deck": "Swedish"}).json()["items"]) == 1


def test_review_history_cursors_and_signed_intervals(api):
    client, backend = api
    note = add(client)
    card = client.get("/v1/cards", params={"note_id": note["id"]}).json()["items"][0]
    backend.seed_review(card["id"], 1700000000001)
    backend.seed_review(card["id"], 1700000000002, ease=4, interval=4)
    first = client.get("/v1/reviews", params={"limit": 1, "card_id": card["id"]}).json()
    assert first["items"][0]["last_interval"] == -60
    second = client.get("/v1/reviews", params={"after_id": first["next_cursor"]}).json()
    assert second["items"][0]["ease"] == 4 and second["next_cursor"] is None


def test_mock_sync_never_uses_network(api, monkeypatch):
    client, _ = api
    monkeypatch.setattr("neuro_anki.backend.SyncServer.__call__", lambda *a, **kw: pytest.fail("Unexpected network call"))
    assert client.post("/v1/sync", json={"mode": "incremental"}).json() == {"status": "mock_noop", "mode": "incremental"}
    assert client.post("/v1/sync", json={"mode": "download"}).json()["status"] == "mock_noop"
    assert client.post("/v1/sync", json={"mode": "upload"}).status_code == 422


@pytest.mark.parametrize("query", [{"limit": 0}, {"limit": 1001}, {"after_id": -1}])
def test_invalid_paging_is_rejected(api, query):
    assert api[0].get("/v1/notes", params=query).status_code == 422


def test_invalid_note_and_missing_resources(api):
    client, _ = api
    assert client.post("/v1/notes", json={"fields": {"Wrong": "x"}}).status_code == 422
    assert client.post("/v1/notes", json={"fields": {"Front": ""}}).status_code == 422
    assert client.post("/v1/notes", json={"fields": {"Front": "x"}, "tags": ["two words"]}).status_code == 422
    assert client.patch("/v1/notes/1", json={"fields": {"Back": "x"}}).status_code == 404
    assert client.delete("/v1/notes/1").status_code == 404
    note = add(client)
    assert client.patch(f"/v1/notes/{note['id']}", json={"fields": {"Wrong": "x"}}).status_code == 422


def test_each_mock_has_an_isolated_in_memory_database():
    first, second = InMemoryBackend(), InMemoryBackend()
    try:
        from neuro_anki.models import CreateNote
        first.create_note(CreateNote(fields={"Front": "private"}))
        assert len(first.notes(0, 100)["items"]) == 1
        assert second.notes(0, 100)["items"] == []
        assert first.collection.path.name == ":memory:"
    finally:
        first.close()
        second.close()


def test_concurrent_requests_allocate_unique_ids(api):
    client, _ = api
    with ThreadPoolExecutor(max_workers=8) as executor:
        notes = list(executor.map(lambda i: add(client, str(i)), range(24)))
    assert len({note["id"] for note in notes}) == 24
    assert len(client.get("/v1/cards").json()["items"]) == 24
