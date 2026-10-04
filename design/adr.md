# ADR: простой запуск BananLoop через Codex с сохранением независимой оценки

## Status

**Proposed**, 2026-10-04. Это проект решения, не реализованная plugin-функциональность.

Предыдущий ADR об извлечении standalone core сохранён без изменений в [adr-standalone-2026-09-19.md](adr-standalone-2026-09-19.md). Текущее решение сохраняет его границы evaluator/store/policy, меняя пользовательский вход и подготовку кампании.

## Task

Пользователь открывает репозиторий в Codex и задаёт цель человеческим языком. BananLoop сам разбирается в проекте, подготавливает рабочее измерение, проводит ограниченную серию улучшений и возвращает лучший проверенный результат. Ручная сборка evaluator, датасета и длинной CLI-команды не является нормальным пользовательским путём.

Пример желаемого UX после установки плагина:

> BananLoop, улучши поиск аналогов. Сократи пропуски, не увеличивая ложные совпадения.

Имена инструментов и настроек ниже — предлагаемый контракт, а не существующий API.

## Research Inputs

- [research_report.md](../research_report.md): факты текущего checkout, места ручной настройки, границы runtime и 24 прошедших targeted tests.
- src/bananloop/bootstrap.py:65,150 — ready-валидация и автоматическая подготовка.
- src/bananloop/engine.py:268,400,462,1063 — receipts, holdout, baseline, validation.
- src/bananloop/contracts.py:110,123 — один AgentConfig и campaign contract.
- [Plugin packaging](https://developers.openai.com/plugins/build/plugins), [Hooks](https://learn.chatgpt.com/docs/hooks), [Skills](https://developers.openai.com/plugins/concepts/skills).
- [Goals](https://learn.chatgpt.com/use-cases/follow-goals), [Subagents](https://learn.chatgpt.com/docs/agent-configuration/subagents), [Profiles](https://learn.chatgpt.com/docs/config-file/config-advanced).

## Context

Короткий start уже реализован. Основная недостающая способность — надёжно превращать цель в проверенный измерительный контракт, восстанавливаться после ошибок подготовки и понятно показывать ход работы.

Текущий core уже содержит baseline/champion, DEV/validation/final holdout, deterministic accept/reject, rollback, durable receipts, reservations и research memory. Их перенос только в инструкции лишит систему программных проверок.

Разные проекты требуют разных evaluators, но формат вызова, хранение receipts, принятие результата и управление бюджетом могут оставаться одинаковыми.

## Decision

Сделать Codex plugin основным пользовательским интерфейсом. Сохранить Python core как владельца измерений, политики принятия, состояния и восстановления. В первом варианте core остаётся и владельцем существующего experiment loop; замена его на native Codex execution — отдельная потенциальная эволюция после проверки эквивалентности гарантий.

Разделить ответственность:

| Слой | Ответственность |
| --- | --- |
| Skill плагина | Понять цель, вызвать подготовку, объяснить выбранное измерение и прогресс, запросить только действительно недостающий смысл/доступ |
| Подготовка кампании | Discovery, выбор measurement recipe, генерация адаптера, readiness проверки, ограниченный ремонт и фиксация protocol |
| Core BananLoop | Snapshot, выполнение evaluator, durable receipt, budgets, policy, promotion/rejection, recovery |
| Пакет оценки проекта | Команды, fixtures/cases, рубрика, split, seeds/settings, fingerprints, checks, optional judge |
| Codex hook | Связать завершение хода с уже существующим состоянием кампании; не вычислять научный verdict самостоятельно |

Для первой локальной интеграции достаточно skill + структурированный CLI bridge. MCP добавляет удобные типизированные инструменты и асинхронный status API, но не является условием независимости оценки. Установка должна проверять Python/Git/Codex и доступ к выбранной модели; работающий plugin manifest сам по себе не доставляет Python runtime.

### Подготовка становится полноценным этапом

Вместо одного доверенного ready-ответа:

1. Найти существующие тесты, benchmarks, datasets, reference outputs, команды и runtime.
2. Выбрать способ измерения, согласованный с целью; сохранить объяснение выбора и ограничения.
3. Собрать evaluator adapter и замкнутый manifest зависимостей измерения в managed workspace.
4. Core запускает дешёвый smoke: валидный JSON, нужные metrics/checks, ненулевое число реальных кейсов, запуск из candidate snapshot.
5. Проверить смысл сигнала на доступных известных good/bad examples или контролируемом ухудшении. Например, ухудшенная реализация не должна получать тот же высокий балл. Такие проверки не доказывают бизнес-валидность полностью, но ловят константный scorer и измерение постороннего свойства.
6. При шуме провести ограниченную A/A калибровку, выбрать repeats/порог, сверить одинаковые inputs и окружение.
7. При технической ошибке вернуть builder только диагностический контекст, дать ограниченное число repair attempts, повторить gate.
8. Заморозить проверенный пакет; измерить baseline и начать улучшение автоматически в разрешённом бюджете.

Bootstrap имеет собственные durable stages и reservations внутри общего лимита запуска. Время и вызовы подготовки перестают быть невидимыми расходами до campaign.

Если готовых labels нет, система сначала использует доступные контракты, invariants, metamorphic checks, regression tests и references. Синтетические кейсы имеют явное происхождение; они не объявляются независимым GOLD. Если задача требует неизвестного продуктового предпочтения, задаётся один предметный вопрос, например о цене ложного совпадения. Пользователю не предлагается самому писать evaluator.

Test pass rate подходит для цели «починить эти тесты», но не автоматически для «сделать поиск качественнее». Если численная цель не дана, выбранный критерий и его ограничения показываются в кратком описании кампании. При простом поиске улучшений до лимита результат называется «улучшение найдено» или «бюджет исчерпан», а не необоснованным GOAL_MET.

### Project-specific evaluation без project-specific lifecycle hook

Постоянный dispatcher узнаёт campaign по repo/session binding в supervisor state. Для каждой кампании создаётся отдельный versioned EvaluationBundle:

- назначение измерения и связь с goal;
- evaluator argv + runtime/environment identities;
- metrics, checks, targets, guards и критерий остановки;
- immutable cases/references/splits/seeds и hashes;
- optional LLM judge: модель, версия rubric/prompt, настройки и правила агрегации;
- readiness receipts, оценка шума и допустимые ограничения evidence.

Содержимое меняется между проектами. После начала поиска тот же пакет не переписывается в ответ на плохой candidate score. Изменение измерения создаёт новую protocol version с новой baseline; старые и новые числа не склеиваются в одну историю.

Builder может видеть материалы подготовки; implementer получает чистый DEV-only контекст. Настоящий финальный holdout не создаётся из уже просмотренных implementer примеров. Если такой независимой проверки нет, результат имеет evidence level DEV_ONLY.

### Hook и /goal

/goal полезен для продолжения работы между ходами; BananLoop определяет, какие измерения позволяют объявить успех. Официальная документация /goal не обещает BananLoop-специфичные rollback, champion policy и sealed holdout.

Stop hook проверяет зарегистрированную кампанию и её receipt/status. Он может запросить продолжение хода, если осталась разрешённая работа. Вызов hook сам по себе не значит «кандидат готов к оценке»: candidate должен быть явно отправлен с snapshot identity. Дорогой benchmark запускается core как job, а не заново при каждом завершении хода.

Правила bridge:

- Нет активной campaign/session binding — no-op.
- READY/RUNNING — получить состояние, при необходимости дождаться/продолжить предусмотренную работу.
- Candidate уже оценён — вернуть имеющийся receipt, не запускать evaluator повторно.
- NEEDS_INPUT/PAUSED_INFRA/BUDGET_EXHAUSTED/USER_STOP — не запускать бесконечные продолжения.
- Только core может записать научный результат и champion; hook не доверяет тексту «готово».
- Проверять campaign_id, session_id, snapshot, protocol hash и budget; не маршрутизировать по последнему assistant message.
- Hook обязан учитывать stop_hook_active, concurrency и идемпотентность.
- Nested codex exec worker с уже имеющимся владельцем loop не запускает второй BananLoop loop.
- Ошибка, timeout, отсутствие trust или отсутствие callback не превращаются в успешную сертификацию. Campaign остаётся незавершённой/непроверенной.

Codex требует trust review точного определения non-managed hook. Поэтому dispatcher поставляется стабильным компонентом плагина; генерация кода нового lifecycle hook на каждый проект не нужна. Это не способ обойти проверку: project evaluator code остаётся отдельной исполняемой частью и запускается с явной границей доверия.

Официальная документация описывает local hooks, но некоторые cloud orchestration режимы имеют другие ограничения. MVP нацелен на локальный Codex; совместимость Windows/Desktop/CLI надо проверить callback-тестом до обещания UX.

### Модели и reasoning

Настройки моделей должны храниться один раз как роль -> backend/model/reasoning/profile, а не передаваться пользователем двадцатью флагами. Минимально нужен один default execution profile; overrides по ролям опциональны.

| Роль | Как выбирать |
| --- | --- |
| Общение/постановка цели | Текущая модель чата |
| Builder и proposer | Настроенный execution profile, overrides при необходимости |
| Implementer | Настроенный coding profile |
| Проверка измерителя | Отдельный контекст reviewer плюс исполняемые проверки core |
| Judge артефакта | Отдельно зафиксированная часть EvaluationBundle; может вообще отсутствовать |

Независимость измерения не требует обязательно другой модели: важнее отсутствие у implementer права менять scorer/данные/решение. Другой LLM reviewer даёт дополнительное мнение, но не заменяет проверяемый evaluator.

Сейчас есть один AgentConfig на кампанию. В первом MVP допускается сохранить его, добавив понятный профиль; расширение ролей может быть простым отображением, без отдельного routing engine.

CLI worker не наследует автоматически модель открытого чата: текущий адаптер запускает отдельный codex exec. Разрешённую effective configuration нужно явно определить и сохранить в launch receipt. Native subagent execution, если будет добавлен позже, может использовать Codex custom agent TOML с model/model_reasoning_effort. Текущий CLI 0.158.0 использует отдельные name.config.toml для --profile; старый пример [profiles.name] не применять.

Profile не меняет утверждённую model/rubric evaluator посреди кампании. Недоступная модель возвращает понятный capability error; silent fallback не допускается. Не обещать hard dollar cap для opaque CLI; поддерживаемые calls/time/work budgets сохраняются.

## Alternatives

| Вариант | Плюсы | Издержки и решение |
| --- | --- | --- |
| Только skill и /goal | Быстрый UX-прототип, работа в чате | Нет замены транзакциям, receipts, rollback и защищённому протоколу. Не подходит как равнозначная замена core |
| Skill + script на Stop, без core | Внешняя проверка при завершении хода | Понадобится заново реализовать identity, бюджеты, replay, champion и recovery; фактически новое ядро. Отклонить |
| Plugin + нынешний core | Сохраняет существующие гарантии, позволяет сосредоточиться на bootstrap | В первом варианте остаются фоновые codex exec workers. Рекомендуемый MVP |
| Native Codex execution + core measurement service | Редактирование в native sessions, больше контроля UX | Нужен новый durable job/lease API, изоляция agent context и доказательство parity. Возможная следующая архитектура |
| Только улучшить CLI | Самый малый технический объём | Не решает предпочтение работы из Codex. Сохранить как headless/expert интерфейс |

## Contracts

### API

Предлагаемый небольшой интерфейс, общий для CLI bridge и будущего MCP:

| Операция | Вход | Выход |
| --- | --- | --- |
| start | repo, goal, optional profile/budget, request_id | campaign_id, preparation status, summary, missing inputs |
| status | campaign_id, optional event cursor | stage, baseline/champion summary, budget, permitted DEV feedback, next event cursor |
| resume | campaign_id, expected revision, optional missing-input values | existing/new job identity и status |
| stop | campaign_id | durable cancellation acknowledgement |
| export | campaign_id, expected champion | patch/manifest/report references |

Long-running start возвращает job identity, не удерживает MCP request на часы. Supervisor process владеет durable state/lock; restart восстанавливает receipt-based execution. На первом шаге adapter оборачивает существующие методы, а не реализует policy второй раз.

status для LLM не возвращает сырые operator reports и holdout details: сейчас report рассчитан на оператора, поэтому его нельзя просто передать в chat model. Финальные подробности могут быть отдельным пользовательским артефактом вне adaptive context.

Для позднего native-execution варианта понадобятся lease/submit API: lease выдаётся на конкретную роль, parent champion и protocol; submit принимает snapshot/patch и структурированный результат. Evaluate/promote остаются core-owned. Это не часть первой упаковки.

### Data

Сохранить SQLite actions/evaluations/decisions/events и прежние immutable CampaignSpec. Добавить campaign registry (несколько целей на repo), BootstrapRun/EvaluationBundle readiness records и host-session bindings. У active campaign отдельный state dir без ручного --state-dir в обычном UX.

Новый старт не перезаписывает существующую кампанию. Незакоммиченная работа требует явной snapshot policy: default — сохранить отдельный managed snapshot и точно показать состав включённого diff; никогда не коммитить/сбрасывать исходный checkout молча. Если input-policy не позволяет безопасно выбрать состав изменений, запросить только этот выбор.

Изменения схемы additive; старые spec hashes и replay сохраняются. Новые поля не должны ломать canonical identity старых кампаний.

### Events / Jobs

Единственный owner на campaign. Bootstrap actions и evaluator jobs имеют request hash, stable action_id, reservations до побочного эффекта и durable completion. Повторный hook/status не создаёт новую evaluation. Unknown external outcome сохраняет нынешнюю conservative recovery semantics.

### Errors

Разделять missing product input, runtime/credential failure, invalid evaluator, unstable measurement, candidate rejection и budget stop. Техническая ошибка запуска не объявляется плохим качеством кандидата. Ремонт evaluator до freeze ограничен; после freeze protocol mutation создаёт новую measurement epoch.

## Security

Сохранить private evaluator workspace, sanitized implementer workspace, hidden labels, scope и integrity guards. Исходный checkout остаётся пользовательским. Hook/MCP не выдают implementer полный доступ к operator evidence.

Если native Codex продолжает редактирование в исходном repo с доступными evaluator assets и общей историей, нельзя утверждать прежнюю изоляцию. Нужны прежние sanitized worker contexts или отдельная runtime sandbox. Простое расположение scorer «в другом файле» недостаточно.

Локальный пользователь/враждебный процесс с доступом ко всем файлам остаётся за пределами текущих гарантий. Секреты — через разрешённые credentials/environment, не в bundle/report. Новые сетевые сервисы или платные providers не выбираются скрыто.

## Testing Strategy

Критерий «проще, не менее эффективно» измеряется отдельно для UX и качества:

- На трёх типах реальных проектов — bugfix, performance, качество outputs — запустить от repo+goal без ручного evaluator/CLI. Измерить время до первой валидной baseline, число вопросов, ручных правок, bootstrap failure/repair rate.
- Проверить wrong-metric и constant-score evaluator, недостающие refs, missing credentials, drift external inputs, noise и бюджет подготовки.
- Сравнить прежний expert path и plugin path на одинаковых starts/protocol/budgets: acceptance, champion, regressions, holdout, затраты и качество. Один красивый пример не доказывает parity.
- Проверить hook trust/disabled/error/timeout, повторные callbacks, user stop, nested workers, Windows quoting, несколько кампаний, disconnect/restart.
- Сохранить существующие policy/store/isolation/replay tests. Проверить совместимость исторических CampaignSpec и отсутствие holdout leakage в chat-facing tools.
- GOAL_MET выдаётся только с нужным terminal evidence; receipt связан с тем самым snapshot, который экспортируется.

Текущие 24 теста — исходная проверка существующего кода, не проверка предлагаемого плагина.

## Consequences

MVP boundary: один skill, локальный bridge, усиленная автоматическая подготовка, минимальный профиль, registry кампаний и понятный progress/result. Дополнительный Stop hook — контроль на границе хода; он не требуется для самостоятельной корректности уже работающего engine.

Сохранить CLI/headless режим, Codex/Pi/command adapters и existing campaign semantics. Не переписывать engine ради нового entrypoint. При отключении плагина кампанию можно inspect/resume/export обычным CLI.

Перенос native исполнения в Codex допустим после проверки parity. Отказ от Python core не является необходимым условием простого UX и не входит в это решение.

## Open Questions

1. Реальная поддержка lifecycle callback и trust UI в целевой версии desktop/CLI: проверить перед обещанием интеграции. Это blocker для hook-based UX, но не для skill + CLI.
2. Выбор первых реальных repo+goal acceptance cases и доступных references: необходим для подтверждения автономного setup и качества.
3. Для native execution: доступный session/subagent API, сохранение контекста и sandbox boundaries. Это blocker только для последующего native режима.
4. Дистрибуция runtime и локального MCP: выбрать способ установки после проверки target-host возможностей. Не обещать, что web-install автоматически доставит локальные скрипты.
