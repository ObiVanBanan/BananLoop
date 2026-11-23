# Разделение benchmark-треков: internal ARE и независимый RPDI-14

**Статус документа:** рабочая граница протоколов по состоянию источников на 23.11.2026. Это не разрешение на запуск, не отчёт о сравнении и не свидетельство независимости.

## Решение о разделении

Вести три раздельных записи: (1) internal 12-task AutoResearchExam diagnostic из `benchmarks/autonomous_v01`, (2) независимые RPDI-14 Optimization и Exploratory Research tracks, (3) отдельный BoardGames pilot. Не объединять их task pools, manifest, evaluator/scorer, назначения, бюджеты, выводы или отчёты. Internal результаты не подтверждают независимую оценку, holdout performance или превосходство над Codex. BoardGames не является частью ARE или RPDI-14 и не добавляется в общий score.

| Свойство | Internal ARE diagnostic | Независимый RPDI-14 | BoardGames pilot |
|---|---|---|---|
| Назначение | Внутреннее диагностическое сравнение 12 ARE задач | Будущее слепое сравнение v0.2 Full с Codex Direct в двух независимых треках | Отдельный короткий исследовательский pilot |
| Дизайн | 12 задач × 2 arm; iterative Codex Direct и BananLoop Full предполагаются | Отдельные task sets, manifests, outcomes, scoring и reports для Optimization и Exploratory Research | 10 вопросов × 2 arm; отдельный manifest и budget |
| Текущее состояние | Offline preparation; `launch_authorized=false`; preflight `PREP_BLOCKED`; production launcher и общий scoring остаются открыты | Только preregistration/harness acceptance contract; benchmark не авторизован и не запускался; eligibility не установлена | Только отдельный pilot; результаты не входят в другие tracks |
| Выводы | Только внутренние диагностические; раскрывать source exposure; никакого blind/independent holdout или outperform claim | Вывод возможен только после всех RPDI gates и по заранее установленному решающему правилу | Отдельный описательный отчёт; не переносить результаты между треками |

## Артефакты и решения по каждой границе

### 1. Manifest

**Internal ARE.** Offline `prepare` фиксирует каталог 12 задач, порядок arm, модель/CLI и Full flags и создаёт `ARE_PREPARATION_ONLY` с `launch_authorized=false`. Указанные 3600 секунд на arm — принятая временная политика подготовки, а не разрешение тратить ресурсы. Замороженный launch manifest, который даёт право назначать задачи и запускать production arm, отсутствует. Нынешний README указывает, что production launcher, READY preflight и общий финальный DEV scoring остаются открытыми.

**RPDI-14.** До назначения задач нужны отдельные canonical manifests для Optimization и Exploratory Research. Каждый должен закреплять track/version, задачи и data hashes, права, начальное состояние, инструкции и критерии включения/исключения, arm mapping, immutable source/Full flags/campaign/evaluator, model/provider/tool pins, ресурсы, outcomes, seeds, feedback, остановку, статистический метод и отчётность. Точные task assignments, labels, evaluator identity и arm mapping остаются sealed до заморозки обоих результатов. Текущий документ RPDI-14 описывает требования, но фактических подписанных manifests и run-gate evidence в предоставленных источниках нет.

**BoardGames.** Требуется собственный manifest, отдельный бюджет и отдельный отчёт. Pilot не следует добавлять как дополнительные ARE задачи или включать в RPDI-14.

### 2. Scorer и метрики

**Internal ARE.** Одинаковое post-run scoring правило для двух arm является плановым требованием BL-043, но общая финальная DEV оценка ещё открыта. Нельзя выдавать результаты локального/internal scorer за независимую оценку. Доступные источники не подтверждают принятый production scoring bundle, его независимую изоляцию и финальные evaluator pins для серии.

**RPDI-14.** Optimization и Exploratory Research имеют раздельные evaluators/rubrics, primary outcomes и отчёты. Валидатор/holdout должен быть запечатан и изолирован от планирующего контекста; scorer получает только финальные анонимизированные outputs после заморозки arm. Протокол требует paired outcomes, uncertainty и предопределённое правило решения. Независимая проверка evaluator isolation и task-specific anonymization ещё должна быть подтверждена реальными attestation на точном manifest digest; harness fixtures не являются evidence.

