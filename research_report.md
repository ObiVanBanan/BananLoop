# BananLoop: запуск, измерение и автономный цикл — AS IS

Дата: 2026-10-04. Исходный снимок исследования: HEAD 62f7830. Основной текст ниже описывает этот исторический снимок; обновление для f062cba приведено в конце. Исходный анализ включал существовавшую локальную правку src/bananloop/agents/codex.py.

## Введение

Запрос: репозиторий + человеческая цель, автоматическая подготовка измерения и автономное улучшение, возможная интеграция в Codex через plugin/skills/hooks. Здесь зафиксированы факты; предлагаемое решение находится в [ADR](design/adr.md).

Предыдущий корневой research_report.md описывал извлечение harness из rag_tender от 19 сентября, а не нынешний BananLoop. Его точная копия сохранена в [историческом отчёте](docs/research-rag-tender-2026-09-19.md); относительные ссылки внутри копии относятся к исходному расположению и старому репозиторию.

## Обзор архитектуры

Действующий путь: cli.cmd_start -> bootstrap_campaign -> CampaignSpec/SQLite -> cli.cmd_run -> Engine.run. start требует только --goal, --repo имеет default ".". Есть --prepare-only. Это уже реализация repo+goal, а не только предложение в документации. Источники: src/bananloop/cli.py:172,329; src/bananloop/bootstrap.py:150.

Основной цикл: PROPOSE -> EXECUTE -> SNAPSHOT -> VERIFY -> EVALUATE -> DECIDE -> ANALYZE/VALIDATE -> PROMOTE/REJECT -> RECORD. Финальная holdout-оценка выполняется отдельно. Источник: src/bananloop/engine.py:1294.

## Компоненты

| Компонент | Реальная ответственность | Источник и символ |
| --- | --- | --- |
| CLI | start/init, run/resume, stop/status/report/export | src/bananloop/cli.py:172,225,271,289,329 |
| Discovery | Эвристики pytest/npm/make/tox и обнаружение Python runtimes | src/bananloop/wizard.py:58,106, discover_project |
| Bootstrap | Один вызов агента, проверка JSON и области изменений, заморозка assets, создание spec | src/bananloop/bootstrap.py:150, bootstrap_campaign |
| Контракты | MetricSpec, BudgetSpec, AgentConfig, CampaignSpec, EvaluationResult | src/bananloop/contracts.py:69,87,110,123 |
| Engine | Стадии, вызовы агентов/evaluator, recovery, финализация | src/bananloop/engine.py:61,268,400,1244 |
| Evaluator | Запуск проектной argv-команды, чтение JSON, валидация EvaluationResult | src/bananloop/evaluators/command.py:16, evaluate |
| Policy | Primary delta, guards, non-regression, checks, paired cases | src/bananloop/policy.py:64, decide |
| Workspace | Private clone, sanitized agent copy, перенос patch, scope | src/bananloop/workspace.py:32,82,115,158 |
| Integrity | SHA файлов evaluator и проверка неизменности | src/bananloop/integrity.py:35,80 |
| Store | Spec, actions, receipts, reservations, atomic promotion | src/bananloop/store.py:157,266,287,371 |
| Research | DEV-only память, механизмы, lessons, режимы планирования | src/bananloop/research.py:20,231,298 |
| Adapters | Codex exec, Pi RPC, произвольная JSON stdin/stdout-команда | src/bananloop/agents/codex.py:197; agents/pi.py:93; agents/command.py:12 |

### Что автоматизировано при start

Bootstrap получает human goal, test-command hints и обнаруженные Python runtimes. В prompt требуется выбрать существующий подходящий evaluator или создать адаптер в .bananloop_eval, фиксировать входы/случайность, проверить smoke и исправить ошибки запуска. Проектный контекст сохраняется для следующих ролей. Источники: bootstrap.py:87–123; agents/codex.py:209–230.

