# План реализации: BananLoop P0 после f062cba

## Общая информация

- Тип: correctness fixes и portable packaging.
- Источники: пользовательское исследование «BananLoop + Codex», research_report.md (обновление f062cba), design/adr-p0-correctness.md.
- Связанный PR: https://github.com/ObiVanBanan/BananLoop/pull/32.
- Один committable milestone: замкнутый набор P0-регрессий и исправлений без новой архитектуры. Запрос пользователя служит основанием для перечисленных изменений; push/merge и запуск GPU dogfood не входят в этот milestone.

## Дорожная карта (Milestones)

### M1: P0 product shell и достоверный учёт

- [x] Task 1.1: Добавить portable manifest и описать установочную структуру.
  - Файлы: plugins/bananloop/plugin.json, docs/codex-plugin.md; сохранить compatibility manifest.
  - DoD: root JSON соответствует официальному формату, skills обнаруживаются в root skills/, metadata согласованы; нет новых runtime dependencies.
- [x] Task 1.2: Защитить bootstrap от false goal/runtime requests.
  - Файлы: src/bananloop/bootstrap.py, tests/test_bootstrap.py.
  - DoD: bounded repair или BootstrapError вместо ложного needs_input; проверены exhausted repair, RU/EN формулировки, существующий interpreter, настоящие missing refs/credentials/packages/version. Готовность по-прежнему проверяет core.
- [x] Task 1.3: Развести измеренный outcome и попытку.
  - Файлы: src/bananloop/engine.py, src/bananloop/research.py (при необходимости), tests/test_engine.py, tests/test_completion.py, tests/test_research.py (по изменённым веткам).
  - DoD: NO_CHANGES/BLOCKED/UNGROUNDED/infra не увеличивают scientific plateau; реальный policy rejection увеличивает; phantom candidate остаётся PAUSED_INFRA; invalid evaluator/validation не становится scientific REJECTED; resume не дублирует учёт.
- [x] Task 1.4: Добавить прозрачный report accounting.
  - Файлы: src/bananloop/report.py, tests/test_attempt_accounting.py (или existing report tests), docs/codex-plugin.md.
  - DoD: JSON/Markdown показывают attempts и взаимоисключающие buckets; scientific count согласован с durable state; historical rows и recovered failure не считаются дважды; нет скрытых payloads в новых полях.
- [x] Task 1.5: Пройти testing, один review, security и deterministic workflow evaluation.
  - Файлы: plan/p0_completion.md, review_report.md, security_review.md; существующие regression tests.
  - DoD: scoped tests + full suite; negative/replay/infra fixtures; точные результаты и ограничения. Existing Codex source comments и untracked campaigns/uv.lock не включать в реализацию.

## Зависимости между задачами

Task 1.4 использует итоговую семантику Task 1.3. Task 1.5 выполняется после 1.1–1.4. Один implementation-agent выполняет M1, затем testing-agent, review-agent (один проход), security-agent; замечания исправляет implementation-agent с targeted rerun.

## Риски и заметки

- Имеющийся Python runtime не гарантирует нужную версию/пакеты. Не превращать любую строку со словом Python в false needs_input.
- Recovery с completed evaluator receipt не должен внезапно менять request hash. На несовместимые данные не выдавать научный verdict.
- Historical campaigns не мигрировать и не переоценивать silently.
- Rollback: отменить только P0 diff перечисленных product/test/docs файлов; schema/config/dependencies не меняются; исходный dirty codex.py сохранить.
- Baseline: полный набор тестов на f062cba запущен в отдельном `.tmp/p0-baseline-20261004-01`; результат будет внесён в completion. Новые basetemp должны иметь отдельные имена.
- Evaluation gate: deterministic command/stub scenarios против исходного поведения, без реальных model calls и без production model/prompt promotion. AnimeDiff dogfood остаётся следующим самостоятельным validation run.

## Completion record (2026-10-04)

- Portable manifest: root and compatibility JSON parsed; identity and user-facing descriptions match.
- Focused regression: `tests/test_bootstrap.py tests/test_engine.py` — 44 passed; attempt accounting — 4 passed after legacy-state review fix; two affected engine regressions — 2 passed.
- Full suite: 142 passed, 2 failed in Windows child-process cancellation (`tests/test_process.py` timeout/stop). The same two cases failed against unmodified f062cba baseline (131 passed, 2 failed).
- Review: one compatibility issue found and fixed; no second review pass. Security review: no new findings. Existing follow-up: consider a post-evaluator fingerprint check for frozen assets (P2).
- AnimeDiff `doctor --json` found `C:\Python313\python.exe` 3.13.1. `goal --prepare-only` could not start because the source checkout is dirty; no campaign state or bootstrap plan was created. The checkout was left intact.
- No commit, push, live model campaign, or GPU dogfood was performed in this milestone.