**BoardGames.** Собственные вопросы, оценивание и сводка; никаких общих composite scores с ARE или RPDI-14.

### 3. Права на задачи и независимость

**Internal ARE.** `source-access-declaration.json` фиксирует прямой просмотр приватных grader snippets для трёх задач: `act-tensor-sparse-panel-imputation-r2`, `causalpfn-cate-pehe-ihdp-surfaceb`, `hicard-latent-encoder`. Также указана потенциальная возможность чтения build context для первой задачи; факт чтения неизвестен. Поиск по указанным типам файлов завершился усечённым выводом, поэтому перечень просмотренных фрагментов может быть неполным. Оператор не открывал private dataset/NPZ и answer key/exact private test seed, насколько помнит; уверенность ограничена усечённым поисковым выводом. Кандидат, evaluator, Harbor grader и модель не запускались в связи с этим доступом. Владелец принял продолжение только для internal non-RPDI-14 диагностики при disclosure. Это операторская декларация, не независимое подтверждение; она не восстанавливает blind conditions.

До любого внутреннего назначения задач нужен task-level rights register и подтверждённые права на использование/распространение. Принятие source exposure не является лицензией на набор данных и не устраняет leakage. Не считать все 12 задач готовыми: решение 15.11 отмечает три новых задачи как `CANDIDATES`, без trusted evaluator fixtures/adapters и content-pinned images.

**RPDI-14.** Права и consent подтверждаются для каждой задачи до назначения arm; права на использование и redistribution должны быть очищены. AutoResearchExam и любой датасет с неясными правами исключены. Задачи с известным или потенциальным предшествующим доступом не становятся независимыми простым раскрытием. Источники не содержат завершённого task-level rights/consent register для RPDI-14.

**BoardGames.** Отдельно подтвердить права и происхождение каждого вопроса; план указывает, что из десяти вопросов только `bg-01` является upstream DiscoveryBench вопросом. Наличие отдельного разрешения на pilot не переносит права или одобрение на другие tracks.

### 4. Authorization и gates

**Internal ARE.** `launch_authorized=false` в подготовительном manifest. Preflight на указанных SHA вернул `PREP_BLOCKED`; README фиксирует непроверенные Docker image IDs из-за отказа в доступе к Docker socket и неуспешную pytest matrix. Production execute требует принятого READY preflight, owner authorization и реализованного launcher/watchdog/архивации. Ни prepare, fake dry-run, ни исторический pilot не являются authorization на реальные arm.

**RPDI-14.** Запуск заблокирован, пока не подтверждены точные RPDI-00–13 DoD и integrated E2E, CI и техническая проверка immutable head для поддерживаемых платформ, независимая проверка evaluator/data/accounting/isolation, task-use/redistribution rights, явные spend/resource ceilings и принятие preregistration независимым оператором. Все обязательные attestations должны относиться к точным frozen manifests. Неизвестное или непроверяемое условие означает `BLOCKED`/`INELIGIBLE`.

**BoardGames.** Отдельное owner-approved secondary run manifest и временной бюджет требуются до новой серии. Такое одобрение ограничено этим pilot.

### 5. Бюджеты и ресурсы

**Internal ARE.** Предлагаемая политика — 3600 wall-clock seconds на arm, включая Exploration, verification, Optimization child и ожидание grader; часы не перезапускаются для child. В решении владельца нет жёсткого лимита model calls/iterations; usage наблюдается, а не считается нулевым или бесплатным. Нет paid API/fallback или обхода квот. Эти значения — плановые ограничения; пока отсутствует authorization на запуск и доказательство enforcement/полного учёта, нельзя утверждать, что реальные arm уложились в них. Любые неизвестные usage/interruptions нужно сохранять как unknown/failure.

**RPDI-14.** Budget устанавливается отдельно для каждой пары/arm и каждого track до freeze: calls, token classes, wall time, evaluator calls, experiments, retries, spend, memory и process ceilings. Все действия требуют reconciled receipts; неизвестные значения не равны нулю. Размер выборки должен удовлетворять заранее выбранной power или precision цели; недостаточная достижимая выборка означает underpowered/ineligible. Значения internal ARE и pilot не являются RPDI budget.

