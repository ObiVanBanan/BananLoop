"""Owner-only localhost UI for exactly one SIWC v2 Exploration smoke.

This is separate from the campaign dashboard deliberately: it does not
introduce a model-dispatch endpoint into the normal API, and never shares
subscription credentials with browser JavaScript or worker role context.
"""
from __future__ import annotations

import argparse
import hmac
import json
import secrets
import subprocess
import sys
import tempfile
import threading
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

from .chatgpt_plan import ChatGPTPlanAuth, SIWCError

MODEL = "gpt-6-luna"

PAGE = """<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="referrer" content="no-referrer">
<title>BananLoop · ChatGPT diagnostic</title>
<style>
:root {color-scheme:dark; font-family:system-ui,sans-serif; background:#111; color:#f1f1f1}
* {box-sizing:border-box} body {margin:0 auto;max-width:780px;padding:32px 20px 72px}
header {display:flex;justify-content:space-between;gap:20px;align-items:center}
h1 {font-size:27px;margin:2px 0 5px} h2 {font-size:18px;margin:0 0 12px}
.muted {color:#aaa;line-height:1.5} .eyebrow {color:#f9cb45;font-size:12px;text-transform:uppercase;letter-spacing:.12em}
.panel {background:#1d1d1d;border:1px solid #383838;border-radius:14px;padding:22px;margin:22px 0}
.row {display:flex;align-items:center;justify-content:space-between;gap:14px;flex-wrap:wrap}
button {background:#f9cb45;color:#131313;border:0;border-radius:9px;padding:11px 15px;font-weight:700;cursor:pointer}
button:disabled {opacity:.45;cursor:not-allowed}
button.secondary {background:#323232;color:#fafafa;border:1px solid #555}
label {display:flex;gap:9px;align-items:start;line-height:1.45;margin:18px 0}
input {margin-top:5px;accent-color:#f9cb45}
pre {background:#101010;border:1px solid #333;padding:16px;border-radius:8px;white-space:pre-wrap;overflow-wrap:anywhere;font-size:13px;line-height:1.5}
.success {color:#9cdea0}.error {color:#f6a3a3}
small {color:#b2b2b2}.state {font-weight:650}
</style>
</head>
<body>
<header>
<div><p class="eyebrow">Local operator console · RPDI-13R</p>
<h1>BananLoop</h1><p class="muted">ChatGPT Plus · одноразовый тест исследователя</p></div>
<button id="refresh" class="secondary" type="button">Обновить</button>
</header>
<section class="panel" aria-label="Subscription status">
<h2>Подключение</h2>
<p id="connection" class="state" role="status">Проверка подключения…</p>
<p id="models" class="muted">Модель: проверяется…</p>
<p class="muted">При отсутствии подключения выполните в терминале <code>bananloop-chatgpt login</code>.
Авторизация хранится только в защищённом локальном файле, не в браузере.</p>
</section>
<section class="panel" aria-label="Test run">
<h2>Exploration v0.2 — один исследовательский запрос</h2>
<p class="muted">Создаёт отдельную тестовую кампанию. Проверяет цепочку:
ChatGPT → закреплённый Researcher → ToolGateway → Evidence → Research Ledger.
Это не полный RPDI-14 и не доказательство готовности всех компонентов Full.</p>
<label><input type="checkbox" id="consent">
<span>Разрешаю один запрос GPT-6 Luna. Понимаю, что он израсходует часть лимита
моей подписки ChatGPT Plus. Платный API-ключ не используется.</span></label>
<div class="row"><button id="run" type="button" disabled>Запустить один тест</button>
<span id="run-state" role="status" aria-live="polite">Не запускался</span></div>
</section>
<section class="panel" aria-label="Results"><h2>Результат</h2>
<pre id="result" aria-live="polite">Запуск ещё не выполнялся.</pre></section>
<script>
(() => {
  "use strict";
  const token = location.hash.startsWith("#") ? location.hash.slice(1) : "";
  history.replaceState(null, "", location.pathname);
  const $ = id => document.getElementById(id);
  let canRun = false;
  let consumed = false;
  const origin = location.origin;
  async function request(path, payload) {
    const init = {method: payload ? "POST" : "GET", cache: "no-store",
      headers: {"X-BananLoop-UI-Key": token}, credentials:"omit"};
    if (payload) {
      init.headers["Content-Type"] = "application/json";
      init.headers["X-BananLoop-CSRF"] = "1";
      init.body = JSON.stringify(payload);
    }
    const response = await fetch(path, init);
    let data;
    try { data = await response.json(); } catch { data = {error:"Неверный ответ сервера"}; }
    if (!response.ok) throw new Error(data.error || "Ошибка " + response.status);
    return data;
  }
  function toggle() { $("run").disabled = !canRun || consumed || !$("consent").checked; }
  async function refresh() {
    $("connection").textContent = "Проверка…";
    try {
      const state = await request("/api/status");
      canRun = state.connected && state.model_available && state.run_state === "IDLE";
      consumed = state.run_state !== "IDLE";
      $("connection").textContent = state.connected ? "ChatGPT подключён" : "Не подключён";
      $("connection").className = state.connected ? "success" : "error";
      $("models").textContent = state.connected
        ? (state.model_available ? "Доступна модель: " + state.model : "Модель " + state.model + " отсутствует в каталоге аккаунта")
        : "Выполните: bananloop-chatgpt login";
      if (consumed) $("run-state").textContent = state.run_state === "RUNNING" ? "Выполняется…" : "Запуск уже использован";
      toggle();
    } catch (e) {
      canRun = false;
      $("connection").textContent = "Нет соединения с локальным сервером: " + e.message;
      $("connection").className = "error";
      toggle();
    }
  }
  $("consent").addEventListener("change", toggle);
  $("refresh").addEventListener("click", refresh);
  $("run").addEventListener("click", async () => {
    if (!canRun || consumed || !$("consent").checked) return;
    if (!confirm("Запустить один реальный запрос GPT-6 Luna через ChatGPT Plus?")) return;
    consumed = true;
    toggle();
    $("run-state").textContent = "Выполняется…";
    $("result").textContent = "Исследовательский запрос выполняется. Не закрывайте страницу.";
    try {
      const result = await request("/api/run", {approve_one_call: true, model:"gpt-6-luna"});
      $("run-state").textContent = "Завершено";
      $("result").textContent = JSON.stringify(result, null, 2);
    } catch (e) {
      $("run-state").textContent = "Ошибка";
      $("result").textContent = e.message + "\nПовтор автоматически не выполняется.";
    }
  });
  refresh();
})();
</script>
</body>
</html>"""


