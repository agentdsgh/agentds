#!/usr/bin/env python3
"""Deterministic GitHub helpers for the `github-tasks` OpenCode skill.

No LLM calls: every command is a plain API/git operation and prints JSON.
The agent (OpenCode) is the model; this script is the hands.

Repo resolution order: --repo  ->  $GITHUB_REPO / $GH_REPO  ->  git remote origin
Token resolution order: $GITHUB_TOKEN / $GH_TOKEN  ->  `gh auth token`

Examples:
    python gh_tasks.py doctor
    python gh_tasks.py list --limit 20
    python gh_tasks.py show 42
    python gh_tasks.py branch 42
    python gh_tasks.py pr --issue 42 --body-file pr.md
    python gh_tasks.py report 42 --body-file report.md
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

API = "https://api.github.com"
AGENT_LABEL = "opencode-agent"
REPORT_MARKER = "<!-- OPENCODE_REPORT -->"
NOTE_MARKER = "<!-- OPENCODE_NOTE -->"
PRIORITY_ORDER = ["priority:p0", "priority:p1", "priority:p2"]
UNTRIAGED_RANK = 1.5
DUP_THRESHOLD = 0.6


class GhError(RuntimeError):
    def __init__(self, message: str, status: int | None = None, headers=None):
        super().__init__(message)
        self.status = status
        self.headers = headers or {}


# ---------------------------------------------------------------- env / auth

# Per-repo credentials live in this file; it makes multiple repos/accounts work
# without restarting the OpenCode background service. Schema:
#   {
#     "agentdsgh/agentds": {"token": "ghp_...", "api": "https://api.github.com"},
#     "other/*":           {"token": "github_pat_..."},
#     "default":           {"token": "ghp_..."}
#   }
# A plain string value is also accepted: {"owner/repo": "ghp_..."}.
CREDS_PATH = "~/.config/opencode/github-tokens.json"

CTX = {"token": None, "api": API, "source": None, "explicit": False}


def creds_path() -> str:
    return os.path.expanduser(os.environ.get("OPENCODE_GITHUB_TOKENS") or CREDS_PATH)


def load_creds() -> dict:
    path = creds_path()
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError) as exc:
        raise GhError(f"cannot read {path}: {exc}")
    if not isinstance(data, dict):
        raise GhError(f"{path} must contain a JSON object")
    return data


def save_creds(data: dict) -> str:
    path = creds_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    return path


def lookup_creds(repo: str):
    """Return (token, api_base, matched_key) for repo, or (None, None, None)."""
    entries = load_creds()
    owner = repo.split("/")[0]
    for key in (repo, f"{owner}/*", "default", "*"):
        if key not in entries:
            continue
        value = entries[key]
        if isinstance(value, str):
            token, api_base = value, API
        elif isinstance(value, dict):
            token, api_base = value.get("token"), value.get("api") or API
        else:
            raise GhError(f"bad entry {key!r} in {creds_path()}")
        if token:
            return token.strip(), api_base.rstrip("/"), key
    return None, None, None


def token_from_gh() -> str | None:
    try:
        out = subprocess.run(
            ["gh", "auth", "token"], capture_output=True, text=True, timeout=10
        )
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return None


def resolve_credentials(repo: str) -> None:
    """Fill CTX for repo unless an explicit --token/--token-env/--token-file was given."""
    if CTX["explicit"]:
        return
    token, api_base, key = lookup_creds(repo)
    if token:
        CTX.update(token=token, api=api_base, source=f"creds:{key}")
        return
    for name in ("GITHUB_TOKEN", "GH_TOKEN"):
        value = os.environ.get(name)
        if value:
            CTX.update(token=value.strip(), api=API, source=f"env:{name}")
            return
    gh_token = token_from_gh()
    if gh_token:
        CTX.update(token=gh_token, api=API, source="gh-cli")
        return
    CTX.update(token=None, api=API, source=None)


def _repo_from_git() -> str | None:
    try:
        out = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    url = out.stdout.strip()
    m = re.search(r"github\.com[:/]+([^/]+)/([^/\s]+?)(?:\.git)?$", url)
    return f"{m.group(1)}/{m.group(2)}" if m else None


def resolve_repo(explicit: str | None) -> str:
    repo = explicit or os.environ.get("GITHUB_REPO") or os.environ.get("GH_REPO")
    if not repo:
        repo = _repo_from_git()
    if not repo:
        raise GhError(
            "Repository is not set. Pass --repo owner/name or set GITHUB_REPO / git origin."
        )
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        raise GhError(f"Bad repo value: {repo!r}; expected owner/name")
    resolve_credentials(repo)
    return repo


# ------------------------------------------------------------------------ api

def api(method: str, path: str, data=None, params=None, retries: int = 3):
    url = CTX["api"] + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    body = json.dumps(data).encode("utf-8") if data is not None else None
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "opencode-github-tasks/1.0",
    }
    tok = CTX["token"]
    if tok:
        headers["Authorization"] = f"Bearer {tok}"

    last_error = None
    for attempt in range(retries):
        request = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                raw = response.read()
                payload = json.loads(raw) if raw else None
                return payload, dict(response.headers)
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", "replace")
            rate_limited = exc.code == 403 and "rate limit" in raw.lower()
            if exc.code >= 500 or rate_limited:
                last_error = GhError(f"GitHub {exc.code}: {raw[:300]}", exc.code, dict(exc.headers))
                delay = min(30, 2 ** attempt * 2)
                retry_after = exc.headers.get("retry-after")
                if retry_after and retry_after.isdigit():
                    delay = min(60, int(retry_after))
                time.sleep(delay)
                continue
            raise GhError(
                f"GitHub {exc.code}: {raw[:400]}\n"
                f"Hint: token needs Issues: Read/Write, Contents: Read/Write, Pull requests: Read/Write.",
                exc.code, dict(exc.headers),
            )
        except urllib.error.URLError as exc:
            last_error = GhError(f"network error: {exc.reason}")
            if attempt + 1 < retries:
                time.sleep(2 ** attempt)
                continue
            raise last_error
    raise last_error or GhError("request failed")


def paginate(path: str, params=None, max_pages: int = 10) -> list:
    query = dict(params or {})
    query.setdefault("per_page", 100)
    items = []
    for page in range(1, max_pages + 1):
        query["page"] = page
        batch, _ = api("GET", path, params=query)
        batch = batch or []
        items.extend(batch)
        if len(batch) < query["per_page"]:
            break
    return items


# ------------------------------------------------------------- issue helpers

def rank(issue: dict) -> float:
    names = {label["name"] for label in issue.get("labels", [])}
    for index, label in enumerate(PRIORITY_ORDER):
        if label in names:
            return index
    return UNTRIAGED_RANK


def slim(issue: dict) -> dict:
    return {
        "number": issue["number"],
        "title": issue["title"],
        "state": issue["state"],
        "labels": [label["name"] for label in issue.get("labels", [])],
        "comments": issue.get("comments", 0),
        "url": issue["html_url"],
        "updated_at": issue.get("updated_at"),
    }


def title_tokens(value: str) -> set[str]:
    return set(re.findall(r"\w{3,}", (value or "").lower()))


def find_duplicate(repo: str, title: str):
    wanted = title_tokens(title)
    if not wanted:
        return None
    for issue in paginate(f"/repos/{repo}/issues", {"state": "all"}):
        if "pull_request" in issue:
            continue
        other = title_tokens(issue.get("title", ""))
        if other and len(wanted & other) / len(wanted | other) >= DUP_THRESHOLD:
            return {"number": issue["number"], "title": issue["title"], "url": issue["html_url"]}
    return None


def get_issue(repo: str, number: int) -> dict:
    issue, _ = api("GET", f"/repos/{repo}/issues/{number}")
    return issue


def default_branch(repo: str) -> str:
    info, _ = api("GET", f"/repos/{repo}")
    return info.get("default_branch") or "main"


# ---------------------------------------------------------------------- git

def git(*args: str, cwd: str | None = None, check: bool = True, env=None) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, timeout=300,
        env={**os.environ, **(env or {})},
    )
    if check and proc.returncode != 0:
        raise GhError(f"git {' '.join(args)} failed: {(proc.stderr or proc.stdout).strip()[:500]}")
    return (proc.stdout or "").strip()


def auth_env() -> dict:
    """Git config overrides that authenticate HTTPS remotes with the resolved token.

    Passed through GIT_CONFIG_* env vars, so the token never appears in argv.
    """
    tok = CTX["token"]
    if not tok:
        return {}
    basic = base64.b64encode(f"x-access-token:{tok}".encode()).decode()
    return {
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "http.extraheader",
        "GIT_CONFIG_VALUE_0": f"Authorization: Basic {basic}",
    }


def ensure_credentials_from_git() -> None:
    if CTX["token"] or CTX["explicit"]:
        return
    repo = _repo_from_git()
    if repo:
        resolve_credentials(repo)


def ensure_identity() -> None:
    """Set a local repo identity when the clone has none, keeping the user's config intact."""
    if git("config", "user.email", check=False):
        return
    ensure_credentials_from_git()
    login = None
    try:
        me, _ = api("GET", "/user")
        login = me.get("login")
    except GhError:
        pass
    name = login or "opencode-agent"
    email = f"{login}@users.noreply.github.com" if login else "opencode-agent@users.noreply.github.com"
    git("config", "user.name", name)
    git("config", "user.email", email)


