# ADR: P0 для goal/skill и учёта попыток

## Status

Implementation scope from the user's supplied research, 2026-10-04. Extends the existing proposed hybrid design with its explicit P0 requirements; no production rollout or PR merge is implied.

## Context and decision

Исходный код f062cba уже реализует goal/skill, bounded bootstrap repair, core readiness и candidate proof. Реализовать ближайший объём из приложенного исследования: portable package, false-input guard и точный scientific-attempt accounting. Сохранить существующие budgets, isolation, command adapters и SQLite schema.

## Contracts

- Root `plugins/bananloop/plugin.json` использует Agent Plugins schema. OpenAI-specific metadata находится под extensions.com.openai; compatibility manifest сохраняется. Skill, CLI, модель и transport остаются прежними.
- Повторный запрос уже переданного goal или обнаруженного Python runtime направляется в bounded repair. Распознавание должно отличать отсутствующий interpreter/path от отсутствующих dependencies, credentials, references или требуемой несовместимой версии. Проверка не утверждает, что обнаруженный Python содержит нужные пакеты.
- Честный no-op завершается со статусом NO_CHANGES; невозможная реализация — IMPLEMENTATION_BLOCKED. Это потраченные attempts/calls, но не scientific trials и не plateau. UNGROUNDED/REDUNDANT/NO_HYPOTHESIS сохраняют несcientific поведение.
- Scientific trial требует корректного измерения и сравнения по неизменному протоколу. Ошибка evaluator, validation, materialization или несовпадение paired inputs не объявляются отрицательным научным результатом. Infra pause сохраняет стадию и receipt/retry semantics; успешный retry очищает временную ошибку перед окончательным учётом.
- Report добавляет `attempt_accounting`: total_attempts, scientific_trials, ungrounded, no_changes, implementation_blocked, infra_failures и необходимые buckets остальных/активных попыток. Это взаимоисключающие текущие исходы попыток, не число всех исторических ошибок actions; повторное чтение/возобновление не удваивает counts. Completed scientific_trials согласован с campaign counter. Historical legacy rows должны классифицироваться по имеющимся фактам без переписывания сохранённых кампаний.
- PRE-EVALUATION scope/leakage rejection имеет отдельный несcientific исход; физически проверенный недопустимый diff не считается измеренным проигрышем.

## Alternatives and limits

Оставить REJECTED для всех исходов — не выполняет запрос. Новая DB schema и полный EvaluatorBinding — лишняя миграция для этого P0. Выбран additive report и небольшие изменения существующей state machine. Signal sanity, расширенная stochastic calibration, profiles, hooks, Docker и MCP остаются последующим roadmap согласно документу пользователя.

## Security and validation

Не передавать hidden evaluator/holdout в worker или report summary дополнительных данных. Не запускать строки needs_input как shell-команды. Добавить регрессии для ложных и реальных missing inputs, смешанных попыток, replay, INVALID_VALIDATION и неизменности champion. Сохранить full-suite regression и отдельный обзор/security pass. Fixture checks проверяют control flow, а не доказанную полезность LLM-generated evaluator на AnimeDiff.

## Compatibility and rollback

Новые статусы относятся к experiment outcome, campaign stop API сохраняется. CampaignSpec hash и SQLite schema не меняются. Исторические reports получают additive accounting без записи в DB. Откат — убрать только изменения P0 в перечисленных файлах; сохранённые receipts/assets не удалять.
