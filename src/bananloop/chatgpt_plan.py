"""Official Sign in with ChatGPT (SIWC), for a trusted local BananLoop owner.

OpenAI's public OSS protocol is used directly. No Codex credential scraping,
ChatGPT backend-api endpoint, paid API key, or LearnHarness code is used here.
This file is trusted-host code: never import it into an untrusted model worker.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import secrets
import stat
import threading
import time
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlencode, urlparse, parse_qs
from urllib.request import Request, build_opener, HTTPRedirectHandler
from urllib.error import HTTPError, URLError

ISSUER = "https://auth.openai.com"
AUTHORIZE = ISSUER + "/api/accounts/authorize"
TOKEN = ISSUER + "/api/accounts/oauth/token"
JWKS = ISSUER + "/.well-known/jwks.json"
RESOURCE = "https://api.openai.com/v1"
MODELS = RESOURCE + "/models"
SCOPES = "openid profile email offline_access resource.invoke chatgpt.tokens.use.direct"
MAX_RESPONSE_BYTES = 2_000_000


class SIWCError(RuntimeError):
    """Fail closed; this error never includes raw OAuth tokens."""


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class OfficialHTTP:
    """Small, redirect-free HTTPS client with bounded response bodies."""

    _allowed = frozenset({TOKEN, JWKS, MODELS, ISSUER + "/.well-known/openid-configuration"})

    def request_json(self, method: str, url: str, *, data: bytes | None = None,
                     headers: dict[str, str] | None = None) -> dict[str, Any]:
        if url not in self._allowed or method not in {"GET", "POST"}:
            raise SIWCError("request target is not an approved OpenAI endpoint")
        request = Request(url, method=method, headers=headers or {}, data=data)
        try:
            with build_opener(NoRedirects).open(request, timeout=20) as response:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
                if len(raw) > MAX_RESPONSE_BYTES:
                    raise SIWCError("OpenAI response exceeds bounded limit")
        except HTTPError as exc:
            raise SIWCError(f"OpenAI authentication/catalog HTTP {exc.code}") from None
        except (URLError, OSError) as exc:
            raise SIWCError("OpenAI authentication/catalog connection failed") from exc
        try:
            value = json.loads(raw)
        except (ValueError, UnicodeError) as exc:
            raise SIWCError("OpenAI returned invalid JSON") from exc
        if not isinstance(value, dict):
            raise SIWCError("OpenAI JSON must be an object")
        return value


def default_vault() -> Path:
    base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "bananloop" / "chatgpt-plan.json"


class SIWCVault:
    """One host, several issued account registrations, owner-only local file."""

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path).expanduser().absolute() if path is not None else default_vault().absolute()

    def _check_path(self) -> None:
        if self.path.is_symlink() or self.path.parent.is_symlink():
            raise SIWCError("refusing symlink for ChatGPT credential path")
        if self.path.exists() and not self.path.is_file():
            raise SIWCError("ChatGPT credential path is not a regular file")
        if os.name != "nt" and self.path.exists():
            if stat.S_IMODE(self.path.stat().st_mode) & 0o077:
                raise SIWCError("ChatGPT credential file must be readable by owner only (chmod 600)")

    def read(self) -> dict[str, Any] | None:
        self._check_path()
        if not self.path.exists():
            return None
        try:
            raw = self.path.read_bytes()
            if len(raw) > 128_000:
                raise SIWCError("credential record is unexpectedly large")
            item = json.loads(raw)
        except (OSError, UnicodeError, ValueError) as exc:
            raise SIWCError("cannot decode protected ChatGPT credential record") from exc
        if (not isinstance(item, dict) or item.get("version") != 1
                or not isinstance(item.get("host_id"), str)
                or not item["host_id"].startswith("urn:uuid:")
                or not isinstance(item.get("accounts"), list)
                or item.get("active_client_id") is not None
                and not isinstance(item["active_client_id"], str)):
            raise SIWCError("invalid ChatGPT credential record")
        return item

    def write(self, item: dict[str, Any]) -> None:
        if not isinstance(item, dict) or item.get("version") != 1:
            raise SIWCError("invalid credential record version")
        self._check_path()
        if self.path.parent.exists():
            if not self.path.parent.is_dir():
                raise SIWCError("credential parent is not a directory")
            if os.name != "nt" and stat.S_IMODE(self.path.parent.stat().st_mode) & 0o077:
                raise SIWCError("ChatGPT credential directory must be owner-only (chmod 700)")
        else:
            self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        encoded = (json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n").encode()
        if len(encoded) > 128_000:
            raise SIWCError("credential record exceeds size limit")
        tmp = self.path.parent / f".chatgpt-plan-{uuid.uuid4().hex}.tmp"
        descriptor = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(tmp, self.path)
            if os.name != "nt":
                self.path.chmod(0o600)
        finally:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass

    def ensure(self) -> dict[str, Any]:
        item = self.read()
        if item is None:
            item = {
                "version": 1, "host_id": "urn:uuid:" + str(uuid.uuid4()),
                "active_client_id": None, "accounts": [],
            }
            self.write(item)
        return item


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def authorization_parameters(host_id: str, *, client_id: str | None = None,
                             redirect_uri: str, state: str, nonce: str,
                             code_verifier: str, id_token_hint: str | None = None) -> dict[str, str]:
    if not redirect_uri.startswith("http://127.0.0.1:") or not redirect_uri.endswith("/auth/callback"):
        raise SIWCError("OAuth redirect must use the exact loopback callback path")
    dynamic = client_id is None
    query = {
        "client_id": client_id if client_id else "dynamic_agent_client",
        "ext_agent_host_id": host_id, "response_type": "code",
        "redirect_uri": redirect_uri, "scope": SCOPES, "resource": RESOURCE,
        "state": state, "nonce": nonce, "code_challenge_method": "S256",
        "code_challenge": _b64(hashlib.sha256(code_verifier.encode()).digest()),
    }
    if dynamic:
        query["agent_name_hint"] = "BananLoop"
    if id_token_hint:
        query["id_token_hint"] = id_token_hint
    return query


def _token_form(grant_type: str, fields: dict[str, str]) -> bytes:
    return urlencode({"grant_type": grant_type, "resource": RESOURCE, **fields}).encode("ascii")


def verify_id_token(raw: str, *, client_id: str, expected_nonce: str | None,
                    http: OfficialHTTP) -> dict[str, Any]:
    """Verify JWT signature and all mandatory claims against fixed OpenAI JWKS."""
    try:
        import jwt
        header = jwt.get_unverified_header(raw)
        alg = header.get("alg")
        kid = header.get("kid")
        if alg not in {"RS256", "ES256"} or not isinstance(kid, str) or not kid:
            raise SIWCError("unsupported OpenAI ID-token signing header")
        jwks = http.request_json("GET", JWKS)
        matching = [k for k in jwks.get("keys", []) if k.get("kid") == kid]
        if len(matching) != 1:
            raise SIWCError("OpenAI ID-token signature key is unavailable")
        key = jwt.PyJWK.from_dict(matching[0]).key
        claims = jwt.decode(
            raw, key=key, algorithms=[alg], audience=client_id, issuer=ISSUER,
            options={"require": ["exp", "iss", "aud", "sub"]},
            leeway=30,
        )
        if not isinstance(claims.get("sub"), str) or not claims["sub"]:
            raise SIWCError("OpenAI ID token has no verified subject")
        if expected_nonce is not None and not secrets.compare_digest(
            str(claims.get("nonce", "")), expected_nonce
        ):
            raise SIWCError("OpenAI ID-token nonce mismatch")
        return claims
    except SIWCError:
        raise
    except (ImportError, Exception) as exc:
        # Never print untrusted token contents or server/JWT exception details.
        raise SIWCError("OpenAI ID-token signature or claims validation failed") from None


def _validate_token_body(body: dict[str, Any], *, previous: dict[str, Any] | None = None) -> dict[str, Any]:
    access = body.get("access_token")
    refresh = body.get("refresh_token") or (previous or {}).get("refresh_token")
    id_token = body.get("id_token") or (previous or {}).get("id_token")
    scopes = body.get("scope") or (previous or {}).get("scope")
    expires = body.get("expires_in")
    if (not isinstance(access, str) or not access or access.startswith("sk-")
            or not isinstance(refresh, str) or not refresh
            or not isinstance(id_token, str) or not id_token
            or not isinstance(scopes, str)
            or body.get("token_type", "Bearer").lower() != "bearer"
            or isinstance(expires, bool) or not isinstance(expires, (int, float))
            or expires <= 0):
        raise SIWCError("OpenAI OAuth token response is incomplete")
    permissions = frozenset(scopes.split())
    if not {"chatgpt.tokens.use.direct", "resource.invoke", "offline_access"} <= permissions:
        raise SIWCError("ChatGPT plan permission was not granted; no inference permitted")
    return {
        "access_token": access, "refresh_token": refresh, "id_token": id_token,
        "scope": scopes, "expires_at": time.time() + float(expires),
        "earliest_refresh_at": float(body.get("earliest_refresh_at") or 0),
    }


class ChatGPTPlanAuth:
    def __init__(self, vault: SIWCVault | None = None, *, http: OfficialHTTP | None = None):
        self.vault = vault or SIWCVault()
        self.http = http or OfficialHTTP()

    def status(self) -> dict[str, Any]:
        data = self.vault.read()
        if data is None:
            return {"connected": False, "active": None, "host_configured": False}
        current = next((a for a in data["accounts"]
                        if a["client_id"] == data["active_client_id"]), None)
        return {
            "connected": bool(current and current.get("access_token") and
                              current.get("refresh_token")),
            "active": {"client_id": current["client_id"],
                       "subject_label": hashlib.sha256(current["subject"].encode()).hexdigest()[:12],
                       "expires_at": current["expires_at"]} if current else None,
            "host_configured": True,
        }

    def _save_account(self, entry: dict[str, Any]) -> None:
        data = self.vault.ensure()
        data["accounts"] = [a for a in data["accounts"] if a["client_id"] != entry["client_id"]]
        data["accounts"].append(entry)
        data["active_client_id"] = entry["client_id"]
        self.vault.write(data)

    def complete_login(self, callback: dict[str, str], *, expected_state: str,
                       expected_nonce: str, verifier: str, redirect_uri: str,
                       previous: dict[str, Any] | None = None) -> None:
        if not secrets.compare_digest(callback.get("state", ""), expected_state):
            raise SIWCError("ChatGPT OAuth state mismatch")
        if callback.get("error"):
            raise SIWCError("ChatGPT authorization declined")
        code = callback.get("code")
        client_id = callback.get("client_id") or (previous or {}).get("client_id")
        if not isinstance(code, str) or not code:
            raise SIWCError("ChatGPT callback omitted an authorization code")
        if (not isinstance(client_id, str) or not client_id
                or client_id == "dynamic_agent_client"
                or previous and client_id != previous["client_id"]):
            raise SIWCError("ChatGPT callback has no valid issued client ID")
        body = self.http.request_json(
            "POST", TOKEN,
            data=_token_form("authorization_code", {
                "client_id": client_id, "code": code,
                "code_verifier": verifier, "redirect_uri": redirect_uri,
            }),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        validated = _validate_token_body(body)
        claims = verify_id_token(
            validated["id_token"], client_id=client_id,
            expected_nonce=expected_nonce, http=self.http,
        )
        if previous and claims["sub"] != previous["subject"]:
            raise SIWCError("ChatGPT reauthorization changed account identity")
        self._save_account({"client_id": client_id, "subject": claims["sub"], **validated})

    def login(self, *, open_browser: Callable[[str], bool] = webbrowser.open,
              timeout_seconds: int = 180) -> dict[str, Any]:
        data = self.vault.ensure()
        previous = next((a for a in data["accounts"]
                         if a["client_id"] == data["active_client_id"]), None)
        verifier = _b64(secrets.token_bytes(48))
        nonce = _b64(secrets.token_bytes(32))
        state = _b64(secrets.token_bytes(32))
        callback: dict[str, str] = {}

        class CallbackHandler(BaseHTTPRequestHandler):
            def log_message(self, *args: Any) -> None:
                pass  # no OAuth authorization code or ID-token hint in logs

            def do_GET(self) -> None:
                parsed = urlparse(self.path)
                values = parse_qs(parsed.query, keep_blank_values=True)
                valid = (parsed.path == "/auth/callback"
                         and all(len(v) == 1 for v in values.values())
                         and len(self.path) < 8192)
                if valid:
                    callback.update({k: v[0] for k, v in values.items()})
                self.send_response(200 if valid else 400)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.end_headers()
                self.wfile.write(
                    b"Return to BananLoop terminal." if valid else b"Invalid callback."
                )

        server = HTTPServer(("127.0.0.1", 0), CallbackHandler)
        redirect = f"http://127.0.0.1:{server.server_port}/auth/callback"
        query = authorization_parameters(
            data["host_id"], client_id=previous["client_id"] if previous else None,
            redirect_uri=redirect, state=state, nonce=nonce, code_verifier=verifier,
            id_token_hint=previous.get("id_token") if previous else None,
        )
        url = AUTHORIZE + "?" + urlencode(query)
        server.timeout = 1
        try:
            if not open_browser(url):
                raise SIWCError("cannot open system browser for ChatGPT authorization")
            end = time.monotonic() + timeout_seconds
            while not callback and time.monotonic() < end:
                server.handle_request()
            if not callback:
                raise SIWCError("ChatGPT OAuth callback timed out")
        finally:
            server.server_close()
        self.complete_login(
            callback, expected_state=state, expected_nonce=nonce,
            verifier=verifier, redirect_uri=redirect, previous=previous,
        )
        return self.status()

    def _selected(self) -> dict[str, Any]:
        state = self.vault.read()
        if not state or not state.get("active_client_id"):
            raise SIWCError("ChatGPT is not signed in; run bananloop-chatgpt login")
        selected = next((a for a in state["accounts"]
                         if a["client_id"] == state["active_client_id"]), None)
        if not selected or not selected.get("refresh_token"):
            raise SIWCError("ChatGPT is signed out; run bananloop-chatgpt login")
        return selected

    def access_token(self) -> str:
        account = self._selected()
        if account.get("expires_at", 0) > time.time() + 90:
            return account["access_token"]
        if account.get("earliest_refresh_at", 0) > time.time():
            raise SIWCError("ChatGPT token cannot yet be refreshed")
        body = self.http.request_json(
            "POST", TOKEN,
            data=_token_form("refresh_token", {
                "client_id": account["client_id"],
                "refresh_token": account["refresh_token"],
            }),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        validated = _validate_token_body(body, previous=account)
        claims = verify_id_token(
            validated["id_token"], client_id=account["client_id"],
            expected_nonce=None, http=self.http,
        )
        if claims["sub"] != account["subject"]:
            raise SIWCError("ChatGPT token refresh changed account identity")
        self._save_account({**account, **validated})
        return validated["access_token"]

    def models(self) -> list[dict[str, str]]:
        data = self.http.request_json(
            "GET", MODELS,
            headers={"Authorization": "Bearer " + self.access_token()},
        )
        listing = data.get("models")
        if not isinstance(listing, list):
            raise SIWCError("ChatGPT plan model catalog is not available")
        result: list[dict[str, str]] = []
        for item in listing:
            if (isinstance(item, dict)
                    and isinstance(item.get("slug"), str)
                    and item.get("visibility") == "list"):
                result.append({
                    "slug": item["slug"],
                    "display_name": str(item.get("display_name") or item["slug"]),
                })
        return result

    def logout(self) -> None:
        data = self.vault.read()
        if not data or not data.get("active_client_id"):
            return
        for account in data["accounts"]:
            if account["client_id"] == data["active_client_id"]:
                for key in ("access_token", "refresh_token", "id_token"):
                    account.pop(key, None)
        self.vault.write(data)
        # Local logout only. Account owner can also revoke app in ChatGPT Settings.


def main() -> None:
    parser = argparse.ArgumentParser(description="Official ChatGPT plan connection (no API key)")
    parser.add_argument("command", choices=("login", "status", "models", "logout"))
    parsed = parser.parse_args()
    client = ChatGPTPlanAuth()
    try:
        if parsed.command == "login":
            value: Any = client.login()
        elif parsed.command == "status":
            value = client.status()
        elif parsed.command == "models":
            value = client.models()
        else:
            client.logout()
            value = {"connected": False, "local_logout": True}
    except SIWCError as exc:
        parser.exit(2, f"ChatGPT plan error: {exc}\n")
    print(json.dumps(value, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
