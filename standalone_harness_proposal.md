# Proposal: самостоятельный harness для измеримых инженерных улучшений

Статус: **Proposed**, решение о создании repository ещё не принято.

Дата: 2026-09-19. Основание: [исследование текущего harness](research_report.md), HEAD `e6e4df230edb07ac89999be4856807af9dcab119`. Технические контракты: [ADR](design/adr.md).

## 1. Рекомендация

**Создавать отдельный repository целесообразно, если первая версия ограничена локальными Git-проектами с доступной воспроизводимой проверкой.** Реализовать небольшой supervisor, который формализует цель, запускает существующего coding agent, самостоятельно измеряет результат и сохраняет только прошедшие проверку изменения.

Главная переносимая ценность `rag_tender`: champion/candidate loop, независимые gates, история проверенных гипотез, различение ошибки инфраструктуры и неудачного эксперимента, восстановление после остановки. Существующий код подтверждает реализуемость этих механизмов. Он не доказывает универсальное улучшение произвольного проекта, надёжность экспертного AI-суждения или экономическую окупаемость campaign.

Предлагаемая формулировка продукта:

> Harness управляет ограниченной серией проверяемых изменений существующего проекта и возвращает лучший подтверждённый результат, историю экспериментов и точную причину остановки.

Обещание «практически любой проект без подготовки» следует сузить: **проект может быть любым по предметной области, но должен предоставить исполнимый контракт проверки и поддерживаемое окружение**. Автоматическое обнаружение тестов помогает настроить campaign; из наличия тестов не следует наличие подходящей бизнес-метрики.

Рекомендация по реализации: новое ядро с небольшим переносом утилит и тестовых сценариев. `rag_tender` продолжает работать независимо и становится первым внешним потребителем через evaluator wrapper. Перенос всей текущей цепочки runner-ов в пакет закрепит её случайные зависимости.

## 2. Что уже доказал prototype

| Наблюдение | Значение для продукта | Основание |
| --- | --- | --- |
| Есть staged loop с champion promotion/rollback | Основная orchestration уже представлена работающим кодом | `v2._execute_active`, `v2._promote` |
| Tests/public/hidden идут до дорогого current-corpus run | Порядок gates может экономить evaluation | `current_dataset_runner.install_current_dataset_optimization` |
| Attempt отделён от scientific iteration | Инфраструктурный сбой не превращается в отрицательный научный результат | `v2_runner._stamp_completion` |
| History хранит гипотезу, evidence, выводы и mechanism family | Есть основа для объяснимого продолжения исследования | `research_first_runner`, `provenance_runner` |
| Есть recovery сохранённого verdict и частичного rollback | Resume является существенной частью продукта | `optimization_runner._recover_already_rolled_back_resume` |
| Есть A/A и запрет считать MATCHED accuracy | Прототип уже столкнулся с шумом и ложными proxy-метриками | `current_dataset_runner`, `GOAL.md` |

Подробные file/function references и границы этих гарантий приведены в [research_report.md](research_report.md).

Самый существенный разрыв: текущая цель описывает пользу на 783 mostly-unlabeled rows, принятие flat benchmark кандидата допускается через AI-validator, а `GOAL_MET` вычисляется по прежним public/hidden метрикам. Кроме того, результат sealed final сейчас не меняет `GOAL_MET`. Универсальный продукт должен иметь один согласованный контракт цели, принятия и финальной проверки.

## 3. Жизнеспособность и границы

Технически концепция жизнеспособна: для последовательной campaign нужны внешний executor, исполнимый evaluator, журнал состояния и детерминированные правила решений. Полноценная распределённая multi-agent платформа для этого не требуется.

Практическая польза вероятнее всего возникает при трёх условиях: evaluator уже существует или создаётся быстро; одна проверка дешевле ручного цикла постановки/анализа; задача допускает несколько небольших проверяемых гипотез. Сложность onboarding и цена evaluation могут оказаться важнее цены генерации кода.

| Класс задачи | Измеримый контракт | Пригодность для первой версии |
| --- | --- | --- |
| Исправление группы ошибок | Количество воспроизводимых failing cases уменьшается; ранее проходившие acceptance tests проходят | Высокая; допускается baseline с целевыми известными failures |
| Производительность | p95 или throughput при фиксированной нагрузке, correctness и memory guardrails | Высокая после проверки стабильности benchmark |
| Retrieval / ML quality | Фиксированный размеченный evaluation set, quality metric, latency/cost guardrails | Высокая при готовых данных и evaluator |
| Prompt optimization | Зафиксированный dataset и grader, повторные измерения, ограниченная стоимость | Возможна, но шум и API расходы должны учитываться |
| Улучшение генерации без эталона | Замороженная рубрика, calibrated judge или human review | Assisted workflow; не обещать объективно доказанную автономную победу |
| «Улучшить архитектуру» без критериев | Нужно договориться о наблюдаемом результате: coupling, latency, regression risk или acceptance checklist | Сначала постановка и проверка; autonomous run пока недоступен |

