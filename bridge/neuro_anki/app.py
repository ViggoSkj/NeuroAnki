from contextlib import asynccontextmanager
import secrets
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
import httpx

from .backend import AnkiBackend, FullSyncNeeded, SyncNotConfigured
from .models import Card, CreateNote, Deck, Note, Page, Review, SyncRequest, SyncResult, UpdateNote


def create_app(backend: AnkiBackend, token: str) -> FastAPI:
    if len(token) < 16 or any(c.isspace() or ord(c) < 32 for c in token):
        raise ValueError("A bridge token of at least 16 characters without whitespace is required")

    @asynccontextmanager
    async def lifespan(app):
        try:
            yield
        finally:
            backend.close()

    app = FastAPI(title="NeuroAnki", version="0.1.0", lifespan=lifespan)
    app.state.backend = backend
    bearer = HTTPBearer(auto_error=False)

    def authorize(credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]):
        if credentials is None or not secrets.compare_digest(credentials.credentials.encode(), token.encode()):
            raise HTTPException(401, detail={"code": "unauthorized"}, headers={"WWW-Authenticate": "Bearer"})

    # Queries can include deck/tag names. Responses must not be cached by shared proxies.
    @app.middleware("http")
    async def no_cache(request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/health")
    def health():
        return {"status": "ok", "mode": backend.mode}

    secured = [Depends(authorize)]
    cursor = Annotated[int, Query(ge=0)]
    page_size = Annotated[int, Query(ge=1, le=1000)]
    positive_id = Annotated[int | None, Query(gt=0)]

    @app.get("/v1/decks", dependencies=secured, response_model=list[Deck])
    def decks():
        return backend.decks()

    @app.get("/v1/notes", dependencies=secured, response_model=Page[Note])
    def notes(after_id: cursor = 0, limit: page_size = 100, deck: str | None = None, tag: str | None = None):
        return backend.notes(after_id, limit, deck, tag)

    @app.get("/v1/notes/{note_id}", dependencies=secured, response_model=Note)
    def get_note(note_id: int):
        try:
            return backend.get_note(note_id)
        except KeyError:
            raise HTTPException(404, detail={"code": "note_not_found"}) from None

    @app.post("/v1/notes", status_code=201, dependencies=secured, response_model=Note)
    def create_note(request: CreateNote):
        try:
            if set(request.fields) & {"model", "deck", "tags"}:
                raise ValueError("Field name conflicts with a fastanki argument")
            return backend.create_note(request)
        except (KeyError, ValueError):
            raise HTTPException(422, detail={"code": "invalid_note", "message": "Check model, field names and required field content"}) from None

    @app.patch("/v1/notes/{note_id}", dependencies=secured, response_model=Note)
    def update_note(note_id: int, request: UpdateNote):
        get_note(note_id)
        try:
            if set(request.fields) & {"note", "tags", "add_tags"}:
                raise ValueError("Field name conflicts with a fastanki argument")
            return backend.update_note(note_id, request)
        except (KeyError, ValueError):
            raise HTTPException(422, detail={"code": "invalid_note", "message": "Check field names and values"}) from None

    @app.delete("/v1/notes/{note_id}", status_code=204, dependencies=secured)
    def delete_note(note_id: int):
        try:
            backend.delete_note(note_id)
        except KeyError:
            raise HTTPException(404, detail={"code": "note_not_found"}) from None
        return Response(status_code=204)

    @app.get("/v1/cards", dependencies=secured, response_model=Page[Card])
    def cards(after_id: cursor = 0, limit: page_size = 100, note_id: positive_id = None, deck: str | None = None):
        return backend.cards(after_id, limit, note_id, deck)

    @app.get("/v1/reviews", dependencies=secured, response_model=Page[Review])
    def reviews(after_id: cursor = 0, limit: page_size = 100, card_id: positive_id = None):
        return backend.reviews(after_id, limit, card_id)

    @app.post("/v1/sync", dependencies=secured, response_model=SyncResult)
    def sync(request: SyncRequest):
        try:
            return backend.sync(request.mode)
        except FullSyncNeeded:
            raise HTTPException(409, detail={"code": "full_sync_required", "message": "Resolve the collection mismatch or explicitly download the remote collection"}) from None
        except SyncNotConfigured:
            raise HTTPException(503, detail={"code": "sync_not_configured"}) from None
        except httpx.HTTPError:
            raise HTTPException(502, detail={"code": "upstream_error"}) from None
        except Exception:
            # fastanki may include upstream messages in arbitrary exceptions. Do not expose them.
            raise HTTPException(502, detail={"code": "sync_failed"}) from None

    return app
