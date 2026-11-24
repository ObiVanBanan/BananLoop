# Приёмка BL-040 и BL-049 · 24.11.2026

Изолированная Git-копия — `/tmp/bl040-bl049.Cpsdey`, ветка
`accept-bl040-bl049`, база `ba5c0ecd5438907d889c6b4e52890809e4a0cc33`.
Исходная папка, стенд предыдущего исполнителя и исторические прогоны читались
без записи. Рабочие квитанции, манифесты и сценарии replay находятся под
`.offline-evidence/` в этой копии. Производственных модельных вызовов, новых
live smoke, публикаций и изменений канбана не было.

## Повтор BL-040 · Успешная Optimization на полной DEV fixture

Оба прогона подготовлены на `ca46e9dd6917b657dcd5ef33ea648954ed5b95a8`.
Использованы штатные Core, SQLite, конфигуратор, Harbor 0.25.0 и проверка
публичной DEV fixture. Подставлены только модельные ответы и измерение.
Integration verifier получил текущие digest и snapshot и вернул 97 корректных PASS.
DEV fixture сохранила закреплённый агрегированный кейс, а fake scorer вернул
все поля идентичности 24 настроек с 32 повторениями. В протоколе выбрано
10 повторных запусков на snapshot в разрешённом диапазоне.

В прямом прогоне `success-direct/arm/` получены `ALL_NODES_COMPLETE` и
`COMPLETED`. Scheduler израсходовал 980 из 1000 evaluator calls.
Оставшихся 20 недостаточно для резервирования 30 на следующий шаг.
Дедлайн, технический лимит шагов и бюджет модели не были превышены.
Отчёт подтвердил DEV baseline 0,7 и Champion 0,5.
Champion `42b7a4fae8731f5f9f8ba8921316623490c2d686`, revision 97.
Манифест `d915cb113196943814ee8d980df40e9e5fddbc1bb01b5d68d97c3a54b5c91d6b`.
SHA-256 итогового `interval.py`
`5cc3120a2950c5aafc3d4f0893ca5e4ec734b9c6d4239efdad34d76486ea59b0`
сверен с Git snapshot. В Docker по двум идентифицированным проектам
пусто. Детали и точный путь к отчёту в
`.offline-evidence/success-direct/arm/acceptance-check.json`.

### Штатный launcher

**BL-040 проходит офлайн-приёмку.** Запуск `success-launcher/arm/`
прошёл свежий bound preflight и `GATE_PASSED`. Watchdog, worker и Harbor
trial завершились, Optimization вернула `ALL_NODES_COMPLETE`. Launcher
сохранил `CAPTURED` из квитанции worker и записал `CLEANED` для обоих
Compose-проектов. Read-only проверка Docker подтвердила отсутствие
контейнеров этих проектов. Git snapshot
`daac7d7378f853f2e42ad4a73b1026f24775a0b0`, revision 97, совпал с
архивным `interval.py`. SHA-256 артефакта одинаков в обоих прогонах.
Дедлайны `1795544077.6633992` и `1795544223.0244079` соответственно
совпали внутри каждой пары watchdog → Exploration → Optimization.
Оба прогона имели диагностический бюджет времени 1800 секунд.

Оба preparation-манифеста имеют один канонический digest
`d915cb113196943814ee8d980df40e9e5fddbc1bb01b5d68d97c3a54b5c91d6b`.
Файлы `.offline-evidence/manifest-success-direct.json` и
`.offline-evidence/manifest-success-launcher.json` сериализованы
по-разному. Квитанция launcher — `success-launcher/arm/result.json`,
сверка — `success-launcher/arm/acceptance-check.json`. Bound preflight —
`.offline-evidence/preflight-success-launcher.json`,
SHA-256 `0d398c0244c770a5cbe6c28bc2bd89906e5ef635041ee0bafd17acd9d8f07aa9`.

В обоих прогонах Core учёл 194 вызова Optimization и 5820 синтетических
токенов. Bridge подтвердил 194 вызова, но поле суммарных токенов оставил
`UNKNOWN`: успешный CLI-результат передаёт историю вызовов без агрегата
`usage`, который читает Harbor. Расход в итоговой квитанции сохранён
как выдал launcher. Live gate по-прежнему закрыт, квитанция preflight
содержит `authorization=NOT_ESTABLISHED`.