def slugify(value: str, limit: int = 40) -> str:
    value = re.sub(r"[^A-Za-z0-9]+", "-", value).strip("-").lower()
    return value[:limit].strip("-") or "task"


def branch_for(number: int, title: str = "", prefix: str = "agent") -> str:
    return f"{prefix}/issue-{number}" + (f"-{slugify(title)}" if title else "")


# ------------------------------------------------------------------ commands

def cmd_doctor(args) -> dict:
    result = {"git_repo": None, "repo": None, "default_branch": None,
              "scopes": None, "gh_cli": False, "errors": []}
    try:
        result["git_repo"] = git("rev-parse", "--show-toplevel", check=False) or None
    except GhError:
        pass
    try:
        subprocess.run(["gh", "--version"], capture_output=True, timeout=10, check=True)
        result["gh_cli"] = True
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        repo = resolve_repo(args.repo)
        result["repo"] = repo
        info, headers = api("GET", f"/repos/{repo}")
        result["default_branch"] = info.get("default_branch")
        result["private"] = info.get("private")
        permissions = info.get("permissions") or {}
        result["permissions"] = permissions
        result["scopes"] = headers.get("X-Oauth-Scopes")
        result["rate_limit_remaining"] = headers.get("X-RateLimit-Remaining")
    except GhError as exc:
        result["errors"].append(str(exc))
    result["token"] = bool(CTX["token"])
    result["token_source"] = CTX["source"]
    result["api"] = CTX["api"]
    result["ok"] = bool(result["repo"]) and not result["errors"]
    return result