class DiagnosticUIError(RuntimeError):
    pass


def launch_one_shot() -> dict[str, Any]:
    """Run the *same* one-shot script as CLI in an isolated disposable state."""
    repo = Path(__file__).resolve().parents[2]
    script = repo / "examples" / "v2_research_diagnostic" / "run_one_chatgpt.py"
    if not script.is_file():
        raise DiagnosticUIError("the one-shot v2 diagnostic script is unavailable")
    root = Path(tempfile.mkdtemp(prefix="bananloop-siwc-ui-"))
    command = [sys.executable, str(script), "--work-dir", str(root),
               "--model", MODEL, "--approve-one-call"]
    try:
        completed = subprocess.run(
            command, cwd=repo, capture_output=True, text=True,
            timeout=210, check=False,
        )
    except subprocess.TimeoutExpired:
        raise DiagnosticUIError("one-shot diagnostic timed out; check saved state before retrying") from None
    if completed.returncode != 0:
        # Never return raw subprocess stdout/stderr or potential secret URLs.
        raise DiagnosticUIError("one-shot diagnostic failed; check local server terminal")
    try:
        result = json.loads(completed.stdout)
    except ValueError:
        raise DiagnosticUIError("one-shot diagnostic returned invalid JSON") from None
    if not isinstance(result, dict) or result.get("status") != "SIWC_ONE_SHOT_EXPLORATION_COMPLETED":
        raise DiagnosticUIError("one-shot diagnostic did not report verified completion")
    return result