Сценарии находятся в `.offline-evidence/one_success_worker.py` и
`.offline-evidence/launch_success.py`, replay — в
`.offline-evidence/fake-success/sitecustomize.py`. Каждый запуск
использовал Python из `harbor-venv` и
`PYTHONPATH="$PWD/.offline-evidence/fake-success:$PWD/src:$PWD"`.
`check-success.py` сверяет обе терминальные квитанции с SQLite,
SHA-256 артефакта и инвентарём Docker.

### Шесть отказов preflight

Причина была в локальных descriptor из первого прогона. Они связывали
устаревшие evaluator bundles с отдельными MCP-контрактами. Извлечение
старых контрактов из ZIP устранило расхождение байтов, но сами контракты
не соответствовали текущей канонической схеме.

| Задача | Прежний digest | Точный дефект старого контракта | Digest принятой пары |
| --- | --- | --- | --- |
| Imputation | `c561ab7b950f` | Расширенная запись предыдущего smoke с лишними полями, инструментами с `_meta` и другим `probe_scope` | `d8bac3004bd6` |
| CARPS | `bb1a77e3a6bf` | Неканонический `discovered_tools/0/inputSchema/properties/note/default` | `01aaf13d39b5` |
| CPU decoder | `34f757eab9ef` | Тот же путь `note/default` | `670d56af2a07` |
| Labelrisk | `c0c16eb8da66` | Тот же путь `note/default` | `65a605455d65` |
| Sketched Newton | `98d0b7b30f7f` | Тот же путь `note/default` | `4e2559fa007be` |
| SOPCC | `091af29b9835` | Тот же путь `note/default` | `2683da2f64ac` |

Для каждой задачи переключён весь descriptor на согласованную пару
fixture и контракта из сохранённого `bl044-safe-20261123/runtime`.
Локальная копия находится в `.offline-evidence/runtime-launch-success/`.
Использованы существующие публичные файлы. Контракты проверены по точным
task и instruction SHA, SHA обоих файлов и байтовому совпадению
контракта внутри ZIP. Все шесть отказов ушли. Свежий штатный preflight
в исходных проверках вернул `READY_NON_RPDI_DIAGNOSTIC`, blockers пуст.
Полные digest, JSON-пути отличий и пути к обеим версиям перечислены в
`.offline-evidence/contract-audit.json`.

Повторная read-only проверка Imputation показала, что `arm/result.json`
по-прежнему отсутствует, а вложенный Harbor job имеет
`finished_at=null`, `updated_at=2026-11-24T14:20:08.387456Z`.
Архив не менялся.

В этом повторе профильная команда из раздела «Воспроизведение» дала
**319 passed за 42,91 с.** Лог — `.offline-evidence/pytest-followup.log`.

## BL-040 · Первый диагностический проход

Прямой Full-worker под штатным watchdog из чистой рабочей области прошёл
`v2 explore`, независимое `APPROVE`, `BENCHMARK_AUTO_CHILD_CREATED`, сборку
дочернего конфига и реальный Harbor 0.25.0. Модельные ответы локальные.
Дочерний план использовал только DEV и ровно один закреплённый путь
`benchmarks/autoresearchexam/solutions/shortest_valid_ci/interval.py`.
В `arm9/bananloop-exploration.jsonl` сохранены proposal digest
`b481a2107dbc8d9ad22cc5c0d8e7e68d66c4ee6441dd8bf33f8a0e75ede6374d` и binding
digest `9d6b27ed7bb6afbb31d7bfaa8338c4625e567de68a3f8dfb083daae06d431003`.
Вход worker, pivot, конфиг Optimization и итоговая квитанция имеют
один абсолютный дедлайн `1795542065.34351`.

- `arm5/result.json` — внешний `TIMED_OUT`, частичные логи и baseline
  `CAPTURED`. В дочернем CLI зафиксирован сбой ограниченного локального
  DEV-вычисления. Прогон использовал прямой диагностический запуск с
  условным digest и служит только доказательством обработки таймаута.
- `arm9/worker-result.json` — настоящий preparation-манфест
  `61457a36d7b9a1c5083cc3a9af0b7f224190760f862e2f447d128c9a7cd16465`,
  код `d6f64c4f1a51af958e97b30d41626a7d70da0824`. Watchdog и Harbor вернули
  код 0, Harbor trial завершился, bridge вернул `RUN_FAILED`, worker записал
  `OPTIMIZATION_FAILED` и `CAPTURED`. Причина прогона — неполная форма
  синтетического DEV-ответа, отклонённая штатным проверяющим адаптером.
  `arm9/inventory.json` различает job и trial и подтверждает полный
  инвентарь для одного trial. После завершения в Docker не наблюдалось
  запущенных контейнеров этого trial.
