"""Loopback-only ChatGPT device login delegated to the official Codex app-server.

Only a short-lived verification code reaches the browser. Codex persists and
refreshes its own credentials. This module never reads token stores or requests
models, creates threads, or accepts a caller-supplied RPC method/URL/command.
"""
from __future__ import annotations

import json
import queue
import re
import shutil
import subprocess
import threading
import time
from urllib.parse import urlsplit
from typing import Any


class LoginUnavailable(RuntimeError):
    """No safe device-code session can be started or continued."""


class CodexDeviceLogin:
    """One app-server authentication session, scoped to one loopback API server."""

    TTL_SECONDS = 600
    MAX_FRAME = 262144

    def __init__(self, *, codex_path: str | None = None, timeout: float = 15.0) -> None:
        self._binary = codex_path if codex_path is not None else shutil.which("codex")
        self._timeout = timeout
        self._lock = threading.RLock()
        self._actions = threading.Lock()
        self._process: subprocess.Popen[str] | None = None
        self._requests: dict[int, queue.Queue[dict[str, Any]]] = {}
        self._next_id = 0
        self._phase = "idle"
        self._started_at = 0.0
        self._login_id: str | None = None
        self._verification_url: str | None = None
        self._user_code: str | None = None

    def _status_unlocked(self) -> dict[str, Any]:
        return {"phase": self._phase,
                "verification_url": self._verification_url if self._phase == "pending" else None,
                "user_code": self._user_code if self._phase == "pending" else None}

    def status(self) -> dict[str, Any]:
        with self._lock:
            if self._phase == "pending" and time.monotonic() - self._started_at > self.TTL_SECONDS:
                self._phase = "expired"
                self._verification_url = self._user_code = None
                process = self._process
            else:
                process = None
            state = self._status_unlocked()
        if process is not None:
            self._terminate(process)
        return state

    def _terminate(self, process: subprocess.Popen[str] | None) -> None:
        if process is None:
            return
        try:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    process.kill()
        except OSError:
            pass
        try:
            if process.stdin is not None:
                process.stdin.close()
        except OSError:
            pass

    def _read(self, process: subprocess.Popen[str]) -> None:
        try:
            assert process.stdout is not None
            for line in process.stdout:
                if len(line) > self.MAX_FRAME:
                    break
                try:
                    message = json.loads(line)
                except (TypeError, ValueError):
                    continue
                if not isinstance(message, dict):
                    continue
                message_id = message.get("id")
                if isinstance(message_id, int):
                    with self._lock:
                        result = self._requests.pop(message_id, None)
                    if result is not None:
                        result.put_nowait(message)
                elif message.get("method") == "account/login/completed":
                    params = message.get("params")
                    if not isinstance(params, dict):
                        continue
                    with self._lock:
                        if (self._process is process and self._login_id is not None
                                and params.get("loginId") == self._login_id
                                and self._phase == "pending"):
                            self._phase = "completed" if params.get("success") is True else "failed"
                            self._verification_url = self._user_code = None
        finally:
            with self._lock:
                if self._process is process and self._phase == "pending":
                    self._phase = "failed"
                    self._verification_url = self._user_code = None

    def _send(self, process: subprocess.Popen[str], method: str,
              params: dict[str, Any], timeout: float | None = None) -> dict[str, Any]:
        q: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=1)
        with self._lock:
            if self._process is not process or process.poll() is not None:
                raise LoginUnavailable("Codex app-server terminated")
            self._next_id += 1
            ident = self._next_id
            self._requests[ident] = q
            try:
                assert process.stdin is not None
                process.stdin.write(json.dumps({"id": ident, "method": method, "params": params},
                                               separators=(",", ":")) + "\n")
                process.stdin.flush()
            except (OSError, ValueError):
                self._requests.pop(ident, None)
                raise LoginUnavailable("Codex app-server is unavailable") from None
        try:
            response = q.get(timeout=timeout or self._timeout)
        except queue.Empty:
            with self._lock:
                self._requests.pop(ident, None)
            raise LoginUnavailable("Codex app-server response timed out") from None
        if response.get("error") or not isinstance(response.get("result"), dict):
            raise LoginUnavailable("Codex app-server rejected the device login request")
        return response["result"]

    def start(self) -> dict[str, Any]:
        with self._actions:
            existing = self.status()
            if existing["phase"] == "pending":
                return existing
            if not self._binary:
                raise LoginUnavailable("Codex CLI is not installed")
            with self._lock:
                old = self._process
                self._process = None
                self._phase = "idle"
                self._login_id = None
                self._verification_url = self._user_code = None
            self._terminate(old)
            try:
                process = subprocess.Popen([self._binary, "app-server"],
                                           stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                           stderr=subprocess.DEVNULL, text=True, bufsize=1)
            except OSError as exc:
                raise LoginUnavailable("Codex app-server could not start") from exc
            with self._lock:
                self._process = process
            threading.Thread(target=self._read, args=(process,), daemon=True,
                             name="bananloop-codex-auth-reader").start()
            try:
                self._send(process, "initialize", {"clientInfo": {
                    "name": "bananloop", "title": "BananLoop", "version": "0.2.0"}})
                with self._lock:
                    if process.poll() is not None:
                        raise LoginUnavailable("Codex app-server stopped during initialization")
                    assert process.stdin is not None
                    process.stdin.write('{"method":"initialized","params":{}}\n')
                    process.stdin.flush()
                result = self._send(process, "account/login/start", {"type": "chatgptDeviceCode"}, timeout=30)
                url, code, login_id = result.get("verificationUrl"), result.get("userCode"), result.get("loginId")
                parsed = urlsplit(url) if isinstance(url, str) else None
                if (result.get("type") != "chatgptDeviceCode" or not isinstance(login_id, str)
                        or not 1 <= len(login_id) <= 100 or not isinstance(code, str)
                        or re.fullmatch(r"[A-Za-z0-9-]{4,32}", code) is None
                        or parsed is None or parsed.scheme != "https"
                        or parsed.hostname != "auth.openai.com"
                        or parsed.path != "/codex/device" or parsed.username or parsed.password
                        or parsed.fragment or parsed.port is not None):
                    raise LoginUnavailable("Unexpected Codex device authorization response")
                with self._lock:
                    self._phase = "pending"
                    self._started_at = time.monotonic()
                    self._login_id = login_id
                    self._verification_url = url
                    self._user_code = code
                return self.status()
            except (LoginUnavailable, OSError, ValueError):
                with self._lock:
                    self._phase = "failed"
                    self._verification_url = self._user_code = None
                self._terminate(process)
                raise LoginUnavailable("ChatGPT device login could not start") from None

    def cancel(self) -> dict[str, Any]:
        with self._actions:
            with self._lock:
                process = self._process
                login_id = self._login_id
                pending = self._phase == "pending"
                self._phase = "cancelled"
                self._verification_url = self._user_code = None
            if pending and process is not None and process.poll() is None and login_id:
                try:
                    self._send(process, "account/login/cancel", {"loginId": login_id}, timeout=3)
                except LoginUnavailable:
                    pass
            self._terminate(process)
            return self.status()

    def close(self) -> None:
        with self._lock:
            process = self._process
            self._process = None
            if self._phase == "pending":
                self._phase = "cancelled"
            self._verification_url = self._user_code = None
        self._terminate(process)