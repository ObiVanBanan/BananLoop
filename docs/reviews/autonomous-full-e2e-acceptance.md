# Приёмка BL-040 и BL-049 · 24.11.2026

Изолированная Git-копия — `/tmp/bl040-bl049.Cpsdey`, ветка
`accept-bl040-bl049`, база `ba5c0ecd5438907d889c6b4e52890809e4a0cc33`.
Исходная папка, стенд предыдущего исполнителя и исторические прогоны читались
без записи. Рабочие квитанции, манифесты и сценарии replay находятся под
`.offline-evidence/` в этой копии. Производственных модельных вызовов, новых
live smoke, публикаций и изменений канбана не было.

## BL-040 · Приёмка открыта

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

**Live gate закрыт.** Штатный launcher остановился до dispatch. Исходный
глобальный runtime preflight отклонил девять старых DEV-overlay. После
подключения safe-overlay и извлечения закреплённых публичных контрактов из
неизменённых fixture в локальные копии descriptor остались шесть
несогласованных контрактов. Подробности в
`.offline-evidence/launcher.log`, `launcher-safe.log` и
`launcher-bound.log`. Для диагностики единственной shortest-CI задачи
использован отдельный прямой worker под watchdog. Успешный
`ALL_NODES_COMPLETE` есть в историческом shortest-CI archive на
`ada1f18`, принятие текущей версии требует собственного успешного
прогона через launcher. Проверка BL-042 принята по условиям задания.
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
