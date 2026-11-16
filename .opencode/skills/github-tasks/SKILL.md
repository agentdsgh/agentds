---
name: GitHub Tasks
description: Работа с задачами GitHub — список и чтение issues, комментарии, создание issue, ветки, коммиты, push, pull request и итоговый отчёт. Использовать, когда просят разобрать issue, починить задачу из GitHub, открыть или обновить PR, оставить комментарий, завести задачу или отчитаться о работе.
---

# GitHub Tasks

Навык заменяет автономный цикл из `agent_free.py`: модель (то есть ты) выполняет
работу, а `scripts/gh_tasks.py` делает только детерминированные операции —
запросы к GitHub API и git. Никаких вызовов LLM внутри скрипта нет.

Все команды печатают JSON в stdout. Успех: `{"ok": true, ...}`. Ошибка:
`{"ok": false, "error": "...", "status": N}` и код возврата 1.

## Подготовка

Токен нужен только скрипту, для GitHub API. Рекомендуемый способ — файл ключей
по репозиториям (разные аккаунты и GitHub Enterprise поддерживаются, сервис
перезапускать не надо):

```bash
S=~/.config/opencode/skills/github-tasks/scripts/gh_tasks.py

# токен вводится через stdin, в историю шелла и в чат не попадает
printf '%s' 'ghp_xxx' | python $S auth set --repo owner/name
# вся организация одним ключом:
printf '%s' 'github_pat_xxx' | python $S auth set --repo owner/*
# GitHub Enterprise:
printf '%s' 'ghp_xxx' | python $S auth set --repo owner/name --api https://ghe.example.com/api/v3
# запасной ключ для всего остального:
printf '%s' 'ghp_xxx' | python $S auth set --repo default
```

Файл: `~/.config/opencode/github-tokens.json` (chmod 600). Посмотреть без
раскрытия секретов: `python $S auth list`.

Порядок разрешения токена: `--token` → `--token-env VAR` → `--token-file F` →
запись в файле ключей (`owner/name`, затем `owner/*`, затем `default`) →
`$GITHUB_TOKEN`/`$GH_TOKEN` → `gh auth token`.

`export GITHUB_TOKEN=...` в своём терминале **не сработает** для агента: shell
инструмента наследует окружение фонового сервиса OpenCode, а не вашего шелла.
Если нужен именно env — задайте его сервису:

```bash
opencode service set env GITHUB_TOKEN ghp_xxx   # останавливает сервис
opencode service start
```

Проверка окружения — **всегда первым шагом**:

```bash
python ~/.config/opencode/skills/github-tasks/scripts/gh_tasks.py --repo owner/name doctor
```

Если навык установлен в проект (`.opencode/skills/github-tasks/`), путь к скрипту —
`.opencode/skills/github-tasks/scripts/gh_tasks.py`.

## Команды

| Команда | Что делает |
|---|---|
| `auth list` / `auth set --repo R` / `auth remove --repo R` | ключи по репозиториям в `~/.config/opencode/github-tokens.json` |
| `doctor` | токен и его источник, доступ к репозиторию, scopes, git, gh CLI |
| `list [--state open] [--label X] [--limit N]` | issues, отсортированные по `priority:p0/p1/p2` |
| `show N [--max-comments 30]` | issue + комментарии (JSON: body, labels, comments) |
| `create --title T --body-file F [--priority p2] [--dry-run]` | новая задача с проверкой дубликатов |
| `comment N --body-file F [--dry-run]` | комментарий (вопрос/пояснение по ходу) |
| `report N --body-file F [--dry-run]` | один итоговый отчёт, upsert по маркеру |
| `close N [--comment TEXT]` | закрыть issue |
| `branch N [--title T] [--base main]` | ветка `agent/issue-N-slug` от актуальной базы |
| `status [--base main]` | грязное дерево, changed files, ahead, diff --stat |
| `commit -m "msg" [--paths ...] [--dry-run]` | `git add` + commit |
| `push [--branch B]` | push ветки в origin |
| `pr --issue N [--body-file F] [--draft] [--dry-run]` | PR, идемпотентно (обновит существующий) |

## Рабочий процесс

1. `doctor` — убедись, что репозиторий и токен рабочие.
2. `list` — выбери задачу. Порядок уже по приоритету; уточни у пользователя,
   если задача не одна и нет явного указания.
3. `show N` — прочитай постановку и **все** комментарии.
4. Исследуй код обычными инструментами (grep/glob/read), не через скрипт.
5. Правь файлы обычными инструментами (edit/write).
6. Проверь: тесты, линтер, сборка — через shell. Не «на глаз».
7. `branch N` — если ещё не на рабочей ветке.
8. `status` → `commit -m "..."` → `push` → `pr --issue N --body-file pr.md`.
9. `report N --body-file report.md` — один отчёт с разделами
   «Сделано», «Не сделано / ограничения», «Проверка».
10. Сообщи пользователю ссылки на PR и отчёт.

## Правила

- **Ничего не выдумывай в отчёте**: если не проверял — напиши, что не проверял.
- **Не проси токен в чат**: пользователь сам делает `auth set` у себя в терминале.
- **Не расширяй скоуп**: правь только то, что нужно для задачи.
- **Никаких секретов** в коде, комментариях и PR: только `{env:...}` / переменные окружения.
- **Не делай** `git push --force`, не удаляй ветки, не мержи PR, не меняй
  настройки репозитория без явной просьбы.
- **Не создавай issue-дубликат**: скрипт сам отклонит похожий заголовок и вернёт
  `duplicate`.
- Если правка не нужна (вопрос, уже исправлено, не воспроизводится) — не коммить
  пустоту: объясни причину в `report` и предложи `close N`.
- Перед PR прочитай свой же diff: `git diff origin/<base>...HEAD`.
- Комментарии в issue — по делу и редко; итоговый отчёт публикуется отдельно.

## Типовые сценарии

Во всех командах репозиторий задаётся `--repo owner/name` (до подкоманды) либо
берётся из `git remote origin` текущего клона. Ключ подхватывается из файла
`github-tokens.json` по этому репозиторию.

Разобрать задачу и открыть PR:

```bash
S=~/.config/opencode/skills/github-tasks/scripts/gh_tasks.py
python $S --repo owner/name show 42
python $S --repo owner/name branch 42 --title "fix login redirect"
# ... правки и проверка ...
python $S --repo owner/name commit -m "fix: handle empty redirect target (#42)"
python $S --repo owner/name push
python $S --repo owner/name pr --issue 42 --body-file /tmp/pr.md
python $S --repo owner/name report 42 --body-file /tmp/report.md
```

Только посмотреть, что в очереди:

```bash
python $S --repo owner/name list --label priority:p1 --limit 10
```

Завести найденный баг:

```bash
python $S --repo owner/name create --title "Crash on empty config" --body-file /tmp/bug.md --priority p1
```
