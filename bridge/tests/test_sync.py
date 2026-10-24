from pathlib import Path

from fastapi.testclient import TestClient
from fastanki.syncer import FullSyncRequired, SanityCheckFailed
import httpx
import pytest

from neuro_anki.app import create_app
from neuro_anki.backend import FastAnkiBackend, FullSyncNeeded
from neuro_anki.models import CreateNote


def test_real_adapter_reopens_persistent_collection(tmp_path):
    path = tmp_path / "collection.anki2"
    first = FastAnkiBackend(path)
    note = first.create_note(CreateNote(fields={"Front": "hej", "Back": "hello"}))
    first.close()
    second = FastAnkiBackend(path)
    try:
        assert second.get_note(note["id"])["fields"]["Front"] == "hej"
    finally:
        second.close()


@pytest.mark.parametrize("error", [FullSyncRequired(), SanityCheckFailed()])
def test_incremental_sync_does_not_fall_back_to_full_replacement(tmp_path, monkeypatch, error):
    backend = FastAnkiBackend(tmp_path / "collection.anki2", host_key="test-key")
    try:
        def failed(server):
            raise error
        monkeypatch.setattr(backend.collection, "sync_collection", failed)
        monkeypatch.setattr(backend.collection, "full_download", lambda *a: pytest.fail("Implicit download"))
        monkeypatch.setattr(backend.collection, "full_upload", lambda *a: pytest.fail("Unexpected upload"))
        with pytest.raises(FullSyncNeeded):
            backend.sync("incremental")
    finally:
        backend.close()


def test_explicit_download_backs_up_collection_first(tmp_path, monkeypatch):
    backend = FastAnkiBackend(tmp_path / "collection.anki2", host_key="test-key")
    try:
        backend.create_note(CreateNote(fields={"Front": "keep me"}))
        def download(server):
            backups = list(tmp_path.glob("*.backup"))
            assert len(backups) == 1 and backups[0].stat().st_size > 0
        monkeypatch.setattr(backend.collection, "full_download", download)
        assert backend.sync("download")["status"] == "downloaded"
    finally:
        backend.close()


def test_failed_download_restores_local_data_and_keeps_api_usable(tmp_path, monkeypatch):
    backend = FastAnkiBackend(tmp_path / "collection.anki2", host_key="test-key")
    try:
        note = backend.create_note(CreateNote(fields={"Front": "keep me"}))
        def broken_download(server):
            backend.collection.close()
            backend.collection.path.write_bytes(b"invalid database")
            raise ValueError("invalid upstream download")
        monkeypatch.setattr(backend.collection, "full_download", broken_download)
        with pytest.raises(ValueError):
            backend.sync("download")
        assert backend.get_note(note["id"])["fields"]["Front"] == "keep me"
    finally:
        backend.close()


def test_sync_authentication_and_errors_are_not_exposed(tmp_path, monkeypatch):
    backend = FastAnkiBackend(tmp_path / "collection.anki2")
    with TestClient(create_app(backend, "test-token-long-enough"), headers={"Authorization": "Bearer test-token-long-enough"}) as client:
        assert client.post("/v1/sync", json={}).status_code == 503
        backend.server.hkey = "key"
        def fail(server):
            raise httpx.HTTPError("secret-key and sensitive upstream content")
        monkeypatch.setattr(backend.collection, "sync_collection", fail)
        response = client.post("/v1/sync", json={})
        assert response.status_code == 502
        assert "secret-key" not in response.text
        def mismatch(server):
            raise FullSyncRequired()
        monkeypatch.setattr(backend.collection, "sync_collection", mismatch)
        response = client.post("/v1/sync", json={})
        assert response.status_code == 409 and response.json()["detail"]["code"] == "full_sync_required"