**BoardGames.** План: 600 секунд на arm для 10×2. Нужен отдельный одобренный time budget; это не бюджет ARE и не лимит RPDI. Историческое потребление из pilot — только historical usage, не потолок будущего расхода.

### 6. Reporting и допустимые выводы

**Internal ARE.** Отчёт должен называться internal/non-RPDI-14 diagnostic, перечислять точный manifest/source SHA, task eligibility и rights, все отклонения, failures/interruptions/unknown usage, фактически выполненный scorer и финальные архивы. Отдельно и заметно раскрыть source-access declaration и ограничение независимости затронутых задач. Не использовать формулировки «blind», «independent holdout», «RPDI-14 result» или «BananLoop outperforms Codex» на основании этих 12 задач. Нельзя подменять им BL-036.

**RPDI-14.** Отчёт содержит пары по задачам, win/tie/loss, paired deltas и uncertainty, ресурсы/стоимость, все failures/exclusions и protocol deviations. Вывод superiority допустим только если его поддерживает preregistered decision rule. Optimization и Exploratory Research выводятся отдельно; совместное утверждение требует заранее принятого multiplicity rule. Публикация результатов также требует RPDI-15 release approval.

**BoardGames.** Отдельный pilot report с явной меткой пилота, своими условиями, бюджетом и ограничениями. Не объединять его с ARE или RPDI-14 в общий рейтинг или вывод о превосходстве.

### 7. Disclosure

Для internal ARE приложить или сослаться на `benchmarks/autonomous_v01/source-access-declaration.json` в самом manifest и итоговом отчёте; сохранять и её ограничения: известные прямые пути не обязательно полны, возможный build-context access остаётся неизвестным, декларация составлена оператором и не является независимой attestation. Повторять disclosure при любом распространении результатов, а не только в примечании к внутреннему manifest.

RPDI-14 manifest и отчёт отдельно раскрывают все несовпадения между arm, происхождение задач, любые исключения, ограничения anonymization/isolation и все неразрешённые риски. Нельзя переносить internal disclosure в RPDI как будто это независимая проверка; RPDI task selection и rights review должны быть выполнены отдельно.

## Связь карточек и статус BL-021

- **BL-048** задаёт документальную границу между внутренним 12-task diagnostic, RPDI-14 и BoardGames.
- **BL-043** относится к подготовке/будущему production запуску 12×2 ARE arm; offline fake archive и `prepare` закрывают только offline части. Production execute, READY gate, owner authorization и общий финальный scoring остаются открытыми.
- **BL-036** относится к независимому A/B BananLoop v0.2 Full против Codex и остаётся заблокированной до RPDI-14 eligibility и авторизованного независимого прогона. Internal ARE её не закрывает.
- **BL-021 не закрыта.** Карточка на проверке; preregistration RPDI-14 — проектный контракт, а не свидетельство состоявшегося независимого benchmark. Этот документ уточняет разделение, но не заменяет независимые approvals, права, gates, запуск и анализ, требуемые BL-021.

## Проверенные источники

Внутренние источники, проверенные при подготовке:

1. `docs/rpdi/rpdi-14-independent-benchmarks.md` — preregistration contract, run-order gates, manifest, scoring, implementation evidence и release boundary.
2. `benchmarks/autonomous_v01/README.md` — offline preparation, false/blocked preflight состояние, незавершённый production path и disclosure.
3. `benchmarks/autonomous_v01/source-access-declaration.json` — task-level сведения об известном/возможном доступе, объёме поиска и ограничениях декларации.
4. `docs/benchmarks/AUTONOMOUS-BENCHMARK-V01-PLAN.md` и `docs/benchmarks/AUTONOMOUS-BENCHMARK-V01-DECISIONS-2026-11-15.md` — ARE budgets, BoardGames separation и owner policy.
5. Obsidian карточки `BL-048`, `BL-036`, `BL-043`, `BL-021` в `Проекты/BananLoop-v0.2/Задачи/` — ожидаемый результат и актуальная гранулярность задач.

Внешние источники GitHub указаны в карточках и README как контекст; этот документ не трактует PR discussion как независимую attestation или разрешение на запуск.