def create_ui_server(*, port: int = 0, secret: str | None = None,
                     auth: Any | None = None,
                     launch: Callable[[], dict[str, Any]] | None = None):
    """Only loopback; a separate random browser secret gates every model action."""
    token = secret or secrets.token_urlsafe(36)
    authorization = auth if auth is not None else ChatGPTPlanAuth()
    launch_callback = launch or launch_one_shot
    lock = threading.Lock()
    state = {"run_state": "IDLE"}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args: Any) -> None:
            pass  # do not leak browser fragment, credentials or research output

        def _valid_host(self) -> bool:
            return self.headers.get("Host") == f"127.0.0.1:{self.server.server_port}"

        def _secret_valid(self) -> bool:
            supplied = self.headers.get("X-BananLoop-UI-Key", "")
            return bool(supplied) and hmac.compare_digest(supplied, token)

        def _headers(self, status: int, kind: str, size: int) -> None:
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(size))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Content-Security-Policy",
                             "default-src 'none'; script-src 'unsafe-inline'; "
                             "style-src 'unsafe-inline'; connect-src 'self'; "
                             "frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
            self.end_headers()

        def _json(self, code: int, payload: dict[str, Any]) -> None:
            raw = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
            self._headers(code, "application/json; charset=utf-8", len(raw))
            self.wfile.write(raw)

        def _guard(self, *, post: bool = False) -> bool:
            if not self._valid_host():
                self._json(HTTPStatus.FORBIDDEN, {"error": "Invalid localhost Host"})
                return False
            if not self._secret_valid():
                self._json(HTTPStatus.UNAUTHORIZED, {"error": "Invalid operator UI session"})
                return False
            if post and (self.headers.get("Origin") != f"http://127.0.0.1:{self.server.server_port}"
                         or self.headers.get("X-BananLoop-CSRF") != "1"):
                self._json(HTTPStatus.FORBIDDEN, {"error": "Invalid operator origin or CSRF control"})
                return False
            return True

        def do_GET(self) -> None:
            if not self._valid_host():
                self._json(HTTPStatus.FORBIDDEN, {"error": "Invalid localhost Host"})
                return
            if self.path == "/":
                raw = PAGE.encode("utf-8")
                self._headers(HTTPStatus.OK, "text/html; charset=utf-8", len(raw))
                self.wfile.write(raw)
                return
            if not self._guard():
                return
            if self.path != "/api/status":
                self._json(HTTPStatus.NOT_FOUND, {"error": "Unknown endpoint"})
                return
            try:
                connection = authorization.status()
                connected = bool(connection.get("connected"))
                visible = connected and MODEL in {
                    item["slug"] for item in authorization.models()
                }
            except (SIWCError, KeyError, TypeError):
                connected, visible = False, False
            with lock:
                run_state = state["run_state"]
            self._json(HTTPStatus.OK, {
                "connected": connected, "model": MODEL,
                "model_available": visible, "run_state": run_state,
            })

        def do_POST(self) -> None:
            if not self._guard(post=True):
                return
            if self.path != "/api/run":
                self._json(HTTPStatus.NOT_FOUND, {"error": "Unknown endpoint"})
                return
            if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                self._json(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, {"error": "JSON required"})
                return
            try:
                size = int(self.headers.get("Content-Length", "-1"))
                if not 0 < size <= 512:
                    raise ValueError("invalid length")
                payload = json.loads(self.rfile.read(size))
            except (ValueError, TypeError):
                self._json(HTTPStatus.BAD_REQUEST, {"error": "Invalid request body"})
                return
            if not isinstance(payload, dict) or payload != {
                "approve_one_call": True, "model": MODEL,
            }:
                self._json(HTTPStatus.BAD_REQUEST, {"error": "Explicit pinned-model consent required"})
                return
            with lock:
                if state["run_state"] != "IDLE":
                    self._json(HTTPStatus.CONFLICT, {"error": "One-shot already dispatched; no automatic retry"})
                    return
                # Authorize once BEFORE calling any plan-consuming logic.
                state["run_state"] = "RUNNING"
            try:
                if not authorization.status().get("connected"):
                    raise DiagnosticUIError("ChatGPT is not connected")
                models = authorization.models()
                if MODEL not in {item["slug"] for item in models}:
                    raise DiagnosticUIError("GPT-6 Luna is not in the authorized account catalog")
                result = launch_callback()
                if not isinstance(result, dict):
                    raise DiagnosticUIError("one-shot runner returned an invalid result")
                with lock:
                    state["run_state"] = "COMPLETED"
                self._json(HTTPStatus.OK, result)
            except (SIWCError, DiagnosticUIError):
                with lock:
                    state["run_state"] = "FAILED"
                self._json(HTTPStatus.BAD_GATEWAY, {
                    "error": "The diagnostic could not complete; no automatic retry was made. See terminal."
                })
            except Exception:
                with lock:
                    state["run_state"] = "FAILED"
                self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {
                    "error": "The diagnostic failed unexpectedly; no automatic retry was made. See terminal."
                })

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    return server, token


def main() -> None:
    parser = argparse.ArgumentParser(description="BananLoop local SIWC one-shot operator UI")
    parser.add_argument("--port", type=int, default=0, help="local TCP port, 0 means random")
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    server, token = create_ui_server(port=args.port)
    url = f"http://127.0.0.1:{server.server_port}/#{token}"
    print("BananLoop SIWC diagnostic — localhost only")
    print("Browser URL (do not share): " + url, flush=True)
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