def cmd_list(args) -> dict:
    repo = resolve_repo(args.repo)
    issues = [
        issue for issue in paginate(f"/repos/{repo}/issues", {"state": args.state})
        if "pull_request" not in issue
    ]
    if args.label:
        issues = [
            issue for issue in issues
            if args.label in {label["name"] for label in issue.get("labels", [])}
        ]
    issues.sort(key=lambda issue: (rank(issue), issue["number"]))
    if args.limit:
        issues = issues[: args.limit]
    return {"repo": repo, "state": args.state, "count": len(issues),
            "issues": [slim(issue) for issue in issues]}


def cmd_show(args) -> dict:
    repo = resolve_repo(args.repo)
    issue = get_issue(repo, args.number)
    comments = paginate(f"/repos/{repo}/issues/{args.number}/comments")
    return {
        "repo": repo,
        "number": issue["number"],
        "title": issue["title"],
        "state": issue["state"],
        "labels": [label["name"] for label in issue.get("labels", [])],
        "url": issue["html_url"],
        "body": issue.get("body") or "",
        "comments": [
            {"author": c["user"]["login"], "created_at": c.get("created_at"),
             "body": (c.get("body") or "")}
            for c in comments[: args.max_comments]
        ],
    }


def cmd_create(args) -> dict:
    repo = resolve_repo(args.repo)
    title = args.title.strip()
    body = read_body(args)
    if len(title) < 8:
        raise GhError("title is too short (min 8 chars)")
    if len(body.strip()) < 40:
        raise GhError("body is too short (min 40 chars): describe what is wrong, where, how to reproduce")
    duplicate = find_duplicate(repo, title)
    if duplicate:
        return {"created": False, "duplicate": duplicate}
    labels = [f"priority:{args.priority}", AGENT_LABEL] + list(args.label or [])
    payload = {"title": title, "body": body, "labels": sorted(set(labels))}
    if args.dry_run:
        return {"created": False, "dry_run": True, "payload": payload}
    issue, _ = api("POST", f"/repos/{repo}/issues", data=payload)
    return {"created": True, "number": issue["number"], "url": issue["html_url"]}


