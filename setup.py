#!/usr/bin/env python3
"""Cross-platform setup script to mount agent-skills & rules into Antigravity, Codex, and Claude Code.

Supports macOS, Windows, and Linux using only the Python 3 standard library.
Works regardless of which local directory the GitHub repository is cloned into.

Modes:
  - Default (clean consumer): installs business skills only; NEVER installs sync-skills or maintainer
    sync rules (skills-management.md / Codex AGENTS.md).
  - --maintainer: installs all skills including sync-skills plus maintainer global sync rules.
  - --only a,b: mount only the specified skill names (maintainer sync rules are installed ONLY if
    sync-skills is explicitly included in --only or --maintainer is set).
  - --init-secrets: create missing local secret files from templates (STRICTLY NEVER overwrites existing files).
  - --verify [--json]: verify mounts, runtime dependencies (Python/openpyxl/Node), and local secret presence.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent
SKILLS_DIR = REPO_ROOT / "skills"
RULES_DIR = REPO_ROOT / "rules"
SKILL_MGMT_RULE_SRC = RULES_DIR / "skills-management.md"
LOCAL_VIEWS_DIR = REPO_ROOT / ".local-views"

MAINTAINER_ONLY_SKILLS = {"sync-skills"}

ANTIGRAVITY_CONFIG_DIR = Path.home() / ".gemini" / "config"
ANTIGRAVITY_SKILLS_JSON = ANTIGRAVITY_CONFIG_DIR / "skills.json"
ANTIGRAVITY_RULES_DIR = ANTIGRAVITY_CONFIG_DIR / "rules"
ANTIGRAVITY_PLUGINS_DIR = ANTIGRAVITY_CONFIG_DIR / "plugins"

CODEX_DIR = Path.home() / ".codex"
CODEX_SKILLS_DIR = CODEX_DIR / "skills"
CODEX_AGENTS_MD = CODEX_DIR / "AGENTS.md"

CLAUDE_DIR = Path.home() / ".claude"
CLAUDE_SKILLS_DIR = CLAUDE_DIR / "skills"

MANAGED_BLOCK_START = "<!-- agent-skills:start -->"
MANAGED_BLOCK_END = "<!-- agent-skills:end -->"


def configure_utf8_output() -> None:
    """Prevent UnicodeEncodeError on Windows legacy code pages."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8")
            except (OSError, ValueError):
                pass


def to_portable_path(path: Path) -> str:
    resolved = path.resolve()
    home = Path.home().resolve()
    try:
        rel = resolved.relative_to(home)
        return "~/" + rel.as_posix()
    except ValueError:
        return resolved.as_posix()


def strip_frontmatter(md_text: str) -> str:
    lines = md_text.splitlines()
    if lines and lines[0].strip() == "---":
        for idx in range(1, len(lines)):
            if lines[idx].strip() == "---":
                return "\n".join(lines[idx + 1 :]).strip()
    return md_text.strip()


def discover_skills() -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    if not SKILLS_DIR.is_dir():
        return items
    for sub in sorted(SKILLS_DIR.iterdir()):
        if not sub.is_dir() or sub.name.startswith("."):
            continue
        main_md = sub / "SKILL.md"
        if not main_md.is_file():
            continue
        manifest: dict[str, Any] = {}
        manifest_path = sub / "skill.manifest.json"
        if manifest_path.is_file():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                manifest = {}

        overrides: dict[str, Path] = {}
        agent_rules: dict[str, list[Path]] = {}
        for agent_name in ("antigravity", "codex", "claude"):
            override_dir = sub / "agents" / agent_name
            if (override_dir / "SKILL.md").is_file():
                overrides[agent_name] = override_dir
            rules_sub = override_dir / "rules"
            if rules_sub.is_dir():
                md_files = sorted(p for p in rules_sub.glob("*.md") if p.is_file())
                if md_files:
                    agent_rules[agent_name] = md_files

        items.append(
            {
                "name": sub.name,
                "root": sub,
                "manifest": manifest,
                "overrides": overrides,
                "agentRules": agent_rules,
            }
        )
    return items


