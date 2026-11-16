# agentds — агент для GitHub-задач на OpenCode

Репозиторий содержит агента, который решает задачи GitHub: читает issue, изучает код,
вносит правки, проверяет их и открывает pull request с отчётом в исходной задаче.

Работает поверх [OpenCode](https://opencode.ai): модель, tool-calls, сессии, права и
субагенты — от OpenCode. Здесь лежат только агент, навык и документация.

## Чем отличается от v1

Раньше вся логика была в одном файле `agent_freedeepseek.py`: свой LLM-клиент, свой цикл
раундов, свой throttle, свой текстовый протокол вызова инструментов и отдельная
модель-судья. Теперь это не нужно:

| v1 (`archive/agent_freedeepseek.py`) | Сейчас |
|---|---|
| свой вызов LLM и стрим | модель OpenCode (бесплатная, Console, Go или локальная) |
| раунды, `StepBudget`, парсинг вызовов | нативные tool-calls и лимит `steps` у агента |
| throttle и circuit breaker под 429 | ретраи на стороне провайдера и скрипта |
| judge-модель | самопроверка агентом + тесты/линтер |
| `read_file`/`write_file`/`run_command` | встроенные `read`, `edit`, `write`, `grep`, `shell` |
| GitHub API + ветки + PR | навык `.opencode/skills/github-tasks` |

Старая реализация сохранена в [`archive/`](archive/) — она больше не используется, но
полезна как история решений и источник идей.

## Быстрый старт

1. Установите [OpenCode](https://opencode.ai/docs/) и запустите `opencode` в корне клона.
2. Модель: подойдёт бесплатная (без ключа) или `/connect` для Console/Go. Платный ключ
   модели для работы агента **не обязателен**.
3. Сохраните токен GitHub (Issues, Contents, Pull requests: Read and write):

   ```bash
   printf '%s' 'ghp_xxx' | python3 .opencode/skills/github-tasks/scripts/gh_tasks.py \
       auth set --repo owner/name
   ```

4. Проверьте доступ и запустите задачу:

   ```bash
   python3 .opencode/skills/github-tasks/scripts/gh_tasks.py --repo owner/name doctor
   ```

   Дальше достаточно сказать агенту: «разбери issue #N в `owner/name`».

## Состав репозитория

| Путь | Назначение |
|---|---|
| `opencode.jsonc` | конфигурация проекта: агент по умолчанию, права |
| `AGENTS.md` | постоянные правила проекта для агента |
| `.opencode/agents/github-maintainer.md` | primary-агент: процесс issue → PR → отчёт |
| `.opencode/skills/github-tasks/SKILL.md` | инструкция навыка |
| `.opencode/skills/github-tasks/scripts/gh_tasks.py` | CLI: issues, ветки, коммиты, PR, отчёты |
| `.opencode/skills/github-tasks/README.md` | документация навыка (дополнение к opencode.ai) |
| `archive/` | прежняя реализация v1 и её документация |

## Модели

| Вариант | Ключ | Когда подходит |
|---|---|---|
| Бесплатные модели OpenCode | не нужен | попробовать, небольшие задачи |
| OpenCode Console | `oc_sk_...`, оплата по факту | нужны любые модели и свои скрипты |
| OpenCode Go | подписка | регулярная работа с фиксированным лимитом |
| Локальные (Ollama, LM Studio, vLLM) | не нужен | приватность, работа без интернета |

Модель выбирается через `/models`; в конфиге проекта её можно закрепить полем `model`.
Навык от модели не зависит.

## Безопасность

- Токены GitHub — только в `~/.config/opencode/github-tokens.json` (права `600`) или env.
  В репозиторий и в issue они не попадают.
- `gh_tasks.py` поддерживает `--dry-run` для всех операций записи.
- Агент не делает force-push, не удаляет ветки и не мержит PR — это решение человека.
- Не просите вставлять токен в чат: используйте `auth set` в своём терминале.
- Перед публикацией отчёта агент обязан отделять проверенное от непроверенного.

## Дальнейшие шаги

- Codespaces-исполнение для тяжёлых тестов.
- Команды `/issue`, `/triage`, `/report` для типовых сценариев.
- Дополнительные навыки: ревью PR, релизы, triage по меткам.
- CI: проверка `py_compile`, линт и `--help` на каждый PR.

## Ссылки

- OpenCode: <https://opencode.ai/docs/>
- Навыки: <https://opencode.ai/docs/skills/>
- Агенты: <https://opencode.ai/docs/agents/>
- История v1: [`archive/README-v1.md`](archive/README-v1.md)
