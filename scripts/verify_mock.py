"""Launch a temporary mock with a random token, exercise it through .NET, then stop it."""

import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import time
import urllib.request


def main():
    root = Path(__file__).resolve().parents[1]
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env = os.environ.copy()
    env["ANKI_BRIDGE_TOKEN"] = secrets.token_urlsafe(32)
    env["ANKI_BRIDGE_URL"] = f"http://127.0.0.1:{port}/"
    env["PYTHONPATH"] = str(root / "bridge")
    server = subprocess.Popen([sys.executable, "-m", "neuro_anki", "--mock", "--port", str(port)],
                              cwd=root, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        deadline = time.monotonic() + 15
        while True:
            if server.poll() is not None:
                raise RuntimeError("The mock service exited before startup")
            try:
                with urllib.request.urlopen(env["ANKI_BRIDGE_URL"] + "health", timeout=1) as response:
                    if json.load(response)["mode"] == "mock":
                        break
            except OSError:
                pass
            if time.monotonic() > deadline:
                raise TimeoutError("The mock did not start within 15 seconds")
            time.sleep(0.1)
        subprocess.run(["dotnet", "run", "--project", "Neuro.Anki.SmokeTest", "-c", "Release", "--no-build"],
                       cwd=root, env=env, check=True, timeout=60)
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait()


if __name__ == "__main__":
    main()