Разрешены изменения только .bananloop_eval/**. После agent call создаётся snapshot, проверяется diff; нарушения откатываются. Все файлы этой директории и обнаруженные evaluator assets хешируются и защищаются. Источник: bootstrap.py:131–147,191–199,217–275.

### Что start пока не подтверждает самостоятельно

- ready проверяет непустые evaluation_command/project_context и отсутствие needs_input. Сам bootstrap не запускает отдельный evaluator probe и не требует проверяемый smoke receipt. Smoke пока является обязанностью агента в prompt. Источник: bootstrap.py:65–75,184–186,217–290.
- Это один supervisor-level agent call: отдельного цикла smoke -> diagnosis -> repair в bootstrap нет. Ошибка агента/схемы вызывает BootstrapError. Внутри собственного вызова агент может делать несколько действий. Источник: bootstrap.py:183–215.
- CampaignStore создаётся после bootstrap. Будущие campaign budgets не учитывают bootstrap как обычный engine action; его ограничивает отдельный timeout, по умолчанию 1200 секунд. Источник: bootstrap.py:150–186,267–290; cli.py:350.
- Default state directory определяется хешем пути repo. Новая цель для repo с существующей DB требует другого state-dir либо обращения к старой кампании. Источник: contracts.py:410–423; bootstrap.py:166–169.
- --allow-dirty только отключает clean-check. Базой остаётся HEAD; незакоммиченные изменения не включаются автоматически. Источник: cli.py:172–175; bootstrap.py:172–175; workspace.py:32–42.
- Expert init и wizard продолжают запрашивать command/metric/target/min_improvement. Wizard не превращает произвольную test-команду в evaluator JSON самостоятельно. Источник: wizard.py:163–209; cli.py:67–152.

## Паттерны и гарантии

1. **Evaluator отделён от coding agent.** Agent workspace не содержит исходную .git-историю, .bananloop_eval и frozen/hidden assets; implementation возвращается patch. Вердикт вычисляет policy, а не self-report агента. Источники: engine.py:100–146; workspace.py:82–142; policy.py:64–138.
2. **Immutable measurement contract.** CampaignSpec canonical JSON хешируется; engine проверяет hash. Файлы assets сверяются перед оценкой. Источники: contracts.py:184; store.py:157; engine.py:1255; integrity.py:80.
3. **Replay через durable receipts.** COMPLETED action переиспользуется; неизвестный исход RUNNING не исполняется слепо повторно. Входы сохраняются/сверяются по hash. Источник: engine.py:268–367.
4. **Atomic promotion.** Champion, promotion event и следующая стадия сохраняются транзакционно; повторное завершение не удваивает счётчики. Rejection возвращает champion. Источники: store.py:266–310; engine.py:1173.
5. **DEV, validation, final holdout.** Validation проверяет только прошедших DEV кандидатов. Configured holdout влияет на GOAL_MET и не возвращает поиск в adaptive loop. Validation/holdout optional. Источники: engine.py:152–189,400–451,1063–1166; contracts.py:131.
6. **Контекст и бюджет.** Агент получает DEV-view; diagnostics разрешаются явно. Лимиты включают calls, attempts, experiments, work units, wall time и plateau; финальный evaluator slot резервируется. Источники: research.py:20–54,231–295; contracts.py:87–102; engine.py:218–265.

## Интеграции и модели

В CampaignSpec один AgentConfig для bootstrap/propose/implement/analyze. Нет выбора модели по роли. --model передаётся Codex как -m; если он отсутствует, адаптер его не добавляет. Extra args позволяют передать настройки Codex. --thinking и --provider CLI относятся к Pi. Источники: contracts.py:110–148; cli.py:49–64; engine.py:85–91; agents/codex.py:258–285.

Текущая локальная правка CodexAdapter передаёт большой prompt через stdin "-", сохраняя schema/output-file протокол. Правка существовала до анализа и не менялась.

В src/pyproject/.github не обнаружены plugin manifest, skill, hook bridge, MCP server или собственный loader профилей BananLoop. Entrypoint — Python CLI. Локально проверены codex-cli 0.158.0 и codex exec --help: доступны -m, -c, --profile, --output-schema и stdin "-". Реальные model calls при исследовании не выполнялись.

Официальная документация на дату анализа подтверждает:

- Plugin может объединять skills, MCP и lifecycle hooks: [упаковка плагина](https://developers.openai.com/plugins/build/plugins).
- Stop hook может запросить продолжение через decision=block/reason. Непроверенные non-managed hooks пропускаются до trust review; Stop matcher не поддерживается. Это событие завершения хода, а не evaluator-контракт BananLoop: [Hooks](https://learn.chatgpt.com/docs/hooks).
- /goal поддерживает продолжение работы между ходами; документация требует определить критерий завершения и проверку: [Follow a goal](https://learn.chatgpt.com/use-cases/follow-goals).
- Модель/reasoning задаются в custom agent TOML или настройках запуска; CLI profiles представлены отдельными name.config.toml: [Subagents](https://learn.chatgpt.com/docs/agent-configuration/subagents), [Profiles](https://learn.chatgpt.com/docs/config-file/config-advanced).

Работоспособность конкретного hook в текущем desktop runtime не испытывалась. Документированная поддержка не равна E2E-проверке здесь.

## Проверки и метрики

Текущий запуск существующих tests/test_bootstrap.py, test_policy.py, test_cli.py, test_wizard.py: **24 passed in 4.94s**.

Команда:

    .\.venv\Scripts\python.exe -m pytest tests/test_bootstrap.py tests/test_policy.py tests/test_cli.py tests/test_wizard.py -o addopts='' -q -p no:cacheprovider --basetemp C:\Users\theso\Desktop\job\BananLoop\.tmp\analysis-20261004-pytest-01 --tb=short

Первый запуск упёрся в WinError 5 в общем pytest temp/cache; повтор с отдельным workspace basetemp и отключённым cache прошёл. Это инфраструктурное ограничение, не провал поведения bootstrap.

tests/test_bootstrap.py:113–158 использует command fixture, не живой Codex. Исторические 106 passed в docs/completion-readiness.md:30–38 относятся к прошлой Linux-проверке и не являются результатом текущего запуска.

В существующем локальном архиве .tmp/experiment_archive_work/report.md записан DEV public_hard_pass_rate 0.6333 -> 0.7000, пять экспериментов, два ACCEPTED, остановка по бюджету. Это прочитанный исторический отчёт; исходные измерения не воспроизводились и обобщение на unseen cases этим не подтверждается.

## Технические ограничения и неизвестности

- Asset discovery не отслеживает автоматически весь граф импортов/внешние datasets. Identity внешних inputs/reference/seed/settings должен обеспечивать evaluator. Источники: integrity.py:35–65; contracts.py:304–309.
- Hash и корректный JSON не доказывают соответствие измерения человеческой цели. Aggregate metrics считаются доверенным вычислением evaluator. Источник: docs/artifact-evaluation-v1.md:138.
- Автоматической A/A noise calibration в runtime не найдено: _baseline делает один DEV action; repetitions/min_wins/tolerances задаются spec, confidence intervals не вычисляются. Источники: engine.py:462–500; policy.py:101–128.
- Это логическая изоляция trusted local projects, не OS boundary против враждебного процесса. Hard USD budget не реализован. Источники: README.md:35; contracts.py:87–102.
- Автономная подготовка корректного evaluator для произвольного нового repo не подтверждена текущими fixture tests. В этом исследовании не запускались платные модели, GPU, внешние сервисы или длительная кампания.
- Публикация плагина и интеграция с OpenSpec не исследовались как отдельные продукты; OpenSpec в запросе рассматривается как аналог привычного UX.

## Заключение

В текущем BananLoop есть простой CLI entrypoint и независимое исполняемое ядро оценки/состояния. Подготовка измерителя содержит LLM-driven шаги, часть требований к которым пока контролируется инструкцией, а не отдельным readiness gate. Codex-интеграция отсутствует. Эти факты являются входом для предлагаемого [архитектурного решения](design/adr.md).

## Обновление AS IS для f062cba, перед исправлениями P0

Этот раздел фиксирует изменения после исходного анализа, не переоценивая его исторические тесты.

- `cli.py:cmd_goal` и `plugins/bananloop/skills/bananloop-goal/SKILL.md` теперь предоставляют путь goal/skill. Есть compatibility manifest `.codex-plugin/plugin.json`, root manifest отсутствует.
- `bootstrap.py:bootstrap_campaign` выполняет до трёх preparation attempts и вызывает `readiness.py:run_bootstrap_readiness`. Core проверяет два smoke-прогона, required metrics/checks, чистоту workspace, case/input/protocol identity и разброс относительно min_improvement. Поэтому старые утверждения об отсутствии integration/readiness относятся только к 62f7830.
- `bootstrap.py:_bootstrap_contract_error` распознаёт некоторые англоязычные повторные запросы goal; обнаруженные runtimes передаются агенту, но проверки false-runtime requests в этой функции нет.
- `engine.py:_stage_execute/_stage_snapshot/_stage_evaluate/_stage_validate` проверяют materialization. Phantom changes приводят к PAUSED_INFRA. `workspace.py:snapshot_proof` связывает parent/candidate/tree/diff hash; полный runtime/evaluator attestation этим не обеспечивается.
- `store.py:finish_attempt` меняет experiment_count/non_improve_streak только при scientific=True. Однако `engine.py:_stage_record` определяет scientific только наличием evaluation_json и присваивает REJECTED любому непринятому исходу. NO_CHANGES и IMPLEMENTATION_BLOCKED тоже проходят через RECORD; INVALID_VALIDATION может иметь DEV evaluation_json.
- `research.py:build_research_state` считает DEV streak для завершённых строк с evaluation_json. `report.py:build_report` показывает общие resources counters, но отдельного attempt_accounting нет.
- Existing tests проверяют no-op/blocker на стадии EXECUTE, phantom transfer/snapshot и readiness. Полная проверка итогового статуса no-op, plateau после смешанных попыток и отчётных bucket counts требует отдельного покрытия.
- Локальная незакоммиченная правка `agents/codex.py` состоит из трёх комментариев к stdin transport. Внешние кампании, runtime probes к платным моделям и GPU не запускались в этом обновлении.
