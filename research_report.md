# Исследование существующего harness rag_tender

Дата: 2026-09-19. Исследованный HEAD: `e6e4df230edb07ac89999be4856807af9dcab119`.

## Введение

Область исследования: `harness_rag/`, его CLI entrypoints, связанные тесты, конфигурация и документы целей. Отчёт описывает фактическую реализацию. Предлагаемые изменения вынесены в [standalone_harness_proposal.md](standalone_harness_proposal.md) и [design/adr.md](design/adr.md).

Проверка выполнена чтением кода и запуском unit-тестов. Живая campaign, платные agent calls, Qdrant evaluation и приватные holdout не запускались. Внешнее состояние прежних campaign не исследовалось. Поэтому наличие механизмов подтверждено, но фактический прирост качества, стоимость и надёжность длительных campaign этим исследованием не измерены.

## Обзор архитектуры

Оба пользовательских entrypoint направляют выполнение в `optimization_runner.main`: [scripts/run_rag_harness.py](scripts/run_rag_harness.py), [harness_rag/__main__.py](harness_rag/__main__.py).

Активная композиция в [optimization_runner.py:159](harness_rag/optimization_runner.py#L159):

1. `install_research_first()` устанавливает research-first поведение.
2. `install_campaign_provenance()` добавляет campaign-scoped артефакты и provenance.
3. `install_current_dataset_optimization()` добавляет evaluation текущего корпуса и A/A сравнение.
4. Подменяются функции policy, rollback diagnostics и adoption champion.
5. Под OS-level lock выполняются recovery и `v2_runner.main()`.
6. `v2_runner.main()` вызывает stage engine `v2._execute_active()`.

Это композиция через замену функций модулей во время выполнения, а не иерархия runner-классов. `orchestrator.py`, `supervisor.py`, `resume.py` и `resume_dispatch.py` содержат предшествующие варианты orchestration. Их наличие само по себе не означает выполнение соответствующего поведения в текущем entrypoint.

## Компоненты

| Компонент | Проверенная роль | Основные символы и источник |
| --- | --- | --- |
| CLI и жизненный цикл | Чтение конфигурации, preflight, baseline, циклы, resume, итоговый outcome | [v2_runner.py:201](harness_rag/v2_runner.py#L201), `main`; [v2.py:799](harness_rag/v2.py#L799), `_parser` |
| Stage engine | Planner, researcher, implementer, tests, public/hidden, review, optional fixer, promotion | [v2.py:941](harness_rag/v2.py#L941), `_execute_active` |
| Research-first слой | Начальное исследование, причинная память, контекст реальных тендеров, изменение prompt/schema | [research_first_runner.py](harness_rag/research_first_runner.py), `install_research_first` |
| Provenance | Изоляция campaign-артефактов, связь campaign/attempt/experiment, механизмы и corpus evidence | [provenance_runner.py](harness_rag/provenance_runner.py), `install_campaign_provenance` |
| Текущий корпус | `match_batch.py`, компактные результаты по строкам, сравнение стадий/товаров, cache и A/A | [current_dataset_runner.py:278](harness_rag/current_dataset_runner.py#L278), `_run_dataset`, `_ensure_champion_current`, `_evaluate_candidate`, `_ensure_champion_repeatability` |
| Evaluation hook | GOLD evaluation, классификация ошибок процесса, public diagnostics, агрегированный hidden result | [hook.py:25](harness_rag/hook.py#L25), `_classify_eval_failure`, `run_eval`, `run_hidden_eval` |
| Metric policy | Восемь специализированных метрик, правила safety и финальный floor | [policy.py:11](harness_rag/policy.py#L11), `Metrics`, `accept_candidate`, `final_goal_met` |
| Активная acceptance policy | Пропускает flat public/hidden guardrails к проверке пользы validator-ом | [diagnostic_runner.py:75](harness_rag/diagnostic_runner.py#L75), `_accept_candidate` |
| Runtime | Git, subprocess, Codex CLI, JSON persistence, lock, rollback, tests, reindex | [runtime.py](harness_rag/runtime.py), `run_codex`, `harness_process_lock`, `rollback`, `rebuild_index` |
| Конфигурация и цель | Конкретные корпуса, модели, бюджеты, команды и текущая исследовательская гипотеза | [config.json](harness_rag/config.json), [GOAL.md](harness_rag/GOAL.md) |

## Поток выполнения

Новая campaign требует чистое рабочее дерево и запрещает branch `main`/`master`. Preflight проверяет public/hidden datasets, обязательные OpenAI/DeepSeek credentials и готовность непустой Qdrant collection. Эти требования зашиты в [v2_runner.py:201](harness_rag/v2_runner.py#L201) и [v2.py:477](harness_rag/v2.py#L477).

Согласно активным wrappers и [README](harness_rag/README.md), рабочий поток: champion -> исследование -> план одной гипотезы -> реализация -> tests -> public GOLD -> adaptive hidden -> полный текущий корпус -> validator -> promotion либо rollback. Полный candidate run текущего корпуса выполняется после public/hidden gates. Baseline champion и его A/A comparison кэшируются.

Принятое изменение становится новым champion; последующие эксперименты сравниваются с ним. Rollback отклонённого кандидата возвращает текущий champion, а не исходный commit campaign. См. [v2.py:571](harness_rag/v2.py#L571), `_rollback_and_record`, и [v2.py:890](harness_rag/v2.py#L890), `_promote`.

Текущий конфиг: 5 scientific iterations, предел CLI 1..7; 28 agent calls, 7 research calls, 7 reviewer calls, 0 fixer calls, 5 index builds. Модели выбираются статически по ролям. Это значения исследованного [config.json](harness_rag/config.json), а не рекомендация для standalone.

Команд `init` и диалогового согласования цели текущий parser не содержит: постановка поступает из фиксированных `GOAL.md`/config, а CLI требует `--holdout`. Источник: [v2.py:799](harness_rag/v2.py#L799), `_parser`.

## Состояние и память

Default state хранится вне репозитория в `~/.rag-trend-harness/<branch>/`; отдельные campaign получают собственные каталоги артефактов. Данные включают `state.json`, `history_v2.jsonl`, `events.jsonl`, preflight, agent outputs, evaluation summaries и compact rows. Источники: [v2.py:825](harness_rag/v2.py#L825), `_state_dir`; [provenance_runner.py](harness_rag/provenance_runner.py); [README](harness_rag/README.md).

В состоянии присутствуют champion commit/collection/metrics, active stage, plan, результаты стадий, usage, history, hypothesis ledger и research memory. `_result_row` сохраняет гипотезу, решение, причины, метрики, ошибки и уроки; research/provenance wrappers дополняют причинные данные. См. [v2.py:286](harness_rag/v2.py#L286), `_compact_memory`, `_update_ledger`, и [v2.py:533](harness_rag/v2.py#L533), `_result_row`.

`attempts_started` отделён от `scientific_iterations`: блокировка реализации или infrastructure pause не равна научно проверенной неудаче. Stable experiment ID связывает повторные attempts одной гипотезы. См. [v2_runner.py:18](harness_rag/v2_runner.py#L18), `_experiment_id`, `_ensure_execution_counters`, `_stamp_completion`, `_record_cycle_scientific`.

Для повторяющихся blockers предусмотрены mechanism counts/suppression; причинная память передаётся в prompts. Эти механизмы не устанавливают семантическую эквивалентность любых двух произвольных гипотез. См. [v2.py:672](harness_rag/v2.py#L672), `_blocked_mechanism_counts`, `_suppressed_mechanisms`; [research_first_runner.py](harness_rag/research_first_runner.py).

## Паттерны надёжности

| Механизм | Наблюдаемое поведение | Граница доказательства |
| --- | --- | --- |
| Atomic JSON replacement | `write_json` пишет `.tmp`, затем заменяет файл | Отдельные state/history/events/Git операции не образуют общую транзакцию: [runtime.py:30](harness_rag/runtime.py#L30), [v2.py:510](harness_rag/v2.py#L510) |
| OS-level lock | `msvcrt` на Windows, `flock` на POSIX; owner JSON диагностический | Lock принадлежит Git directory текущего worktree; это не изоляция всех внешних ресурсов: [runtime.py:77](harness_rag/runtime.py#L77) |
| Resume по стадии | Active stage сохраняется; infrastructure pause не закрывает experiment | Есть специальные recovery paths, а не общая транзакционная модель: [v2.py:599](harness_rag/v2.py#L599), [optimization_runner.py:66](harness_rag/optimization_runner.py#L66) |
| Persisted verdict | Сохранённый reviewer result применяется без второго agent call | Проверено соответствующим unit-test: [test_rag_harness_resume_review.py](tests/test_rag_harness_resume_review.py) |
| Partial rollback recovery | Чистое дерево при сохранённой candidate stage распознаётся как уже удалённый candidate | Закрывается как non-scientific failure, а не evaluation champion под именем candidate: [optimization_runner.py:66](harness_rag/optimization_runner.py#L66) |
| Promotion allowlist | Перед commit сравнивается множество текущих и ожидаемых paths, затем staged paths | Проверяются пути; content hash полного evaluated tree здесь не сопоставляется: [v2.py:853](harness_rag/v2.py#L853), `_promotion_paths`, `_promote` |
| No-op guard | Только tests/OpenSpec/harness changes не считаются causal product diff | Это существующая policy данного проекта: [v2.py:831](harness_rag/v2.py#L831), `_candidate_path_snapshot` |

Promotion сначала выполняет `git commit`, затем обновляет champion state и записывает completion. Из этой последовательности не следует гарантия exactly-once при аварии между commit и persistence. См. [v2.py:890](harness_rag/v2.py#L890).

## Интеграции и границы доступа

`run_codex` вызывает один конкретный CLI: `codex exec`, с моделью, reasoning effort, sandbox, schema и output file. Structured output разбирается как JSON object. Отдельного общего `AgentAdapter` в runtime нет. См. [runtime.py:282](harness_rag/runtime.py#L282).

`ROOT` определяется расположением harness-файла. Runtime subprocess работают в этом ROOT. `v2.py` импортирует `nomenclature_matcher.settings.Settings`; preflight и reindex знают Qdrant, LD catalog и provider credentials. См. [runtime.py:14](harness_rag/runtime.py#L14), [v2.py:36](harness_rag/v2.py#L36), [v2.py:477](harness_rag/v2.py#L477), [runtime.py:343](harness_rag/runtime.py#L343).

Protected paths содержат `harness_rag/`, `data/`, catalog, index/evaluation scripts и domain rules. Проверка реализована через анализ изменённых paths. `tests/` не входит в этот protected list. См. [runtime.py:16](harness_rag/runtime.py#L16), `protected_changes`.

Hidden dataset должен быть вне ROOT. После успешной hidden evaluation raw rows удаляются, сохраняется summary. `_agent_env` удаляет переменные с holdout-префиксами, но начинается с `os.environ.copy()`. Эти меры ограничивают передаваемый контекст; в этих функциях нет OS-level запрета чтения произвольного внешнего файла или общего env allowlist. Raw output существует до удаления; авария в этом интервале не покрывается обещанием отсутствия приватных файлов. См. [hook.py:128](harness_rag/hook.py#L128), [runtime.py:253](harness_rag/runtime.py#L253).

Rollback выполняет `git reset --hard` и `git clean -fd` в текущем ROOT с исключениями runtime temp-каталогов. Отдельного sandbox checkout для каждого кандидата в данном runtime нет. См. [runtime.py:212](harness_rag/runtime.py#L212).

Отдельный legacy [scripts/codex_loop.py](scripts/codex_loop.py) содержит timeout для agent/verification (`run_codex`, `verify`), diff budget с учётом untracked files (`diff_stats`, `enforce_diff_budget`) и блокировку повторяющегося failure по plan hash (`register_failure`, `iteration`). Эти функции не являются доказательством аналогичного поведения активного v2 entrypoint.

## Метрики и ограничения

1. **Primary evidence и goal predicate различаются.** [GOAL.md](harness_rag/GOAL.md) определяет текущие 783 mostly-unlabeled rows как primary corpus. `_accept_candidate` допускает flat labeled guardrails; пользу затем оценивает validator. Однако финальный `GOAL_MET` вычисляется через `final_goal_met(public, hidden, coverage_floor)`; метрики 783 корпуса в predicate не участвуют. Источники: [diagnostic_runner.py:75](harness_rag/diagnostic_runner.py#L75), [current_dataset_runner.py:577](harness_rag/current_dataset_runner.py#L577), [v2_runner.py:340](harness_rag/v2_runner.py#L340), [policy.py:85](harness_rag/policy.py#L85).
2. **Sealed final является отдельным отчётным измерением.** `_maybe_run_sealed_final` запускается только после выполнения public/hidden goal и сохраняет summary. Outcome текущего runner после этого снова вычисляется по champion public/hidden; sealed final summary не включается в `goal_met`. Источники: [v2.py:1561](harness_rag/v2.py#L1561), [v2_runner.py:346](harness_rag/v2_runner.py#L346).
3. **Отсутствующая метрика получает 0.0.** `Metrics.from_summary` заменяет `None`/отсутствие значения нулём. Нет общего контракта completeness для произвольного metric set. Источник: [policy.py:22](harness_rag/policy.py#L22).
4. **Budget ограничивает counts.** `_remaining_budgets` проверяет agent/role/index calls; elapsed и reported tokens собираются как телеметрия. Токены извлекаются regex из текстового лога. В этих бюджетах нет денежного ceiling или campaign deadline; основные `subprocess.run` вызовы не задают timeout. Источники: [v2.py:355](harness_rag/v2.py#L355), [runtime.py:37](harness_rag/runtime.py#L37), [runtime.py:282](harness_rag/runtime.py#L282), [hook.py:72](harness_rag/hook.py#L72).
5. **Usage фиксируется после agent call.** Счётчики обновляются в `finally`; до запуска внешнего процесса durable reservation не производится. При прекращении процесса до `finally` соответствующая запись может отсутствовать. Источник: [v2.py:390](harness_rag/v2.py#L390).
6. **Cache не является полным execution fingerprint.** Champion cache использует commit, dataset SHA и наличие output. Candidate fingerprint строится по `git diff --`, который не включает staged/untracked содержимое. В cache predicate нет полного набора зависимостей, environment, evaluator и внешнего index state. Источник: [current_dataset_runner.py:364](harness_rag/current_dataset_runner.py#L364), `_ensure_champion_current`, `_evaluate_candidate`.
7. **A/A существует как evidence.** Выполняется один cached repeat champion, сравниваются row-level переходы. Результат включается в validator prompt. Это не реализованный общий статистический тест улучшения. Источник: [current_dataset_runner.py:510](harness_rag/current_dataset_runner.py#L510), `_ensure_champion_repeatability`, `_current_prompt_block`.
8. **Adaptive hidden используется в цикле.** Его агрегаты участвуют в сравнении кандидатов и передаются через разрешённый summary. Существует отдельный optional sealed final. Разделение явно описано в [GOAL.md](harness_rag/GOAL.md) и реализовано в [v2.py](harness_rag/v2.py), `_visible_hidden`, `_execute_active`, `_maybe_run_sealed_final`.
9. **Итоговый отчёт уже есть, но его метрики специализированы.** `FINAL_REPORT_V2.md` содержит outcome, counts, champion, public/hidden/final hard-pass и историю. Это не общий отчёт с произвольными objective/guardrail units и денежной стоимостью. Источник: [v2_runner.py:161](harness_rag/v2_runner.py#L161).

## Проверка тестами

`python -m pytest -q`: **263 passed, 1 warning in 4.62s**. Предупреждение: pytest не смог записать `.pytest_cache/v/cache` из-за Windows permissions. Первый запуск в sandbox завершился `233 passed, 30 errors`; ошибки возникли при доступе fixture `tmp_path` к системному `pytest-of-theso`. Повтор с необходимым доступом прошёл.

| Область | Примеры проверяющих файлов |
| --- | --- |
| Acceptance и final floor | [test_rag_harness_policy.py](tests/test_rag_harness_policy.py), [test_rag_harness_v2_diagnostics.py](tests/test_rag_harness_v2_diagnostics.py) |
| Counts и scientific semantics | [test_rag_harness_v2_scientific_budget.py](tests/test_rag_harness_v2_scientific_budget.py), [test_rag_harness_v2.py](tests/test_rag_harness_v2.py) |
| Resume и recovery | [test_rag_harness_resume_review.py](tests/test_rag_harness_resume_review.py), [test_rag_harness_optimization_recovery.py](tests/test_rag_harness_optimization_recovery.py), [test_rag_harness_v2_recovery.py](tests/test_rag_harness_v2_recovery.py) |
| Память и provenance | [test_rag_harness_planner_memory.py](tests/test_rag_harness_planner_memory.py), [test_rag_harness_v2_research_first.py](tests/test_rag_harness_v2_research_first.py), [test_rag_harness_v2_provenance.py](tests/test_rag_harness_v2_provenance.py) |
| Current corpus и A/A | [test_rag_harness_current_dataset.py](tests/test_rag_harness_current_dataset.py) |
| Lock и Windows paths | [test_rag_harness_runtime_windows.py](tests/test_rag_harness_runtime_windows.py) |
| Hidden summary | [test_rag_harness_v2_hidden_summary.py](tests/test_rag_harness_v2_hidden_summary.py) |

Наличие passing tests не подтверждает все интеграционные свойства. Например, `test_v2_entrypoints_route_directly_to_v2` ищет строку импорта, которая теперь сохранена в compatibility comment entrypoint; фактическая цепочка установлена чтением executable imports, а не только этим тестом.

## Вывод

В прототипе присутствуют законченные механизмы iteration, champion promotion, staged gates, experiment memory, counts, resume и reports. Их реализация тесно связана с конкретным репозиторием, Codex CLI, OpenSpec, RAG datasets, Qdrant и доменными метриками. Не установлены фактическая эффективность прошлых campaign, стоимость достижения результата, общая изоляция скрытых данных и переносимость на другой проект. Дизайн самостоятельного продукта должен опираться на эти факты, не считать такие свойства уже доказанными.
