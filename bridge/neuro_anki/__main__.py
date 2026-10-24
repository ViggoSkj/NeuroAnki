import argparse
import os
from pathlib import Path

import uvicorn

from .app import create_app
from .backend import FastAnkiBackend, InMemoryBackend


def main():
    parser = argparse.ArgumentParser(description="NeuroAnki fastanki bridge or in-memory mock")
    parser.add_argument("--mock", action="store_true", help="Use an isolated in-memory Anki database; no network sync")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--collection", type=Path, default=Path("data/collection.anki2"), help="Dedicated bridge collection, never a live Anki Desktop database")
    args = parser.parse_args()
    token = os.environ.get("ANKI_BRIDGE_TOKEN", "")
    if len(token) < 16 or any(c.isspace() or ord(c) < 32 for c in token):
        parser.error("Set ANKI_BRIDGE_TOKEN to at least 16 characters without whitespace")
    if args.mock:
        backend = InMemoryBackend()
    else:
        backend = FastAnkiBackend(args.collection,
                                  username=os.environ.get("ANKIWEB_USERNAME"),
                                  password=os.environ.get("ANKIWEB_PASSWORD"),
                                  host_key=os.environ.get("ANKIWEB_HOST_KEY"),
                                  endpoint=os.environ.get("ANKI_SYNC_ENDPOINT"))
    # One worker owns one collection. Disable access logs, which would record deck/tag queries.
    uvicorn.run(create_app(backend, token), host=args.host, port=args.port, access_log=False)


if __name__ == "__main__":
    main()