def select_skills(
    all_skills: list[dict[str, Any]],
    *,
    maintainer_mode: bool,
    only_arg: str | None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Filter skills according to --maintainer and --only. Default excludes sync-skills."""
    by_name = {s["name"]: s for s in all_skills}
    if only_arg:
        requested = [item.strip() for item in only_arg.split(",") if item.strip()]
        unknown = [name for name in requested if name not in by_name]
        if unknown:
            available = ", ".join(sorted(by_name.keys()))
            raise SystemExit(
                f"ERROR: Unknown skill(s) in --only: {', '.join(unknown)}. Available: {available}"
            )
        selected = [by_name[name] for name in dict.fromkeys(requested)]
    elif maintainer_mode:
        selected = list(all_skills)
    else:
        selected = [s for s in all_skills if s["name"] not in MAINTAINER_ONLY_SKILLS]

    selected_names = {s["name"] for s in selected}
    excluded_names = sorted(name for name in by_name if name not in selected_names)
    return selected, excluded_names


def is_reparse_or_symlink(path: Path) -> bool:
    if path.is_symlink():
        return True
    if platform.system() == "Windows" and path.exists():
        try:
            st = os.lstat(path)
            return bool(getattr(st, "st_file_attributes", 0) & 0x400)
        except OSError:
            return False
    return False


def points_into_repo(path: Path) -> bool:
    if not is_reparse_or_symlink(path):
        return False
    try:
        target = path.resolve()
        target.relative_to(REPO_ROOT.resolve())
        return True
    except (OSError, ValueError):
        try:
            raw = os.readlink(path)
            return str(REPO_ROOT) in raw
        except OSError:
            return False


def remove_link(path: Path) -> None:
    if platform.system() == "Windows" and path.is_dir() and not path.is_symlink():
        os.rmdir(path)
    else:
        path.unlink()


def create_dir_link(src: Path, dst: Path, dry_run: bool) -> None:
    if dry_run:
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    if platform.system() == "Windows":
        res = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(dst), str(src)],
            capture_output=True,
            text=True,
        )
        if res.returncode == 0:
            return
        os.symlink(src, dst, target_is_directory=True)
    else:
        os.symlink(src, dst)


def link_or_copy_file(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists() or dst.is_symlink():
        dst.unlink()
    if platform.system() == "Windows":
        try:
            os.symlink(src, dst)
            return
        except OSError:
            shutil.copy2(src, dst)
            return
    os.symlink(src, dst)


def merge_directory_view(base_dir: Path, override_dir: Path, dst_dir: Path) -> None:
    dst_dir.mkdir(parents=True, exist_ok=True)
    names: set[str] = set()
    if base_dir.is_dir():
        names.update(p.name for p in base_dir.iterdir() if p.name != ".DS_Store")
    if override_dir.is_dir():
        names.update(p.name for p in override_dir.iterdir() if p.name != ".DS_Store")

    for name in sorted(names):
        b_item = base_dir / name if base_dir.is_dir() else None
        o_item = override_dir / name if override_dir.is_dir() else None
        b_exists = bool(b_item and b_item.exists())
        o_exists = bool(o_item and o_item.exists())

        if o_exists and o_item is not None:
            if o_item.is_dir():
                if b_exists and b_item is not None and b_item.is_dir():
                    merge_directory_view(b_item, o_item, dst_dir / name)
                else:
                    create_dir_link(o_item, dst_dir / name, dry_run=False)
            else:
                link_or_copy_file(o_item, dst_dir / name)
        elif b_exists and b_item is not None:
            if b_item.is_dir():
                create_dir_link(b_item, dst_dir / name, dry_run=False)
            else:
                link_or_copy_file(b_item, dst_dir / name)


def build_agent_override_view(skill: dict[str, Any], agent_name: str, dry_run: bool) -> Path:
    view_skill_dir = LOCAL_VIEWS_DIR / agent_name / skill["name"]
    if dry_run:
        return view_skill_dir

    if view_skill_dir.exists() or view_skill_dir.is_symlink():
        if is_reparse_or_symlink(view_skill_dir):
            remove_link(view_skill_dir)
        else:
            shutil.rmtree(view_skill_dir)
    view_skill_dir.mkdir(parents=True, exist_ok=True)

    root: Path = skill["root"]
    override_dir: Path = skill["overrides"][agent_name]

    link_or_copy_file(override_dir / "SKILL.md", view_skill_dir / "SKILL.md")

    for fname in ("README.md", "skill.manifest.json"):
        if (override_dir / fname).is_file():
            link_or_copy_file(override_dir / fname, view_skill_dir / fname)
        elif (root / fname).is_file():
            link_or_copy_file(root / fname, view_skill_dir / fname)

    for dirname in ("scripts", "templates", "references", "tests"):
        base_sub = root / dirname
        over_sub = override_dir / dirname
        if over_sub.is_dir() and base_sub.is_dir():
            merge_directory_view(base_sub, over_sub, view_skill_dir / dirname)
        elif over_sub.is_dir():
            create_dir_link(over_sub, view_skill_dir / dirname, dry_run=False)
        elif base_sub.is_dir():
            create_dir_link(base_sub, view_skill_dir / dirname, dry_run=False)

    if agent_name == "codex" and (override_dir / "openai.yaml").is_file():
        link_or_copy_file(override_dir / "openai.yaml", view_skill_dir / "agents" / "openai.yaml")

    return view_skill_dir


def mount_link_based_agent(
    agent_name: str,
    target_dir: Path,
    selected_skills: list[dict[str, Any]],
    dry_run: bool,
    force: bool,
    uninstall: bool,
    quiet: bool = False,
) -> dict[str, Any]:
    report: dict[str, Any] = {
        "targetDir": str(target_dir),
        "mounted": [],
        "updated": [],
        "ok": [],
        "skippedConflicts": [],
        "pruned": [],
    }
    if not quiet:
        print(f"\n[{agent_name}:skills] Target directory: {target_dir}")
    view_agent_root = LOCAL_VIEWS_DIR / agent_name

    if uninstall:
        if target_dir.is_dir():
            for child in sorted(target_dir.iterdir()):
                if points_into_repo(child):
                    report["pruned"].append(child.name)
                    if not quiet:
                        print(f"  Removing link: {child.name}")
                    if not dry_run:
                        remove_link(child)
        if view_agent_root.exists() and not dry_run:
            shutil.rmtree(view_agent_root)
        return report

    desired_names = {s["name"] for s in selected_skills}
    if target_dir.is_dir():
        for child in sorted(target_dir.iterdir()):
            if child.name not in desired_names and points_into_repo(child):
                report["pruned"].append(child.name)
                if not quiet:
                    print(f"  Pruning unselected/stale link: {child.name}")
                if not dry_run:
                    remove_link(child)

    for skill in selected_skills:
        name = skill["name"]
        if agent_name in skill["overrides"]:
            src = build_agent_override_view(skill, agent_name, dry_run=dry_run)
            src_label = f".local-views/{agent_name}/{name} (merged override)"
        else:
            src = skill["root"]
            src_label = src.relative_to(REPO_ROOT).as_posix()
        dst = target_dir / name

        if dst.exists() or dst.is_symlink():
            if points_into_repo(dst):
                try:
                    if not dry_run and dst.resolve() == src.resolve():
                        report["ok"].append(name)
                        if not quiet:
                            print(f"  OK       {name} -> {src_label}")
                        continue
                except OSError:
                    pass
                report["updated"].append(name)
                if not quiet:
                    print(f"  Updating {name} -> {src_label}")
                if not dry_run:
                    remove_link(dst)
                create_dir_link(src, dst, dry_run=dry_run)
            elif force:
                report["updated"].append(name)
                if not quiet:
                    print(f"  Replacing (--force) {name} -> {src_label}")
                if not dry_run:
                    if is_reparse_or_symlink(dst):
                        remove_link(dst)
                    elif dst.is_dir():
                        shutil.rmtree(dst)
                    else:
                        dst.unlink()
                create_dir_link(src, dst, dry_run=dry_run)
            else:
                report["skippedConflicts"].append(str(dst))
                if not quiet:
                    print(
                        f"  SKIP     {name}: existing non-managed folder/link at {dst} "
                        f"(use --force to replace)"
                    )
        else:
            report["mounted"].append(name)
            if not quiet:
                print(f"  Mounting {name} -> {src_label}")
            create_dir_link(src, dst, dry_run=dry_run)

    return report


def load_json_or_empty(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"entries": []}
    raw = path.read_text(encoding="utf-8")
    if not raw.strip():
        return {"entries": []}
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError(f"Expected JSON object in {path}")
    return data


def normalize_entry_path(raw_path: str) -> str:
    return Path(raw_path).expanduser().resolve().as_posix()


def mount_antigravity_skills(
    selected_skills: list[dict[str, Any]],
    excluded_names: list[str],
    dry_run: bool,
    uninstall: bool,
    quiet: bool = False,
) -> dict[str, Any]:
    report: dict[str, Any] = {
        "configFile": str(ANTIGRAVITY_SKILLS_JSON),
        "selected": [s["name"] for s in selected_skills],
        "excluded": excluded_names,
        "legacyPluginConflicts": [],
    }
    if not quiet:
        print(f"\n[antigravity:skills] Config file: {ANTIGRAVITY_SKILLS_JSON}")

    config = load_json_or_empty(ANTIGRAVITY_SKILLS_JSON)
    entries: list[dict[str, Any]] = list(config.get("entries", []))

    main_portable = to_portable_path(SKILLS_DIR)
    main_norm = SKILLS_DIR.resolve().as_posix()
    view_dir = LOCAL_VIEWS_DIR / "antigravity"
    view_portable = to_portable_path(view_dir)
    view_norm = view_dir.resolve().as_posix()

    preserved = [
        e
        for e in entries
        if isinstance(e, dict)
        and isinstance(e.get("path"), str)
        and normalize_entry_path(e["path"]) not in (main_norm, view_norm)
    ]

    if uninstall:
        config["entries"] = preserved
        if not quiet:
            print(f"  Removing {main_portable} from skills.json")
        if not dry_run:
            if view_dir.exists():
                shutil.rmtree(view_dir)
            if ANTIGRAVITY_SKILLS_JSON.parent.exists():
                ANTIGRAVITY_SKILLS_JSON.write_text(
                    json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
                )
        return report

    overrides = [s for s in selected_skills if "antigravity" in s["overrides"]]
    override_names = [s["name"] for s in overrides]
    main_excludes = sorted(set(excluded_names) | set(override_names))

    new_entries = list(preserved)
    if overrides:
        if not quiet:
            print(f"  Building merged override views for: {', '.join(override_names)}")
        if not dry_run:
            if view_dir.exists():
                shutil.rmtree(view_dir)
            view_dir.mkdir(parents=True, exist_ok=True)
            for s in overrides:
                build_agent_override_view(s, "antigravity", dry_run=False)
        new_entries.append({"path": view_portable})
    elif view_dir.exists() and not dry_run:
        shutil.rmtree(view_dir)

    non_override_selected = [s for s in selected_skills if "antigravity" not in s["overrides"]]
    if non_override_selected:
        main_entry: dict[str, Any] = {"path": main_portable}
        if main_excludes:
            main_entry["exclude"] = main_excludes
        new_entries.append(main_entry)
        if not quiet:
            excl_note = f" (exclude: {', '.join(main_excludes)})" if main_excludes else ""
            print(f"  Registered entry: {main_portable}{excl_note}")

    if overrides and not quiet:
        print(f"  Registered override view: {view_portable}")

    for s in selected_skills:
        legacy_plugin = ANTIGRAVITY_PLUGINS_DIR / s["name"]
        if legacy_plugin.is_dir():
            report["legacyPluginConflicts"].append(str(legacy_plugin))
            if not quiet:
                print(
                    f"  NOTE     legacy Antigravity plugin also exists at {legacy_plugin}; "
                    f"disable/uninstall it in Customizations to avoid duplicates."
                )

    config["entries"] = new_entries
    if not dry_run:
        ANTIGRAVITY_SKILLS_JSON.parent.mkdir(parents=True, exist_ok=True)
        ANTIGRAVITY_SKILLS_JSON.write_text(
            json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    return report


def collect_antigravity_rule_sources(
    selected_skills: list[dict[str, Any]],
    *,
    include_maintainer_rules: bool,
) -> list[tuple[str, Path]]:
    sources: list[tuple[str, Path]] = []
    if include_maintainer_rules and RULES_DIR.is_dir():
        for md in sorted(RULES_DIR.glob("*.md")):
            if md.is_file():
                sources.append((md.name, md))
    for s in selected_skills:
        for md in s["agentRules"].get("antigravity", []):
            sources.append((md.name, md))
    return sources


def sync_antigravity_rules(
    selected_skills: list[dict[str, Any]],
    *,
    include_maintainer_rules: bool,
    dry_run: bool,
    uninstall: bool,
    quiet: bool = False,
) -> dict[str, Any]:
    report: dict[str, Any] = {"targetDir": str(ANTIGRAVITY_RULES_DIR), "installed": [], "removed": []}
    if not quiet:
        print(f"\n[antigravity:rules] Target directory: {ANTIGRAVITY_RULES_DIR}")
    sources = collect_antigravity_rule_sources(
        selected_skills, include_maintainer_rules=include_maintainer_rules
    )
    desired_names = {fname for fname, _ in sources}

    if uninstall:
        if ANTIGRAVITY_RULES_DIR.is_dir():
            for child in sorted(ANTIGRAVITY_RULES_DIR.iterdir()):
                if points_into_repo(child):
                    report["removed"].append(child.name)
                    if not quiet:
                        print(f"  Removing rule link: {child.name}")
                    if not dry_run:
                        child.unlink()
        return report

    if not dry_run:
        ANTIGRAVITY_RULES_DIR.mkdir(parents=True, exist_ok=True)

    # Prune managed rule links from this repo that are no longer desired (e.g., switching to --user)
    if ANTIGRAVITY_RULES_DIR.is_dir():
        for child in sorted(ANTIGRAVITY_RULES_DIR.iterdir()):
            if child.name not in desired_names and points_into_repo(child):
                report["removed"].append(child.name)
                if not quiet:
                    print(f"  Pruning unselected rule link: {child.name}")
                if not dry_run:
                    child.unlink()

    for filename, src in sources:
        dst = ANTIGRAVITY_RULES_DIR / filename
        report["installed"].append(filename)
        if dry_run:
            if not quiet:
                print(f"  [dry-run] sync {dst} <- {src.relative_to(REPO_ROOT).as_posix()}")
            continue
        if dst.is_symlink():
            try:
                if dst.resolve() == src.resolve():
                    if not quiet:
                        print(f"  OK       {filename}")
                    continue
            except OSError:
                pass
            dst.unlink()
        elif dst.exists():
            dst.unlink()
        link_or_copy_file(src, dst)
        if not quiet:
            print(f"  Linked   {filename} -> {src.relative_to(REPO_ROOT).as_posix()}")

    return report


def sync_codex_rule(dry_run: bool, uninstall: bool, quiet: bool = False) -> dict[str, Any]:
    report: dict[str, Any] = {"target": str(CODEX_AGENTS_MD), "action": "noop"}
    if not quiet:
        print(f"\n[codex:rule] Target: {CODEX_AGENTS_MD}")
    block_re = re.compile(
        re.escape(MANAGED_BLOCK_START) + r".*?" + re.escape(MANAGED_BLOCK_END),
        re.DOTALL,
    )

    existing = (
        CODEX_AGENTS_MD.read_text(encoding="utf-8", errors="replace")
        if CODEX_AGENTS_MD.is_file()
        else ""
    )

    if uninstall:
        if not block_re.search(existing):
            if not quiet:
                print("  Nothing to uninstall.")
            return report
        cleaned = block_re.sub("", existing).strip()
        report["action"] = "removed"
        if not quiet:
            print("  Removing managed block from ~/.codex/AGENTS.md (preserving user rules)")
        if not dry_run:
            CODEX_AGENTS_MD.write_text((cleaned + "\n") if cleaned else "", encoding="utf-8")
        return report

    if not SKILL_MGMT_RULE_SRC.is_file():
        if not quiet:
            print(f"  SKIP: template missing at {SKILL_MGMT_RULE_SRC}")
        return report

    body = strip_frontmatter(SKILL_MGMT_RULE_SRC.read_text(encoding="utf-8", errors="replace"))
    managed_block = f"{MANAGED_BLOCK_START}\n{body}\n{MANAGED_BLOCK_END}"

    if block_re.search(existing):
        updated = block_re.sub(lambda _: managed_block, existing)
        if updated == existing:
            report["action"] = "ok"
            if not quiet:
                print("  OK       managed block already up to date")
            return report
        action = "updated"
    else:
        prefix = existing.rstrip()
        updated = f"{prefix}\n\n{managed_block}\n" if prefix else f"{managed_block}\n"
        action = "appended"

    report["action"] = action
    if not quiet:
        print(f"  {action.capitalize()} managed block in ~/.codex/AGENTS.md (existing content preserved)")
    if not dry_run:
        CODEX_DIR.mkdir(parents=True, exist_ok=True)
        CODEX_AGENTS_MD.write_text(updated, encoding="utf-8")
    return report


def init_missing_secrets(
    selected_skills: list[dict[str, Any]],
    *,
    dry_run: bool,
    quiet: bool = False,
) -> list[dict[str, str]]:
    """Create missing local secret files from templates. STRICTLY NEVER overwrites existing files."""
    results: list[dict[str, str]] = []
    if not quiet:
        print("\n[secrets] Initializing missing local templates (existing files are NEVER overwritten)")

    for skill in selected_skills:
        manifest = skill["manifest"]
        tpl_rel = manifest.get("localSecretsTemplate")
        target_raw = manifest.get("localSecretsTarget")
        if not tpl_rel or not target_raw:
            continue
        tpl_path = skill["root"] / tpl_rel
        dst_path = Path(target_raw).expanduser()
        if not tpl_path.is_file():
            continue

        # Hard safety guardrail: NEVER overwrite an existing user secret/config file
        if dst_path.exists():
            results.append(
                {
                    "skill": skill["name"],
                    "target": str(dst_path),
                    "status": "SKIP_EXISTING",
                }
            )
            if not quiet:
                print(f"  KEEP     {skill['name']}: existing local file preserved at {dst_path}")
        else:
            results.append(
                {
                    "skill": skill["name"],
                    "target": str(dst_path),
                    "status": "WOULD_CREATE" if dry_run else "CREATED_FROM_TEMPLATE",
                }
            )
            if not quiet:
                tag = "[dry-run] create" if dry_run else "CREATED "
                print(f"  {tag} {skill['name']}: {dst_path} <- {tpl_rel} (fill in your local values)")
            if not dry_run:
                dst_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(tpl_path, dst_path)
    return results


def check_node_version() -> tuple[bool, str]:
    node_bin = shutil.which("node")
    if not node_bin:
        return False, "not found"
    try:
        out = subprocess.check_output([node_bin, "-v"], text=True).strip()
        m = re.match(r"v?(\d+)", out)
        major = int(m.group(1)) if m else 0
        return major >= 22, out
    except Exception as exc:
        return False, str(exc)


def run_verify(
    selected_skills: list[dict[str, Any]],
    *,
    agent: str,
    user_mode: bool,
) -> dict[str, Any]:
    """Verify mounts, SKILL.md readability, runtime dependencies, and local secrets."""
    checks: list[dict[str, Any]] = []
    warnings: list[str] = []
    errors: list[str] = []

    py_ver = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    has_openpyxl = importlib.util.find_spec("openpyxl") is not None
    node_ok, node_ver = check_node_version()

    # Antigravity mount check
    agy_entries: list[dict[str, Any]] = []
    if ANTIGRAVITY_SKILLS_JSON.is_file():
        try:
            agy_entries = list(load_json_or_empty(ANTIGRAVITY_SKILLS_JSON).get("entries", []))
        except Exception as exc:
            errors.append(f"Failed to parse {ANTIGRAVITY_SKILLS_JSON}: {exc}")

    def is_mounted_in_antigravity(skill: dict[str, Any]) -> bool:
        name = skill["name"]
        has_override = "antigravity" in skill["overrides"]
        expected_root = (
            (LOCAL_VIEWS_DIR / "antigravity").resolve().as_posix()
            if has_override
            else SKILLS_DIR.resolve().as_posix()
        )
        for entry in agy_entries:
            if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
                continue
            if normalize_entry_path(entry["path"]) == expected_root:
                excluded = set(entry.get("exclude", []) or [])
                if name not in excluded:
                    skill_md = Path(expected_root) / name / "SKILL.md"
                    return skill_md.is_file()
        return False

    for skill in selected_skills:
        name = skill["name"]
        item: dict[str, Any] = {"skill": name, "mounts": {}, "runtime": {}, "secrets": None}

        if agent in ("all", "antigravity"):
            ok = is_mounted_in_antigravity(skill)
            item["mounts"]["antigravity"] = "ok" if ok else "missing"
            if not ok:
                errors.append(f"[antigravity] {name} is not mounted in {ANTIGRAVITY_SKILLS_JSON}")
            if (ANTIGRAVITY_PLUGINS_DIR / name).is_dir():
                warnings.append(
                    f"[antigravity] legacy plugin exists for '{name}' at {ANTIGRAVITY_PLUGINS_DIR / name}"
                )

        if agent in ("all", "codex") and (agent == "codex" or CODEX_DIR.exists()):
            dst = CODEX_SKILLS_DIR / name
            if points_into_repo(dst) and (dst / "SKILL.md").is_file():
                item["mounts"]["codex"] = "ok"
            elif dst.exists():
                item["mounts"]["codex"] = "conflict_non_managed"
                warnings.append(f"[codex] {name} at {dst} is an existing non-managed folder (not linked to repo)")
            else:
                item["mounts"]["codex"] = "missing"
                errors.append(f"[codex] {name} is missing from {CODEX_SKILLS_DIR}")

        if agent == "claude" or (agent == "all" and CLAUDE_DIR.exists()):
            dst = CLAUDE_SKILLS_DIR / name
            if points_into_repo(dst) and (dst / "SKILL.md").is_file():
                item["mounts"]["claude"] = "ok"
            elif dst.exists():
                item["mounts"]["claude"] = "conflict_non_managed"
                warnings.append(f"[claude] {name} at {dst} is an existing non-managed folder")
            else:
                item["mounts"]["claude"] = "missing"
                errors.append(f"[claude] {name} is missing from {CLAUDE_SKILLS_DIR}")

        # Runtime dependency checks per skill
        if name == "amazon-ads-optimizer":
            item["runtime"]["python"] = py_ver
            item["runtime"]["openpyxl"] = has_openpyxl
            if sys.version_info < (3, 10):
                warnings.append(
                    f"[runtime] amazon-ads-optimizer requires Python 3.10+ (current launcher: {py_ver})"
                )
            if not has_openpyxl:
                warnings.append(
                    "[runtime] amazon-ads-optimizer Excel (.xlsx) input needs openpyxl (pip install openpyxl); CSV works without it"
                )
        elif name == "web-access":
            item["runtime"]["node"] = node_ver
            item["runtime"]["node22Plus"] = node_ok
            if not node_ok:
                warnings.append(
                    f"[runtime] web-access CDP scripts require Node.js 22+ (detected: {node_ver})"
                )
        else:
            item["runtime"]["python"] = py_ver

        # Local secrets presence check
        target_raw = skill["manifest"].get("localSecretsTarget")
        if target_raw:
            secret_path = Path(target_raw).expanduser()
            exists = secret_path.is_file()
            item["secrets"] = {"path": str(secret_path), "exists": exists}
            if not exists:
                warnings.append(
                    f"[secrets] {name} local config not found at {secret_path} "
                    f"(run setup.py --init-secrets to create from template without overwriting)"
                )

        checks.append(item)

    return {
        "ok": len(errors) == 0,
        "mode": "user" if user_mode else "maintainer",
        "repo": str(REPO_ROOT),
        "platform": platform.system(),
        "skills": checks,
        "errors": errors,
        "warnings": warnings,
    }


def print_status(all_skills: list[dict[str, Any]], as_json: bool) -> None:
    agy_mounted = False
    if ANTIGRAVITY_SKILLS_JSON.is_file():
        try:
            cfg = load_json_or_empty(ANTIGRAVITY_SKILLS_JSON)
            norms = {
                normalize_entry_path(e["path"])
                for e in cfg.get("entries", [])
                if isinstance(e, dict) and isinstance(e.get("path"), str)
            }
            agy_mounted = (
                SKILLS_DIR.resolve().as_posix() in norms
                or (LOCAL_VIEWS_DIR / "antigravity").resolve().as_posix() in norms
            )
        except Exception:
            agy_mounted = False

    rule_sources = collect_antigravity_rule_sources(all_skills, include_maintainer_rules=True)
    agy_rules_ok = sum(1 for fname, _ in rule_sources if (ANTIGRAVITY_RULES_DIR / fname).exists())
    codex_links = [s["name"] for s in all_skills if points_into_repo(CODEX_SKILLS_DIR / s["name"])]
    codex_rule_ok = (
        CODEX_AGENTS_MD.is_file()
        and MANAGED_BLOCK_START in CODEX_AGENTS_MD.read_text(encoding="utf-8", errors="replace")
    )

    if as_json:
        payload = {
            "repo": str(REPO_ROOT),
            "platform": platform.system(),
            "machine": platform.machine(),
            "skills": [
                {
                    "name": s["name"],
                    "overrides": sorted(s["overrides"].keys()),
                    "rules": sorted(s["agentRules"].keys()),
                    "localSecretsTarget": s["manifest"].get("localSecretsTarget"),
                }
                for s in all_skills
            ],
            "antigravity": {
                "mounted": agy_mounted,
                "rulesInstalled": agy_rules_ok,
                "rulesTotal": len(rule_sources),
            },
            "codex": {
                "mountedCount": len(codex_links),
                "totalSkills": len(all_skills),
                "maintainerRuleInstalled": codex_rule_ok,
            },
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    print(f"Repository: {REPO_ROOT}")
    print(f"Platform:   {platform.system()} ({platform.machine()})")
    print(f"Skills ({len(all_skills)}):")
    for s in all_skills:
        ov = ", ".join(sorted(s["overrides"].keys())) or "none (shared SKILL.md)"
        rules_info = ", ".join(sorted(s["agentRules"].keys()))
        extra = f", rules: {rules_info}" if rules_info else ""
        print(f"  - {s['name']} (agent overrides: {ov}{extra})")

    print(
        f"\nAntigravity: skills={'MOUNTED' if agy_mounted else 'MISSING'}, "
        f"rules={agy_rules_ok}/{len(rule_sources)} installed ({ANTIGRAVITY_RULES_DIR})"
    )
    print(
        f"Codex:       skills={len(codex_links)}/{len(all_skills)} mounted, "
        f"maintainerRule={'INSTALLED' if codex_rule_ok else 'NOT INSTALLED'} ({CODEX_AGENTS_MD})"
    )
    if CLAUDE_DIR.exists():
        claude_links = [
            s["name"] for s in all_skills if points_into_repo(CLAUDE_SKILLS_DIR / s["name"])
        ]
        print(f"Claude Code: skills={len(claude_links)}/{len(all_skills)} mounted ({CLAUDE_SKILLS_DIR})")
    else:
        print("Claude Code: not installed (skipped)")


def main() -> int:
    configure_utf8_output()
    parser = argparse.ArgumentParser(
        description="Mount agent-skills and rules into Antigravity, Codex, and Claude Code."
    )
    parser.add_argument(
        "--agent",
        choices=["all", "antigravity", "codex", "claude"],
        default="all",
        help="Target agent to configure (default: all)",
    )
    parser.add_argument(
        "--maintainer",
        action="store_true",
        help="Maintainer mode (repo owner / fork owner): also install sync-skills and global sync rules",
    )
    parser.add_argument(
        "--user",
        action="store_true",
        help="Consumer mode (now the default; kept for backwards compatibility)",
    )
    parser.add_argument(
        "--only",
        help="Comma-separated list of specific skills to mount (e.g., listing-generation,web-access)",
    )
    parser.add_argument(
        "--init-secrets",
        action="store_true",
        help="Initialize missing local secret/config files from templates (NEVER overwrites existing files)",
    )
    parser.add_argument("--verify", action="store_true", help="Verify mounts, runtimes, and local secrets")
    parser.add_argument("--status", action="store_true", help="Show current mount and rule status")
    parser.add_argument("--json", action="store_true", help="Output structured JSON where supported")
    parser.add_argument("--rules-only", action="store_true", help="Only install/update rules")
    parser.add_argument("--no-rules", action="store_true", help="Skip all rule installations")
    parser.add_argument("--dry-run", action="store_true", help="Preview actions without changing files")
    parser.add_argument("--force", action="store_true", help="Replace existing non-managed links/folders")
    parser.add_argument("--uninstall", action="store_true", help="Remove mounts and rules managed by this repo")

    args = parser.parse_args()
    all_skills = discover_skills()

    if args.status:
        print_status(all_skills, as_json=args.json)
        return 0

    maintainer_mode = bool(args.maintainer and not args.user)
    selected_skills, excluded_names = select_skills(
        all_skills, maintainer_mode=maintainer_mode, only_arg=args.only
    )
    selected_names = {s["name"] for s in selected_skills}
    include_maintainer_rules = maintainer_mode or ("sync-skills" in selected_names)

    if args.verify:
        result = run_verify(
            selected_skills, agent=args.agent, user_mode=not include_maintainer_rules
        )
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(f"Verify mode: {result['mode']} ({len(result['skills'])} skill(s))")
            for item in result["skills"]:
                mounts = ", ".join(f"{k}={v}" for k, v in item["mounts"].items())
                print(f"  - {item['skill']}: {mounts}")
            if result["warnings"]:
                print(f"\nWarnings ({len(result['warnings'])}):")
                for w in result["warnings"]:
                    print(f"  WARN  {w}")
            if result["errors"]:
                print(f"\nErrors ({len(result['errors'])}):")
                for e in result["errors"]:
                    print(f"  FAIL  {e}")
            else:
                print("\nVerify PASSED.")
        return 0 if result["ok"] else 1

    quiet = args.json
    summary: dict[str, Any] = {
        "mode": "maintainer" if include_maintainer_rules else "user",
        "dryRun": args.dry_run,
        "selectedSkills": [s["name"] for s in selected_skills],
        "excludedSkills": excluded_names,
    }

    if not args.rules_only:
        if args.agent in ("all", "antigravity"):
            summary["antigravitySkills"] = mount_antigravity_skills(
                selected_skills,
                excluded_names,
                dry_run=args.dry_run,
                uninstall=args.uninstall,
                quiet=quiet,
            )
        if args.agent in ("all", "codex"):
            summary["codexSkills"] = mount_link_based_agent(
                "codex",
                CODEX_SKILLS_DIR,
                selected_skills,
                dry_run=args.dry_run,
                force=args.force,
                uninstall=args.uninstall,
                quiet=quiet,
            )
        if args.agent == "claude" or (args.agent == "all" and CLAUDE_DIR.exists()):
            summary["claudeSkills"] = mount_link_based_agent(
                "claude",
                CLAUDE_SKILLS_DIR,
                selected_skills,
                dry_run=args.dry_run,
                force=args.force,
                uninstall=args.uninstall,
                quiet=quiet,
            )

    if not args.no_rules:
        if args.agent in ("all", "antigravity"):
            summary["antigravityRules"] = sync_antigravity_rules(
                selected_skills,
                include_maintainer_rules=include_maintainer_rules,
                dry_run=args.dry_run,
                uninstall=args.uninstall,
                quiet=quiet,
            )
        if args.agent in ("all", "codex") and (include_maintainer_rules or args.uninstall):
            summary["codexRule"] = sync_codex_rule(
                dry_run=args.dry_run, uninstall=args.uninstall, quiet=quiet
            )

    if args.init_secrets and not args.uninstall:
        summary["secretsInit"] = init_missing_secrets(
            selected_skills, dry_run=args.dry_run, quiet=quiet
        )

    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print("\nDone.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