Отдельный важный случай: при bugfix campaign требование «все тесты зелёные на baseline» блокировало бы задачу. Нужны фиксированные target failures и отдельный набор уже проходящих regression guardrails. Нельзя улучшить показатель удалением, skip или уменьшением числа проверяемых cases.

### Соседние решения

Это точечная проверка реализуемости, не полный анализ рынка и не доказательство уникальности продукта.

| Подход | Что подтверждает внешний источник | Вывод для данного proposal |
| --- | --- | --- |
| `autoresearch` | Есть ограниченный цикл изменения training code, evaluation и keep/discard; фиксированы editable surface, metric и время эксперимента | Узкая проверяемая среда облегчает автономность; переносить идею ограничений, не training-specific реализацию. [Источник](https://github.com/karpathy/autoresearch) |
| Optuna | Framework для оптимизации hyperparameters | Если задача сводится к поиску числовых параметров, готовый optimizer может быть более подходящим решением. Наш фокус: проверяемые изменения кода и конфигурации существующим агентом. [Источник](https://optuna.readthedocs.io/en/stable/) |
| Codex | CLI поддерживает non-interactive execution, JSONL events и structured final output | Есть пригодный транспорт для первого adapter; протокол и schema нужно валидировать в harness. [Официальная документация](https://learn.chatgpt.com/docs/non-interactive-mode) |
| Pi | Есть print/JSON и RPC режимы интеграции | Реальный второй adapter может проверить независимость ядра от Codex. [Документация Pi](https://github.com/earendil-works/pi/tree/main/packages/coding-agent) |
| Ollama | Tool calling возвращает вызовы инструментов, исполняемые приложением в agent loop | Ollama рассматривается как model provider для существующего агента. Написание своего coding/tool loop поверх него выходит за MVP. [Документация Ollama](https://docs.ollama.com/capabilities/tool-calling) |

Предполагаемое отличие продукта: готовый контракт campaign вокруг существующего проекта, где goal, baseline, evaluation, resume и evidence связаны между собой. Коммерческий спрос и готовность пользователей платить пока не проверены.

## 4. Пользовательский путь

Желаемый путь сохраняется: `install -> harness init -> диалог -> подтверждение -> harness run`.

### `harness init`

1. Определяет Git root, selected commit, dirty state, manifests, доступные test/benchmark команды и установленные agent CLI. Статически читает metadata; произвольные setup scripts при discovery не исполняются.
2. Принимает цель обычным языком. Обнаруженное в репозитории и предложенное моделью показывает как предложения, а не установленные факты.
3. Уточняет только отсутствующие элементы: outcome, направление и units метрики, target, запрещённые ухудшения, editable scope, resource budget, доступность evaluation.
4. Показывает конкретные команды baseline и их внешние зависимости. Пользователь разрешает это измерение; команды и расходы входят в учёт подготовки campaign.
5. Измеряет baseline и, если требуется, повторяемость. Не подставляет пример `0.72` как фактический результат проекта.
6. Показывает итоговую формализованную цель с измеренным baseline, ограничения, доступные гарантии и разрешённые действия. После подтверждения фиксирует версию контракта.

В v0.1 исходным состоянием служит выбранный committed snapshot. При dirty worktree `init` показывает, какие незакоммиченные изменения не попадут в campaign, и требует явного выбора продолжить от commit либо сначала подготовить commit самостоятельно. Harness не делает stash/reset и не подменяет исходное состояние молча. Baseline измеряется в той же частной рабочей копии и среде, что и кандидаты; необходимая установка зависимостей оформляется как разрешённый preparation step.

Диалог реализуется wizard-ом со структурированными ответами и необязательным LLM-помощником. Для естественного языка используется тот же настроенный executor в read-only planning role. Он не становится вторым обязательным provider. Есть fallback на последовательные вопросы без модели; YAML вручную писать не нужно.

Для каждого ответа сохраняются происхождение (`discovered`, `suggested`, `measured`, `confirmed`) и статус подтверждения. Фраза «на 10%» уточняется как относительный рост или процентные пункты. Новое ограничение после запуска требует новой версии контракта и, при изменении измерения, нового baseline.

Если evaluator отсутствует, `init` заканчивается состоянием `DRAFT_NEEDS_EVALUATION`: предлагает существующий test command, небольшой evaluator wrapper либо ручную рубрику. Созданную агентом проверку пользователь рассматривает отдельно и замораживает до optimization. Автономная campaign без принятого способа оценки не запускается.

### Иллюстрация итогового контракта

Числа ниже только пример UX; они не являются результатами `rag_tender`.

| Поле | Значение |
| --- | --- |
| Цель | Recall@5 >= 0.80 |
| Измеренный baseline | Recall@5 = 0.72, p95 = 200 ms |
| Принятие промежуточного результата | Recall@5 улучшается относительно champion минимум на 0.005, то есть на 0.5 процентного пункта |
| Guardrails | p95 <= 240 ms; фиксированные acceptance tests проходят; evaluation set и правила scoring неизменны |
| Budget | 15 evaluated experiments, 30 attempts, 40 agent calls, 60 evaluation calls, deadline 2 h |
| Разрешённая область | Заданные source/config paths; existing acceptance tests и harness contract защищены |
| Режим | Normal |
| Деньги | USD cap доступен только при поддерживаемом полном учёте и ограничении расходов; иначе не обещается |

p95 привязан к **исходному baseline**. Привязка «не более +20% к предыдущему champion» позволила бы накапливать ухудшение на каждом шаге.

### Во время и после запуска

Минимальные команды: `init`, `run`, `status`, `resume`, `stop`, `report`. `run` продолжает только утверждённый контракт; `resume` не открывает новую campaign и не обнуляет бюджет. `stop` отменяет управляемые процессы и оставляет последний подтверждённый champion.

Статус показывает текущую стадию, champion, baseline delta, consumed/reserved budget и точную причину паузы. Завершение даёт Markdown + JSON report, commit/patch артефакт и журнал экспериментов. Пользовательский checkout не перезаписывается; merge/push не выполняются автоматически в MVP.

## 5. Минимальная универсальная модель

Нужны шесть основных сущностей. Роли исследователя/планировщика/reviewer являются видами заданий executor-у, а не отдельными обязательными подсистемами.

| Сущность | Минимальные данные |
| --- | --- |
| `CampaignSpec` | Goal, metric definitions, acceptance/stop rules, evaluation plan, scope, autonomy, budgets, executor binding, schema version |
| `CampaignState` | Spec hash, lifecycle, immutable baseline, champion, active attempt, counters/reservations, stop reason |
| `Experiment` | Hypothesis, rationale, mechanism key, expected observation, falsification condition, parent champion, relation to previous attempts |
| `Attempt` | Executor invocation, stage, workspace snapshot, call IDs, times, failure classification, associated experiment |
| `EvaluationResult` | Status, named values with units/sample counts, frozen protocol identity, candidate identity, provenance, artifacts |
| `DecisionRecord` | Accept/reject/inconclusive/pause/stop, exact predicates, comparison to baseline/champion, evidence references |

Минимальные внешние границы:

| Граница | Обязанность |
| --- | --- |
| `AgentAdapter` | Узнать capabilities, запустить структурированное задание, вернуть evidence/usage/errors, отменить выполнение |
| `Evaluator` | Выполнить принятую команду над зафиксированным candidate и вернуть проверяемый result |
| `WorkspaceManager` | Создать управляемую рабочую копию, зафиксировать содержимое, проверить scope, сохранить candidate/champion |
| `CampaignStore` | Транзакционно хранить состояние, события, budget reservations и ссылки на артефакты |

`DecisionPolicy` и `BudgetPolicy` в MVP являются обычными детерминированными функциями ядра. Не нужны универсальный workflow DSL, plugin marketplace, шина сообщений, graph database, vector memory или отдельный service discovery.

Ядро не знает слов `RAG`, `LD`, `DN`, `Qdrant`, `Recall@5` как специальных сущностей. Для него `recall_at_5` является именованной метрикой с направлением `maximize` и диапазоном `[0, 1]`. Подготовка индекса или модели является declared preparation command с бюджетом и artifact identity; логика Qdrant остаётся в интеграции проекта.

## 6. Правила экспериментального цикла

Поток первой версии:

`preflight -> baseline -> propose hypothesis -> execute -> freeze candidate -> verify -> measure -> decide -> record -> next/stop`.

Вызов proposer получает approved goal, текущие результаты, компактную историю и разрешённые paths. Возвращает одну ограниченную гипотезу либо `NO_HYPOTHESIS`. Implementer получает эту гипотезу и работает в выделенной копии. Обе роли могут использовать один Codex adapter и одну модель. Mandatory researcher/reviewer/fixer calls отсутствуют.

После завершения implementer supervisor останавливает его процессы, сохраняет diff и полный snapshot до любой оценки. Сначала проверяет scope и неизменность evaluation assets, затем запускает дешёвые gates, затем primary evaluation. Измеряется именно snapshot, который может быть принят. Новый файл, staged change или изменение содержимого прежнего path должны менять snapshot identity.

Baseline и champion различаются:

- **Baseline** неизменен в рамках campaign; нужен для target, общего delta и cumulative guardrails.
- **Champion** последний принятый candidate; нужен для incremental acceptance и следующей гипотезы.
- **Best observed** может иметь лучшую primary metric, но нарушать guardrails; в отчёте его нельзя выдавать за допустимый итог.

Для deterministic metric кандидат принимается, когда result полон и сопоставим, все guards проходят, направление primary улучшено минимум на заранее заданный `min_improvement`. Равный score по умолчанию не меняет champion. Нарушение хотя бы одного hard guard не компенсируется ростом другой метрики.

`target_met` вычисляется тем же набором metric definitions и guardrails. Если baseline уже достигает цели, campaign завершается без inventing work. Если обязательна final evaluation, сначала `TARGET_REACHED_PENDING_FINAL`; только её успех даёт `GOAL_MET`. Ошибка или ухудшение final не должны отображаться как подтверждённая победа.

### Шум и оценка качества

В MVP поддержать фиксированный протокол: deterministic evaluation либо заранее заданное число повторов и способ агрегации. Для noisy benchmark baseline repeat устанавливает, можно ли различить эффект ожидаемого масштаба. При несопоставимых средах или слишком большом шуме outcome `INCONCLUSIVE`; автоматическое улучшение не объявляется.

Min-improvement и повторные измерения являются практической защитой от случайных колебаний, а не доказательством статистической значимости. Оценку доверительных интервалов может выполнять внешний evaluator. Формальные последовательные statistical tests и adaptive experiment design отложить.

LLM-as-a-judge в дальнейшей версии должен фиксировать модель, rubric, prompt, sample order и repeats; его результат маркируется `judged`. Ручная оценка даёт `human_approved` и паузу, а не поддельную числовую accuracy. В MVP основной путь требует deterministic command evaluation; judge/manual могут быть дополнительным подтверждением через pause, без отдельного judge framework.

### Остановка

| Код | Условие |
| --- | --- |
| `GOAL_MET` | Target и все обязательные guards/final checks выполнены |
| `BUDGET_EXHAUSTED` | Следующее действие не помещается в заранее зарезервированный budget |
| `DEADLINE_REACHED` | Наступил deadline; процессы отменены, результат unfinished stage не принимается |
| `PLATEAU` | Заданное число завершённых scientific experiments подряд не улучшило champion |
| `ATTEMPT_LIMIT` | Исчерпан предел попыток, включая non-scientific failures |
| `ERROR_LIMIT` | Достигнут предел infrastructure/protocol retries |
| `PAUSED_NEEDS_INPUT` | Не хватает разрешения, доказательств или требуется решение пользователя |
| `PAUSED_INFRA` | Зависимость недоступна; сохранён checkpoint, budget не сбрасывается |
| `NO_HYPOTHESIS` | Proposer не предложил обоснованную гипотезу; это мнение агента, не доказательство глобального оптимума |
| `USER_STOPPED` | Получена команда пользователя |

Проверки выполняются перед новым действием и после результата. Если последняя разрешённая оценка подтвердила target, фиксируется `GOAL_MET`, хотя budget теперь исчерпан. Повторный одинаковый failure не запускает бесконечную repair-петлю. Pause сохраняет абсолютный deadline; продолжение после его истечения требует явного расширения бюджета с записью события.

## 7. Исполнители и ресурсы

Первый adapter: Codex CLI. Второй extension point: явный executable wrapper с versioned JSON request/result contract. Это не «любая команда автоматически становится агентом»: wrapper обязан соблюдать schema, timeout/cancel, workspace и usage/error protocol. Для проверки архитектуры нужен smoke со вторым настоящим CLI, предпочтительно Pi, до заявления о поддержке нескольких агентов.

Capabilities минимальны: `read_project`, `edit_workspace`, `structured_result`, `cancel`, `usage_reporting`, `execution_isolation`. Неподдерживаемые обязательные возможности обнаруживаются до baseline/run. Нельзя подменять отсутствующий sandbox promise-инструкцией в prompt.

В MVP выбор статический: один default executor для propose/implement; необязательный explicit override. Автоматическая оценка «какая модель лучше для текущей гипотезы» пока не имеет ни данных, ни критерия оптимальности. Фиксируем adapter/model/version/config в каждой записи, чтобы routing можно было добавить после накопления evidence.

Учёт ресурсов включает init planning, baseline, repeats, agent calls, failed attempts, evaluator calls и preparation commands. Infrastructure error не расходует scientific iteration, но расходует фактически использованные calls/time/cost. Резервирование происходит **до** вызова, settlement после результата; неизвестный исход не становится бесплатным вызовом.

**Жёсткие денежные лимиты нельзя обещать для любого CLI.** Для hard USD cap нужны полный учёт всех billable paths и верхняя граница расхода каждого действия, включая вложенные model/tool calls. Одних token events после завершения недостаточно. Provider quota или gateway также должны иметь проверенную семантику, а не только уведомление о превышении.

Поэтому MVP гарантирует ограничение launch counts и локального времени управляемых процессов. USD отображается как `actual`, `estimated` или `unknown` с покрытием источников. Campaign с обязательным hard USD cap и неподдерживаемым adapter/evaluator получает `UNSUPPORTED_BUDGET`, пока пользователь явно не выберет другой исполнимый контракт. Подписка не записывается как «API cost = $0». Убийство локального процесса не доказывает отмену уже принятого удалённого job; такие jobs вне первой версии.

## 8. Автономность и защита результата

| Режим | Поведение |
| --- | --- |
| Conservative | Показ каждой новой гипотезы и diff перед promotion; отдельное подтверждение существенных действий |
| Normal | Автоматическое выполнение заранее разрешённых локальных experiments; запрос при выходе за scope |
| Autonomous | Автоматическое продолжение внутри той же жёсткой policy; выход за неё приводит к pause, а не расширению прав |

Risk определяется конкретными действиями: изменение dependency manifest, сеть, запись вне workspace, доступ к secret, destructive command или external mutation. Оценка риска самой моделью может объяснять предложение, но не выдаёт разрешение.

Минимальные меры:

1. Частная Git-копия campaign, без записи в рабочие файлы пользователя; rollback касается только harness-owned workspace.
2. Frozen evaluator, acceptance tests, datasets и measurement config с manifest/hash. Новые тесты агента допускаются отдельно; существующие acceptance tests исполняются из доверенного snapshot. Test count/IDs и skips входят в проверку полноты.
3. Agent не может менять store, approved spec, результаты evaluator и содержимое frozen assets. Решение об acceptance принимает supervisor.
4. Credentials передаются только нужному процессу через явный список. Unrestricted shell и network нельзя считать безопасными только из-за allowlist изменённых файлов.
5. Baseline/candidate имеют полный fingerprint: code tree, evaluator/dataset/protocol, dependencies/runtime и declared external resource revision. Несовпадение означает новое измерение или invalid result.

Замороженный script сам по себе недостаточен: он может импортировать изменяемые scoring helpers или принимать поддельный output от candidate. Нужно явно разделить trusted scorer и candidate implementation. Для same-process unit tests это защита от обычного metric gaming, а не доказательство устойчивости к произвольному вредоносному коду.

**MVP рассчитан на доверенный проект и ограниченного исполнителя.** Это не sandbox для запуска неизвестного malicious repository. Требования к запрещённым действиям подтверждаются capabilities execution backend; конфигурация, которую backend не способен обеспечить, блокируется. Полная generic OS/container isolation с private holdout является отдельным объёмом работ.

Hidden holdout нельзя сделать недоступным лишь перемещением файла за пределы Git. Нужен evaluator, которому доступны labels, при этом agent и candidate runtime не получают эти файлы, ключи и диагностические ответы. Итеративно используемый hidden score следует назвать validation; он уже влияет на разработку. Настоящий sealed final не возвращается в planning loop. В MVP strict private holdout не поддерживается, если нет уже изолированного evaluator; этот запрет показывается в `init`, а не молча ослабляется.

## 9. Память и восстановление

Хранить полный журнал и ограниченный planning context. Для каждого experiment: причина, ожидаемый эффект, parent champion, full candidate diff/snapshot, executor identity, evaluation protocol/results, decision predicates, failure category, resources и выводы. Diff сохраняется и для кандидата, не прошедшего ранний gate.

Защита от повторов в MVP: exact hypothesis key + parent/protocol identity, mechanism tag и ссылки `repeats/supersedes`. Повтор допускается при изменившихся предпосылках или явно запланированной репликации. Semantic dedup с embeddings не нужен; одинаковые идеи в разных формулировках остаются ограничением, которое отражается в отчёте.

Для local state выбрать SQLite с транзакциями, unique action IDs и schema version; крупные outputs хранить файлами по hash. JSON/Markdown являются экспортом. Это устраняет необходимость согласовывать несколько вручную изменяемых state/history files, но не делает Git и внешние API частью одной транзакции.

Resume восстанавливает последнюю подтверждённую стадию по `action_id`, input fingerprint и durable output receipt. Неизвестный исход внешнего действия получает `UNKNOWN_OUTCOME`; он не повторяется слепо. Promotion использует intent + reconciliable Git ref update + store completion. Exactly-once внешнего API не обещается; автоматическое принятие одного evaluated candidate должно быть идемпотентным.

## 10. Standalone repository

Рабочее название пакета и CLI: `harness`; доступность имени в registries не проверялась. Начальный стек: Python 3.11+, Pydantic для schema, SQLite из stdlib, стандартный subprocess runner, простой CLI. Не добавлять framework оркестрации до появления конкретной потребности.

```text
src/harness/
  cli.py                   # init/run/status/resume/stop/report
  onboarding.py            # discovery, wizard, approval of spec
  contracts.py             # versioned data models
  engine.py                # explicit sequential state machine
  policy.py                # acceptance, guardrails, stop rules
  budget.py                # reservations, settlement, deadlines
  store.py                 # SQLite transactions and migrations
  workspace.py             # private Git checkout and snapshots
  process.py               # process groups, cancel, timeouts, env
  report.py                # Markdown/JSON from durable evidence
  agents/
    base.py
    codex.py
    command.py             # wrapper contract for second executor
  evaluators/
    command.py             # structured JSON output
    tests.py               # fixed test-suite gate
examples/
  rag_tender/              # config and evaluator bridge only
  latency/                 # non-RAG benchmark fixture
  bugfix/                  # target failures + regression guards
tests/
  unit/
  contract/
  recovery/
  integration/
```

В проекте пользователя `.harness/` хранит generated spec, approval metadata и repository ID; приватные runtime artifacts остаются в user-state directory с ключом `repository_id/campaign_id`. State нельзя адресовать только именем branch: одинаковые branch names встречаются в разных repositories.

Default workspace: отдельный local clone campaign без пользовательских remotes/credentials, затем supervisor-owned candidate snapshots. Это дороже linked worktree по диску, но уменьшает связь с Git metadata исходного проекта. Linked worktree может стать оптимизацией позднее: Git документирует общие repository данные у связанных worktrees, поэтому отдельная директория сама по себе не является security boundary. [Документация Git](https://git-scm.com/docs/git-worktree).

Частный clone также не ограничивает произвольный shell доступ к диску пользователя: эту гарантию должен обеспечивать execution backend. Изоляция Git-изменений и запрет чтения/записи за пределами workspace проверяются отдельно.

Native Windows и один POSIX путь должны входить в smoke-проверку первого релиза: quoting, CLI shims, process tree cancellation и locks уже оказались значимыми в prototype. Неподдерживаемые submodules/LFS/remote jobs объявляются на preflight; универсальность предметной области не требует поддержки всех вариантов Git/environment в v0.1.

## 11. Что извлекать из rag_tender

| Материал | Решение | Объём адаптации |
| --- | --- | --- |
| `runtime.write_json`, OS lock | Перенести в utilities/artifact handling | Небольшой: убрать глобальный ROOT, сделать ключи repo/campaign; SQLite становится источником state |
| Windows CLI resolution, subprocess error conventions | Перенести в Codex/process adapters | Небольшой/средний: добавить отмену всего дерева процессов и deadlines |
| `hook.EvaluationError`, separation scored/UNSCORED | Перенести семантику typed results | Средний: заменить фиксированный GOLD JSON на общий contract |
| Attempts vs scientific_iterations, stable experiment IDs | Перенести понятия и regression tests | Средний: reservations и idempotent completion |
| Hypothesis/mechanism history, lessons, blocker suppression | Перенести формат evidence и принцип ограниченной prompt memory | Средний: убрать taxonomy и domain heuristics |
| Persisted verdict, partial rollback recovery tests | Перенести как acceptance scenarios | Новый engine должен проходить эти сценарии без monkey-patching |
| Staged cheap/expensive gates | Перенести порядок и short-circuit | Небольшой дизайн; стоимости и dependencies задаёт evaluation plan |
| `scripts/codex_loop.py`: timeout, diff budget, repeated-failure guard | Использовать как дополнительный источник отдельных функций и тестовых идей | Проверить отдельно: legacy loop не является текущим runtime |
| `runtime.run_codex` | Основа первого adapter | Переписать границу I/O, usage/cancel/version checks; не переносить ROOT/env copy |
| `policy.Metrics`, `accept_candidate`, `final_goal_met` | Перепроектировать | Именованные метрики, missing/NaN как invalid, единый success contract |
| `v2.py` и runner monkey-patches | Не переносить как архитектуру ядра | Явная state machine и зависимости |
| Current-corpus compact rows/A/A | Оставить RAG-specific summary в example integration; общий fingerprint/repeat protocol реализовать в ядре | Domain analysis не входит в generic store |
| Qdrant preflight, aliases/rebuilds; LD constraints; GOLD/blind generation | Оставить в `rag_tender` / example wrapper | Ноль обязательных RAG dependencies в standalone |
| Обязательный OpenSpec planning flow | Не переносить как prerequisite | Structured hypothesis достаточна; OpenSpec может остаться опциональным artifact |
| Текущие campaign state/private data | Не мигрировать автоматически | При необходимости импортировать старые отчёты как историческую evidence с provenance |

Практически без изменений можно взять лишь небольшие утилиты и формулировки инвариантов тестов. Основной активный runner нельзя честно назвать готовым generic engine. Процент переиспользуемого кода без реализации не оценивался.

Извлечение выполнять добавлением нового проекта; старый package, config, datasets и live campaigns не перемещать. Первый RAG evaluator bridge читает существующие публичные outputs/commands и преобразует их в новый result schema. Любое подключение live Qdrant идёт только к отдельному тестовому ресурсу. Историю происхождения перенесённого кода сохранить; лицензию нового repository выбрать владельцу перед публикацией.

## 12. MVP и отложенное

| В v0.1 | Критерий готовности |
| --- | --- |
| Existing Git project + `init` dialog | Новый пользователь получает утверждённый spec без ручного YAML |
| Goal/metrics/constraints/budget | Missing fields и неподдерживаемые hard requirements блокируют run |
| Baseline и один primary objective | Measurement связано с code/protocol/environment identity |
| Codex adapter + command adapter contract | Второй реальный wrapper проходит smoke без изменения engine |
| Последовательный propose/execute/evaluate/decide | Acceptance и stop не зависят от self-report implementer |
| Guardrails и защищённая evaluation definition | Попытка ослабить baseline acceptance tests отклоняется |
| History, best feasible candidate, report | Восстанавливается цепочка experiment -> diff -> measurement -> decision |
| Resume и cancellation | Пройден crash/restart matrix, нет повторного promotion |
| Attempts/calls/evals/deadline/plateau limits | Failed и interrupted calls не обходят budget |
| Conservative/Normal/Autonomous | Различаются approvals внутри одного action policy |

Отложить: автоматический model routing, параллельные experiments, distributed workers, обязательную команду из четырёх агентов, autonomous evaluator invention, reinforcement learning стратегии, semantic/vector memory, Pareto optimization, сложный workflow DSL, web UI/SaaS/plugins, GPU jobs, автоматический deploy/push и универсальный private-holdout sandbox.

Также отложить самостоятельный Ollama coding agent и общий LLM-judge framework. Подключение существующего агента к локальной модели можно добавить через adapter, не меняя основной цикл.

Hard USD cap для opaque CLI является **явно неподдерживаемым требованием**, а не скрыто удалённой строкой scope. Если именно такой cap обязателен для первого использования, нужен более узкий metered execution путь до v0.1 release.

## 13. Главные риски

| Приоритет | Риск | Решение в MVP / предел |
| --- | --- | --- |
| Критический | Улучшение proxy выдаётся за пользу продукта | Confirmed metric definition; unlabeled coverage только diagnostic; manual/judged evidence явно маркируется |
| Критический | Agent меняет evaluator, tests или output | Frozen trusted assets, completeness checks, snapshot identity, supervisor-owned decision; не обещать malicious-code security |
| Критический | Повреждение пользовательского checkout | Private clone; cleanup только подтверждённых harness-owned paths; нет автоматического apply |
| Высокий | Crash между subprocess/Git/state создаёт двойное действие | Reservations, action receipts, promotion reconciliation, crash-injection tests |
| Высокий | Costs/time выходят за обещания | Включить все стадии; отказаться от unsupported hard USD; deadline и process-tree cancellation |
| Высокий | Baseline и candidate несопоставимы | Полный fingerprint и validation sample count; drift -> invalidation/rebaseline |
| Высокий | Шум и повторный выбор лучшего подгоняют результат | Fixed repeats/min delta; отмечать adaptive validation; sealed final отдельно при наличии изоляции |
| Высокий | Env, network или logs раскрывают приватные данные | Explicit capabilities/credential scopes; strict holdout только с отдельным evaluator boundary |
| Высокий | Onboarding дороже ручного loop | Первые проекты с уже существующей проверкой; измерять setup time и количество вмешательств |
| Средний | Общий abstraction превращается в workflow framework | Четыре интеграционные границы и одна state machine |
| Средний | Агент повторяет идею с другим названием | Mechanism history + exact dedup; semantic nonrepeat не обещается |
| Средний | Оптимизация застревает без временных ухудшений | MVP monotonic champion; отклонённые эксперименты сохраняются, non-monotonic search отложен |
| Средний | Agent CLI обновляет протокол | Adapter version probe, recorded versions и contract fixtures |

## 14. Как доказать пользу MVP

До публичного обещания универсальности провести три campaign на разных проектах: `rag_tender` с фиксированным labeled evaluator, небольшой не-RAG latency benchmark и bugfix suite с заранее перечисленными failures. Fixtures для latency/bugfix должны быть маленькими и независимыми от данного repository.

Предлагаемые release gates:

1. Все три проекта подключаются без изменения `engine.py` или `policy.py`; адаптация ограничена config и evaluator wrapper.
2. На каждой campaign одинаково проверяются baseline, candidate, guards и stop conditions; хотя бы один reject и один interrupted/resumed attempt продемонстрированы интеграционно.
3. На двух разных классах задач достигается заранее утверждённый improvement target в рамках budget. Отсутствие улучшения на трудной задаче допустимо как outcome, но не как единственное доказательство полезности продукта.
4. Ноль принятых кандидатов с нарушенными guards; неизменные пользовательские файлы; ноль повторных promotion после crash tests. Это тестовые release criteria, не обещание абсолютной безошибочности.
5. Пользователь завершает init за ориентировочно 15 минут при готовом evaluator; число ручных управляющих сообщений после старта существенно меньше ручного loop. Сравнение провести на сопоставимых задачах и с тем же executor; время подготовки evaluation учитывать отдельно.
6. Второй настоящий agent wrapper выполняет короткую campaign без изменения core. Замена только названия Codex model этот критерий не выполняет.
7. Финальный отчёт можно воспроизвести из store без повторного вызова LLM и без утраты rejected hypotheses.

Оценка усилий, не обещание срока: **20-35 инженерных дней одного опытного разработчика** для описанного локального MVP, включая unit/contract/recovery tests, два OS smoke paths и три dogfood integrations. Это примерно 4-7 рабочих недель при полной занятости и готовых внешних evaluator. Создание разметки, платные campaign, строгая изоляция holdout и полноценный денежный gateway в эту оценку не входят. До фиксации срока отдельно проверить процессы/отмену в двух ОС и реальный второй CLI protocol.

## 15. Решение после proposal

Предлагается одобрить создание отдельного repository для v0.1 с указанными ограничениями и использовать `rag_tender` как внешнюю dogfood-интеграцию. Первый end-to-end результат должен быть короткой campaign с реальным evaluator, сохранённым champion и проверенным resume; расширение количества ролей не является условием старта.

Если практическая проверка покажет, что большинство задач требуют длительной ручной разработки evaluator, продукт стоит позиционировать как harness для **evaluation-ready проектов**, а не расширять автономию на неподготовленные задачи. Если пользовательским приоритетом являются приватный holdout или строгий USD budget уже в первой версии, сначала потребуется соответствующий enforcement backend; простой CLI adapter этого требования не закрывает.

Результат текущей работы: proposal и design, без переноса кода и создания нового repository. Существующий harness не изменён. Repository unit-suite проверен: **263 passed**, одно предупреждение о записи pytest cache; live качество и затраты campaign не измерялись.