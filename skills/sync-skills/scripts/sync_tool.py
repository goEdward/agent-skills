#!/usr/bin/env python3
"""Cross-platform skill repository audit, rule doctor & GitHub sync tool.

Pure Python 3 standard library implementation for macOS, Windows, and Linux.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

DEFAULT_SECRETS_FILE = Path.home() / ".config" / "agent-secrets" / "sync-skills.env"
ANTIGRAVITY_SKILLS_JSON = Path.home() / ".gemini" / "config" / "skills.json"
ANTIGRAVITY_RULE_FILE = Path.home() / ".gemini" / "config" / "rules" / "skills-management.md"
CODEX_SKILLS_DIR = Path.home() / ".codex" / "skills"
CODEX_AGENTS_MD = Path.home() / ".codex" / "AGENTS.md"
MANAGED_BLOCK_START = "<!-- agent-skills:start -->"
MANAGED_BLOCK_END = "<!-- agent-skills:end -->"

FORBIDDEN_FILENAME_PATTERNS = [
    re.compile(r"(^|/)\.env(\.[^/]+)?$", re.IGNORECASE),
    re.compile(r"\.local\.[^/]+$", re.IGNORECASE),
    re.compile(r"\.secret\.[^/]+$", re.IGNORECASE),
    re.compile(r"\.(pem|key|p12|pfx)$", re.IGNORECASE),
    re.compile(r"(^|/)\.DS_Store$"),
    re.compile(r"(^|/)Thumbs\.db$", re.IGNORECASE),
]

ALLOWED_FILENAME_EXCEPTIONS = [
    re.compile(r"(^|/)\.env\.example$", re.IGNORECASE),
    re.compile(r"\.example\.env$", re.IGNORECASE),
    re.compile(r"\.env\.template$", re.IGNORECASE),
    re.compile(r"(^|/)config\.example\.[^/]+$", re.IGNORECASE),
]

SECRET_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("OpenAI/Anthropic API key", re.compile(r"\bsk-(?:proj-|ant-)?[A-Za-z0-9_-]{20,}\b")),
    ("GitHub personal access token", re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}\b")),
    ("GitHub fine-grained token", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b")),
    ("AWS access key ID", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("Slack token", re.compile(r"\bxox[baprs]-[0-9A-Za-z-]{10,}\b")),
    (
        "Private key block",
        re.compile(r"-----BEGIN " + r"(?:RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY-----"),
    ),
]

MAC_USER_PATH_RE = re.compile(r"/Users/([A-Za-z0-9._-]+)/")
WIN_USER_PATH_RE = re.compile(r"[A-Za-z]:[\\/]+Users[\\/]+([A-Za-z0-9._-]+)[\\/]")
EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@([A-Za-z0-9.-]+\.[A-Za-z]{2,})\b")

PLACEHOLDER_USERS = {
    "user",
    "username",
    "your-username",
    "your_username",
    "yourname",
    "example",
    "alice",
    "bob",
    "name",
    "me",
    "test",
}

SAFE_EMAIL_DOMAINS = {
    "example.com",
    "example.org",
    "example.net",
    "users.noreply.github.com",
    "localhost",
}

CRLF_ALLOWED_SUFFIXES = {".ps1", ".bat", ".cmd"}


def load_local_env(env_path: Path = DEFAULT_SECRETS_FILE) -> dict[str, str]:
    data: dict[str, str] = {}
    if not env_path.is_file():
        return data
    for raw_line in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        data[key] = value
    return data


def resolve_repo_dir(cli_repo: str | None, local_env: dict[str, str]) -> Path:
    if cli_repo:
        return Path(cli_repo).expanduser().resolve()
    env_repo = local_env.get("SKILLS_REPO_DIR", "").strip()
    if env_repo:
        return Path(env_repo).expanduser().resolve()

    cur = Path(__file__).resolve().parent
    for parent in [cur, *cur.parents]:
        if (parent / ".git").exists():
            return parent
    return (Path.home() / "agent-skills").resolve()


def run_git(repo: Path, args: list[str], check: bool = False) -> subprocess.CompletedProcess[str]:
    cmd = ["git", "-C", str(repo), "-c", "core.quotepath=false", *args]
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=check,
    )


def strip_frontmatter(md_text: str) -> str:
    lines = md_text.splitlines()
    if lines and lines[0].strip() == "---":
        for idx in range(1, len(lines)):
            if lines[idx].strip() == "---":
                return "\n".join(lines[idx + 1 :]).strip()
    return md_text.strip()


def parse_frontmatter(md_text: str) -> dict[str, str] | None:
    lines = md_text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    end_idx = None
    for idx in range(1, len(lines)):
        if lines[idx].strip() == "---":
            end_idx = idx
            break
    if end_idx is None:
        return None

    result: dict[str, str] = {}
    current_key: str | None = None
    multiline_buf: list[str] = []

    def flush() -> None:
        nonlocal current_key, multiline_buf
        if current_key is not None and multiline_buf:
            result[current_key] = " ".join(x.strip() for x in multiline_buf if x.strip())
        current_key = None
        multiline_buf = []

    for line in lines[1:end_idx]:
        if current_key is not None and (line.startswith(" ") or line.startswith("\t")):
            multiline_buf.append(line)
            continue
        flush()
        if not line.strip() or line.strip().startswith("#"):
            continue
        if ":" in line:
            k, v = line.split(":", 1)
            k = k.strip()
            v = v.strip()
            if v in (">", ">-", "|", "|-"):
                current_key = k
                result[k] = ""
            else:
                if len(v) >= 2 and v[0] == v[-1] and v[0] in ("'", '"'):
                    v = v[1:-1]
                result[k] = v
    flush()
    return result


def is_binary_bytes(raw: bytes) -> bool:
    return b"\x00" in raw[:8192]


def run_doctor(repo: Path, local_env: dict[str, str]) -> dict[str, Any]:
    """Check whether global rules, local agent mounts, and secrets config are set up."""
    rule_src = repo / "rules" / "skills-management.md"
    skills_dir = repo / "skills"
    issues: list[str] = []
    notices: list[str] = []

    # 1. Antigravity skills.json check
    agy_skills_ok = False
    if ANTIGRAVITY_SKILLS_JSON.is_file():
        try:
            cfg = json.loads(ANTIGRAVITY_SKILLS_JSON.read_text(encoding="utf-8"))
            norms = {
                Path(e["path"]).expanduser().resolve().as_posix()
                for e in cfg.get("entries", [])
                if isinstance(e, dict) and isinstance(e.get("path"), str)
            }
            agy_skills_ok = skills_dir.resolve().as_posix() in norms
        except Exception:
            agy_skills_ok = False
    if not agy_skills_ok:
        issues.append(f"Antigravity skills.json does not register {skills_dir}")

    # 2. Antigravity global rule check
    agy_rule_status = "missing"
    if ANTIGRAVITY_RULE_FILE.is_symlink():
        try:
            if ANTIGRAVITY_RULE_FILE.resolve() == rule_src.resolve():
                agy_rule_status = "ok"
            else:
                agy_rule_status = "outdated"
        except OSError:
            agy_rule_status = "broken"
    elif ANTIGRAVITY_RULE_FILE.is_file():
        if rule_src.is_file() and ANTIGRAVITY_RULE_FILE.read_text(encoding="utf-8", errors="replace") == rule_src.read_text(encoding="utf-8", errors="replace"):
            agy_rule_status = "ok"
        else:
            agy_rule_status = "outdated"
    if agy_rule_status != "ok":
        issues.append(f"Antigravity global rule (~/.gemini/config/rules/skills-management.md) is {agy_rule_status}")

    # 3. Codex skills & AGENTS.md managed block check
    codex_dir_exists = (Path.home() / ".codex").exists()
    codex_rule_status = "not_installed"
    codex_sync_mounted = False
    if codex_dir_exists:
        sync_link = CODEX_SKILLS_DIR / "sync-skills"
        if sync_link.exists() or sync_link.is_symlink():
            try:
                sync_link.resolve().relative_to(repo.resolve())
                codex_sync_mounted = True
            except (OSError, ValueError):
                codex_sync_mounted = False
        if not codex_sync_mounted:
            issues.append("Codex skill mount (~/.codex/skills/sync-skills) is missing or points elsewhere")

        if CODEX_AGENTS_MD.is_file():
            content = CODEX_AGENTS_MD.read_text(encoding="utf-8", errors="replace")
            if MANAGED_BLOCK_START in content and MANAGED_BLOCK_END in content:
                if rule_src.is_file():
                    expected_body = strip_frontmatter(rule_src.read_text(encoding="utf-8", errors="replace"))
                    m = re.search(
                        re.escape(MANAGED_BLOCK_START) + r"\s*(.*?)\s*" + re.escape(MANAGED_BLOCK_END),
                        content,
                        re.DOTALL,
                    )
                    if m and m.group(1).strip() == expected_body.strip():
                        codex_rule_status = "ok"
                    else:
                        codex_rule_status = "outdated"
                else:
                    codex_rule_status = "ok"
            else:
                codex_rule_status = "missing"
        else:
            codex_rule_status = "missing"
        if codex_rule_status != "ok":
            issues.append(f"Codex global rule block in ~/.codex/AGENTS.md is {codex_rule_status}")

    # 4. Local secrets file check (advisory)
    custom_terms = [
        t.strip() for t in local_env.get("CUSTOM_REDACT_TERMS", "").split(",") if len(t.strip()) >= 2
    ]
    if not DEFAULT_SECRETS_FILE.is_file():
        notices.append(
            f"Optional local secrets file not created yet: {DEFAULT_SECRETS_FILE} "
            "(copy from templates/config.example.env to enable custom redaction terms)"
        )
    elif not custom_terms:
        notices.append(
            f"CUSTOM_REDACT_TERMS is empty in {DEFAULT_SECRETS_FILE} "
            "(consider adding your username/brand/private terms for extra public-repo safety)"
        )

    return {
        "ok": len(issues) == 0,
        "repo": str(repo),
        "platform": platform.system(),
        "antigravity": {
            "skillsMounted": agy_skills_ok,
            "globalRule": agy_rule_status,
        },
        "codex": {
            "installed": codex_dir_exists,
            "syncSkillMounted": codex_sync_mounted,
            "globalRule": codex_rule_status,
        },
        "localSecrets": {
            "exists": DEFAULT_SECRETS_FILE.is_file(),
            "customRedactTermCount": len(custom_terms),
        },
        "issues": issues,
        "notices": notices,
        "fixCommand": f"python3 {repo / 'setup.py'}" if platform.system() != "Windows" else f"python {repo / 'setup.py'}",
    }


def get_status_info(repo: Path, local_env: dict[str, str]) -> dict[str, Any]:
    if not (repo / ".git").exists():
        return {"ok": False, "error": f"Not a git repository: {repo}"}

    branch_res = run_git(repo, ["rev-parse", "--abbrev-ref", "HEAD"])
    branch = branch_res.stdout.strip() if branch_res.returncode == 0 else "(unborn)"

    remote_res = run_git(repo, ["remote", "get-url", "origin"])
    remote_url = remote_res.stdout.strip() if remote_res.returncode == 0 else ""

    porcelain = run_git(repo, ["status", "--porcelain=v1", "-uall"])
    staged: list[str] = []
    modified: list[str] = []
    untracked: list[str] = []

    for line in porcelain.stdout.splitlines():
        if len(line) < 4:
            continue
        xy = line[:2]
        rel = line[3:].strip()
        if " -> " in rel:
            rel = rel.split(" -> ", 1)[1].strip()
        if xy == "??":
            untracked.append(rel)
        else:
            if xy[0] != " ":
                staged.append(rel)
            if xy[1] != " ":
                modified.append(rel)

    ahead = 0
    behind = 0
    upstream_res = run_git(repo, ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"])
    upstream = upstream_res.stdout.strip() if upstream_res.returncode == 0 else ""
    if upstream:
        ab = run_git(repo, ["rev-list", "--left-right", "--count", f"HEAD...{upstream}"])
        if ab.returncode == 0:
            parts = ab.stdout.strip().split()
            if len(parts) == 2:
                ahead, behind = int(parts[0]), int(parts[1])

    custom_terms = [
        t.strip() for t in local_env.get("CUSTOM_REDACT_TERMS", "").split(",") if len(t.strip()) >= 2
    ]

    return {
        "ok": True,
        "repo": str(repo),
        "platform": platform.system(),
        "branch": branch,
        "remote": remote_url or "(none)",
        "upstream": upstream or "(none)",
        "ahead": ahead,
        "behind": behind,
        "staged": sorted(set(staged)),
        "modified": sorted(set(modified)),
        "untracked": sorted(set(untracked)),
        "hasChanges": bool(staged or modified or untracked),
        "localSecretsLoaded": DEFAULT_SECRETS_FILE.is_file(),
        "customRedactTermCount": len(custom_terms),
    }


def list_files_for_audit(repo: Path, scan_all: bool) -> list[Path]:
    if scan_all:
        res = run_git(repo, ["ls-files", "--cached", "--others", "--exclude-standard"])
        rels = [line.strip() for line in res.stdout.splitlines() if line.strip()]
    else:
        status = run_git(repo, ["status", "--porcelain=v1", "-uall"])
        rels = []
        for line in status.stdout.splitlines():
            if len(line) < 4:
                continue
            xy = line[:2]
            if "D" in xy and xy != "??":
                continue
            rel = line[3:].strip()
            if " -> " in rel:
                rel = rel.split(" -> ", 1)[1].strip()
            rels.append(rel)
    paths: list[Path] = []
    for rel in sorted(set(rels)):
        full = repo / rel
        if full.is_file():
            paths.append(full)
    return paths


def check_filename_allowed(rel_posix: str) -> bool:
    for allow in ALLOWED_FILENAME_EXCEPTIONS:
        if allow.search(rel_posix):
            return True
    for forbid in FORBIDDEN_FILENAME_PATTERNS:
        if forbid.search(rel_posix):
            return False
    return True


def audit_skills_frontmatter(repo: Path) -> list[str]:
    errors: list[str] = []
    skills_root = repo / "skills"
    if not skills_root.is_dir():
        return errors

    for skill_dir in sorted(skills_root.iterdir()):
        if not skill_dir.is_dir() or skill_dir.name.startswith("."):
            continue
        expected_name = skill_dir.name
        main_md = skill_dir / "SKILL.md"
        if not main_md.is_file():
            errors.append(f"[frontmatter] skills/{expected_name}/ is missing SKILL.md")
            continue

        candidates = [main_md]
        agents_dir = skill_dir / "agents"
        if agents_dir.is_dir():
            for agent_sub in sorted(agents_dir.iterdir()):
                override_md = agent_sub / "SKILL.md"
                if override_md.is_file():
                    candidates.append(override_md)

        for md_path in candidates:
            rel = md_path.relative_to(repo).as_posix()
            fm = parse_frontmatter(md_path.read_text(encoding="utf-8", errors="replace"))
            if fm is None:
                errors.append(f"[frontmatter] {rel}: missing valid YAML frontmatter (--- ... ---)")
                continue
            fm_name = fm.get("name", "").strip()
            fm_desc = fm.get("description", "").strip()
            if fm_name != expected_name:
                errors.append(
                    f"[frontmatter] {rel}: frontmatter name '{fm_name}' must match folder '{expected_name}'"
                )
            if not fm_desc:
                errors.append(f"[frontmatter] {rel}: frontmatter description must be non-empty")
    return errors


def run_audit(repo: Path, local_env: dict[str, str], scan_all: bool = False) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []

    errors.extend(audit_skills_frontmatter(repo))

    files = list_files_for_audit(repo, scan_all=scan_all)
    allowed_emails = {
        e.strip().lower() for e in local_env.get("ALLOWED_EMAILS", "").split(",") if e.strip()
    }
    custom_terms = [
        t.strip()
        for t in local_env.get("CUSTOM_REDACT_TERMS", "").split(",")
        if len(t.strip()) >= 2
    ]

    for file_path in files:
        rel = file_path.relative_to(repo).as_posix()
        if not check_filename_allowed(rel):
            errors.append(f"[forbidden-file] {rel}: file pattern is blocked for public repo")
            continue

        raw = file_path.read_bytes()
        if is_binary_bytes(raw):
            continue

        if b"\r\n" in raw and file_path.suffix.lower() not in CRLF_ALLOWED_SUFFIXES:
            warnings.append(f"[line-ending] {rel}: contains CRLF (expected LF)")

        text = raw.decode("utf-8", errors="replace")
        for line_no, line in enumerate(text.splitlines(), start=1):
            if "audit:ignore" in line:
                continue

            for label, pat in SECRET_PATTERNS:
                if pat.search(line):
                    errors.append(f"[secret] {rel}:{line_no}: matched {label}")

            for m in MAC_USER_PATH_RE.finditer(line):
                user = m.group(1).lower()
                if user not in PLACEHOLDER_USERS and not user.startswith("<"):
                    errors.append(
                        f"[local-path] {rel}:{line_no}: hardcoded macOS user path (/Users/{m.group(1)}/)"
                    )

            for m in WIN_USER_PATH_RE.finditer(line):
                user = m.group(1).lower()
                if user not in PLACEHOLDER_USERS and not user.startswith("<"):
                    errors.append(
                        f"[local-path] {rel}:{line_no}: hardcoded Windows user path (Users\\{m.group(1)})"
                    )

            for m in EMAIL_RE.finditer(line):
                email = m.group(0).lower()
                domain = m.group(1).lower()
                if domain not in SAFE_EMAIL_DOMAINS and email not in allowed_emails:
                    errors.append(f"[email] {rel}:{line_no}: possible personal email detected")

            lower_line = line.lower()
            for term in custom_terms:
                if term.lower() in lower_line:
                    errors.append(
                        f"[custom-redact] {rel}:{line_no}: matched a custom redacted term from local config"
                    )

    return {
        "ok": len(errors) == 0,
        "scannedFiles": len(files),
        "scope": "all" if scan_all else "changed",
        "errors": errors,
        "warnings": warnings,
    }


def cmd_doctor(repo: Path, local_env: dict[str, str], as_json: bool) -> int:
    res = run_doctor(repo, local_env)
    if as_json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
    else:
        print(f"Repo:        {res['repo']}")
        print(f"Platform:    {res['platform']}")
        print(
            f"Antigravity: skills={'OK' if res['antigravity']['skillsMounted'] else 'MISSING'}, "
            f"globalRule={res['antigravity']['globalRule']}"
        )
        if res["codex"]["installed"]:
            print(
                f"Codex:       skills={'OK' if res['codex']['syncSkillMounted'] else 'MISSING'}, "
                f"globalRule={res['codex']['globalRule']}"
            )
        else:
            print("Codex:       not installed (skipped)")
        print(
            f"Secrets:     {'loaded' if res['localSecrets']['exists'] else 'not configured'} "
            f"(custom redact terms: {res['localSecrets']['customRedactTermCount']})"
        )
        if res["issues"]:
            print(f"\nMissing / Outdated ({len(res['issues'])}):")
            for item in res["issues"]:
                print(f"  ACTION {item}")
            print(f"Fix command: {res['fixCommand']}")
        else:
            print("\nDoctor PASSED. Global rules and agent mounts are ready.")
        if res["notices"]:
            print("\nNotices:")
            for n in res["notices"]:
                print(f"  NOTE   {n}")
    return 0 if res["ok"] else 1


def cmd_status(repo: Path, local_env: dict[str, str], as_json: bool) -> int:
    info = get_status_info(repo, local_env)
    if as_json:
        print(json.dumps(info, ensure_ascii=False, indent=2))
        return 0 if info.get("ok") else 1

    if not info.get("ok"):
        print(f"ERROR: {info['error']}", file=sys.stderr)
        return 1

    print(f"Repo:     {info['repo']}")
    print(f"Platform: {info['platform']}")
    print(f"Branch:   {info['branch']} (ahead {info['ahead']}, behind {info['behind']})")
    print(f"Remote:   {info['remote']}")
    print(
        f"Secrets:  {'loaded' if info['localSecretsLoaded'] else 'not configured'} "
        f"(custom redact terms: {info['customRedactTermCount']})"
    )
    if not info["hasChanges"]:
        print("Changes:  clean")
    else:
        if info["staged"]:
            print("Staged:")
            for p in info["staged"]:
                print(f"  + {p}")
        if info["modified"]:
            print("Modified:")
            for p in info["modified"]:
                print(f"  M {p}")
        if info["untracked"]:
            print("Untracked:")
            for p in info["untracked"]:
                print(f"  ? {p}")
    return 0


def cmd_audit(repo: Path, local_env: dict[str, str], scan_all: bool, as_json: bool) -> int:
    res = run_audit(repo, local_env, scan_all=scan_all)
    if as_json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
    else:
        print(f"Audit scope: {res['scope']} ({res['scannedFiles']} file(s) scanned)")
        if res["warnings"]:
            print(f"Warnings ({len(res['warnings'])}):")
            for w in res["warnings"]:
                print(f"  WARN  {w}")
        if res["errors"]:
            print(f"Errors ({len(res['errors'])}):")
            for e in res["errors"]:
                print(f"  BLOCK {e}")
            print("Audit FAILED. Resolve blocking errors before committing/pushing.")
        else:
            print("Audit PASSED. No blocking issues detected.")
    return 0 if res["ok"] else 1


def cmd_pull(repo: Path, refresh_setup: bool) -> int:
    remote_res = run_git(repo, ["remote", "get-url", "origin"])
    if remote_res.returncode != 0 or not remote_res.stdout.strip():
        print("ERROR: No 'origin' remote configured yet.", file=sys.stderr)
        return 1

    print(f"Pulling latest updates in {repo} ...")
    pull_res = run_git(repo, ["pull", "--rebase", "--autostash"])
    if pull_res.stdout.strip():
        print(pull_res.stdout.strip())
    if pull_res.stderr.strip():
        print(pull_res.stderr.strip(), file=sys.stderr)
    if pull_res.returncode != 0:
        print("ERROR: git pull --rebase failed. Resolve conflicts manually before pushing.", file=sys.stderr)
        return pull_res.returncode

    if refresh_setup:
        setup_script = repo / "setup.py"
        if setup_script.is_file():
            print("Refreshing local agent mounts & global rules via setup.py ...")
            setup_res = subprocess.run([sys.executable, str(setup_script)])
            if setup_res.returncode != 0:
                return setup_res.returncode
    return 0


def cmd_commit_push(repo: Path, local_env: dict[str, str], message: str, no_push: bool) -> int:
    audit_res = run_audit(repo, local_env, scan_all=False)
    if not audit_res["ok"]:
        print("ERROR: Pre-commit audit failed. Aborting commit/push.", file=sys.stderr)
        for e in audit_res["errors"]:
            print(f"  BLOCK {e}", file=sys.stderr)
        return 1

    status = get_status_info(repo, local_env)
    if not status.get("ok"):
        print(f"ERROR: {status['error']}", file=sys.stderr)
        return 1

    if status["hasChanges"]:
        add_res = run_git(repo, ["add", "-A"])
        if add_res.returncode != 0:
            print(add_res.stderr, file=sys.stderr)
            return add_res.returncode

        commit_res = run_git(repo, ["commit", "-m", message])
        if commit_res.stdout.strip():
            print(commit_res.stdout.strip())
        if commit_res.returncode != 0:
            print(commit_res.stderr, file=sys.stderr)
            return commit_res.returncode
    else:
        print("No local file changes to commit.")

    if no_push:
        return 0

    remote_res = run_git(repo, ["remote", "get-url", "origin"])
    if remote_res.returncode != 0 or not remote_res.stdout.strip():
        print("WARNING: Commit succeeded locally, but no 'origin' remote is configured yet.")
        return 0

    upstream_res = run_git(repo, ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"])
    if upstream_res.returncode == 0 and upstream_res.stdout.strip():
        pull_res = run_git(repo, ["pull", "--rebase"])
        if pull_res.returncode != 0:
            print("ERROR: git pull --rebase encountered conflicts:", file=sys.stderr)
            print(pull_res.stdout + pull_res.stderr, file=sys.stderr)
            return pull_res.returncode
        push_args = ["push"]
    else:
        branch = run_git(repo, ["rev-parse", "--abbrev-ref", "HEAD"]).stdout.strip() or "main"
        push_args = ["push", "-u", "origin", branch]

    push_res = run_git(repo, push_args)
    if push_res.stdout.strip():
        print(push_res.stdout.strip())
    if push_res.stderr.strip():
        print(push_res.stderr.strip())
    return push_res.returncode


def main() -> int:
    parser = argparse.ArgumentParser(description="Cross-platform agent-skills doctor, audit and sync CLI")
    parser.add_argument("--repo", help="Path to agent-skills repository")
    sub = parser.add_subparsers(dest="command", required=True)

    p_doctor = sub.add_parser("doctor", help="Check global rules, agent mounts, and local secrets setup")
    p_doctor.add_argument("--json", action="store_true", help="Output JSON")

    p_status = sub.add_parser("status", help="Show repository status")
    p_status.add_argument("--json", action="store_true", help="Output JSON")

    p_audit = sub.add_parser("audit", help="Audit SKILL.md compliance and public-repo privacy")
    p_audit.add_argument("--all", action="store_true", help="Scan entire repository instead of only changed files")
    p_audit.add_argument("--json", action="store_true", help="Output JSON")

    p_pull = sub.add_parser("pull", help="Pull latest updates from GitHub")
    p_pull.add_argument("--refresh-setup", action="store_true", help="Run setup.py after pull")

    p_push = sub.add_parser("commit-push", help="Audit, commit, rebase-pull, and push to GitHub")
    p_push.add_argument("-m", "--message", required=True, help="Commit message")
    p_push.add_argument("--no-push", action="store_true", help="Only commit locally without pushing")

    args = parser.parse_args()
    local_env = load_local_env()
    repo = resolve_repo_dir(args.repo, local_env)

    if args.command == "doctor":
        return cmd_doctor(repo, local_env, as_json=args.json)
    if args.command == "status":
        return cmd_status(repo, local_env, as_json=args.json)
    if args.command == "audit":
        return cmd_audit(repo, local_env, scan_all=args.all, as_json=args.json)
    if args.command == "pull":
        return cmd_pull(repo, refresh_setup=args.refresh_setup)
    if args.command == "commit-push":
        return cmd_commit_push(repo, local_env, message=args.message, no_push=args.no_push)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