def cmd_comment(args) -> dict:
    repo = resolve_repo(args.repo)
    body = read_body(args).strip()
    if not body:
        raise GhError("comment body is empty")
    text = f"{body[:6000]}\n\n{NOTE_MARKER}"
    if args.dry_run:
        return {"posted": False, "dry_run": True, "body": text}
    comment, _ = api("POST", f"/repos/{repo}/issues/{args.number}/comments", data={"body": text})
    return {"posted": True, "url": comment["html_url"]}


def cmd_report(args) -> dict:
    repo = resolve_repo(args.repo)
    body = read_body(args).strip()
    if not body:
        raise GhError("report body is empty")
    text = f"{REPORT_MARKER}\n{body[:60000]}\n\n— 🤖 opencode"
    if args.dry_run:
        return {"posted": False, "dry_run": True, "body": text}
    existing = next(
        (c for c in paginate(f"/repos/{repo}/issues/{args.number}/comments")
         if REPORT_MARKER in (c.get("body") or "")),
        None,
    )
    if existing:
        comment, _ = api("PATCH", f"/repos/{repo}/issues/comments/{existing['id']}",
                         data={"body": text})
        return {"posted": True, "updated": True, "url": comment["html_url"]}
    comment, _ = api("POST", f"/repos/{repo}/issues/{args.number}/comments", data={"body": text})
    return {"posted": True, "updated": False, "url": comment["html_url"]}


def cmd_close(args) -> dict:
    repo = resolve_repo(args.repo)
    if args.comment and not args.dry_run:
        api("POST", f"/repos/{repo}/issues/{args.number}/comments", data={"body": args.comment})
    if args.dry_run:
        return {"closed": False, "dry_run": True}
    issue, _ = api("PATCH", f"/repos/{repo}/issues/{args.number}", data={"state": "closed"})
    return {"closed": True, "number": issue["number"], "state": issue["state"]}


def cmd_branch(args) -> dict:
    repo = resolve_repo(args.repo)
    base = args.base or default_branch(repo)
    name = args.name or branch_for(args.number, args.title or "")
    git("fetch", "origin", base, "--prune", check=False, env=auth_env())
    if git("rev-parse", "--verify", f"origin/{base}", check=False):
        start = f"origin/{base}"
    elif git("rev-parse", "--verify", base, check=False):
        start = base
    else:
        raise GhError(f"base branch not found: neither origin/{base} nor {base} exists")
    git("checkout", "-B", name, start)
    return {"branch": name, "base": base, "start": start, "head": git("rev-parse", "--short", "HEAD")}


def cmd_status(args) -> dict:
    repo = resolve_repo(args.repo)
    base = args.base or default_branch(repo)
    branch = git("rev-parse", "--abbrev-ref", "HEAD", check=False)
    porcelain = git("status", "--porcelain", check=False)
    ahead = git("rev-list", "--count", f"origin/{base}..HEAD", check=False)
    diff = git("diff", "--stat", f"origin/{base}...HEAD", check=False)
    return {
        "repo": repo, "branch": branch, "base": base,
        "dirty": bool(porcelain),
        "changed_files": [line[3:] for line in porcelain.splitlines() if line.strip()],
        "ahead_commits": int(ahead) if ahead.isdigit() else None,
        "diff_stat": diff,
    }


def cmd_commit(args) -> dict:
    ensure_credentials_from_git()
    if not git("status", "--porcelain", check=False):
        return {"committed": False, "reason": "nothing to commit"}
    if args.paths:
        git("add", "--", *args.paths)
    else:
        git("add", "-A")
    if not git("diff", "--cached", "--name-only", check=False):
        return {"committed": False, "reason": "nothing staged"}
    if args.dry_run:
        return {"committed": False, "dry_run": True,
                "staged": git("diff", "--cached", "--name-only").splitlines()}
    ensure_identity()
    git("commit", "-m", args.message[:200])
    return {"committed": True, "commit": git("rev-parse", "--short", "HEAD"),
            "files": git("diff", "--name-only", "HEAD~1", "HEAD").splitlines()}


