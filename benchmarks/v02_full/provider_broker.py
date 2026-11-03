"""One-action host-side provider broker for networkless RPDI-14 workers.

The worker receives only this broker's private Unix socket. Credentials and the
provider adapter stay in the trusted host process. This module does not provide
an actual provider adapter; integrations must supply one that verifies the
upstream provider receipt and enforces a single request with a hard timeout.
"""
from __future__ import annotations

import base64
import json
import os
import socket
import stat
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .accounting import ResourceUsage
from .prereg import ArmConfig

MAX_REQUEST_BYTES = 4_000_000
MAX_RESPONSE_BYTES = 2_000_000
MAX_PROMPT_BYTES = 1_500_000
MAX_PROVIDER_RECEIPT_BYTES = 256_000


class ProviderBrokerError(RuntimeError):
    """The one-shot provider broker could not verify or complete its action."""


@dataclass(frozen=True)
class ProviderInvocation:
    output: bytes
    provider_receipt: bytes


class OneCallProviderAdapter(Protocol):
    """Trusted host adapter with hard bounds and safe cancellation.

    `cancel_action` must be idempotent, stop an in-flight provider action, and
    be a no-op if that action already completed. This covers the narrow race
    between provider completion and receipt publication by the broker thread.
    """

    enforces_single_call: bool
    enforces_timeout: bool
    enforces_provider_ceilings: bool
    enforces_cancellation: bool

    def invoke_once(
        self, prompt: bytes, *, action_id: str, arm_config: ArmConfig,
        resource_bounds: ResourceUsage, timeout_seconds: int,
    ) -> ProviderInvocation: ...

    def cancel_action(self, action_id: str) -> None: ...


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False).encode("utf-8")


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _read_request(conn: socket.socket) -> dict:
    data = bytearray()
    while len(data) <= MAX_REQUEST_BYTES:
        chunk = conn.recv(min(65536, MAX_REQUEST_BYTES + 1 - len(data)))
        if not chunk:
            break
        data.extend(chunk)
        if b"\n" in data:
            if data.count(b"\n") != 1 or data[-1:] != b"\n":
                raise ProviderBrokerError("request must contain exactly one newline-terminated JSON record")
            break
    if len(data) > MAX_REQUEST_BYTES or not data.endswith(b"\n"):
        raise ProviderBrokerError("provider request is missing or oversized")
    raw = bytes(data[:-1])
    try:
        value = json.loads(raw, object_pairs_hook=_unique_object,
                           parse_constant=lambda _: (_ for _ in ()).throw(ValueError("non-finite JSON")))
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ProviderBrokerError("provider request is not strict JSON") from exc
    if not isinstance(value, dict) or _canonical(value) != raw:
        raise ProviderBrokerError("provider request must use canonical JSON")
    return value


