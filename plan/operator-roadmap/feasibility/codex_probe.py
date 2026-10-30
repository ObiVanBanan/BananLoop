"""Bounded, read-only Codex app-server capability probe.

Starts a local app-server, initializes JSON-RPC, and requests the model catalog.
It does not start a thread, turn, login, or inference request. Output is
deliberately limited to method success and catalog size.
"""

from __future__ import annotations

import json
import queue
import subprocess
import threading
import time


def main() -> int:
    proc = subprocess.Popen(
        ["cmd", "/c", "codex", "app-server", "--stdio"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        bufsize=1,
    )
    assert proc.stdin and proc.stdout
    replies: queue.Queue[dict] = queue.Queue()

    def reader() -> None:
        for line in proc.stdout:
            try:
                replies.put(json.loads(line))
            except json.JSONDecodeError:
                continue

    thread = threading.Thread(target=reader, daemon=True)
    thread.start()

    def request(req: dict, timeout: float = 8.0) -> dict:
        proc.stdin.write(json.dumps(req) + "\n")
        proc.stdin.flush()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                msg = replies.get(timeout=min(0.5, deadline - time.monotonic()))
            except queue.Empty:
                continue
            if msg.get("id") == req["id"]:
                return msg
        raise TimeoutError(f"No reply for {req['method']}")

    try:
        init = request({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"clientInfo": {"name": "bananloop_probe", "title": "BananLoop Probe", "version": "0.1.0"}},
        })
        if "error" in init:
            print("initialize: error", init["error"].get("code"))
            return 1
        print("initialize: ok")
        proc.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "initialized", "params": {}}) + "\n")
        proc.stdin.flush()
        models = request({"jsonrpc": "2.0", "id": 2, "method": "model/list", "params": {"limit": 20}})
        if "error" in models:
            print("model/list: error", models["error"].get("code"))
            return 1
        result = models.get("result") or {}
        print("model/list: ok; catalog entries:", len(result.get("data") or []))
        account = request({"jsonrpc": "2.0", "id": 3, "method": "account/read", "params": {"refreshToken": False}})
        if "error" in account:
            print("account/read: error", account["error"].get("code"))
            return 1
        print("account/read: ok; account present:", bool((account.get("result") or {}).get("account")))
        return 0
    except (OSError, TimeoutError, ValueError) as exc:
        print("probe: unavailable", type(exc).__name__)
        return 1
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=3)


if __name__ == "__main__":
    raise SystemExit(main())