- Регрессия `test_fake_24_arm_series_uses_real_backend_wiring_and_archives_all_outputs[child_deadline]`
  проверяет Harbor exit 0 с дочерним `DEADLINE_EXCEEDED`, итоговый
  `TIMED_OUT`, сохранённые логи и артефакт, а также неизвестное значение
  расхода при частичной квитанции.
- `test_benchmark_pivot_authorizer_binds_exact_frozen_scope_and_deadline`
  отклоняет изменения task, evaluator и candidate path даже с пересчитанным
  digest. `test_supervised_pivot_keeps_explicit_approval_requirement_and_receipt`
  подтверждает обязательность HIL в обычной Supervised-кампании.

**Первый preflight.** Штатный launcher остановился до dispatch. Исходный
глобальный runtime preflight отклонил девять старых DEV-overlay. После
подключения safe-overlay и извлечения закреплённых публичных контрактов из
неизменённых fixture в локальные копии descriptor остались шесть
несогласованных контрактов. Подробности в
`.offline-evidence/launcher.log`, `launcher-safe.log` и
`launcher-bound.log`. Для диагностики единственной shortest-CI задачи
использован отдельный прямой worker под watchdog. Успешный
`ALL_NODES_COMPLETE` есть в историческом shortest-CI archive на
`ada1f18`, следующий раздел фиксирует повтор текущей
версии через launcher. Проверка BL-042 принята по условиям задания.
BL-043 отложена.

## BL-049 · Офлайн-проверки выполнены, карточка открыта

Сценарий `test_optimization_lifecycle_integrates_durable_evaluator_and_verification_queues`
работает с реальными Core и SQLite. Он проверяет неизменный Champion при
Core DEV FAIL с `regression_policy=false`, восстановление очереди и новую
гипотезу с evidence предыдущего отклонения. Вариант `[verifier-unknown]`
после нового DEV-результата симулирует сбой verifier после dispatch.
Результат и расход остаются UNKNOWN, integration receipt отсутствует,
pause/resume снова останавливается на неопределённом job без повторного
вызова, stop терминален, Champion предыдущей ревизии сохранён. При
долговременном UNKNOWN продолжение требует явного разрешения по процедуре
recovery и доказательства состояния расхода и квитанции. В зачёт
числовых ограничений также входят проверки лимита итераций, бюджета,
общего дедлайна и компактных идемпотентных ключей.

Диагностика verifier пишет класс ошибки, фиксированную стадию и
безопасные schema-категории либо место в проектном коде. Стадии
`provider call`, `response validation` и `result persistence` видны в
событии job. Из произвольного исключения строки и входные значения не
копируются. Тесты launcher/inventory подтверждают сохранение
`CAPTURED`, отдельный учёт cleanup и UNKNOWN при неполном инвентаре.

### Исторические артефакты

- **Imputation.** `/home/sonik/bananloop-benchmark-env/runs/bl049-imputation-fixed-smoke-20261124`.
  Код `13fda028ff54f64b6ea1a5aa5482a810ac995d57`, manifest
  `164f0e93c4ac7acaf62ca4a3e2fef908a6e4be20f8d22d8a4d049b84f81fed85`.
  Неизменяемое чтение `state/are-01-bananloop_full/campaigns/c-1cb2b633fa89d395138fef1cb74a88fb/campaign.sqlite3`
  показало 18 FAIL, 3 PASS, Champion
  `3705b0c59ac29ae34f28d45d6f1c6e658676d3d0` revision 3 и один
  `VERIFICATION/READY` узел. На момент проверки `arm/result.json`
  отсутствовал, Harbor job не имел `finished_at`. Итог, usage и cleanup
  по промежуточной квитанции не устанавливаются.
- **CARPS.** `/home/sonik/bananloop-benchmark-env/runs/bl049-carps-timeout-smoke-20261124/arm/result.json`.
  Код `2093475557b2d62f85fc39800e32cfc44737edaa`, manifest
  `34af3de5b2b7e1ffc462f1b312f2d890d9ac4961a983d3a7bd19b0b031bf19fb`.
  `OPTIMIZATION_FAILED`, `PUBLIC_DEV_CANDIDATE_TIMEOUT`, завершённый
  Harbor trial, `CAPTURED`, cleanup `CLEANED`. Exploration 47 003
  входных и 1 562 выходных токена. Один Optimization call, usage UNKNOWN.
  Это диагностика таймаута DEV. Квитанции решения integration verifier
  для данного кандидата нет.

