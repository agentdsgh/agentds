# github-tasks — навык OpenCode для работы с задачами GitHub

Навык переносит полезную часть автономного агента из [`agent_freedeepseek.py`](../../../archive/agent_freedeepseek.py)
в формат OpenCode skill: `SKILL.md` с инструкцией и детерминированный CLI
`scripts/gh_tasks.py`. Встроенного LLM-цикла здесь нет — роль модели выполняет сам
агент OpenCode, а скрипт делает только операции с GitHub API и git.

Документ дополняет <https://opencode.ai/docs/skills/> и <https://opencode.ai/docs/models/>.

---

## 1. Зачем: skill, ключ, Console, Go

| Что | Что это | Нужен ли платный ключ |
|---|---|---|
| **Skill** | Папка с `SKILL.md` (+ `scripts/`), которую агент загружает по описанию | **Нет** |
| **OpenCode TUI/CLI** | Клиент, который исполняет навыки и вызывает инструменты | Нет, есть бесплатные модели |
| **OpenCode Console** (`opencode.ai/console`) | Шлюз к моделям, ключи `oc_sk_...` | Только для платных моделей и внешних клиентов |
| **OpenCode Go** | Подписка с включённым объёмом | Необязательна |

Ключ `oc_sk_...` не нужен, чтобы «запустить навык»: навык — это инструкция для
уже работающей модели. Ключ нужен только самому `gh_tasks.py`, и это **токен
GitHub**, а не ключ модели. Console навыки не исполняет; она лишь позволяет
делиться ими внутри workspace.

## 2. Анатомия и установка

```text
.opencode/skills/github-tasks/
├── SKILL.md            # frontmatter + когда применять + процесс + правила
├── scripts/
│   └── gh_tasks.py     # stdlib-only CLI, JSON на stdout
└── README.md           # этот документ
```

OpenCode ищет навыки в `~/.config/opencode/skills` и `.opencode/skills`
(плюс совместимые `~/.claude/skills`, `~/.agents/skills`). Каталог с `SKILL.md` —
один навык; ID берётся из имени каталога, а не из поля `name`.

В этом репозитории навык уже лежит в проектной папке `.opencode/skills/github-tasks`,
поэтому достаточно запустить `opencode` в корне клона — навык подхватится сам.

Глобальная установка (навык будет доступен во всех проектах):

```bash
mkdir -p ~/.config/opencode/skills
ln -s "$PWD/.opencode/skills/github-tasks" ~/.config/opencode/skills/github-tasks
# или копией: cp -r .opencode/skills/github-tasks ~/.config/opencode/skills/
```

Проверка: запустить `opencode` и упомянуть `@github-tasks` либо попросить
«разбери issue #N». Навык появится в списке доступных, если в frontmatter есть
`description` и нет `metadata.opencode/autoinvoke: false`.

## 3. Что взято из `agent_free.py`, а что выброшено

В `agent_freedeepseek.py` смешаны две разные вещи: **агентный цикл** (LLM, раунды,
бюджет шагов, throttle, judge) и **операции с GitHub**. OpenCode уже даёт цикл и
инструменты, поэтому в навык переехало только второе.

| В `agent_freedeepseek.py` | В навыке |
|---|---|
| `llm_stream`, `_throttle`, `_on_rate_limit`, circuit breaker | **Убрано.** Модель — агент; ретраи остались только для GitHub API |
| `run_round`, `StepBudget`, `parse_text_call`, `coerce_args`, `text_protocol_prompt` | **Убрано.** У агента нативные tool-calls |
| `judge_result` (second model) | **Убрано.** Самопроверка агентом + тесты/линтер |
| `compose_comment`, `compose_pr_body` | Текст пишет агент, скрипт только отправляет |
| `read_file`, `write_file`, `patch_file`, `search_code`, `delete_file` | Инструменты агента: `read`, `edit`, `write`, `grep`, `glob` |
| `run_command` | Инструмент `shell` агента (лимиты harness) |
| `gh_upsert_report` | `report` (upsert по маркеру `<!-- OPENCODE_REPORT -->`) |
| `codespace_exec` | **Нет** (вне скоупа) |
| `_draft`, `agent_out/` | Не нужно: история в сессии и PR |

Сохранено и оформлено как команды:

| Логика `agent_free.py` | Команда |
|---|---|
| `_gh_request`, `gh_paginate` | внутри `api()` / `paginate()` |
| `fetch_issues`, `_issue_rank` (`priority:p0..p2`) | `list` |
| `issue_text` (+ комментарии) | `show` |
| `load_known_issues`, `find_duplicate` (Jaccard ≥ 0.6) | `create` (дубликат отклоняется) |
| `gh_create_issue` | `create` |
| `gh_post_note` | `comment` |
| `gh_upsert_report` | `report` |
| `prepare_branch`, `find_remote_branch` | `branch` (`agent/issue-N-slug`) |
| `git`, `_ahead`, `_has_changes` | `status`, `commit`, `push` |
| `gh_open_pr` (в т.ч. обработка «already exists») | `pr` (идемпотентно) |

## 4. Ключи GitHub

`export GITHUB_TOKEN=...` в своём терминале **не виден агенту**: его shell наследует
окружение фонового сервиса OpenCode. Рабочий способ — файл ключей по репозиториям:

