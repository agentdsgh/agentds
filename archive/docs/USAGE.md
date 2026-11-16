# Полная инструкция использования и запуска

Здесь описано, как настроить и запустить agent_freedeepseek.py, какие есть режимы работы и как диагностировать проблемы.

## 1. Требования

- Python 3.8+
- Пакет requests (`pip install requests`)
- Локально запущенный прокси FreeDeepseekAPI на http://localhost:9655/v1 (OpenAI-совместимый эндпоинт к DeepSeek Web)
- Git в PATH
- (опционально) GitHub CLI gh для проверок в Codespaces; агент умеет устанавливать его сам
- Токен GitHub с правами: Contents: Read and write; Pull requests: Read and write; Issues: Read and write; опционально Codespaces и Workflows. Токен должен быть выдан на этот репозиторий.

## 2. Настройка

Все константы — в начале agent_freedeepseek.py.

### 2.1. Токен

Порядок поиска: Kaggle Secrets (UserSecretsClient().get_secret("GITHUB_TOKEN")), затем переменная окружения GITHUB_TOKEN. Если токена нет, репозиторий читается как публичный (с лимитом на API), но push/PR/комментарии будут недоступны.

### 2.2. Репозиторий

Значения по умолчанию: GITHUB_OWNER = "agentdsgh", GITHUB_REPO = "agentds". BASE_BRANCH = None означает ветку по умолчанию.

Их можно переопределить без правки кода — через CLI-аргументы или переменные окружения (приоритет: CLI → env → значения по умолчанию). Полный список флагов — в разделе 2.7.

### 2.3. Модели и прокси

FREEDEEPSEEK_URL = "http://localhost:9655/v1"; FREEDEEPSEEK_MODEL = "deepseek-chat" (агент); FREEDEEPSEEK_JUDGE_MODEL = "deepseek-reasoner" (судья); FREEDEEPSEEK_N_CTX = 64000. Если прокси требует ключ — задайте FREEDEEPSEEK_API_KEY.

### 2.4. Поведение

STEPS_PER_ROUND = 6 (вызовов на раунд); MAX_ROUNDS = 3; JUDGE = True; GITHUB_WRITE = True; GITHUB_PUSH = True; OPEN_PR = True; PR_DRAFT = True.

### 2.5. Ограничения времени и бюджета

CMD_MAX_SEC = 300; CMD_TOTAL_SEC = 900; CODESPACE_CMD_MAX_SEC = 300; CODESPACE_TOTAL_SEC = 900; CODESPACE_MAX_RUNS_PER_ISSUE = 6.

### 2.6. Выбор issue

ISSUE_STATE = "open" (open/closed/all); ONLY_ISSUES = None (например [2, 3]); ISSUE_LIMIT = None; MAX_TOTAL_SEC = None; RERUN_DONE = False.

## 3. Запуск

Установите requests и запустите python agent_freedeepseek.py.

Что происходит: клонирование репозитория в /tmp/agent_repo; получение открытых issues с сортировкой priority:p0 → p1 → p2 → untriaged; для каждого issue создаётся/продолжается ветка agent/issue-N и запускаются раунды; после раундов — git diff относительно базы, судья-ревьюер, push, PR, комментарий в issue; итоговый отчёт — в agent_out/agent_report.md (или /kaggle/working/agent_out/... в Kaggle), результаты — в agent_out/results.json.

## 4. Инструменты агента

Агент вызывает инструменты текстовым протоколом: строка CALL, затем пары ARG имя/значение, закрывается строкой END. Правила: один вызов за сообщение; закрывающая строка обязательна, иначе вызов не выполняется; после неё всё игнорируется; значения аргументов пишутся как есть, без кавычек, многострочные тоже допустимы.

Ключевые инструменты: read_file / search_code / list_files — навигация; patch_file / write_file / delete_file — правка; run_command — локальная проверка (с ограничениями); run_in_codespace — проверка в Codespaces; git_diff / commit — управление изменениями; comment_issue / create_issue — обратная связь и новые issues; report_work(final=True) — завершение; report_no_action_needed — «правка не нужна».

## 5. Ограничения и запреты

run_command блокирует: git push/remote/config/credential; sudo; опасные rm -rf по системным путям; curl ... | sh (запуск скриптов из сети); gh; shutdown / reboot / mkfs / dd; обращения к секретам (GITHUB_TOKEN, KAGGLE_KEY, .git/config). Если нужно — расширьте BLOCKED_CMDS осознанно.

## 6. GitHub Codespaces

run_in_codespace(command, timeout_sec) создаёт/переиспользует Codespace на ветке issue; переносит туда текущий git diff патчем (git apply); запускает команду с таймаутом и кэширует результат по (команда, sha1 патча); имеет лимиты CODESPACE_MAX_RUNS_PER_ISSUE и CODESPACE_TOTAL_SEC. Если gh CLI не установлен — агент попробует поставить его через apt (нужны права root в контейнере).

## 7. Диагностика

- FreeDeepseekAPI недоступен / Connection refused — проверьте, что прокси запущен на localhost:9655.
- Много 429 — увеличьте MIN_INTERVAL_SEC (по умолчанию 8 с), уменьшите STEPS_PER_ROUND.
- 403 rate limit в GitHub — проверьте GITHUB_TOKEN; _gh_retry_after уважает Retry-After.
- Ветка создаётся, но push падает — у токена есть Contents: Read and write? Репозиторий выбран в токене?
- PR не создаётся, хотя ветка есть — права Pull requests: Read and write; draft может падать для бесплатных планов, агент сам повторяет без draft.
- ContextExceeded — уменьшить TOOL_OUTPUT_LIMIT / STEPS_PER_ROUND, увеличить FREEDEEPSEEK_N_CTX.
- format (модель не соблюдает формат) — уменьшить LLM_MAX_OUTPUT_TOKENS, но не ниже ~4000; проверить, что используется deepseek-chat, а не deepseek-reasoner.
- Codespace не стартует — токен с Codespaces: Read and write; проверить .devcontainer (нужна фича ghcr.io/devcontainers/features/sshd:1).

## 8. Артефакты прогона

В agent_out/ (или /kaggle/working/agent_out/): agent_report.md — итоговый отчёт; results.json — машиночитаемые результаты; patches/issue-N.patch — diff по каждому issue; traces/issue-N.jsonl — лог вызовов и раундов; drafts/ — черновики комментариев и новых issues (когда GITHUB_WRITE=False).
