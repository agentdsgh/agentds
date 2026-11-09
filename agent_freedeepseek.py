#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Агент для GitHub-репозитория через FreeDeepseekAPI (localhost:9655).
Логика агента (инструменты, раунды, git, GitHub, Codespaces) вроде без изменений.
Транспорт модели заменён на OpenAI-совместимый прокси FreeDeepseekAPI.
"""
import os, re, json, time, subprocess, shutil, pathlib, inspect, functools, signal, requests, base64, shlex, hashlib

# ======================= FreeDeepseekAPI =======================
FREEDEEPSEEK_URL     = "http://localhost:9655/v1"
FREEDEEPSEEK_API_KEY = None          # если PROXY_API_KEY задан — укажите его
FREEDEEPSEEK_MODEL   = None          # None = первый из /v1/models
FREEDEEPSEEK_N_CTX   = 320000        # лимит контекста DeepSeek Web (точно узнать нельзя)

# ======================= Поведение сборки =======================
RUN_SELFTEST = False                 # True: прогнать самотест инструментов при запуске

# ======================= GitHub =======================
GITHUB_OWNER = "agentdsgh"
GITHUB_REPO  = "agentds"
BASE_BRANCH  = None
REPO_CLONE_URL = None
ISSUE_STATE  = "open"
ONLY_ISSUES  = None                  # например [5] для первого прогона
ISSUE_LIMIT  = None
RERUN_DONE   = False
MAX_TOTAL_SEC = None

# ======================= Лимиты агента =======================
STEPS_PER_ROUND   = 14
MAX_ROUNDS        = 4
TEXT_EXTRA_TURNS  = 6
TOOL_OUTPUT_LIMIT = 120000
CMD_MAX_SEC       = 300
CMD_TOTAL_SEC     = 900
LLM_MAX_OUTPUT_TOKENS = 16000
AGENT_TEMPERATURE = 0.6
AGENT_THINKING    = True
STREAM_VIEW       = "text"
JUDGE             = True

# ======================= GitHub запись =======================
GITHUB_WRITE = True
COMMENT_STATUSES = ("solved", "partial")
MAX_COMMENTS_PER_ISSUE = 2
MAX_ISSUES_CREATED_PER_RUN = 5
AGENT_LABEL = "freedeepseek-agent"
PATCH_IN_COMMENT_LIMIT = 40000

# ======================= Ветка / push / PR =======================
GITHUB_PUSH   = True
OPEN_PR       = True
PR_DRAFT      = True
BRANCH_PREFIX = "agent"
PUSH_STATUSES = ("solved", "partial")

# ======================= Codespaces =======================
USE_CODESPACES = True
CODESPACE_MACHINE = "basicLinux32gb"
CODESPACE_IDLE_TIMEOUT_MIN = 30
CODESPACE_WORKDIR = f"/workspaces/{GITHUB_REPO}"
CODESPACE_CMD_MAX_SEC = 300
CODESPACE_TOTAL_SEC = 900
CODESPACE_MAX_RUNS_PER_ISSUE = 6

REPO_DIR = "/tmp/agent_repo"
OUT_DIR  = "./agent_out"
os.makedirs(f"{OUT_DIR}/patches", exist_ok=True)
os.makedirs(f"{OUT_DIR}/traces", exist_ok=True)

# ======================= Токен =======================
try:
    from kaggle_secrets import UserSecretsClient
    GITHUB_TOKEN = UserSecretsClient().get_secret("GITHUB_TOKEN")
except Exception:
    GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")
print("GITHUB_TOKEN:", "есть" if GITHUB_TOKEN else "нет (публичный репозиторий будет работать, но с лимитом запросов)")

# ======================= Страховочные константы =======================
# N_CTX используется в run_round() для проверки 0.85 * N_CTX.
# PRODUCTIVE_TOOLS используется в with_budget() для отметки «правка сделана».
N_CTX = FREEDEEPSEEK_N_CTX
PRODUCTIVE_TOOLS = {"write_file", "patch_file", "delete_file", "commit", "report_work", "report_no_action_needed"}


# ===== cell f967ab56 =====
import os, re, json, time, subprocess, shutil, pathlib, inspect, functools, signal, requests

# ===== Репозиторий и режим работы =====
GITHUB_OWNER = "agentdsgh"
GITHUB_REPO  = "agentds"
BASE_BRANCH  = None          # None = ветка репозитория по умолчанию
REPO_CLONE_URL = None        # None = https://github.com/OWNER/REPO.git (можно указать другой источник)
ISSUE_STATE  = "open"        # какие issues брать: open / closed / all
ONLY_ISSUES  = None          # например [12, 15]: решать только эти issues
ISSUE_LIMIT  = None          # например 3: взять только первые N (после сортировки по приоритету)
RERUN_DONE   = False         # True: заново решать issues, уже обработанные в прошлых запусках
MAX_TOTAL_SEC = None         # например 8*3600: не начинать новые issues после этого времени

# ===== Лимиты агента =====
STEPS_PER_ROUND   = 14       # вызовов инструментов за раунд
MAX_ROUNDS        = 4        # раундов на один issue (каждый раунд - новый чат)
TEXT_EXTRA_TURNS  = 6        # ходов сверх шагов: итоговый текст, ошибки формата
TOOL_OUTPUT_LIMIT = 12000    # символов результата инструмента, который видит модель
CMD_MAX_SEC       = 300      # потолок одной команды run_command
CMD_TOTAL_SEC     = 900      # суммарное время команд на один issue
LLM_MAX_OUTPUT_TOKENS = 160000
AGENT_TEMPERATURE = 0.8      # для вызовов инструментов ниже, чем в обычном чате
AGENT_THINKING    = True
STREAM_VIEW       = "text"   # "dots" - точки по мере генерации, "text" - печатать текст, "off"
JUDGE             = True     # самопроверка решения той же моделью (ориентир, не истина)

# ===== Запись в GitHub (нужен токен: Issues: Read and write; для Codespaces: Codespaces: Read and write) =====
GITHUB_WRITE = True          # False: ничего не отправлять в GitHub, комментарии и новые issues сохраняются черновиками
COMMENT_STATUSES = ("solved", "partial")   # по каким итогам публиковать комментарий-отчёт (добавьте "no_action", если нужно)
MAX_COMMENTS_PER_ISSUE = 2   # комментарии-вопросы модели по ходу работы (итоговый отчёт публикует система и не считается)
MAX_ISSUES_CREATED_PER_RUN = 5
AGENT_LABEL = "deepseek-agent"   # метка на issues, созданных агентом
PATCH_IN_COMMENT_LIMIT = 48000   # символов патча в комментарии (лимит GitHub на комментарий - 65536)

# ===== Ветка, push и pull request (нужны права токена: Contents: Read and write и Pull requests: Read and write) =====
GITHUB_PUSH   = True         # False: как раньше - только патч и комментарий, в репозиторий ничего не отправляется
OPEN_PR       = True         # после push открыть pull request (если для этой ветки он уже есть - обновить описание)
PR_DRAFT      = True         # открывать как черновик (draft): слить его можете только вы после просмотра
BRANCH_PREFIX = "agent"      # ветки вида agent/issue-12-fix-login; пушатся ТОЛЬКО ветки с этим префиксом, базовая - никогда
PUSH_STATUSES = ("solved", "partial")   # при каких итогах отправлять ветку ("partial" - работа не закончена: PR получит пометку [WIP])

# ===== Codespaces: облачная среда для проверок, которые нельзя выполнить локально =====
USE_CODESPACES = True        # среда создаётся лениво, только при первом вызове run_in_codespace; в конце прогона останавливается
CODESPACE_MACHINE = "basicLinux32gb"
CODESPACE_IDLE_TIMEOUT_MIN = 30
CODESPACE_WORKDIR = f"/workspaces/{GITHUB_REPO}"
CODESPACE_CMD_MAX_SEC = 300
CODESPACE_TOTAL_SEC = 900    # суммарное время команд в Codespaces на один issue
CODESPACE_MAX_RUNS_PER_ISSUE = 6

# ===== Папки =====
REPO_DIR = "/tmp/agent_repo"
OUT_DIR  = "/kaggle/working/agent_out" if os.path.isdir("/kaggle/working") else "./agent_out"
os.makedirs(f"{OUT_DIR}/patches", exist_ok=True)
os.makedirs(f"{OUT_DIR}/traces", exist_ok=True)

# Токен нужен только для приватного репозитория или чтобы не упереться в лимит 60 запросов/час.
# Kaggle: Add-ons -> Secrets -> GITHUB_TOKEN (fine-grained; репозиторий выбран в токене; Resource owner = владелец репозитория).
# Для чтения хватит Contents: Read + Issues: Read. Для комментариев, push и PR: Contents, Issues, Pull requests - Read and write.
try:
    from kaggle_secrets import UserSecretsClient
    GITHUB_TOKEN = UserSecretsClient().get_secret("GITHUB_TOKEN")
except Exception:
    GITHUB_TOKEN = None
print("GITHUB_TOKEN:", "есть" if GITHUB_TOKEN else "нет (публичный репозиторий будет работать, но с лимитом запросов)")

# ===== cell 42ff3e4a =====
GITHUB_API = "https://api.github.com"
REPO_PATH  = f"/repos/{GITHUB_OWNER}/{GITHUB_REPO}"
PRIORITY_ORDER = ["priority:p0", "priority:p1", "priority:p2"]
UNTRIAGED_RANK = 1.5

def _gh_headers():
    h = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if GITHUB_TOKEN:
        h["Authorization"] = f"Bearer {GITHUB_TOKEN}"
    return h

def _gh_request(method, path, retries=3, **kwargs):
    last = None
    for attempt in range(retries):
        last = requests.request(method, GITHUB_API + path, headers=_gh_headers(), timeout=30, **kwargs)
        if last.status_code < 500 and last.status_code != 403:
            return last
        if last.status_code == 403 and "rate limit" not in last.text.lower():
            return last
        time.sleep(2 ** attempt)
    return last

def gh_paginate(path, params=None, max_pages=10):
    params = dict(params or {}); params.setdefault("per_page", 100)
    items, page = [], 1
    while page <= max_pages:
        params["page"] = page
        r = _gh_request("GET", path, params=params)
        if r.status_code != 200:
            raise RuntimeError(f"GitHub {r.status_code}: {r.text[:300]}")
        batch = r.json()
        items.extend(batch)
        if len(batch) < params["per_page"]:
            break
        page += 1
    return items

def _issue_rank(it):
    names = {l["name"] for l in it.get("labels", [])}
    for rank, label in enumerate(PRIORITY_ORDER):
        if label in names:
            return rank
    return UNTRIAGED_RANK

def fetch_issues(state="open"):
    """Все issues (без pull request), от важных к неважным, внутри приоритета - по номеру."""
    items = [it for it in gh_paginate(f"{REPO_PATH}/issues", {"state": state}) if "pull_request" not in it]
    items.sort(key=lambda it: (_issue_rank(it), it["number"]))
    return [{"number": it["number"], "title": it["title"], "body": it.get("body") or "",
             "labels": [l["name"] for l in it.get("labels", [])], "url": it["html_url"],
             "state": it["state"], "comments": it.get("comments", 0)} for it in items]

def issue_text(it, max_comments=30, comment_limit=3000):
    out = [f"ISSUE #{it['number']}: {it['title']}",
           f"state: {it['state']} | labels: {','.join(it['labels']) or '-'} | {it['url']}",
           "", "--- ТЕКСТ ---", it["body"] or "(пусто)"]
    if it.get("comments"):
        try:
            for c in gh_paginate(f"{REPO_PATH}/issues/{it['number']}/comments")[:max_comments]:
                out += ["", f"--- КОММЕНТАРИЙ {c['user']['login']} ---", (c.get("body") or "")[:comment_limit]]
        except Exception as e:
            out.append(f"\n(комментарии недоступны: {e})")
    return "\n".join(out)


# ================= запись: комментарии и новые issues =================
AGENT_MARKER = "<!-- QWEN_AGENT_REPORT -->"   # по нему находим собственный отчёт и обновляем, а не дублируем
NOTE_MARKER  = "<!-- QWEN_AGENT_NOTE -->"
GH_WRITE = {"ok": True, "reason": None}       # станет False после первого отказа по правам: дальше не пробуем

def _err(label, resp):
    return f"{label}: GitHub {resp.status_code}: {resp.text[:200]}"

def _write_failed(label, resp):
    msg = _err(label, resp)
    if resp.status_code in (401, 403, 404):    # у fine-grained токена нехватка прав часто приходит как 404
        GH_WRITE["ok"] = False
        GH_WRITE["reason"] = msg + " (похоже на нехватку прав токена: нужен Issues: Read and write). Дальше запись отключена."
    return msg

def gh_upsert_report(number, body):
    """Публикует итоговый отчёт; если отчёт агента в этом issue уже есть (маркер) - обновляет его. -> (url, ошибка)"""
    if not GH_WRITE["ok"]:
        return None, GH_WRITE["reason"]
    try:
        mine = next((c for c in gh_paginate(f"{REPO_PATH}/issues/{number}/comments")
                     if AGENT_MARKER in (c.get("body") or "")), None)
    except RuntimeError as e:
        return None, str(e)
    if mine:
        r = _gh_request("PATCH", f"{REPO_PATH}/issues/comments/{mine['id']}", json={"body": body}); want = 200
    else:
        r = _gh_request("POST", f"{REPO_PATH}/issues/{number}/comments", json={"body": body}); want = 201
    if r.status_code != want:
        return None, _write_failed("комментарий", r)
    return r.json().get("html_url"), None

def gh_post_note(number, body):
    if not GH_WRITE["ok"]:
        return None, GH_WRITE["reason"]
    r = _gh_request("POST", f"{REPO_PATH}/issues/{number}/comments", json={"body": body})
    if r.status_code != 201:
        return None, _write_failed("комментарий", r)
    return r.json().get("html_url"), None

def gh_create_issue(title, body, labels):
    if not GH_WRITE["ok"]:
        return None, GH_WRITE["reason"]
    r = _gh_request("POST", f"{REPO_PATH}/issues", json={"title": title, "body": body, "labels": labels})
    if r.status_code != 201:
        return None, _write_failed("создание issue", r)
    return r.json(), None

# Названия ВСЕХ issues (и закрытых) - чтобы агент не заводил дубликаты
KNOWN_ISSUES = {}
def load_known_issues():
    KNOWN_ISSUES.clear()
    for it in gh_paginate(f"{REPO_PATH}/issues", {"state": "all"}):
        if "pull_request" not in it:
            KNOWN_ISSUES[it["number"]] = it["title"]

def _title_tokens(s):
    return set(re.findall(r"\w{3,}", (s or "").lower()))

def find_duplicate(title, threshold=0.6):
    t = _title_tokens(title)
    for n, other in KNOWN_ISSUES.items():
        o = _title_tokens(other)
        if t and o and len(t & o) / len(t | o) >= threshold:
            return n, other
    return None


# ================= ветка, push и pull request =================
GH_PUSH = {"ok": True, "reason": None}       # станет False после отказа по правам: дальше push и PR не пробуем

def _push_failed(label, text):
    msg = f"{label}: {text}"
    if re.search(r"\b(401|403|404)\b|denied|not accessible|Write access|Permission", text, re.I):
        GH_PUSH["ok"] = False
        GH_PUSH["reason"] = (msg + " (похоже на нехватку прав токена: нужны Contents: Read and write и Pull requests: Read and write, "
                             "репозиторий должен быть выбран в токене; если правился .github/workflows - ещё Workflows: Read and write). "
                             "Дальше push и PR отключены.")
    return msg

def gh_open_pr(branch, base, title, body, draft):
    """Открывает pull request; если PR для ветки уже есть - обновляет его описание. -> (url, ошибка)"""
    if not GH_PUSH["ok"]:
        return None, GH_PUSH["reason"]
    payload = {"title": title, "head": branch, "base": base, "body": body[:60000], "draft": bool(draft)}
    r = _gh_request("POST", f"{REPO_PATH}/pulls", json=payload)
    if r.status_code == 422 and "draft" in r.text.lower() and payload["draft"]:
        payload["draft"] = False                      # черновики в этом репозитории недоступны (например, приватный на бесплатном тарифе)
        r = _gh_request("POST", f"{REPO_PATH}/pulls", json=payload)
    if r.status_code == 201:
        return r.json()["html_url"], None
    if r.status_code == 422 and "already exists" in r.text.lower():
        ex = _gh_request("GET", f"{REPO_PATH}/pulls", params={"head": f"{GITHUB_OWNER}:{branch}", "state": "open"})
        if ex.status_code == 200 and ex.json():
            pr = ex.json()[0]
            _gh_request("PATCH", f"{REPO_PATH}/pulls/{pr['number']}", json={"body": payload["body"]})
            return pr["html_url"], None
    return None, _push_failed("pull request", f"GitHub {r.status_code}: {r.text[:200]}")

def check_token_access():
    """Проверка прав токена БЕЗ побочных эффектов: пустые запросы ничего не создают (GitHub проверяет права раньше содержимого,
    поэтому 422 = права есть, 403/404 = нет), а push идёт с --dry-run."""
    if not GITHUB_TOKEN:
        print("Токен не задан: запись в GitHub невозможна."); return
    u = _gh_request("GET", "/user")
    print("Токен создан аккаунтом:", u.json().get("login") if u.status_code == 200 else f"(ошибка {u.status_code})",
          "| владелец репозитория:", GITHUB_OWNER)
    def verdict(code):
        return "✅ есть" if code in (200, 201, 422) else f"❌ нет (HTTP {code})"
    r = _gh_request("POST", f"{REPO_PATH}/issues", json={})
    print("  Issues: Read and write     ->", verdict(r.status_code))
    r = _gh_request("POST", f"{REPO_PATH}/pulls", json={})
    print("  Pull requests: Read and write ->", verdict(r.status_code))
    import tempfile
    tmp = tempfile.mkdtemp()
    try:
        git_net("init", "-q", cwd=tmp)
        git_net("-c", "user.name=x", "-c", "user.email=x@x", "commit", "-q", "--allow-empty", "-m", "x", cwd=tmp)
        url = REPO_CLONE_URL or f"https://github.com/{GITHUB_OWNER}/{GITHUB_REPO}.git"
        p = git_net("push", "--dry-run", url, f"HEAD:refs/heads/{BRANCH_PREFIX}/_access-check", cwd=tmp, check=False, with_err=True)
        print("  Contents: Read and write   ->", "✅ есть" if p[0] == 0 else "❌ нет: " + _scrub(p[1].strip().splitlines()[-1][:160] if p[1].strip() else "?"))
    except Exception as e:
        print("  Contents: не удалось проверить:", _scrub(str(e))[:200])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ===== cell 2a34ebd2 =====
class ContextExceeded(Exception):
    pass

def _ts():
    return time.strftime("%H:%M:%S")

def llm_stream(messages, max_tokens=None, think=None, show=True):
    """OpenAI-совместимый вызов FreeDeepseekAPI (localhost:9655/v1)."""
    think = AGENT_THINKING if think is None else think
    headers = {"Content-Type": "application/json"}
    if FREEDEEPSEEK_API_KEY:
        headers["Authorization"] = f"Bearer {FREEDEEPSEEK_API_KEY}"
    payload = {
        "model": FREEDEEPSEEK_MODEL,
        "messages": messages,
        "stream": True,
        "stream_options": {"include_usage": True},
        "max_tokens": max_tokens or LLM_MAX_OUTPUT_TOKENS,
        "temperature": AGENT_TEMPERATURE if think else 0.7,
        "top_p": 0.95 if think else 0.8,
    }
    r = requests.post(FREEDEEPSEEK_URL + "/chat/completions", json=payload, headers=headers,
                      stream=True, timeout=(30, 3600))
    r.encoding = "utf-8"
    if r.status_code != 200:
        body = r.text
        if r.status_code == 400 and re.search(r"context|exceed|too long", body, re.I):
            raise ContextExceeded(body[:300])
        raise RuntimeError(f"HTTP {r.status_code}: {body[:500]}")
    content, usage, finish, chunks, t0 = [], None, None, 0, time.time()
    for raw in r.iter_lines(decode_unicode=True):
        if not raw or not raw.startswith("data:"):
            continue
        data = raw[5:].strip()
        if data == "[DONE]":
            break
        try:
            obj = json.loads(data)
        except json.JSONDecodeError:
            continue
        usage = obj.get("usage") or usage
        for ch in obj.get("choices") or []:
            d = ch.get("delta") or {}
            c = d.get("content")
            if c:
                content.append(c)
                if show and STREAM_VIEW == "text":
                    print(c, end="", flush=True)
            if d.get("reasoning_content") or c:
                chunks += 1
                if show and STREAM_VIEW == "dots" and chunks % 60 == 0:
                    print(".", end="", flush=True)
            if ch.get("finish_reason"):
                finish = ch["finish_reason"]
    if show and STREAM_VIEW != "off":
        n = (usage or {}).get("completion_tokens")
        print(f" ← модель: {n or chunks} ток за {time.time() - t0:.0f} с", flush=True)
    return {"content": "".join(content), "finish": finish, "usage": usage or {}}

def clean_answer(text):
    text = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S)
    if "</think>" in text:
        text = text.split("</think>", 1)[1]
    return text.strip()

# ---------------- бюджет шагов ----------------
PRODUCTIVE_TOOLS = {"write_file", "patch_file", "delete_file", "commit", "report_work", "report_no_action_needed"}

class StepBudget:
    def __init__(self, limit, log=None):
        self.limit, self.used, self.made_progress = limit, 0, False
        self.log = log if log is not None else []
    def consume(self):
        if self.used >= self.limit:
            return False
        self.used += 1
        return True
    @property
    def remaining(self):
        return max(0, self.limit - self.used)

def _step_suffix(b):
    s = f"\n[шаг {b.used}/{b.limit}]"
    if b.remaining == 0:
        s += " бюджет раунда исчерпан: заверши раунд коротким текстом (что сделано, что осталось)."
    elif b.remaining <= 2:
        s += (f" осталось {b.remaining}. Не отчитывайся, пока не сделано ВСЁ: если сделано всё — "
              "report_work(..., final=True); если нет — заверши раунд текстом (что сделано, что осталось).")
    elif b.remaining <= 4 and not b.made_progress:
        s += (f" осталось {b.remaining}, а правок в этом раунде ещё не было — только чтение. "
              "Не перечитывай известное: переходи к правке.")
    return s

def _short(args, kwargs, limit=150):
    s = ", ".join([repr(a) for a in args] + [f"{k}={v!r}" for k, v in kwargs.items()])
    return s if len(s) <= limit else s[:limit] + "…"

def with_budget(fn, budget):
    @functools.wraps(fn)
    def wrapped(*args, **kwargs):
        if not budget.consume():
            return f"ERROR: бюджет шагов раунда исчерпан ({budget.limit}). Заверши раунд текстом."
        t0 = time.time()
        print(f"[{_ts()}] → {fn.__name__}({_short(args, kwargs)})", flush=True)
        try:
            result = fn(*args, **kwargs)
        except Exception as e:
            result = f"ERROR: {type(e).__name__}: {e}"
        print(f"[{_ts()}] ← {fn.__name__} за {time.time() - t0:.0f} с", flush=True)
        if fn.__name__ in PRODUCTIVE_TOOLS and not str(result).startswith("ERROR"):
            budget.made_progress = True
        budget.log.append({"tool": fn.__name__, "args": kwargs or list(args), "result": str(result)[:1500]})
        return f"{result}{_step_suffix(budget)}"
    return wrapped

# ---------------- текстовый протокол вызова инструментов ----------------
_CALL_RE = re.compile(r"^\s*@@CALL[ \t]+([A-Za-z_][A-Za-z0-9_]*)\s*$")
_ARG_RE  = re.compile(r"^\s*@@ARG[ \t]+([A-Za-z_][A-Za-z0-9_]*)[ \t]*(?::[ \t]?(.*))?$")
_END_RE  = re.compile(r"^\s*@@END\s*$")
_STRIP_CALL_RE = re.compile(r"(?ms)^\s*@@CALL\b.*?(?:^\s*@@END\s*$|\Z)")
_TRAILING_NEWLINE_ARGS = {("write_file", "content")}

def text_protocol_prompt():
    return (
        "ФОРМАТ ВЫЗОВА ИНСТРУМЕНТОВ. Нативных вызовов функций здесь нет: чтобы вызвать инструмент, "
        "напиши в ответе блок РОВНО такого вида:\n\n"
        "@@CALL имя_инструмента\n@@ARG имя_аргумента\nзначение (может занимать несколько строк — как есть, "
        "без кавычек и экранирования)\n@@ARG другой_аргумент\nзначение\n@@END\n\n"
        "Пример:\n@@CALL read_file\n@@ARG path\nsrc/app.js\n@@ARG start_line\n1\n@@END\n\n"
        "Правила:\n"
        "- ОДИН вызов за сообщение. Всё после @@END игнорируется; результат придёт следующим сообщением "
        "(«РЕЗУЛЬТАТ ...»). Не придумывай результаты сам.\n"
        "- Ответ БЕЗ блока @@CALL означает, что ты закончил раунд. Не пиши «сейчас прочитаю...» без блока: "
        "если хочешь действовать — сразу вызывай инструмент.\n"
        "- Не оборачивай блок в ``` и ничего не добавляй между строкой @@ARG и значением. Значение — все строки "
        "до следующей строки @@ARG или @@END. Необязательные аргументы можно опустить. Булевы значения — true/false.\n"
        f"- Длина одного ответа ограничена (около {LLM_MAX_OUTPUT_TOKENS} токенов вместе с размышлениями). "
        "Блок без @@END (оборванный ответ) НЕ выполняется. Небольшие правки делай через patch_file, а не "
        "переписывай файл целиком.\n\nИНСТРУМЕНТЫ:\n"
    )

def _ann_name(a):
    return "str" if a is inspect.Parameter.empty else getattr(a, "__name__", str(a))

def tool_signature(t):
    parts = []
    for p in inspect.signature(t).parameters.values():
        s = f"{p.name}: {_ann_name(p.annotation)}"
        if p.default is not inspect.Parameter.empty:
            s += f" = {p.default!r}"
        parts.append(s)
    return f"{t.__name__}({', '.join(parts)})"

def build_tool_spec(tools):
    return "\n".join(f"- {tool_signature(t)} — {' '.join((inspect.getdoc(t) or '').split())}" for t in tools)

def parse_text_call(text):
    """(name, raw_args, error, n_calls). name=None и error=None: вызова нет."""
    lines = (text or "").replace("\r\n", "\n").split("\n")
    idxs = [i for i, l in enumerate(lines) if _CALL_RE.match(l)]
    if not idxs:
        return None, None, None, 0
    name = _CALL_RE.match(lines[idxs[0]]).group(1)
    args, cur, buf, closed = {}, None, [], False
    for l in lines[idxs[0] + 1:]:
        if _END_RE.match(l):
            closed = True
            break
        m = _ARG_RE.match(l)
        if m:
            if cur is not None:
                args[cur] = "\n".join(buf)
            cur, buf = m.group(1), ([m.group(2)] if m.group(2) is not None else [])
        elif cur is not None:
            buf.append(l)
    if cur is not None:
        args[cur] = "\n".join(buf)
    if not closed:
        return name, args, ("вызов не закончен: нет строки @@END (ответ мог быть оборван лимитом длины — "
                            "для больших правок используй patch_file)"), len(idxs)
    return name, args, None, len(idxs)

def coerce_args(t, name, raw_args):
    sig = inspect.signature(t)
    kwargs = {}
    for k, raw in raw_args.items():
        p = sig.parameters.get(k)
        if p is None:
            raise ValueError(f"неизвестный аргумент «{k}»")
        ann, has_default = p.annotation, p.default is not inspect.Parameter.empty
        if ann in (int, "int", float, "float", bool, "bool"):
            s = raw.strip()
            if s == "" and has_default:
                continue
            try:
                if ann in (int, "int"):
                    kwargs[k] = int(s)
                elif ann in (float, "float"):
                    kwargs[k] = float(s)
                elif s.lower() in ("true", "1", "yes", "да"):
                    kwargs[k] = True
                elif s.lower() in ("false", "0", "no", "нет"):
                    kwargs[k] = False
                else:
                    raise ValueError(s)
            except ValueError:
                raise ValueError(f"аргумент «{k}» должен быть {_ann_name(ann)}, получено {s[:40]!r}") from None
        else:
            v = raw if "\n" in raw else raw.strip()
            if (name, k) in _TRAILING_NEWLINE_ARGS and v and not v.endswith("\n"):
                v += "\n"
            kwargs[k] = v
    try:
        sig.bind(**kwargs)
    except TypeError as e:
        raise ValueError(str(e)) from None
    return kwargs

def strip_calls(text):
    return _STRIP_CALL_RE.sub("", text or "").strip()

# ===== cell 968a13df =====
# Codespaces: облачная среда для проверок. Код у нас только локально (push запрещён), поэтому перед КАЖДОЙ командой
# локальный diff переносится в чистое состояние базовой ветки внутри Codespace. Жизненный цикл - через REST, команды - через gh CLI.
import base64, shlex, hashlib

CS = {"name": None, "error": None, "gh_ready": None, "used": False}
SSH_NOT_READY_MARKERS = ("error getting ssh server details", "failed to start ssh server")
SSH_READY_RETRIES, SSH_READY_RETRY_DELAY_SEC = 6, 10
_TIMEOUT_EXIT_CODES = (124, 137)

def _ensure_gh_cli():
    """Ставит gh CLI (если нет) и проверяет авторизацию по GITHUB_TOKEN. Один раз за прогон."""
    if CS["gh_ready"] is not None:
        return CS["gh_ready"]
    if shutil.which("gh") is None:
        steps = ["apt-get update -qq", "apt-get install -y -qq curl",
                 "curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg -o /usr/share/keyrings/githubcli-archive-keyring.gpg",
                 "chmod go+r /usr/share/keyrings/githubcli-archive-keyring.gpg",
                 "bash -c 'echo \"deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] "
                 "https://cli.github.com/packages stable main\" > /etc/apt/sources.list.d/github-cli.list'",
                 "apt-get update -qq", "apt-get install -y -qq gh"]
        for cmd in steps:
            r = subprocess.run(cmd, shell=True, capture_output=True, text=True)
            if r.returncode != 0:
                print(f"gh CLI: шаг установки провалился ({cmd[:40]}...): {r.stderr[:200]}")
                CS["gh_ready"] = False
                return False
    os.environ["GH_TOKEN"] = GITHUB_TOKEN or ""
    check = subprocess.run(["gh", "auth", "status"], capture_output=True, text=True)
    CS["gh_ready"] = check.returncode == 0
    print("gh CLI готов и авторизован." if CS["gh_ready"] else (check.stdout + check.stderr)[:300])
    return CS["gh_ready"]

def gh_create_codespace(branch):
    r = _gh_request("POST", f"{REPO_PATH}/codespaces",
                    json={"ref": branch, "machine": CODESPACE_MACHINE, "idle_timeout_minutes": CODESPACE_IDLE_TIMEOUT_MIN})
    if r.status_code not in (200, 201):
        return None, _err("создание codespace", r) + " (нужен токен с Codespaces: Read and write)"
    return r.json(), None

def gh_wait_codespace_available(name, timeout_sec=240):
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        r = _gh_request("GET", f"/user/codespaces/{name}")
        state = (r.json() if r.status_code == 200 else {}).get("state")
        if state == "Available":
            return True, state
        if state in ("Failed", "Deleted", "Shutdown", "Unavailable"):
            return False, state
        time.sleep(5)
    return False, "Timeout"

def gh_find_codespace_for_branch(branch):
    r = _gh_request("GET", f"{REPO_PATH}/codespaces")
    if r.status_code != 200:
        return None
    for cs in r.json().get("codespaces", []):
        if cs.get("ref") == branch and cs.get("state") not in ("Shutdown", "Deleted", "Failed"):
            return cs["name"]
    return None

def ensure_codespace(base):
    if CS["name"]:
        return CS["name"], None
    if CS["error"]:
        return None, CS["error"]
    if not GITHUB_TOKEN or not _ensure_gh_cli():
        CS["error"] = "gh CLI недоступен или токен не авторизован: Codespaces не работают, проверяй локально через run_command"
        return None, CS["error"]
    name = gh_find_codespace_for_branch(base)
    if not name:
        print(f"[{_ts()}] создаю Codespace на ветке {base} (1-3 минуты)…", flush=True)
        data, err = gh_create_codespace(base)
        if err:
            CS["error"] = err
            return None, err
        name = data["name"]
    ok, state = gh_wait_codespace_available(name)
    if not ok:
        CS["error"] = f"Codespace не стал доступен (состояние: {state})"
        return None, CS["error"]
    CS["name"], CS["used"] = name, True
    print(f"[{_ts()}] Codespace готов: {name}", flush=True)
    return name, None

def stop_codespace_if_any():
    if CS["name"]:
        r = _gh_request("POST", f"/user/codespaces/{CS['name']}/stop")
        print(f"Codespace {CS['name']} " + ("остановлен." if r.status_code in (200, 202) else f"не удалось остановить ({r.status_code}), его остановит idle_timeout"))
        CS["name"] = None

def _codespace_script(command, patch_b64, remote_limit):
    """Однострочный удалённый скрипт: чистая база -> перенос локального diff -> команда с таймаутом на стороне Codespace."""
    q = shlex.quote(command)
    return "; ".join([
        f'cd {shlex.quote(CODESPACE_WORKDIR)} 2>/dev/null || cd "$(ls -d /workspaces/*/ 2>/dev/null | head -1)"',
        "git reset -q --hard HEAD 2>/dev/null; git clean -fdq 2>/dev/null",
        f"echo {patch_b64} | base64 -d > /tmp/agent.patch",
        'if [ -s /tmp/agent.patch ]; then git apply --whitespace=nowarn /tmp/agent.patch || echo "[sync] ОШИБКА git apply: правки не перенесены" >&2; fi',
        f"if command -v timeout >/dev/null 2>&1; then timeout -k 5 {int(remote_limit)} bash -c {q}; else bash -c {q}; fi",
    ])

def _codespace_run(name, script, timeout_sec):
    # ssh склеивает argv пробелами и отдаёт удалённому шеллу одной строкой, поэтому без shlex.quote команда ломается
    return subprocess.run(["gh", "codespace", "ssh", "--codespace", name, "--", "bash", "-lc", shlex.quote(script)],
                          capture_output=True, text=True, timeout=timeout_sec)

def codespace_exec(sess, command, timeout_sec=120):
    base = sess["base"]
    if sess["cs_runs"] >= CODESPACE_MAX_RUNS_PER_ISSUE:
        return f"ERROR: лимит запусков в Codespaces на issue исчерпан ({CODESPACE_MAX_RUNS_PER_ISSUE}). Опиши в отчёте, что осталось проверить."
    left = CODESPACE_TOTAL_SEC - sess["cs_sec"]
    if left <= 0:
        return f"ERROR: исчерпан общий лимит времени Codespaces на issue ({CODESPACE_TOTAL_SEC} с)."
    git("add", "-A", "-N")
    patch = git("diff", "--binary", base)
    key = (" ".join(command.split()), hashlib.sha1(patch.encode()).hexdigest())
    if key in sess["cs_cache"]:
        return "[кэш: код не менялся с прошлого запуска этой команды, повторный запуск не нужен]\n" + sess["cs_cache"][key]
    b64 = base64.b64encode(patch.encode()).decode()
    if len(b64) > 90000:
        return "ERROR: патч слишком велик для переноса в Codespace. Проверь локально через run_command или сократи изменения."
    name, err = ensure_codespace(base)
    if err:
        return "ERROR: " + err
    to = max(10, min(int(timeout_sec), CODESPACE_CMD_MAX_SEC, left))
    t0, result = time.time(), None
    for attempt in range(1, SSH_READY_RETRIES + 1):
        try:
            r = _codespace_run(name, _codespace_script(command, b64, to), to + 90)
        except subprocess.TimeoutExpired:
            result = f"ERROR: команда не уложилась в {to} с и остановлена. Уменьши объём проверки."
            break
        if r.returncode != 0 and any(m in r.stderr.lower() for m in SSH_NOT_READY_MARKERS) and attempt < SSH_READY_RETRIES:
            time.sleep(SSH_READY_RETRY_DELAY_SEC)
            continue
        out = f"exit={r.returncode}\n--- stdout ---\n{r.stdout}\n--- stderr ---\n{r.stderr}"
        if len(out) > 6000:
            out = "[... начало обрезано ...]\n" + out[-6000:]
        if r.returncode in _TIMEOUT_EXIT_CODES:
            out += f"\n[команда остановлена по лимиту {to} с]"
        result = out
        sess["commands"].append({"cmd": "[codespace] " + command[:180], "exit": r.returncode})
        break
    sess["cs_runs"] += 1; sess["cs_total"] += 1; sess["cs_sec"] += time.time() - t0
    if result and not result.startswith("ERROR"):
        sess["cs_cache"][key] = result
    return result or "ERROR: sshd в Codespace не поднялся (проверьте .devcontainer: нужна фича ghcr.io/devcontainers/features/sshd:1)"

# ===== cell dba7d58c =====
# ---------------- git и безопасность путей ----------------
def git(*args, check=True, timeout=180):
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_AUTHOR_NAME": "qwen-agent",
           "GIT_AUTHOR_EMAIL": "agent@localhost", "GIT_COMMITTER_NAME": "qwen-agent",
           "GIT_COMMITTER_EMAIL": "agent@localhost"}
    r = subprocess.run(["git", *args], cwd=REPO_DIR, capture_output=True, text=True, timeout=timeout, env=env)
    if check and r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args[:3])} завершился с ошибкой: {(r.stderr or r.stdout).strip()[:500]}")
    return r.stdout

def clone_repo():
    """Свежий клон репозитория. Токен нужен только для клонирования и в рабочей копии не остаётся."""
    shutil.rmtree(REPO_DIR, ignore_errors=True)
    clean_url = REPO_CLONE_URL or f"https://github.com/{GITHUB_OWNER}/{GITHUB_REPO}.git"
    url = clean_url
    if GITHUB_TOKEN and url.startswith("https://github.com/"):
        url = url.replace("https://", f"https://x-access-token:{GITHUB_TOKEN}@", 1)
    cmd = ["git", "clone", "--depth", "100"] + (["--branch", BASE_BRANCH] if BASE_BRANCH else []) + [url, REPO_DIR]
    r = subprocess.run(cmd, capture_output=True, text=True, env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})
    if r.returncode != 0:
        raise RuntimeError("git clone не удался: " + r.stderr.replace(GITHUB_TOKEN or "\0", "***")[:500])
    git("remote", "set-url", "origin", clean_url)
    with open(f"{REPO_DIR}/.git/info/exclude", "a") as f:        # мусор от запуска тестов не попадёт в коммиты
        f.write("\n__pycache__/\n*.pyc\n.pytest_cache/\nnode_modules/\n.venv/\n")
    return BASE_BRANCH or git("rev-parse", "--abbrev-ref", "HEAD").strip()

import base64

def _basic_b64():
    return base64.b64encode(f"x-access-token:{GITHUB_TOKEN}".encode()).decode() if GITHUB_TOKEN else None

def _scrub(text):
    for secret in (GITHUB_TOKEN, _basic_b64()):
        if secret:
            text = text.replace(secret, "***")
    return text

def git_net(*args, cwd=None, timeout=300, check=True, with_err=False):
    """git с доступом к GitHub (push, fetch, ls-remote). Токен передаётся через переменные окружения только этому процессу:
    он не попадает ни в .git/config, ни в аргументы команды, ни в окружение run_command."""
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_AUTHOR_NAME": "qwen-agent", "GIT_AUTHOR_EMAIL": "agent@localhost",
           "GIT_COMMITTER_NAME": "qwen-agent", "GIT_COMMITTER_EMAIL": "agent@localhost"}
    if GITHUB_TOKEN:
        env.update(GIT_CONFIG_COUNT="1", GIT_CONFIG_KEY_0="http.https://github.com/.extraheader",
                   GIT_CONFIG_VALUE_0=f"AUTHORIZATION: basic {_basic_b64()}")
    r = subprocess.run(["git", *args], cwd=cwd or REPO_DIR, capture_output=True, text=True, timeout=timeout, env=env)
    if with_err:
        return r.returncode, _scrub(r.stdout + r.stderr)
    if check and r.returncode != 0:
        raise RuntimeError(_scrub(f"git {args[0]} не удался: {(r.stderr or r.stdout).strip()[:500]}"))
    return r.stdout

_RU = dict(zip("абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
               ["a","b","v","g","d","e","e","zh","z","i","y","k","l","m","n","o","p","r","s","t","u","f","h","ts","ch","sh","sch","","y","","e","yu","ya"]))

def _slug(text, limit=40):
    s = "".join(_RU.get(ch, ch) for ch in (text or "").lower())
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")[:limit].rstrip("-")

def branch_name(issue_number, title=""):
    """agent/issue-12-dobav-fayl-v-repozitoriy: префикс, номер issue и короткое название латиницей."""
    slug = _slug(title)
    return f"{BRANCH_PREFIX}/issue-{issue_number}" + (f"-{slug}" if slug else "")

def find_remote_branch(issue_number):
    """Ветка этого issue, уже существующая в GitHub (по префиксу и номеру; название после номера может быть любым)."""
    pats = [f"refs/heads/{BRANCH_PREFIX}/issue-{issue_number}", f"refs/heads/{BRANCH_PREFIX}/issue-{issue_number}-*"]
    out = git_net("ls-remote", "--heads", "origin", *pats, check=False)
    names = [ln.split("\t", 1)[1][len("refs/heads/"):] for ln in out.splitlines() if "\t" in ln]
    return sorted(names)[0] if names else None

BRANCH_STATE = {}
def prepare_branch(issue_number, base, title=""):
    """Рабочая ветка issue: если она уже есть в GitHub - продолжаем с неё (работа прошлых запусков не теряется), иначе создаём от базовой."""
    git("checkout", "-f", base)
    git("clean", "-fd")
    branch, state = branch_name(issue_number, title), "new"
    if GITHUB_PUSH:
        found = find_remote_branch(issue_number)
        if found:
            git_net("fetch", "origin", f"+refs/heads/{found}:refs/remotes/origin/{found}")
            git("checkout", "-B", found, f"refs/remotes/origin/{found}")
            branch, state = found, "resumed"
    if state == "new":
        git("checkout", "-B", branch)
    BRANCH_STATE[branch] = state
    print(f"Ветка {branch}: " + ("уже есть в GitHub, продолжаю с неё" if state == "resumed" else "создана от базовой"))
    return branch

def _safe(path):
    root = pathlib.Path(REPO_DIR).resolve()
    p = (root / path).resolve()
    if p != root and root not in p.parents:
        raise ValueError("путь вне репозитория")
    if ".git" in p.relative_to(root).parts:
        raise ValueError("нельзя трогать .git")
    return p

def _clip(text, limit=None):
    text, limit = str(text), limit or TOOL_OUTPUT_LIMIT
    return text if len(text) <= limit else text[:limit] + f"\n...[обрезано, всего {len(text)} символов]"

def _has_changes(base):
    git("add", "-A", "-N")
    return bool(git("diff", base).strip())

def _ahead(base):
    return int(git("rev-list", "--count", f"{base}..HEAD").strip() or 0)

BLOCKED_CMDS = [
    (r"\bgit\s+(?:-\S+\s+)*(push|remote|config|credential)\b", "git push/remote/config запрещены: ветку отправляет система"),
    (r"\bsudo\b", "sudo запрещён"),
    (r"\brm\s+-[a-zA-Z]*[rf][a-zA-Z]*\s+(/|~|\$HOME|\.\.)(\s|$)", "опасное удаление запрещено"),
    (r"curl[^|;&]*\|\s*(ba)?sh", "запуск скриптов из сети запрещён"),
    (r"\bgh\s", "gh CLI недоступен"),
    (r"(^|\s)(shutdown|reboot|mkfs)\b|\bdd\s+if=", "системные команды запрещены"),
    (r"GITHUB_TOKEN|KAGGLE_KEY|\.git/config", "доступ к секретам запрещён"),
]
_SECRET_ENV = re.compile(r"TOKEN|SECRET|KEY|PASSWORD|KAGGLE", re.I)

CREATED_ISSUES = []          # все issues, созданные агентом (и черновики); сохраняются в OUT_DIR/created_issues.json
RUN_CREATED = {"n": 0}       # сколько создано в ЭТОМ прогоне (лимит MAX_ISSUES_CREATED_PER_RUN)
CREATED_PATH = f"{OUT_DIR}/created_issues.json"
if os.path.exists(CREATED_PATH):
    CREATED_ISSUES.extend(json.load(open(CREATED_PATH, encoding="utf-8")))

def _save_created():
    json.dump(CREATED_ISSUES, open(CREATED_PATH, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

def _draft(name, text):
    os.makedirs(f"{OUT_DIR}/drafts", exist_ok=True)
    pathlib.Path(f"{OUT_DIR}/drafts/{name}").write_text(text, encoding="utf-8")

def new_sess(issue, base, branch, text):
    return {"issue": issue, "base": base, "branch": branch, "issue_text": text, "final": None, "no_action": None,
            "reports": [], "commands": [], "cmd_sec": 0.0, "cs_runs": 0, "cs_total": 0, "cs_sec": 0.0, "cs_cache": {},
            "comments": 0, "created": []}

def make_tools(sess, budget):
    """Инструменты, замкнутые на один issue (sess) и бюджет раунда."""
    base = sess["base"]

    def get_issue() -> str:
        """Показать текст текущего issue целиком (с комментариями)."""
        return _clip(sess["issue_text"], 30000)

    def list_files(subdir: str = "") -> str:
        """Список файлов репозитория (путь относительно корня). subdir — показать только эту папку."""
        files = [f for f in git("ls-files", "-co", "--exclude-standard").splitlines()
                 if not subdir or f.startswith(subdir.strip("/") + "/") or f == subdir]
        if not files:
            return "Файлов не найдено."
        head = f"Файлов: {len(files)}" + (" (показаны первые 400, уточни subdir)" if len(files) > 400 else "")
        return head + "\n" + "\n".join(files[:400])

    def read_file(path: str, start_line: int = 1, max_lines: int = 250) -> str:
        """Прочитать файл (текст без номеров строк). Большие файлы читай частями: start_line и max_lines."""
        p = _safe(path)
        if not p.is_file():
            return f"ERROR: файла нет: {path}"
        data = p.read_bytes()
        if b"\0" in data[:4096]:
            return f"ERROR: {path} — бинарный файл ({len(data)} байт)"
        lines = data.decode("utf-8", "replace").splitlines()
        a = max(1, start_line); b = min(len(lines), a + max(1, max_lines) - 1)
        body = "\n".join(lines[a - 1:b])
        tail = f"\n[... ещё {len(lines) - b} строк: читай с start_line={b + 1}]" if b < len(lines) else ""
        return _clip(f"[{path}: строки {a}-{b} из {len(lines)}]\n{body}{tail}")

    def search_code(query: str, subdir: str = "") -> str:
        """Поиск подстроки (без учёта регистра) по файлам репозитория. Результат: путь:строка: текст."""
        q, hits = query.lower(), []
        for f in git("ls-files", "-co", "--exclude-standard").splitlines():
            if subdir and not f.startswith(subdir.strip("/")):
                continue
            p = pathlib.Path(REPO_DIR) / f
            try:
                if not p.is_file() or p.stat().st_size > 1_000_000:
                    continue
                for i, line in enumerate(p.read_text("utf-8", "ignore").splitlines(), 1):
                    if q in line.lower():
                        hits.append(f"{f}:{i}: {line.strip()[:200]}")
                        if len(hits) >= 60:
                            return "\n".join(hits) + "\n[показаны первые 60 совпадений, уточни запрос]"
            except OSError:
                continue
        return "\n".join(hits) or "Совпадений нет."

    def write_file(path: str, content: str) -> str:
        """Создать файл или полностью перезаписать его (нужен ПОЛНЫЙ текст). Для небольших правок используй patch_file."""
        p = _safe(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        warn = ""
        if path.endswith(".py"):
            try:
                compile(content, path, "exec")
            except SyntaxError as e:
                warn = f"\nВНИМАНИЕ: файл не компилируется: {e}"
        elif path.endswith(".json"):
            try:
                json.loads(content)
            except ValueError as e:
                warn = f"\nВНИМАНИЕ: невалидный JSON: {e}"
        return f"OK: записан {path} ({content.count(chr(10)) + 1} строк){warn}"

    def patch_file(path: str, old_text: str, new_text: str) -> str:
        """Заменить фрагмент old_text на new_text. old_text должен совпасть символ в символ и встречаться ровно один раз."""
        p = _safe(path)
        if not p.is_file():
            return f"ERROR: файла нет: {path}"
        src = p.read_text("utf-8")
        n = src.count(old_text)
        if not old_text:
            return "ERROR: old_text пуст"
        if n == 0:
            return "ERROR: old_text не найден (проверь пробелы и отступы: прочитай нужный фрагмент через read_file)"
        if n > 1:
            return f"ERROR: old_text встречается {n} раз: добавь соседние строки, чтобы фрагмент был уникальным"
        p.write_text(src.replace(old_text, new_text, 1), encoding="utf-8")
        return f"OK: заменён фрагмент в {path}"

    def delete_file(path: str) -> str:
        """Удалить файл."""
        p = _safe(path)
        if not p.is_file():
            return f"ERROR: файла нет: {path}"
        p.unlink()
        return f"OK: удалён {path}"

    def run_command(command: str, timeout_sec: int = 120) -> str:
        """Выполнить shell-команду в корне репозитория (тесты, линтер, запуск скрипта). Без интерактива и вечных процессов."""
        for pat, why in BLOCKED_CMDS:
            if re.search(pat, command):
                return f"ERROR: команда заблокирована: {why}"
        left = CMD_TOTAL_SEC - sess["cmd_sec"]
        if left <= 0:
            return f"ERROR: исчерпан общий лимит времени команд на этот issue ({CMD_TOTAL_SEC} с). Опиши в отчёте, что осталось проверить."
        to = max(5, min(int(timeout_sec), CMD_MAX_SEC, left))
        env = {k: v for k, v in os.environ.items() if not _SECRET_ENV.search(k)}
        env["PYTHONDONTWRITEBYTECODE"] = "1"   # иначе правка той же длины в ту же секунду даёт устаревший .pyc
        t0 = time.time()
        proc = subprocess.Popen(["bash", "-lc", command], cwd=REPO_DIR, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, env=env, start_new_session=True)
        try:
            out, _ = proc.communicate(timeout=to)
            code = proc.returncode
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            out, _ = proc.communicate()
            code = "TIMEOUT"
            out = (out or "") + f"\n[команда остановлена по таймауту {to} с]"
        sess["cmd_sec"] += time.time() - t0
        sess["commands"].append({"cmd": command[:200], "exit": code})
        out = out or ""
        if len(out) > 6000:
            out = "[... начало обрезано ...]\n" + out[-6000:]
        return f"exit={code}\n{out}"

    def git_diff() -> str:
        """Показать все изменения в рабочей копии относительно базовой ветки (и закоммиченные, и нет)."""
        git("add", "-A", "-N")
        d = git("diff", base)
        return _clip(d, 20000) if d.strip() else "Изменений относительно базовой ветки нет."

    def commit(message: str) -> str:
        """Закоммитить все текущие изменения в рабочую ветку (сообщение коммита — по-английски или по-русски, кратко)."""
        git("add", "-A")
        if not git("diff", "--cached", "--name-only").strip():
            return "ERROR: нечего коммитить"
        git("commit", "-m", message[:200])
        return f"OK: коммит {git('rev-parse', '--short', 'HEAD').strip()} ({', '.join(git('diff', '--name-only', 'HEAD~1', 'HEAD').split()[:8])})"

    def report_work(summary: str, final: bool = False) -> str:
        """Отчёт по issue. Без final — промежуточная заметка, задачу НЕ засчитывает. final=True — ОДИН раз, когда сделано ВСЁ: summary с разделами «Сделано», «Не сделано / ограничения», «Проверка»."""
        if not final:
            sess["reports"].append(summary)
            return "OK: промежуточный отчёт записан (задачу не засчитывает)."
        if not _has_changes(base):
            return ("ERROR: в рабочей копии нет изменений относительно базы — финальный отчёт не принят. "
                    "Внеси правку или используй report_no_action_needed.")
        if sess["final"] and not git("status", "--porcelain").strip():
            return "ERROR: финальный отчёт уже принят, новых изменений нет."
        if git("status", "--porcelain").strip():
            git("add", "-A")
            git("commit", "-m", f"agent: issue #{sess['issue']}")
        sess["final"] = summary
        return f"OK: финальный отчёт принят (коммитов в ветке: {_ahead(base)}). Раунд можно завершать."

    def report_no_action_needed(reason: str) -> str:
        """Использовать ВМЕСТО report_work, если после честного изучения issue и кода правка репозитория НЕ нужна. reason — конкретная причина."""
        if _has_changes(base):
            return "ERROR: в рабочей копии есть изменения. Если они нужны — report_work(final=True)."
        sess["no_action"] = reason
        return "OK: принято. Причина будет проверена."

    def comment_issue(body: str) -> str:
        """Оставить комментарий в ТЕКУЩЕМ issue: вопрос людям или пояснение по ходу работы. Итоговый отчёт публикуется автоматически, его дублировать не нужно. Лимит комментариев на issue."""
        if sess["comments"] >= MAX_COMMENTS_PER_ISSUE:
            return f"ERROR: лимит комментариев на issue исчерпан ({MAX_COMMENTS_PER_ISSUE})."
        if len(body.strip()) < 20:
            return "ERROR: комментарий слишком короткий: напиши конкретный вопрос или пояснение."
        text = f"{NOTE_MARKER}\n{body.strip()[:6000]}\n\n— 🤖 локальная модель (вопрос/пояснение по ходу работы)"
        sess["comments"] += 1
        _draft(f"note-issue-{sess['issue']}-{sess['comments']}.md", text)
        if not GITHUB_WRITE:
            return "OK: черновик комментария сохранён (GITHUB_WRITE=False), в GitHub не отправлен."
        url, err = gh_post_note(sess["issue"], text)
        return f"OK: комментарий опубликован: {url}" if not err else f"ERROR: не удалось опубликовать: {err}"

    def create_issue(title: str, body: str, priority: str = "p2") -> str:
        """Завести НОВЫЙ issue под отдельную проблему, найденную вне рамок текущей задачи (не чини её молча и не раздувай текущий diff). priority: p0 — блокирует использование репозитория, p1 — реальная проблема с обходным путём, p2 — мелочь. В body: что не так, где (файл/строка), как воспроизвести, как предлагаешь исправить. Дубликаты отклоняются, лимит за прогон."""
        p = priority.strip().lower().replace("priority:", "")
        if p not in ("p0", "p1", "p2"):
            return "ERROR: priority должен быть p0, p1 или p2"
        if RUN_CREATED["n"] >= MAX_ISSUES_CREATED_PER_RUN:
            return f"ERROR: лимит новых issues за прогон исчерпан ({MAX_ISSUES_CREATED_PER_RUN}). Опиши находку в итоговом отчёте."
        if len(title.strip()) < 8 or len(body.strip()) < 40:
            return "ERROR: нужны содержательные title (от 8 символов) и body (от 40): что не так, где и как воспроизвести."
        dup = find_duplicate(title)
        if dup:
            return f"ERROR: похожий issue уже есть: #{dup[0]} «{dup[1][:80]}». Новый не создан."
        full = body.strip()[:8000] + f"\n\n---\n🤖 Найдено автоматически при работе над #{sess['issue']} (локальная модель). {NOTE_MARKER}"
        rec = {"found_in": sess["issue"], "title": title.strip()[:200], "priority": p, "number": None, "url": None, "draft": not GITHUB_WRITE}
        if GITHUB_WRITE:
            issue, err = gh_create_issue(rec["title"], full, [f"priority:{p}", AGENT_LABEL])
            if err:
                return f"ERROR: issue не создан: {err}"
            rec["number"], rec["url"] = issue["number"], issue["html_url"]
            KNOWN_ISSUES[issue["number"]] = rec["title"]
        else:
            KNOWN_ISSUES[-(len(CREATED_ISSUES) + 1)] = rec["title"]
            _draft(f"new-issue-{len(CREATED_ISSUES) + 1}.md", f"# {rec['title']}\n\nlabels: priority:{p}, {AGENT_LABEL}\n\n{full}")
        CREATED_ISSUES.append(rec); sess["created"].append(rec); RUN_CREATED["n"] += 1
        _save_created()
        return (f"OK: создан issue #{rec['number']}: {rec['url']}" if GITHUB_WRITE else
                "OK: черновик нового issue сохранён (GITHUB_WRITE=False), в GitHub не отправлен.")

    def run_in_codespace(command: str, timeout_sec: int = 120) -> str:
        """Выполнить команду в облачной среде GitHub Codespaces с ТВОИМИ правками (они переносятся из локальной копии автоматически, push не нужен). Используй, когда локально проверить нельзя: нужно окружение/зависимости из devcontainer репозитория, другая версия рантайма, база данных и т. п. Первый вызов создаёт среду (1-3 минуты). Остальное проверяй локально через run_command."""
        return codespace_exec(sess, command, timeout_sec)

    tools = [get_issue, list_files, read_file, search_code, write_file, patch_file, delete_file,
             run_command] + ([run_in_codespace] if (USE_CODESPACES and GITHUB_TOKEN) else []) + \
            [git_diff, commit, comment_issue, create_issue, report_work, report_no_action_needed]
    return [with_budget(t, budget) for t in tools]

# ===== cell 42a0cb18 =====
SYSTEM_PROMPT = (
    f"Ты — инженер-агент. Ты работаешь с локальной копией git-репозитория {GITHUB_OWNER}/{GITHUB_REPO} "
    "ТОЛЬКО через инструменты. Рабочая ветка для задачи уже создана и выбрана. Твоя задача — решить ОДИН issue.\n\n"
    "Цикл работы: изучить issue -> найти код (list_files / search_code / read_file) -> внести правку "
    "(patch_file для небольших изменений, write_file для новых файлов) -> проверить (run_command: тесты, запуск "
    "скрипта, если они есть) -> git_diff -> report_work(final=True).\n\n"
    "Правила:\n"
    "- Никогда не выдумывай содержимое файлов: сначала читай. Перед patch_file прочитай нужный фрагмент: old_text "
    "должен совпасть символ в символ и встречаться один раз.\n"
    + ("- Ты сам ничего не отправляешь в GitHub (git push, pull request, gh запрещены). После твоего финального отчёта "
       "система сама отправит твою ветку, откроет pull request и опубликует отчёт в issue. Не переключайся на другие ветки. "
       "Коммитить можно через commit(message) или автоматически при финальном отчёте.\n" if GITHUB_PUSH else
       "- git push и pull request недоступны: изменения остаются локально. Система сама сохранит diff твоей ветки и "
       "опубликует итоговый отчёт с патчем комментарием в issue. Коммитить можно через commit(message) или "
       "автоматически при финальном отчёте.\n") +
    "- comment_issue — только чтобы задать людям вопрос или оставить пояснение по ходу работы; итоговый отчёт "
    "дублировать не нужно.\n"
    "- Если находишь ОТДЕЛЬНУЮ проблему вне своей задачи (баг, уязвимость, сломанный тест) — заведи под неё "
    "create_issue(title, body, priority) и продолжай свою задачу: не чини её молча и не раздувай свой diff. "
    "В body: что не так, где (файл/строка), как воспроизвести, как предлагаешь исправить. Дубликаты (в том "
    "числе закрытых issues) создавать нельзя, лимит — несколько за прогон.\n"
    + ("- run_in_codespace(command) — проверка в облачной среде GitHub Codespaces с твоими правками (они "
       "переносятся автоматически). Первый вызов занимает 1-3 минуты и тратит квоту владельца репозитория, "
       "поэтому сначала проверяй локально через run_command; Codespaces — только если локально не получается "
       "(нужно окружение из devcontainer, другая версия рантайма, база данных).\n" if (USE_CODESPACES and GITHUB_TOKEN) else "")
    +     f"- run_command выполняется в корне репозитория; лимит одной команды {CMD_MAX_SEC} с, всего {CMD_TOTAL_SEC} с на issue. "
    "Не запускай интерактивные программы, серверы и всё, что не завершается само. Если зависимостей нет — "
    "установи (pip install / npm install), но коротко.\n"
    "- Если после ЧЕСТНОГО изучения issue и кода ты убедился, что правка репозитория НЕ нужна (проблема вне "
    "кода, issue уже решён, просьба неосуществима) — вызови report_no_action_needed(reason) с конкретной "
    "причиной ВМЕСТО report_work. Это проверит независимый ревьюер; отговорка «лень» не пройдёт.\n"
    "- report_work(summary, final=True) вызывай ОДИН раз и только когда сделано ВСЁ, что просит issue. "
    "summary обязан содержать три раздела: «Сделано», «Не сделано / ограничения», «Проверка» (что запускал и "
    "что получилось; если не проверял — так и напиши). Отчёт сверяется с реальным diff: заявлять о том, чего "
    "в diff нет, бесполезно.\n"
    "- Пометка [шаг N/M] в конце результата показывает остаток бюджета раунда. Задача может занять несколько "
    "раундов — это нормально. Не отчитывайся раньше времени: если не закончил, заверши раунд коротким текстом "
    "(что сделано, что осталось)."
)

def task_context(sess):
    return (f"Репозиторий: {GITHUB_OWNER}/{GITHUB_REPO}. Базовая ветка: {sess['base']}. "
            f"Рабочая ветка: {sess['branch']} (уже выбрана).\n\n"
            f"ЗАДАЧА: реши issue #{sess['issue']}. Его текст:\n\n{sess['issue_text']}")

def repo_facts(sess):
    base = sess["base"]
    ahead = _ahead(base)
    changed = git("diff", "--name-only", base).split()
    dirty = bool(git("status", "--porcelain").strip())
    lines = [f"- коммитов в рабочей ветке: {ahead}",
             f"- изменённые файлы относительно {base}: {', '.join(changed[:15]) or 'нет'}",
             f"- есть незакоммиченные правки: {'да' if dirty else 'нет'}",
             f"- финальный отчёт: {'принят' if sess['final'] else 'не принят'}"]
    if sess["commands"]:
        last = sess["commands"][-3:]
        lines.append("- последние команды: " + "; ".join(f"`{c['cmd'][:60]}` → exit {c['exit']}" for c in last))
    return "\n".join(lines)

def format_log(entries, keep=30):
    rows = []
    for i, e in enumerate(entries[-keep:], 1):
        a = json.dumps(e["args"], ensure_ascii=False)[:100] if e["args"] else ""
        rows.append(f"{i}. {e['tool']}({a}) → {e['result'].splitlines()[0][:120] if e['result'] else ''}")
    return "\n".join(rows) or "(вызовов ещё не было)"

def issue_status(sess, base):
    changes = _has_changes(base)
    if sess["final"] and changes:
        return "solved"
    if sess["no_action"] and not changes:
        return "no_action"
    return "partial" if changes else "failed"

def run_round(system, first_msg, tools, budget):
    by_name = {t.__name__: t for t in tools}
    messages = [{"role": "system", "content": system}, {"role": "user", "content": first_msg}]
    last_text, fmt_errors, over = "", 0, 0
    for _ in range(STEPS_PER_ROUND + TEXT_EXTRA_TURNS):
        try:
            out = llm_stream(messages)
        except ContextExceeded:
            return last_text or "(контекст переполнен)", "context"
        text = clean_answer(out["content"])
        used = (out["usage"] or {}).get("prompt_tokens", 0)
        if used > 0.85 * N_CTX:
            return strip_calls(text) or last_text, "context"
        if not text:
            fmt_errors += 1
            print(f"[{_ts()}] пустой ответ модели ({fmt_errors}/3): вероятно, лимит токенов ушёл на размышления")
            if fmt_errors >= 3:
                return last_text, "format"
            messages.append({"role": "user", "content": "Ответ пуст. Отвечай короче и сразу вызови инструмент (блок @@CALL ... @@END)."})
            continue
        last_text = text
        messages.append({"role": "assistant", "content": text})
        name, raw, problem, n_calls = parse_text_call(text)
        if name is None:
            return text, "text"
        t = by_name.get(name)
        kwargs = None
        if problem is None and t is None:
            problem = f"неизвестный инструмент «{name}». Доступны: {', '.join(by_name)}"
        if problem is None:
            try:
                kwargs = coerce_args(t, name, raw)
            except ValueError as e:
                problem = f"{e}. Сигнатура: {tool_signature(t)}"
        if problem is not None:
            fmt_errors += 1
            print(f"[{_ts()}] ошибка формата вызова ({fmt_errors}/3): {problem}")
            if fmt_errors >= 3:
                return strip_calls(text), "format"
            messages.append({"role": "user", "content": f"ОШИБКА ФОРМАТА (инструмент НЕ вызван, шаг не потрачен): {problem}\nПовтори вызов в правильном формате."})
            continue
        fmt_errors = 0
        if budget.remaining == 0:
            over += 1
            if over >= 2:
                return strip_calls(text), "budget"
        result = t(**kwargs)
        note = "\n(В сообщении было несколько блоков @@CALL: выполнен только первый.)" if n_calls > 1 else ""
        messages.append({"role": "user", "content": f"РЕЗУЛЬТАТ {name}:\n{result}{note}\n\nСледующий вызов (блок @@CALL ... @@END) или, если раунд закончен, короткий текстовый итог без блока."})
    return strip_calls(last_text), "turns"

# ---------------- самопроверка (ориентир) ----------------
def judge_result(sess, patch, status):
    if not JUDGE or status not in ("solved", "no_action"):
        return None, ""
    if status == "solved":
        user = (f"ISSUE:\n{sess['issue_text'][:6000]}\n\nОТЧЁТ АГЕНТА:\n{(sess['final'] or '')[:3000]}\n\nDIFF:\n{patch[:20000]}\n\n"
                "Решает ли diff проблему из issue? Ответь двумя строками:\nВЕРДИКТ: ДА / НЕТ / ЧАСТИЧНО\nПОЯСНЕНИЕ: <1-2 предложения>")
    else:
        user = (f"ISSUE:\n{sess['issue_text'][:6000]}\n\nАГЕНТ РЕШИЛ НЕ МЕНЯТЬ КОД. ПРИЧИНА:\n{sess['no_action'][:3000]}\n\n"
                "Обоснована ли причина? Ответь двумя строками:\nВЕРДИКТ: ДА / НЕТ / ЧАСТИЧНО\nПОЯСНЕНИЕ: <1-2 предложения>")
    try:
        out = llm_stream([{"role": "system", "content": "Ты — строгий ревьюер кода. Оценивай по фактам из diff и текста issue, не по уверенности автора."},
                          {"role": "user", "content": user}], max_tokens=500, think=False, show=False)
        txt = clean_answer(out["content"])
    except Exception as e:
        return None, f"ревьюер недоступен: {e}"
    m = re.search(r"ВЕРДИКТ:\s*(ДА|НЕТ|ЧАСТИЧНО)", txt, re.I)
    note = re.sub(r"(?is)^.*?ПОЯСНЕНИЕ:\s*", "", txt).strip()[:600] if "ПОЯСНЕНИЕ" in txt.upper() else txt[:300]
    return (m.group(1).upper() if m else None), note

# ---------------- комментарий-отчёт в issue ----------------
def compose_comment(res, patch):
    push = res.get("push") or {}
    pushed = push.get("state") == "pushed"
    where = ((f"Ветка `{res['branch']}` отправлена в репозиторий" + (f", pull request: {push['pr_url']}" if push.get("pr_url") else "") + ".")
             if pushed else "Изменения не отправлены в репозиторий: патч приложен ниже.")
    head = {"solved": f"✅ **Решено.** {where}",
            "partial": f"⚠️ **Частично.** Правка есть, но агент не подтвердил завершение работы. {where}"}[res["status"]]
    L = [AGENT_MARKER, "### 🤖 Отчёт локальной модели (Qwen3.8-27B)", "", head, ""]
    if res.get("final_report"):
        L += ["**Отчёт агента:**", "", res["final_report"], ""]
    else:
        L += ["**Последние заметки агента (не проверенный факт):**", "", res.get("last_note") or "(нет)", ""]
    L += ["**Изменённые файлы:**", "```", res["diffstat"], "```", ""]
    if res.get("commands"):
        L += ["**Проверки:** " + "; ".join(f"`{c['cmd'][:60]}` → {c['exit']}" for c in res["commands"]), ""]
    if res.get("judge"):
        L += [f"**Самопроверка (та же модель, ориентир):** {res['judge']}. {res.get('judge_note', '')}", ""]
    if res.get("created_issues"):
        L += ["**Заведены новые issues:** " + ", ".join(f"#{c['number']}" if c.get("number") else f"«{c['title']}» (черновик)"
                                                      for c in res["created_issues"]), ""]
    cut = patch[:PATCH_IN_COMMENT_LIMIT]
    cut_note = f"\n[патч обрезан: показано {len(cut)} из {len(patch)} символов; полный патч — у владельца запуска]" if len(patch) > len(cut) else ""
    if push.get("state") == "error":
        L += [f"**Push/PR не выполнен:** {push.get('error')}", ""]
    if push.get("pr_error"):
        L += [f"**Pull request не открыт:** {push['pr_error']}", ""]
    if not pushed:
        L += ["<details><summary>Патч (применить: <code>git apply</code>)</summary>", "", "````diff", cut + cut_note, "````", "", "</details>"]
    return "\n".join(L)

def compose_pr_body(res):
    kw = "Closes" if res["status"] == "solved" else "Refs"      # «Closes» закроет issue при слиянии PR вами
    L = [f"{kw} #{res['number']}", "", "### 🤖 Pull request подготовлен локальной моделью (Qwen3.8-27B)", "",
         "Проверьте diff перед слиянием: самопроверка модели — ориентир, а не гарантия.", ""]
    if res["status"] != "solved":
        L += ["⚠️ **Работа не закончена:** агент не подтвердил завершение.", ""]
    if res.get("final_report"):
        L += ["**Отчёт агента:**", "", res["final_report"], ""]
    else:
        L += ["**Последние заметки агента (не проверенный факт):**", "", res.get("last_note") or "(нет)", ""]
    L += ["**Изменённые файлы:**", "```", res["diffstat"], "```", ""]
    if res.get("commands"):
        L += ["**Проверки:** " + "; ".join(f"`{c['cmd'][:60]}` → {c['exit']}" for c in res["commands"]), ""]
    if res.get("judge"):
        L += [f"**Самопроверка (та же модель, ориентир):** {res['judge']}. {res.get('judge_note', '')}", ""]
    if res.get("created_issues"):
        L += ["**Заведены новые issues:** " + ", ".join(f"#{c['number']}" if c.get("number") else f"«{c['title']}» (черновик)"
                                                      for c in res["created_issues"]), ""]
    return "\n".join(L)

def publish_branch(res, patch):
    """Отправляет рабочую ветку в GitHub и открывает pull request. -> {"state": skipped | pushed | error, ...}"""
    if not GITHUB_PUSH or res["status"] not in PUSH_STATUSES or not patch.strip():
        return {"state": "skipped"}
    branch, base = res["branch"], res["base"]
    if not branch.startswith(BRANCH_PREFIX + "/") or branch == base:
        return {"state": "error", "error": f"push в ветку «{branch}» запрещён: отправляются только ветки {BRANCH_PREFIX}/…"}
    if not GITHUB_TOKEN:
        return {"state": "error", "error": "нет GITHUB_TOKEN"}
    if not GH_PUSH["ok"]:
        return {"state": "error", "error": GH_PUSH["reason"]}
    try:
        if git("status", "--porcelain").strip():
            git("add", "-A")
            git("commit", "-m", f"agent: issue #{res['number']}" + ("" if res["status"] == "solved" else " (WIP)"))
        git_net("push", "origin", f"refs/heads/{branch}:refs/heads/{branch}")      # без --force: чужие правки не перезапишутся
    except RuntimeError as e:
        return {"state": "error", "error": _push_failed("git push", str(e))}
    out = {"state": "pushed", "branch": branch}
    if OPEN_PR:
        title = ("" if res["status"] == "solved" else "[WIP] ") + f"#{res['number']}: {res['title']}"
        out["pr_url"], out["pr_error"] = gh_open_pr(branch, base, title[:240], compose_pr_body(res), PR_DRAFT)
    return out

def publish_comment(res, patch):
    if res["status"] not in COMMENT_STATUSES or not patch.strip():
        return {"state": "skipped"}
    body = compose_comment(res, patch)
    _draft(f"report-issue-{res['number']}.md", body)
    if not GITHUB_WRITE:
        return {"state": "draft"}
    url, err = gh_upsert_report(res["number"], body)
    return {"state": "posted", "url": url} if url else {"state": "error", "error": err}

# ---------------- один issue ----------------
STOP_TEXT = {"context": "переполнился контекст модели", "format": "модель не смогла соблюсти формат вызовов",
             "budget": "исчерпан бюджет шагов раунда", "turns": "исчерпан лимит ходов раунда", "text": "модель завершила раунд текстом"}

def solve_issue(it, base):
    n, t0 = it["number"], time.time()
    branch = prepare_branch(n, base, it["title"])
    sess = new_sess(n, base, branch, issue_text(it))
    call_log, trace, carry, why, rounds = [], [], "", "", 0
    for round_num in range(1, MAX_ROUNDS + 1):
        rounds = round_num
        budget = StepBudget(STEPS_PER_ROUND, call_log)
        tools = make_tools(sess, budget)
        system = SYSTEM_PROMPT + "\n\n" + text_protocol_prompt() + build_tool_spec(tools)
        if round_num == 1:
            msg = task_context(sess) + f"\n\nУ тебя до {STEPS_PER_ROUND} вызовов инструментов на раунд и до {MAX_ROUNDS} раундов."
        else:
            msg = (task_context(sess) + f"\n\nРаунд {round_num}/{MAX_ROUNDS}. Новый бюджет: {STEPS_PER_ROUND} вызовов.\n"
                   f"Последние вызовы:\n{format_log(call_log)}\n"
                   + (f"\nТвои заметки в конце прошлого раунда (твои слова, не проверенный факт):\n{carry}\n" if carry else "")
                   + f"\nПРОВЕРЕНО СИСТЕМОЙ (приоритетнее твоих заметок):\n{repo_facts(sess)}\n"
                   "\nНе повторяй уже сделанное. Не считай задачу выполненной, пока финальный отчёт не принят.")
        print(f"\n===== issue #{n} · раунд {round_num}/{MAX_ROUNDS} =====", flush=True)
        text, why = run_round(system, msg, tools, budget)
        carry = (text or "").strip()[-2000:]
        trace.append({"round": round_num, "end_reason": why, "end_text": carry})
        status = issue_status(sess, base)
        if status in ("solved", "no_action"):
            break
    git("add", "-A", "-N")
    patch = git("diff", "--binary", base)
    status = issue_status(sess, base)
    if patch.strip():
        pathlib.Path(f"{OUT_DIR}/patches/issue-{n}.patch").write_text(patch, encoding="utf-8")
    verdict, note = judge_result(sess, patch, status)
    with open(f"{OUT_DIR}/traces/issue-{n}.jsonl", "w", encoding="utf-8") as f:
        for e in call_log:
            f.write(json.dumps({"call": e}, ensure_ascii=False) + "\n")
        for e in trace:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    res = {"number": n, "title": it["title"], "url": it["url"], "labels": it["labels"], "status": status,
            "rounds": rounds, "branch": branch, "base": base, "commits": _ahead(base),
            "files": git("diff", "--name-only", base).split(), "diffstat": git("diff", "--stat", base).strip(),
            "final_report": sess["final"], "no_action_reason": sess["no_action"], "last_note": carry,
            "stop_reason": (STOP_TEXT.get(why, why) if status in ("solved", "no_action") else
                            f"после {rounds} раунд(ов) финальный отчёт не принят; последний раунд: {STOP_TEXT.get(why, why)}"), "judge": verdict, "judge_note": note,
            "commands": sess["commands"][-6:], "patch_file": f"patches/issue-{n}.patch" if patch.strip() else None,
            "elapsed_sec": round(time.time() - t0), "error": None,
            "created_issues": sess["created"], "cs_runs": sess["cs_total"]}
    res["push"] = publish_branch(res, patch)           # сначала ветка и PR: ссылка на PR попадёт в комментарий к issue
    res["comment"] = publish_comment(res, patch)
    return res

# ---------------- результаты и отчёт ----------------
RESULTS_PATH = f"{OUT_DIR}/results.json"
STATUS_LABEL = {"solved": "✅ решено", "no_action": "➖ правка не нужна", "partial": "⚠️ частично",
                "failed": "❌ не решено", "error": "🛑 ошибка", "skipped": "⏭ не запускалось"}

def load_results():
    return json.load(open(RESULTS_PATH, encoding="utf-8")) if os.path.exists(RESULTS_PATH) else {}

def save_results(results):
    json.dump(results, open(RESULTS_PATH, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

def build_report(issues, results):
    total = {k: 0 for k in STATUS_LABEL}
    for it in issues:
        total[results.get(str(it["number"]), {}).get("status", "skipped")] += 1
    L = [f"# Отчёт агента: {GITHUB_OWNER}/{GITHUB_REPO}", "",
         f"Дата: {time.strftime('%Y-%m-%d %H:%M')} · issues в очереди: {len(issues)} · "
         + " · ".join(f"{STATUS_LABEL[k]}: {v}" for k, v in total.items() if v), "",
         ("Ветки отправляются в GitHub (`GITHUB_PUSH`), по ним открываются pull request; патчи тоже сохранены (`patches/issue-N.patch`)."
          if GITHUB_PUSH else
          "Агент ничего не отправлял в GitHub: правки лежат в патчах (`patches/issue-N.patch`). "
          "Применить: `git checkout -b agent/issue-N && git apply patches/issue-N.patch`."),
         "Статус «решено» значит: есть правка и принят финальный отчёт агента. "
         "«Самопроверка» — мнение той же модели, это ориентир, а не гарантия.", "",
         "| Issue | Статус | Раундов | Коммитов | Файлов | Самопроверка |", "|---|---|---|---|---|---|"]
    for it in issues:
        r = results.get(str(it["number"]))
        if r:
            L.append(f"| #{it['number']} {it['title'][:60]} | {STATUS_LABEL[r['status']]} | {r.get('rounds', '-')} | "
                     f"{r.get('commits', '-')} | {len(r.get('files', []))} | {r.get('judge') or '-'} |")
        else:
            L.append(f"| #{it['number']} {it['title'][:60]} | {STATUS_LABEL['skipped']} | - | - | - | - |")
    if not GH_WRITE["ok"]:
        L += ["", f"> ⚠️ **Запись в GitHub отключилась:** {GH_WRITE['reason']}"]
    if not GH_PUSH["ok"]:
        L += ["", f"> ⚠️ **Push и PR отключились:** {GH_PUSH['reason']}"]
    if CREATED_ISSUES:
        L += ["", "## Issues, заведённые агентом", ""]
        for c in CREATED_ISSUES:
            L.append(f"- {('#' + str(c['number']) + ' ' + c['url']) if c.get('number') else '(черновик)'} · priority:{c['priority']} · "
                     f"«{c['title']}» · найдено при работе над #{c['found_in']}")
    for it in issues:
        r = results.get(str(it["number"]))
        L += ["", "---", "", f"## #{it['number']} {it['title']}", f"{it['url']}", ""]
        if not r:
            L.append("Не запускалось (закончился лимит времени или issue пропущен).")
            continue
        L.append(f"**Статус:** {STATUS_LABEL[r['status']]} · раундов: {r['rounds']} · время: {r['elapsed_sec']} с")
        if r.get("error"):
            L += ["", f"**Ошибка:** {r['error']}"]
        if r["status"] == "solved":
            L += ["", "**Что сделано (отчёт агента):**", "", r["final_report"] or ""]
        elif r["status"] == "no_action":
            L += ["", "**Агент решил, что правка не нужна. Причина:**", "", r["no_action_reason"] or ""]
        else:
            L += ["", f"**Почему не завершено:** {r.get('stop_reason', '-')}.",
                  "", "**Последние заметки агента (его слова, не проверенный факт):**", "", r.get("last_note") or "(нет)"]
        if r.get("files"):
            L += ["", "**Изменённые файлы:**", "", "```", r["diffstat"], "```"]
            if r.get("patch_file"):
                L.append(f"Патч: `{r['patch_file']}`")
        if r.get("commands"):
            L += ["", "**Последние команды:** " + "; ".join(f"`{c['cmd'][:60]}` → {c['exit']}" for c in r["commands"])]
        if r.get("judge") or r.get("judge_note"):
            L += ["", f"**Самопроверка:** {r.get('judge') or '—'}. {r.get('judge_note', '')}"]
        cm = r.get("comment") or {}
        if cm.get("state") == "posted":
            L += ["", f"**Комментарий в issue:** опубликован ({cm.get('url')})"]
        elif cm.get("state") == "draft":
            L += ["", f"**Комментарий в issue:** черновик в `drafts/report-issue-{r['number']}.md` (GITHUB_WRITE=False)"]
        elif cm.get("state") == "error":
            L += ["", f"**Комментарий в issue:** НЕ опубликован: {cm.get('error')}. Черновик: `drafts/report-issue-{r['number']}.md`"]
        pu = r.get("push") or {}
        if pu.get("state") == "pushed":
            L += ["", f"**Ветка:** `{r['branch']}` отправлена в GitHub" + (f" · **Pull request:** {pu['pr_url']}" if pu.get("pr_url")
                  else (f" · PR не открыт: {pu.get('pr_error')}" if pu.get("pr_error") else ""))]
        elif pu.get("state") == "error":
            L += ["", f"**Push/PR не выполнен:** {pu.get('error')}"]
        if r.get("created_issues"):
            L += ["", "**Новые issues, заведённые агентом:** " + ", ".join(
                f"#{c['number']} (priority:{c['priority']})" if c.get("number") else f"«{c['title']}» (черновик)" for c in r["created_issues"])]
        if r.get("cs_runs"):
            L += ["", f"**Запусков в Codespaces:** {r['cs_runs']}"]
    return "\n".join(L)

def write_report(issues, results):
    rep = build_report(issues, results)
    pathlib.Path(f"{OUT_DIR}/agent_report.md").write_text(rep, encoding="utf-8")
    return rep

def run_agent():
    t_start = time.time()
    base = clone_repo()
    issues = fetch_issues(ISSUE_STATE)
    if ONLY_ISSUES:
        issues = [i for i in issues if i["number"] in ONLY_ISSUES]
    if ISSUE_LIMIT:
        issues = issues[:ISSUE_LIMIT]
    print(f"База: {base}. Issues в очереди ({len(issues)}): " + ", ".join(f"#{i['number']}" for i in issues))
    results = load_results()
    RUN_CREATED["n"] = 0
    try:
        load_known_issues()
    except Exception as e:
        print(f"Не удалось загрузить список всех issues для защиты от дубликатов: {e}")
    try:
        _run_queue(issues, results, base, t_start)
    finally:
        stop_codespace_if_any()
    rep = write_report(issues, results)
    print("\n" + "=" * 70 + "\n" + rep)
    return results

def _run_queue(issues, results, base, t_start):
    for it in issues:
        key = str(it["number"])
        if key in results and not RERUN_DONE and results[key]["status"] != "error":
            print(f"#{key}: уже обработан ({results[key]['status']}), пропускаю (RERUN_DONE=False)")
            continue
        if MAX_TOTAL_SEC and time.time() - t_start > MAX_TOTAL_SEC:
            print("Достигнут MAX_TOTAL_SEC: новые issues не запускаются.")
            break
        print(f"\n######## issue #{key}: {it['title']} ########", flush=True)
        try:
            res = solve_issue(it, base)
        except Exception as e:
            res = {"number": it["number"], "title": it["title"], "url": it["url"], "labels": it["labels"],
                   "status": "error", "rounds": 0, "commits": 0, "files": [], "elapsed_sec": 0,
                   "error": f"{type(e).__name__}: {str(e)[:300]}"}
        results[key] = res
        save_results(results)
        write_report(issues, results)
        print(f"#{key}: {STATUS_LABEL[res['status']]}")

# ===== cell d0b92a4b =====
if RUN_SELFTEST:
    # Проверка инструментов и протокола БЕЗ модели и без GitHub (на временном git-репозитории)
    _old_repo_dir = REPO_DIR
    REPO_DIR = "/tmp/agent_selftest_repo"
    try:
        shutil.rmtree(REPO_DIR, ignore_errors=True); os.makedirs(REPO_DIR)
        subprocess.run("git init -q -b main . 2>/dev/null || (git init -q . && git checkout -q -b main)", shell=True, cwd=REPO_DIR, check=True)
        pathlib.Path(f"{REPO_DIR}/calc.py").write_text("def add(a, b):\n    return a - b\n")
        pathlib.Path(f"{REPO_DIR}/test_calc.py").write_text("from calc import add\nassert add(2, 3) == 5, 'add сломан'\nprint('tests ok')\n")
        git("add", "-A"); git("commit", "-q", "-m", "init")
        sess = new_sess(1, "main", "agent/issue-1", "add() вычитает")
        git("checkout", "-q", "-b", "agent/issue-1")
        T = {t.__name__: t for t in make_tools(sess, StepBudget(60))}
        assert "calc.py" in T["list_files"]()
        assert "return a - b" in T["read_file"]("calc.py")
        assert "calc.py:2" in T["search_code"]("a - b")
        assert "вне репозитория" in T["read_file"]("../etc/passwd") or "ERROR" in T["read_file"]("../etc/passwd")
        assert "заблокирована" in T["run_command"]("git push origin main")
        assert "exit=1" in T["run_command"]("python test_calc.py")
        assert "не найден" in T["patch_file"]("calc.py", "a * b", "a + b")
        assert T["patch_file"]("calc.py", "return a - b", "return a + b").startswith("OK")
        assert "tests ok" in T["run_command"]("python test_calc.py")
        assert "ВНИМАНИЕ" in T["write_file"]("broken.py", "def f(:\n")
        T["delete_file"]("broken.py")
        assert "return a + b" in T["git_diff"]()
        assert T["report_no_action_needed"]("не нужно").startswith("ERROR")      # есть изменения: отказ
        assert T["report_work"]("промежуточно").startswith("OK")
        assert T["report_work"]("Сделано: ...", True).startswith("OK")
        assert _ahead("main") == 1 and issue_status(sess, "main") == "solved"
        name, args, err, n = parse_text_call("@@CALL read_file\n@@ARG path\nx.py\n@@END")
        assert (name, args, err) == ("read_file", {"path": "x.py"}, None)
        assert parse_text_call("@@CALL write_file\n@@ARG path\na")[2] is not None     # блок без @@END не выполняется
        # запись в GitHub: в самотесте работаем только черновиками (без сети)
        _w, _k = GITHUB_WRITE, dict(KNOWN_ISSUES)
        GITHUB_WRITE = False; KNOWN_ISSUES.clear(); KNOWN_ISSUES[7] = "Падает логин при пустом пароле"
        assert "похожий issue уже есть: #7" in T["create_issue"]("Логин падает при пустом пароле", "x" * 60)
        assert "слишком" in T["create_issue"]("коротко", "x") or "ERROR" in T["create_issue"]("коротко", "x")
        assert T["create_issue"]("Нет проверки деления на ноль", "В calc.py нет функции div; деление на ноль не обработано. Воспроизвести: div(1, 0).", "p1").startswith("OK")
        assert "похожий issue уже есть" in T["create_issue"]("Нет проверки деления на ноль", "y" * 60)       # второй раз - дубликат
        assert T["comment_issue"]("Уточните, нужна ли поддержка float? Сейчас add принимает любые типы.").startswith("OK")
        assert "ERROR" in T["create_issue"]("Ошибка в стиле кода", "z" * 60, "p9")
        CREATED_ISSUES.clear(); GITHUB_WRITE = _w; KNOWN_ISSUES.clear(); KNOWN_ISSUES.update(_k)
        assert "base64 -d" in _codespace_script("pytest -q", "QUJD", 60) and "timeout -k 5 60" in _codespace_script("pytest -q", "QUJD", 60)
        # --- ветка, push и PR: локальный bare-репозиторий вместо GitHub, GitHub API подменён ---
        assert branch_name(12, "Добавь файл в репозиторий") == "agent/issue-12-dobav-fayl-v-repozitoriy"
        assert branch_name(5, "!!!") == "agent/issue-5" and len(branch_name(1, "а" * 200)) <= len("agent/issue-1-") + 40
        remote_dir = "/tmp/agent_selftest_remote.git"
        shutil.rmtree(remote_dir, ignore_errors=True)
        subprocess.run(["git", "init", "-q", "--bare", remote_dir], check=True)
        git("remote", "add", "origin", remote_dir); git("push", "-q", "origin", "main")
        _p = (GITHUB_PUSH, OPEN_PR)
        GITHUB_PUSH, OPEN_PR = True, False
        res_t = {"number": 1, "title": "add() вычитает", "status": "solved", "branch": "agent/issue-1", "base": "main"}
        assert publish_branch(res_t, "diff --git")["state"] == "pushed"
        assert "refs/heads/agent/issue-1" in git_net("ls-remote", "--heads", "origin")
        assert publish_branch({**res_t, "branch": "main"}, "diff")["state"] == "error"           # базовую ветку пушить нельзя
        assert publish_branch({**res_t, "status": "failed"}, "diff")["state"] == "skipped"
        git("checkout", "-q", "main")
        assert prepare_branch(1, "main", "Совсем другое название") == "agent/issue-1" and _ahead("main") == 1   # ветка уже есть: продолжаем с неё
        assert prepare_branch(2, "main", "Новая задача") == "agent/issue-2-novaya-zadacha" and _ahead("main") == 0
        GITHUB_PUSH, OPEN_PR = _p
        _orig_req, _calls = _gh_request, []
        class _R:
            def __init__(self, code, data=None, text=""): self.status_code, self._d, self.text = code, data, text
            def json(self): return self._d
        def _fake_draft_off(method, path, **kw):
            _calls.append((method, path, (kw.get("json") or {}).get("draft")))
            if method == "POST" and kw["json"].get("draft"):
                return _R(422, text="Draft pull requests are not supported in this repository")
            return _R(201, {"html_url": "https://github.com/o/r/pull/9"})
        def _fake_exists(method, path, **kw):
            if method == "POST": return _R(422, text="A pull request already exists for o:agent/issue-1")
            if method == "GET": return _R(200, [{"number": 4, "html_url": "https://github.com/o/r/pull/4"}])
            return _R(200, {})
        try:
            _gh_request = _fake_draft_off
            url, err = gh_open_pr("agent/issue-1", "main", "t", "b", True)
            assert url.endswith("/pull/9") and err is None and _calls[-1][2] is False          # черновики недоступны: открылся обычный PR
            _gh_request = _fake_exists
            url, err = gh_open_pr("agent/issue-1", "main", "t", "b", True)
            assert url.endswith("/pull/4") and err is None                                       # PR уже был: обновили описание
        finally:
            _gh_request = _orig_req
        assert "Closes #1" in compose_pr_body({**res_t, "diffstat": "x | 1 +", "judge": "ДА", "judge_note": ""})
        print("✅ Инструменты и протокол работают.")
    finally:
        REPO_DIR = _old_repo_dir


# ======================= ЗАПУСК =======================
def init_model():
    global FREEDEEPSEEK_MODEL
    headers = {}
    if FREEDEEPSEEK_API_KEY:
        headers["Authorization"] = f"Bearer {FREEDEEPSEEK_API_KEY}"
    r = requests.get(FREEDEEPSEEK_URL + "/models", headers=headers, timeout=10)
    r.raise_for_status()
    models = r.json().get("data", [])
    if not models:
        raise RuntimeError("FreeDeepseekAPI не вернул ни одной модели")
    FREEDEEPSEEK_MODEL = FREEDEEPSEEK_MODEL or models[0]["id"]
    test = requests.post(
        FREEDEEPSEEK_URL + "/chat/completions",
        headers={**headers, "Content-Type": "application/json"},
        json={"model": FREEDEEPSEEK_MODEL, "messages": [{"role": "user", "content": "ping"}],
              "max_tokens": 5, "stream": False},
        timeout=60,
    )
    test.raise_for_status()
    print(f"✅ FreeDeepseekAPI доступен. Модель: {FREEDEEPSEEK_MODEL} | N_CTX(manual): {FREEDEEPSEEK_N_CTX}")


if __name__ == "__main__":
    if RUN_SELFTEST:
        # самотест уже выполнен при импорте (см. ячейку d0b92a4b под if RUN_SELFTEST)
        pass
    init_model()
    results = run_agent()
    print("\nГотово. Результаты в", OUT_DIR)