class OneShotProviderBroker:
    """Serve exactly one bounded request over a private socket for one action."""

    def __init__(
        self,
        adapter: OneCallProviderAdapter,
        *,
        action_id: str,
        arm_config: ArmConfig,
        resource_bounds: ResourceUsage,
        timeout_seconds: int,
    ) -> None:
        if not callable(getattr(adapter, "invoke_once", None)):
            raise ProviderBrokerError("trusted invoke_once provider adapter is required")
        if (getattr(adapter, "enforces_single_call", False) is not True
                or getattr(adapter, "enforces_timeout", False) is not True
                or getattr(adapter, "enforces_provider_ceilings", False) is not True
                or getattr(adapter, "enforces_cancellation", False) is not True
                or not callable(getattr(adapter, "cancel_action", None))):
            raise ProviderBrokerError(
                "provider adapter must enforce one call, all provider ceilings, hard timeout and idempotent cancellation"
            )
        if (not isinstance(action_id, str) or len(action_id) != 64
                or any(ch not in "0123456789abcdef" for ch in action_id)):
            raise ProviderBrokerError("action_id must be a lowercase SHA-256 digest")
        if not isinstance(resource_bounds, ResourceUsage) or resource_bounds.unknown_dimensions():
            raise ProviderBrokerError("all resource bounds must be known before provider dispatch")
        if resource_bounds.agent_calls != 1:
            raise ProviderBrokerError("one model action must reserve exactly one agent call")
        if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 3600:
            raise ProviderBrokerError("provider timeout must be between 1 and 3600 seconds")
        self.adapter = adapter
        self.action_id = action_id
        self.arm_config = arm_config
        self.resource_bounds = resource_bounds
        self.timeout_seconds = timeout_seconds
        self.provider_receipt: bytes | None = None
        self._error: BaseException | None = None
        self._server: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._provider_lock = threading.Lock()
        self._provider_started = False
        self._provider_finished = False
        self._temp: tempfile.TemporaryDirectory | None = None
        self.socket_path: Path | None = None
        self._calls = 0

    def __enter__(self) -> "OneShotProviderBroker":
        self._temp = tempfile.TemporaryDirectory(prefix="rpdi14-provider-broker-")
        directory = Path(self._temp.name)
        os.chmod(directory, 0o700)
        path = directory / "provider.sock"
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            server.bind(str(path))
            os.chmod(path, 0o600)
            server.listen(1)
            server.settimeout(self.timeout_seconds + 5)
        except OSError:
            server.close()
            self._temp.cleanup()
            self._temp = None
            raise
        self._server = server
        self.socket_path = path
        # Keep this non-daemon: a broken cancellation contract must not turn an
        # in-flight billable provider call into an invisible detached thread.
        self._thread = threading.Thread(target=self._serve, name="rpdi14-provider-broker", daemon=False)
        self._thread.start()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        with self._provider_lock:
            self._stop.set()
            should_cancel = self._provider_started and not self._provider_finished
        if self._server is not None:
            try:
                self._server.close()
            except OSError:
                pass
        exit_error = None
        try:
            if self._thread is not None:
                self._thread.join(timeout=0.25)
                # State changes are coordinated with call start/finish. The
                # adapter's idempotent cancel contract covers a provider call
                # that completes just before cancellation is delivered.
                if self._thread.is_alive() and should_cancel:
                    try:
                        self.adapter.cancel_action(self.action_id)
                    except Exception as cancel_error:
                        self._error = cancel_error
                        exit_error = ProviderBrokerError("provider cancellation failed")
                if self._thread.is_alive():
                    self._thread.join(timeout=2)
                if self._thread.is_alive():
                    exit_error = ProviderBrokerError(
                        "provider adapter or broker thread did not stop within the cancellation bound"
                    )
        finally:
            if self._temp is not None:
                self._temp.cleanup()
                self._temp = None
        if exit_error is not None:
            raise exit_error
        if exc is None:
            self.verify_complete()

    def verify_complete(self) -> bytes:
        if self._error is not None:
            raise ProviderBrokerError(f"provider broker failed: {type(self._error).__name__}") from self._error
        if self._calls != 1 or not self.provider_receipt:
            raise ProviderBrokerError("worker did not complete exactly one provider call")
        return self.provider_receipt

    def _serve(self) -> None:
        assert self._server is not None
        try:
            self._server.settimeout(0.2)
            while True:
                try:
                    conn, _ = self._server.accept()
                    break
                except socket.timeout:
                    if self._stop.is_set():
                        return
            with conn:
                conn.settimeout(self.timeout_seconds + 5)
                self._serve_connection(conn)
        except BaseException as exc:
            self._error = exc

    def _serve_connection(self, conn: socket.socket) -> None:
        request = _read_request(conn)
        if set(request) != {"schema_version", "action_id", "prompt_base64"}:
            raise ProviderBrokerError("provider request has an invalid field set")
        if request.get("schema_version") != 1 or request.get("action_id") != self.action_id:
            raise ProviderBrokerError("provider request is bound to another action")
        encoded = request.get("prompt_base64")
        if not isinstance(encoded, str) or len(encoded) > (MAX_PROMPT_BYTES * 4 // 3 + 8):
            raise ProviderBrokerError("provider prompt is missing or oversized")
        try:
            prompt = base64.b64decode(encoded, validate=True)
        except (ValueError, base64.binascii.Error) as exc:
            raise ProviderBrokerError("provider prompt is not valid base64") from exc
        if not prompt or len(prompt) > MAX_PROMPT_BYTES:
            raise ProviderBrokerError("provider prompt is empty or oversized")
        with self._provider_lock:
            if self._stop.is_set():
                raise ProviderBrokerError("worker ended before provider dispatch")
            self._provider_started = True
            self._calls += 1
        result = self.adapter.invoke_once(
            prompt, action_id=self.action_id, arm_config=self.arm_config,
            resource_bounds=self.resource_bounds,
            timeout_seconds=self.timeout_seconds,
        )
        # An exception is an uncertain provider outcome. Leave the call marked
        # unfinished so __exit__ attempts provider-side cancellation before
        # the meter records UNKNOWN. Only a normal adapter return proves the
        # action has stopped, even if its receipt later fails verification.
        with self._provider_lock:
            self._provider_finished = True
        if (not isinstance(result, ProviderInvocation) or not isinstance(result.output, bytes)
                or not isinstance(result.provider_receipt, bytes) or not result.provider_receipt
                or len(result.output) > MAX_RESPONSE_BYTES
                or len(result.provider_receipt) > MAX_PROVIDER_RECEIPT_BYTES):
            raise ProviderBrokerError("provider adapter returned an invalid or oversized result")
        self.provider_receipt = result.provider_receipt
        response = _canonical({
            "schema_version": 1,
            "output_base64": base64.b64encode(result.output).decode("ascii"),
            "provider_receipt_base64": base64.b64encode(result.provider_receipt).decode("ascii"),
        }) + b"\n"
        if len(response) > MAX_REQUEST_BYTES:
            raise ProviderBrokerError("provider response envelope exceeds its size limit")
        conn.sendall(response)


def request_one_provider_action(
    socket_path: str | Path, *, action_id: str, prompt: bytes, timeout_seconds: int,
) -> ProviderInvocation:
    """Worker-side RPC helper; returns model output and the host-verified receipt."""
    if not isinstance(prompt, bytes) or not prompt or len(prompt) > MAX_PROMPT_BYTES:
        raise ProviderBrokerError("prompt is empty or exceeds the broker limit")
    request = _canonical({
        "schema_version": 1,
        "action_id": action_id,
        "prompt_base64": base64.b64encode(prompt).decode("ascii"),
    }) + b"\n"
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        client.settimeout(timeout_seconds)
        client.connect(str(socket_path))
        client.sendall(request)
        response = _read_request(client)
    except OSError as exc:
        raise ProviderBrokerError("provider broker connection failed") from exc
    finally:
        client.close()
    if set(response) != {"schema_version", "output_base64", "provider_receipt_base64"} or response.get("schema_version") != 1:
        raise ProviderBrokerError("provider response has an invalid field set")
    try:
        output = base64.b64decode(response["output_base64"], validate=True)
        receipt = base64.b64decode(response["provider_receipt_base64"], validate=True)
    except (ValueError, KeyError, TypeError, base64.binascii.Error) as exc:
        raise ProviderBrokerError("provider response contains malformed base64") from exc
    if len(output) > MAX_RESPONSE_BYTES or not receipt or len(receipt) > MAX_PROVIDER_RECEIPT_BYTES:
        raise ProviderBrokerError("provider response is empty or oversized")
    return ProviderInvocation(output=output, provider_receipt=receipt)