def cmd_push(args) -> dict:
    ensure_credentials_from_git()
    branch = args.branch or git("rev-parse", "--abbrev-ref", "HEAD")
    if args.dry_run:
        return {"pushed": False, "dry_run": True, "branch": branch}
    git("push", "-u", "origin", branch, env=auth_env())
    return {"pushed": True, "branch": branch}


def cmd_pr(args) -> dict:
    repo = resolve_repo(args.repo)
    base = args.base or default_branch(repo)
    branch = args.branch or git("rev-parse", "--abbrev-ref", "HEAD")
    title = args.title
    if not title:
        if args.issue and not args.dry_run:
            issue = get_issue(repo, args.issue)
            title = f"Fix #{args.issue}: {issue['title']}"
        elif args.issue:
            title = f"Fix #{args.issue}"
        else:
            title = branch
    if args.body_file:
        body = read_body(args)
    else:
        body = f"Closes #{args.issue}\n" if args.issue else ""
    if args.dry_run:
        return {"opened": False, "dry_run": True,
                "payload": {"title": title, "head": branch, "base": base, "draft": args.draft}}
    payload = {"title": title, "head": branch, "base": base,
               "body": body[:60000], "draft": bool(args.draft)}
    try:
        pull, _ = api("POST", f"/repos/{repo}/pulls", data=payload)
        return {"opened": True, "url": pull["html_url"], "number": pull["number"]}
    except GhError as exc:
        if exc.status != 422 or "already exists" not in str(exc).lower():
            raise
        existing, _ = api("GET", f"/repos/{repo}/pulls",
                          params={"head": f"{repo.split('/')[0]}:{branch}", "state": "open"})
        if not existing:
            raise
        pull = existing[0]
        api("PATCH", f"/repos/{repo}/pulls/{pull['number']}", data={"body": payload["body"]})
        return {"opened": True, "updated": True, "url": pull["html_url"], "number": pull["number"]}


# ------------------------------------------------------------------- auth

def _mask(value: str) -> str:
    if not value:
        return ""
    return (value[:8] + "…" + value[-4:]) if len(value) > 14 else value[:4] + "…"


def cmd_auth_list(args) -> dict:
    entries = load_creds()
    rendered = []
    for key, value in entries.items():
        if isinstance(value, str):
            token_value, api_base = value, API
        else:
            value = value or {}
            token_value, api_base = value.get("token", ""), value.get("api", API)
        rendered.append({"repo": key, "token": _mask(token_value), "api": api_base})
    return {"path": creds_path(), "exists": os.path.isfile(creds_path()),
            "entries": rendered}


def cmd_auth_set(args) -> dict:
    key = args.repo.strip()
    if key not in ("default", "*") and not re.fullmatch(r"[A-Za-z0-9_.-]+/(?:[A-Za-z0-9_.-]+|\*)", key):
        raise GhError("expected owner/name, owner/* or default")
    value = (args.token or "").strip()
    if not value:
        if sys.stdin.isatty():
            raise GhError("no token: pipe it on stdin or pass --token")
        value = sys.stdin.read().strip()
    if not value:
        raise GhError("empty token")
    entry = {"token": value}
    if args.api:
        entry["api"] = args.api.rstrip("/")
    entries = load_creds()
    entries[key] = entry
    path = save_creds(entries)
    return {"saved": True, "path": path, "repo": key, "token": _mask(value)}


def cmd_auth_remove(args) -> dict:
    key = args.repo.strip()
    entries = load_creds()
    if key not in entries:
        return {"removed": False, "reason": f"{key} not found"}
    del entries[key]
    path = save_creds(entries)
    return {"removed": True, "path": path, "repo": key}


# -------------------------------------------------------------------- output