Блокер закрытия BL-049 — ограниченный live smoke на проверенной
версии с терминальной квитанцией, расходом и подтверждением cleanup. В рамках этого задания live запрещён.

### Повтор с полным публичным DEV fixture

`arm11/arm/` на `62b1939a122345f31fc989b8c6b69ef922d0ffd6`, manifest
`e802b908dd1e8d9a6b930675707e0e07a95558d2389a65f17709dde6ea6af695`.
Тот же реальный Harbor и штатный DEV-адаптер с локальной подстановкой
измерения прошли три последовательных кандидата. Первый получил PASS и
стал Champion, второй получил Core FAIL по регрессии, третий дошёл до
отправки verifier и остался UNKNOWN. Это отрицательный сквозной сценарий
через реальные Core, SQLite и Harbor. В дочернем SQLite есть только один
PASS и один FAIL, Champion `da277496753943a97a39e2afbd751278519d7dd7`
revision 1. В registry job `integration-6b864dc91bc43a87a067d4d0fdc7edfdf12e4e57`
имеет событие `JOB_UNKNOWN` с классом `RuntimeError` и стадией
`provider call`. Обе резервации в состоянии UNKNOWN с `actual=NULL`.
Worker записал `OPTIMIZATION_FAILED`, один UNKNOWN usage event и
завершённый Harbor trial. Общий дедлайн `1795542167.3168619` совпал в
манифестированных входах worker, pivot и дочернем конфиге.

Этот прогон воспроизвёл отдельную потерю в отчётности: в архив
`final-artifacts` попал baseline, хотя первый Champion сохранялся в
дочернем дереве snapshot. Исправление читает последнее продвижение из
Core только при наличии связанной PASS-квитанции для того же действия и
кампании, проверяет Git HEAD и чистоту snapshot и сохраняет артефакт
Champion с явным источником. Статус ошибки и расход UNKNOWN сохраняются.

На исправленном коде `622201dae60072c4bed82fd5e604371234c60d81`
повтор `arm12/arm/` с manifest
`20e2f3296279b30bfcc2546dca4256602845ce94fc3eece2d0b5e99cd66da743`
подтвердил тот же путь и архивирование (worker `OPTIMIZATION_FAILED`,
Harbor trial `COMPLETED`, источник артефакта `VERIFIED_CHAMPION`).
Champion `811d2e05f64a9f4819181e28f0d61f1198a3dd52` revision 1.
SHA-256 скопированного `interval.py`
`5f3c921bb4a9e599236399159d0f9b3093383b61fff3f21fd7d80540f1d39e4c`
совпадает с Git snapshot. Абсолютный дедлайн `1795542322.9328172`
совпал у watchdog, worker, pivot и Optimization. Проверка
`acceptance-check.json` сверила SQLite, неизменившийся Champion, одну
отправку UNKNOWN job, неизвестные agent_calls/model_tokens, digest
артефакта и отсутствие контейнеров для двух Compose-проектов этого trial.
Очистка production launcher в данном прогоне не вызывалась.

## Воспроизведение

Из корня изолированной копии:

```sh
PYTHONPATH=src:. /home/sonik/bananloop-benchmark-env/harbor-venv/bin/python -m pytest -o addopts='' -q \
  tests/test_autonomous_v01*.py tests/test_v2_campaign_cli.py tests/test_v2_pivot_saga.py \
  tests/test_v2_optimization*.py tests/test_optimization_lifecycle.py \
  tests/test_evaluator_verification.py tests/test_rpdi13_integrated_acceptance.py
```

**319 тестов прошли за 43,84 с.** Лог — `.offline-evidence/pytest-final.log`. Самостоятельный запуск выбранного Full-worker
использовал `PYTHONPATH="$PWD/.offline-evidence/fake:$PWD/src:$PWD"` и
`.offline-evidence/one_worker.py` под Python из того же venv, с новым
именем прогона и preparation-манифестом для точного SHA. Скрипт сохраняет
вход, watchdog, worker receipt и инвентарь в отдельной папке. Replay
отключает HTTPS модели, ответы и оценка детерминированы локально.

## Offline verification of Optimization token accounting

The production launcher replay on `cb0d32b` finished with
`ALL_NODES_COMPLETE` and cleanup `CLEANED`. The Harbor bridge reported
194 Optimization calls and 3880 input plus 1940 output tokens (5820 total).
Those counts match the saved Core provider calls. The arm separately
recorded 40 input and 20 output tokens for Exploration, with no unknown
usage events. These are synthetic replay tokens. The output is in
`.offline-evidence/success-usage-launcher/arm/result.json`.
