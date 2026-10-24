"""Both backends use fastanki's collection methods and schema, not a second fake implementation."""

from datetime import datetime
from pathlib import Path
import shutil
import threading
import time
from urllib.parse import urlsplit

from fastanki.collection import Collection
from fastanki import schema
from fastanki.syncer import FullSyncRequired, SanityCheckFailed, SyncServer

from .models import CreateNote, UpdateNote


# fastanki 0.0.3 assumes tm_gmtoff, which CPython on Windows does not provide.
# Use the same local UTC offset on that platform without changing its collection format.
if not hasattr(time.localtime(), "tm_gmtoff"):
    schema.day_offset = lambda: -int(datetime.now().astimezone().utcoffset().total_seconds() // 60)


class FullSyncNeeded(Exception):
    pass


class SyncNotConfigured(Exception):
    pass


class InMemoryCollection(Collection):
    def __init__(self):
        self.path = Path(":memory:")
        self.con = schema.create_collection(":memory:")


class AnkiBackend:
    def __init__(self, collection: Collection, mode: str):
        self.collection = collection
        self.mode = mode
        self.lock = threading.RLock()

    def close(self):
        with self.lock:
            self.collection.close()

    def decks(self):
        with self.lock:
            return [dict(id=row[0], name=row[1].replace("\x1f", "::"))
                    for row in self.collection.q("select id, name from decks order by name")]

    @staticmethod
    def note(note):
        return dict(id=note.id, guid=note.guid, model=note.nt.name,
                    modified_seconds=note.mod, fields=note.fields, tags=note.tags)

    def get_note(self, note_id):
        with self.lock:
            return self.note(self.collection.get_note(note_id))

    @staticmethod
    def page(items, limit):
        return dict(items=items[:limit], next_cursor=items[limit - 1]["id"] if len(items) > limit else None)

    @staticmethod
    def deck_filter(deck, clauses, values, column="d.name"):
        if deck is not None:
            # Escape LIKE metacharacters so deck names such as 100% match literally.
            name = deck.replace("::", "\x1f")
            escaped = name.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            clauses.append(f"({column}=? or {column} like ? escape '\\')")
            values.extend([name, escaped + "\x1f%"])

    def notes(self, after_id, limit, deck=None, tag=None):
        with self.lock:
            clauses, values = ["n.id> ?"], [after_id]
            if deck is not None:
                subclauses, subvalues = [], []
                self.deck_filter(deck, subclauses, subvalues)
                clauses.append("exists(select 1 from cards c join decks d on d.id=c.did where c.nid=n.id and " + subclauses[0] + ")")
                values.extend(subvalues)
            if tag is not None:
                # Match a complete tag, never arbitrary SQL or a substring of another tag.
                clauses.append("instr(' ' || trim(n.tags) || ' ', ?) > 0")
                values.append(" " + tag + " ")
            ids = self.collection.q("select n.id from notes n where " + " and ".join(clauses) + " order by n.id limit ?", *values, limit + 1)
            return self.page([self.get_note(row[0]) for row in ids], limit)

    def create_note(self, request: CreateNote):
        with self.lock:
            return self.note(self.collection.add(model=request.model, deck=request.deck, tags=request.tags, **request.fields))

    def update_note(self, note_id, request: UpdateNote):
        with self.lock:
            return self.note(self.collection.update_note(note_id, tags=request.tags, **request.fields))

    def delete_note(self, note_id):
        with self.lock:
            self.collection.get_note(note_id)  # Missing notes consistently return 404.
            self.collection.remove_notes(note_id)

    def cards(self, after_id, limit, note_id=None, deck=None):
        with self.lock:
            clauses, values = ["c.id>?"], [after_id]
            if note_id is not None:
                clauses.append("c.nid=?")
                values.append(note_id)
            self.deck_filter(deck, clauses, values)
            rows = self.collection.q("select c.id,c.nid,c.did,d.name,c.ord,c.mod,c.type,c.queue,c.due,c.ivl,c.reps,c.lapses "
                                     "from cards c join decks d on d.id=c.did where " + " and ".join(clauses) + " order by c.id limit ?", *values, limit + 1)
            names = ("id", "note_id", "deck_id", "deck", "ordinal", "modified_seconds", "type", "queue", "due", "interval", "repetitions", "lapses")
            items = [dict(zip(names, row)) for row in rows]
            for item in items:
                item["deck"] = item["deck"].replace("\x1f", "::")
            return self.page(items, limit)

    def reviews(self, after_id, limit, card_id=None):
        with self.lock:
            clauses, values = ["id>?"], [after_id]
            if card_id is not None:
                clauses.append("cid=?")
                values.append(card_id)
            rows = self.collection.q("select id,cid,ease,ivl,lastIvl,factor,time,type from revlog where " +
                                     " and ".join(clauses) + " order by id limit ?", *values, limit + 1)
            names = ("id", "card_id", "ease", "interval", "last_interval", "factor", "duration_ms", "type")
            return self.page([dict(zip(names, row)) for row in rows], limit)


class InMemoryBackend(AnkiBackend):
    """Isolated in-memory SQLite database with real Anki notes, cards and review-log tables."""

    def __init__(self):
        super().__init__(InMemoryCollection(), "mock")

    def sync(self, mode):
        # Deliberately touches no network and does not simulate Anki's scheduler.
        return dict(status="mock_noop", mode=mode)

    def seed_review(self, card_id, review_id, *, ease=3, interval=1, last_interval=-60, duration_ms=1000):
        """Test-only Python fixture API; not exposed as a production HTTP route."""
        with self.lock:
            if not self.collection.q1("select id from cards where id=?", card_id):
                raise KeyError("Card not found")
            self.collection.con.execute("insert into revlog(id,cid,usn,ease,ivl,lastIvl,factor,time,type) values(?,?,-1,?,?,?,?,?,1)",
                                        (review_id, card_id, ease, interval, last_interval, 2500, duration_ms))


class FastAnkiBackend(AnkiBackend):
    def __init__(self, path: Path, *, username=None, password=None, host_key=None, endpoint=None):
        if endpoint:
            address = urlsplit(endpoint)
            loopback = address.hostname in {"localhost", "127.0.0.1", "::1"}
            if (not address.hostname or (address.scheme != "https" and not (address.scheme == "http" and loopback))
                    or address.username or address.password or address.query or address.fragment):
                raise ValueError("The sync endpoint must use HTTPS or loopback HTTP, without credentials/query/fragment")
            endpoint = endpoint.rstrip("/") + "/"
        path = path.expanduser().resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        super().__init__(Collection.open(path), "fastanki")
        self.username, self.password = username, password
        self.server = SyncServer(endpoint=endpoint, hkey=host_key or "")

    def close(self):
        with self.lock:
            self.server.client.close()
            super().close()

    def sync(self, mode):
        with self.lock:
            if not self.server.hkey:
                if not self.username or not self.password:
                    raise SyncNotConfigured()
                self.server.login(self.username, self.password)
            if mode == "download":
                # Explicit local replacement only; keep a recoverable pre-download copy.
                self.collection.con.execute("pragma wal_checkpoint(truncate)")
                backup = self.collection.path.with_name(self.collection.path.name + "." + str(time.time_ns()) + ".backup")
                shutil.copy2(self.collection.path, backup)
                try:
                    self.collection.full_download(self.server)
                except Exception:
                    # The upstream download closes the old connection before validating its
                    # temporary database. Restore our checkpointed backup if any step fails.
                    path = self.collection.path
                    try:
                        self.collection.close()
                    except Exception:
                        pass
                    for suffix in ("-wal", "-shm"):
                        Path(str(path) + suffix).unlink(missing_ok=True)
                    shutil.copy2(backup, path)
                    self.collection = Collection.open(path)
                    raise
                return dict(status="downloaded", mode=mode)
            try:
                status = self.collection.sync_collection(self.server)
            except (FullSyncRequired, SanityCheckFailed) as error:
                raise FullSyncNeeded() from error
            return dict(status=status, mode=mode)