def read_body(args) -> str:
    if getattr(args, "body_file", None):
        if args.body_file == "-":
            return sys.stdin.read()
        return open(args.body_file, encoding="utf-8").read()
    return getattr(args, "body", "") or ""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Deterministic GitHub task helpers (no LLM).")
    parser.add_argument("--repo", help="owner/name (default: $GITHUB_REPO or git origin)")
    parser.add_argument("--token", help="token for this call only (visible in process list)")
    parser.add_argument("--token-env", help="read token from this environment variable")
    parser.add_argument("--token-file", help="read token from this file")
    parser.add_argument("--api", help="API base URL for GitHub Enterprise (default: api.github.com)")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("doctor", help="check token, repo access, git and gh availability")
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("auth", help="manage per-repo keys in github-tokens.json")
    auth_sub = p.add_subparsers(dest="auth_command", required=True)
    q = auth_sub.add_parser("list", help="list configured repos (tokens masked)")
    q.set_defaults(func=cmd_auth_list)
    q = auth_sub.add_parser("set", help="add or replace a key; token from stdin or --token")
    q.add_argument("--repo", required=True, help="owner/name, owner/* or default")
    q.add_argument("--token", help="prefer piping the token on stdin instead")
    q.add_argument("--api", help="API base URL for GitHub Enterprise")
    q.set_defaults(func=cmd_auth_set)
    q = auth_sub.add_parser("remove", help="remove a repo entry")
    q.add_argument("--repo", required=True)
    q.set_defaults(func=cmd_auth_remove)

    p = sub.add_parser("list", help="list issues sorted by priority label")
    p.add_argument("--state", default="open", choices=["open", "closed", "all"])
    p.add_argument("--label", action="append", help="only issues with this label (repeatable)")
    p.add_argument("--limit", type=int, default=0)
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("show", help="show one issue with comments")
    p.add_argument("number", type=int)
    p.add_argument("--max-comments", type=int, default=30)
    p.set_defaults(func=cmd_show)

    p = sub.add_parser("create", help="create an issue (dedupe + priority label)")
    p.add_argument("--title", required=True)
    p.add_argument("--body", default="")
    p.add_argument("--body-file")
    p.add_argument("--priority", default="p2", choices=["p0", "p1", "p2"])
    p.add_argument("--label", action="append")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_create)

    p = sub.add_parser("comment", help="post a comment on an issue")
    p.add_argument("number", type=int)
    p.add_argument("--body", default="")
    p.add_argument("--body-file")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_comment)

    p = sub.add_parser("report", help="upsert the single final report comment")
    p.add_argument("number", type=int)
    p.add_argument("--body", default="")
    p.add_argument("--body-file")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_report)

    p = sub.add_parser("close", help="close an issue, optionally with a comment")
    p.add_argument("number", type=int)
    p.add_argument("--comment")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_close)

    p = sub.add_parser("branch", help="create/fetch branch agent/issue-N from the base branch")
    p.add_argument("number", type=int)
    p.add_argument("--title", default="")
    p.add_argument("--name")
    p.add_argument("--base")
    p.set_defaults(func=cmd_branch)

    p = sub.add_parser("status", help="git status, ahead commits and diff stat vs base")
    p.add_argument("--base")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("commit", help="stage and commit changes")
    p.add_argument("-m", "--message", required=True)
    p.add_argument("--paths", nargs="*")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_commit)

    p = sub.add_parser("push", help="push current (or given) branch to origin")
    p.add_argument("--branch")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_push)

    p = sub.add_parser("pr", help="open a pull request (idempotent)")
    p.add_argument("--issue", type=int)
    p.add_argument("--branch")
    p.add_argument("--base")
    p.add_argument("--title")
    p.add_argument("--body", default="")
    p.add_argument("--body-file")
    p.add_argument("--draft", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_pr)

    return parser


def apply_explicit_token(args) -> None:
    value = None
    source = None
    if getattr(args, "token", None):
        value, source = args.token.strip(), "flag"
    elif getattr(args, "token_env", None):
        value, source = os.environ.get(args.token_env, "").strip() or None, f"env:{args.token_env}"
    elif getattr(args, "token_file", None):
        try:
            value = open(args.token_file, encoding="utf-8").read().strip()
        except OSError as exc:
            raise GhError(f"cannot read token file: {exc}")
        source = f"file:{args.token_file}"
    if value:
        CTX.update(token=value, api=(getattr(args, "api", None) or API).rstrip("/"),
                   source=source, explicit=True)
    elif getattr(args, "api", None):
        CTX["api"] = args.api.rstrip("/")


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        apply_explicit_token(args)
        result = args.func(args)
    except GhError as exc:
        print(json.dumps({"ok": False, "error": str(exc), "status": exc.status},
                         ensure_ascii=False, indent=2))
        return 1
    print(json.dumps({"ok": True, **result}, ensure_ascii=False, indent=2, default=str))
    return 0 if result.get("ok", True) else 1


if __name__ == "__main__":
    sys.exit(main())
