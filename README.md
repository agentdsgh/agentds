# agentds

Нейросетевой агент, который автоматически решает issue в GitHub-репозиториях: читает задачу, изучает код, вносит правки, проверяет их и отправляет результат в новую ветку с pull request'ом и отчётом в исходном issue.

Вся логика агента реализована в одном файле — [`agent_freedeepseek.py`](agent_freedeepseek.py). В качестве LLM используется **DeepSeek** через OpenAI-совместимый локальный прокси `FreeDeepseekAPI` (`http://localhost:9655/v1`).

## Возможности

- **Работа по текстовому протоколу** — модель вызывает инструменты блоками `@@CALL ... @@END`, без нативных function-calling'ов, что позволяет использовать обычный чат-эндпоинт DeepSeek.
- **Инструменты агента**: `get_issue`, `list_files`, `read_file`, `search_code`, `write_file`, `patch_file`, `delete_file`, `run_command`, `git_diff`, `commit`, `comment_issue`, `create_issue`, `report_work`, `report_no_action_needed`, `run_in_codespace`.
- **Бюджет шагов и раундов** — на каждый issue выделяется несколько раундов по N вызовов, с автоматическим переносом контекста между раундами.
- **Проверка в GitHub Codespaces** — опциональный запуск команд в облачной среде для проверок, которым мало локальной песочницы (devcontainer, другая версия рантайма, базы данных).
- **Git-слой**: безопасные пути (`_safe`), защита `.git`, коммиты, ветка `agent/issue-N`, push и PR через GitHub API.
- **GitHub-слой**: rate-limit-aware ретраи (`_gh_retry_after`), upsert-комментарий с отчётом, создание issue с защитой от дубликатов, PR-draft.
- **Судья-ревьюер** (`deepseek-reasoner`) — независимая оценка решения по diff'у и тексту issue.
- **Троттлинг и ретраи LLM** — общий минимальный интервал между запросами, уважение `Retry-After`, обработка 429/5xx.

## Как это работает (кратко)

1. Агент клонирует репозиторий и получает список открытых issue, упорядоченный по меткам `priority:p0..p2`.
2. Для каждого issue создаётся ветка `agent/issue-<N>` (или продолжается уже существующая).
3. Модель в цикле вызывает инструменты: читает код, вносит правки (`patch_file` / `write_file`), проверяет (`run_command` / `run_in_codespace`).
4. Когда задача решена, модель вызывает `report_work(final=True)` (или `report_no_action_needed`, если правка не нужна).
5. Ветка пушится в GitHub, открывается pull request, а в issue публикуется отчёт (отдельно — судья-ревьюер).

## Быстрый старт

Нужны: Python 3.8+, локально запущенный прокси `FreeDeepseekAPI` на `localhost:9655`, токен GitHub с правами `Contents: Read and write` и `Pull requests: Read and write` (опционально — `Codespaces: Read and write` и `Workflows: Read and write`).

```bash
pip install requests
export GITHUB_TOKEN="ghp_..."
python agent_freedeepseek.py
```

Полная инструкция по запуску, переменным окружения, режимам работы и диагностике — в [`docs/USAGE.md`](docs/USAGE.md).

## Документация

- [`docs/USAGE.md`](docs/USAGE.md) — полная инструкция использования и запуска.
- [`docs/IMPROVEMENTS.md`](docs/IMPROVEMENTS.md) — предложения по доработке и исправлению проекта.

## Ограничения

- Проект рассчитан на конкретный репозиторий: `GITHUB_OWNER` и `GITHUB_REPO` заданы константами в начале файла.
- Требуется локальный прокси к DeepSeek Web (`localhost:9655`) — публичный DeepSeek API напрямую не используется.
- Проверки выполняются в песочнице агента: опасные команды (`git push`, `sudo`, `gh`, доступ к секретам и т.п.) заблокированы.