```bash
S=~/.config/opencode/skills/github-tasks/scripts/gh_tasks.py

printf '%s' 'ghp_xxx'        | python3 $S auth set --repo owner/name
printf '%s' 'github_pat_xxx' | python3 $S auth set --repo owner/*
printf '%s' 'ghp_xxx'        | python3 $S auth set --repo default
printf '%s' 'ghp_xxx'        | python3 $S auth set --repo corp/repo --api https://ghe.example.com/api/v3
python3 $S auth list        # ключи маскируются
```

Файл `~/.config/opencode/github-tokens.json` создаётся с правами `600`:

```json
{
  "agentdsgh/agentds": { "token": "ghp_..." },
  "corp/*": { "token": "github_pat_...", "api": "https://ghe.example.com/api/v3" },
  "default": { "token": "ghp_..." }
}
```

Порядок разрешения: `--token` → `--token-env VAR` → `--token-file F` → запись
`owner/name` → `owner/*` → `default` → `$GITHUB_TOKEN`/`$GH_TOKEN` → `gh auth token`.
Репозиторий: `--repo owner/name` → `$GITHUB_REPO`/`$GH_REPO` → `git remote origin`.

Если нужен именно env для сервиса:

```bash
opencode service set env GITHUB_TOKEN ghp_xxx   # останавливает сервис
opencode service start
```

## 5. Права токена

| Право | Зачем |
|---|---|
| Issues: Read and write | `list`, `show`, `create`, `comment`, `report`, `close` |
| Contents: Read and write | `branch`, `commit`, `push` |
| Pull requests: Read and write | `pr` |
| Workflows: Read and write | правка `.github/workflows` |

При 401/403/404 скрипт печатает `{"ok": false, "error": ...}` с подсказкой о правах
и возвращает код 1. Для публичного репозитория чтение возможно без токена, но
лимит 60 запросов/час.

## 6. Безопасность

- Токены — только в `github-tokens.json` (600) или env; в репозиторий не коммитятся.
- `--dry-run` у `create`, `comment`, `report`, `close`, `commit`, `push`, `pr`.
- Запрещены force-push, удаление веток и merge: это делает человек.
- В issue/PR не попадают секреты; в отчёте нельзя писать непроверенное как проверенное.
- В старых файлах репозитория оставался захардкоженный ключ провайдера — его нужно
  перевыпустить и вынести в env; в этом навыке ключей нет.

## 7. Команды

| Команда | Назначение |
|---|---|
| `doctor` | токен и источник, доступ, scopes, ветка по умолчанию, лимит |
| `auth list` / `set` / `remove` | управление файлом ключей |
| `list [--state] [--label] [--limit]` | issues по приоритету |
| `show N [--max-comments]` | issue + комментарии |
| `create --title --body-file [--priority]` | новая задача с дедупликацией |
| `comment N --body-file` | комментарий по ходу работы |
| `report N --body-file` | один итоговый отчёт (upsert) |
| `close N [--comment]` | закрыть задачу |
| `branch N [--title] [--base]` | ветка `agent/issue-N-slug` |
| `status [--base]` | dirty, changed files, ahead, diff --stat |
| `commit -m MSG [--paths]` | `git add` + commit |
| `push [--branch]` | push в origin |
| `pr --issue N [--body-file] [--draft]` | PR, идемпотентно |

Вывод — JSON; успех `{"ok": true, ...}`, ошибка `{"ok": false, "error": ...}` и код 1.

## 8. Проверка

```bash
python3 -m py_compile .opencode/skills/github-tasks/scripts/gh_tasks.py
python3 $S --help
python3 $S auth list                       # работает без сети
python3 $S --repo owner/name doctor
python3 $S --repo owner/name list --limit 10
```

Сценарий целиком:

```bash
python3 $S --repo owner/name show 42
python3 $S --repo owner/name branch 42 --title "fix login redirect"
# правки и проверка тестами
python3 $S --repo owner/name commit -m "fix: handle empty redirect target (#42)"
python3 $S --repo owner/name push
python3 $S --repo owner/name pr --issue 42 --body-file /tmp/pr.md
python3 $S --repo owner/name report 42 --body-file /tmp/report.md
```

## 9. Ограничения

- Нет автономного цикла: между командами решение принимает агент, а не скрипт.
- Нет Codespaces.
- Нет собственного бюджета шагов/времени — лимиты задаёт harness OpenCode.
- `branch`/`commit`/`push` работают с **локальным клоном**; `--repo` задаёт, чей API и ключ.
- GitHub Enterprise поддерживается через `--api` / поле `api` в файле ключей.
- Дедупликация — эвристика по заголовку (Jaccard ≥ 0.6), возможны ложные срабатывания.

## 10. Совместимость с прежними артефактами

- Маркер отчёта прежнего агента (`DEEPSEEK_AGENT_REPORT`) остаётся в истории issue;
  новый навык пишет свой маркер `OPENCODE_REPORT` и не перезаписывает чужой отчёт.
- Ветки по-прежнему называются `agent/issue-N-slug`, поэтому старые PR и новые
  не конфликтуют по имени.
- `agent_out/` больше не создаётся: отчёты и черновики живут в issue/PR и сессии.

## Ссылки

- Навыки: <https://opencode.ai/docs/skills/>
- Модели и бесплатные модели: <https://opencode.ai/docs/models/>
- Console (платные модели): <https://opencode.ai/docs/console/models/>
- Go (подписка): <https://opencode.ai/docs/console/go/>
- Исходный агент: [`agent_freedeepseek.py`](../../../archive/agent_freedeepseek.py)
