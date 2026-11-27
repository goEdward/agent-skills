#!/usr/bin/env python3
"""Cross-platform setup script to mount agent-skills & global rules into Antigravity, Codex, and Claude Code.

Supports macOS, Windows, and Linux using only the Python 3 standard library.
Works regardless of which local directory the GitHub repository is cloned into.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent
SKILLS_DIR = REPO_ROOT / "skills"
RULES_DIR = REPO_ROOT / "rules"
SKILL_MGMT_RULE_SRC = RULES_DIR / "skills-management.md"
LOCAL_VIEWS_DIR = REPO_ROOT / ".local-views"

ANTIGRAVITY_CONFIG_DIR = Path.home() / ".gemini" / "config"
ANTIGRAVITY_SKILLS_JSON = ANTIGRAVITY_CONFIG_DIR / "skills.json"
ANTIGRAVITY_RULES_DIR = ANTIGRAVITY_CONFIG_DIR / "rules"
ANTIGRAVITY_RULE_DST = ANTIGRAVITY_RULES_DIR / "skills-management.md"

CODEX_DIR = Path.home() / ".codex"
CODEX_SKILLS_DIR = CODEX_DIR / "skills"
CODEX_AGENTS_MD = CODEX_DIR / "AGENTS.md"

CLAUDE_DIR = Path.home() / ".claude"
CLAUDE_SKILLS_DIR = CLAUDE_DIR / "skills"

MANAGED_BLOCK_START = "<!-- agent-skills:start -->"
MANAGED_BLOCK_END = "<!-- agent-skills:end -->"


def to_portable_path(path: Path) -> str:
    """Convert path under HOME to ~/... when possible, otherwise POSIX path."""
    resolved = path.resolve()
    home = Path.home().resolve()
    try:
        rel = resolved.relative_to(home)
        return "~/" + rel.as_posix()
    except ValueError:
        return resolved.as_posix()


def strip_frontmatter(md_text: str) -> str:
    """Strip leading YAML frontmatter block for agents that use plain AGENTS.md."""
    lines = md_text.splitlines()
    if lines and lines[0].strip() == "---":
        for idx in range(1, len(lines)):
            if lines[idx].strip() == "---":
                return "\n".join(lines[idx + 1 :]).strip()
    return md_text.strip()


def discover_skills() -> list[dict[str, Any]]:
    """Discover skill folders under <repo>/skills/<name>/."""
    items: list[dict[str, Any]] = []
    if not SKILLS_DIR.is_dir():
        return items
    for sub in sorted(SKILLS_DIR.iterdir()):
        if not sub.is_dir() or sub.name.startswith("."):
            continue
        main_md = sub / "SKILL.md"
        if not main_md.is_file():
            continue
        overrides: dict[str, Path] = {}
        for agent_name in ("antigravity", "codex", "claude"):
            override_dir = sub / "agents" / agent_name
            if (override_dir / "SKILL.md").is_file():
                overrides[agent_name] = override_dir
        items.append({"name": sub.name, "root": sub, "overrides": overrides})
    return items


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
        print(f"  [dry-run] link {dst} -> {src}")
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


def mount_link_based_agent(
    agent_name: str,
    target_dir: Path,
    skills: list[dict[str, Any]],
    dry_run: bool,
    force: bool,
    uninstall: bool,
) -> None:
    print(f"\n[{agent_name}:skills] Target directory: {target_dir}")
    if uninstall:
        if not target_dir.is_dir():
            print("  Nothing to uninstall.")
            return
        removed = 0
        for child in sorted(target_dir.iterdir()):
            if points_into_repo(child):
                print(f"  Removing link: {child.name}")
                if not dry_run:
                    remove_link(child)
                removed += 1
        print(f"  Removed {removed} managed link(s).")
        return

    desired_names = {s["name"] for s in skills}
    if target_dir.is_dir():
        for child in sorted(target_dir.iterdir()):
            if child.name not in desired_names and points_into_repo(child):
                print(f"  Pruning stale link: {child.name}")
                if not dry_run:
                    remove_link(child)

    for skill in skills:
        name = skill["name"]
        src: Path = skill["overrides"].get(agent_name, skill["root"])
        dst = target_dir / name

        if dst.exists() or dst.is_symlink():
            if points_into_repo(dst):
                try:
                    if dst.resolve() == src.resolve():
                        print(f"  OK       {name} -> {src.relative_to(REPO_ROOT).as_posix()}")
                        continue
                except OSError:
                    pass
                print(f"  Updating {name} -> {src.relative_to(REPO_ROOT).as_posix()}")
                if not dry_run:
                    remove_link(dst)
                create_dir_link(src, dst, dry_run=dry_run)
            elif force:
                print(f"  Replacing (--force) {name} -> {src.relative_to(REPO_ROOT).as_posix()}")
                if not dry_run:
                    if is_reparse_or_symlink(dst):
                        remove_link(dst)
                    elif dst.is_dir():
                        shutil.rmtree(dst)
                    else:
                        dst.unlink()
                create_dir_link(src, dst, dry_run=dry_run)
            else:
                print(
                    f"  SKIP     {name}: existing non-managed folder/link at {dst} "
                    f"(use --force to replace)"
                )
        else:
            print(f"  Mounting {name} -> {src.relative_to(REPO_ROOT).as_posix()}")
            create_dir_link(src, dst, dry_run=dry_run)


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


def mount_antigravity_skills(skills: list[dict[str, Any]], dry_run: bool, uninstall: bool) -> None:
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
        print(f"  Removing {main_portable} from skills.json")
        if not dry_run:
            if view_dir.exists():
                shutil.rmtree(view_dir)
            if ANTIGRAVITY_SKILLS_JSON.parent.exists():
                ANTIGRAVITY_SKILLS_JSON.write_text(
                    json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
                )
        return

    overrides = [s for s in skills if "antigravity" in s["overrides"]]
    new_entries = list(preserved)

    if overrides:
        override_names = [s["name"] for s in overrides]
        print(f"  Preparing agent-specific overrides: {', '.join(override_names)}")
        if not dry_run:
            if view_dir.exists():
                shutil.rmtree(view_dir)
            view_dir.mkdir(parents=True, exist_ok=True)
            for s in overrides:
                create_dir_link(s["overrides"]["antigravity"], view_dir / s["name"], dry_run=False)
        new_entries.append({"path": view_portable})
        new_entries.append({"path": main_portable, "exclude": override_names})
    else:
        if view_dir.exists() and not dry_run:
            shutil.rmtree(view_dir)
        new_entries.append({"path": main_portable})

    config["entries"] = new_entries
    print(f"  Registered entry: {main_portable}")
    if overrides:
        print(f"  Registered override view: {view_portable}")

    if not dry_run:
        ANTIGRAVITY_SKILLS_JSON.parent.mkdir(parents=True, exist_ok=True)
        ANTIGRAVITY_SKILLS_JSON.write_text(
            json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )


def sync_antigravity_rule(dry_run: bool, uninstall: bool) -> None:
    print(f"\n[antigravity:rule] Target: {ANTIGRAVITY_RULE_DST}")
    if uninstall:
        if ANTIGRAVITY_RULE_DST.is_symlink() or ANTIGRAVITY_RULE_DST.is_file():
            if points_into_repo(ANTIGRAVITY_RULE_DST) or ANTIGRAVITY_RULE_DST.is_file():
                print("  Removing skills-management.md")
                if not dry_run:
                    ANTIGRAVITY_RULE_DST.unlink()
        else:
            print("  Nothing to uninstall.")
        return

    if not SKILL_MGMT_RULE_SRC.is_file():
        print(f"  SKIP: template missing at {SKILL_MGMT_RULE_SRC}")
        return

    if dry_run:
        print(f"  [dry-run] sync {ANTIGRAVITY_RULE_DST} <- {SKILL_MGMT_RULE_SRC}")
        return

    ANTIGRAVITY_RULES_DIR.mkdir(parents=True, exist_ok=True)
    if ANTIGRAVITY_RULE_DST.is_symlink():
        try:
            if ANTIGRAVITY_RULE_DST.resolve() == SKILL_MGMT_RULE_SRC.resolve():
                print("  OK       linked to repository rule template")
                return
        except OSError:
            pass
        ANTIGRAVITY_RULE_DST.unlink()
    elif ANTIGRAVITY_RULE_DST.exists():
        # Overwrite only if managed copy or identical; replace with symlink/copy
        ANTIGRAVITY_RULE_DST.unlink()

    if platform.system() == "Windows":
        try:
            os.symlink(SKILL_MGMT_RULE_SRC, ANTIGRAVITY_RULE_DST)
            print("  Linked   skills-management.md -> rules/skills-management.md")
            return
        except OSError:
            shutil.copyfile(SKILL_MGMT_RULE_SRC, ANTIGRAVITY_RULE_DST)
            print("  Copied   rules/skills-management.md (Windows symlink fallback)")
            return
    else:
        os.symlink(SKILL_MGMT_RULE_SRC, ANTIGRAVITY_RULE_DST)
        print("  Linked   skills-management.md -> rules/skills-management.md")


def sync_codex_rule(dry_run: bool, uninstall: bool) -> None:
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
            print("  Nothing to uninstall.")
            return
        cleaned = block_re.sub("", existing).strip()
        print("  Removing managed block from ~/.codex/AGENTS.md (preserving user rules)")
        if not dry_run:
            CODEX_AGENTS_MD.write_text((cleaned + "\n") if cleaned else "", encoding="utf-8")
        return

    if not SKILL_MGMT_RULE_SRC.is_file():
        print(f"  SKIP: template missing at {SKILL_MGMT_RULE_SRC}")
        return

    body = strip_frontmatter(SKILL_MGMT_RULE_SRC.read_text(encoding="utf-8", errors="replace"))
    managed_block = f"{MANAGED_BLOCK_START}\n{body}\n{MANAGED_BLOCK_END}"

    if block_re.search(existing):
        updated = block_re.sub(managed_block, existing)
        if updated == existing:
            print("  OK       managed block already up to date")
            return
        action = "Updating"
    else:
        prefix = existing.rstrip()
        updated = f"{prefix}\n\n{managed_block}\n" if prefix else f"{managed_block}\n"
        action = "Appending"

    print(f"  {action} managed block in ~/.codex/AGENTS.md (existing content preserved)")
    if not dry_run:
        CODEX_DIR.mkdir(parents=True, exist_ok=True)
        CODEX_AGENTS_MD.write_text(updated, encoding="utf-8")


def print_status(skills: list[dict[str, Any]]) -> None:
    print(f"Repository: {REPO_ROOT}")
    print(f"Platform:   {platform.system()} ({platform.machine()})")
    print(f"Skills ({len(skills)}):")
    for s in skills:
        ov = ", ".join(sorted(s["overrides"].keys())) or "none (shared SKILL.md)"
        print(f"  - {s['name']} (agent overrides: {ov})")

    agy_mounted = False
    if ANTIGRAVITY_SKILLS_JSON.is_file():
        try:
            cfg = load_json_or_empty(ANTIGRAVITY_SKILLS_JSON)
            norms = {
                normalize_entry_path(e["path"])
                for e in cfg.get("entries", [])
                if isinstance(e, dict) and isinstance(e.get("path"), str)
            }
            agy_mounted = SKILLS_DIR.resolve().as_posix() in norms
        except Exception:
            agy_mounted = False
    agy_rule_ok = ANTIGRAVITY_RULE_DST.is_file() or ANTIGRAVITY_RULE_DST.is_symlink()

    codex_links = [s["name"] for s in skills if points_into_repo(CODEX_SKILLS_DIR / s["name"])]
    codex_rule_ok = (
        CODEX_AGENTS_MD.is_file()
        and MANAGED_BLOCK_START in CODEX_AGENTS_MD.read_text(encoding="utf-8", errors="replace")
    )

    print(
        f"\nAntigravity: skills={'MOUNTED' if agy_mounted else 'MISSING'}, "
        f"rule={'INSTALLED' if agy_rule_ok else 'MISSING'} ({ANTIGRAVITY_RULE_DST})"
    )
    print(
        f"Codex:       skills={len(codex_links)}/{len(skills)} mounted, "
        f"rule={'INSTALLED' if codex_rule_ok else 'MISSING'} ({CODEX_AGENTS_MD})"
    )
    if CLAUDE_DIR.exists():
        claude_links = [
            s["name"] for s in skills if points_into_repo(CLAUDE_SKILLS_DIR / s["name"])
        ]
        print(f"Claude Code: skills={len(claude_links)}/{len(skills)} mounted ({CLAUDE_SKILLS_DIR})")
    else:
        print(f"Claude Code: not installed (skipped)")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Mount agent-skills and global rules into Antigravity, Codex, and Claude Code."
    )
    parser.add_argument(
        "--agent",
        choices=["all", "antigravity", "codex", "claude"],
        default="all",
        help="Target agent to configure (default: all)",
    )
    parser.add_argument("--status", action="store_true", help="Show current mount and rule status")
    parser.add_argument("--rules-only", action="store_true", help="Only install/update global rules")
    parser.add_argument("--no-rules", action="store_true", help="Skip global rules installation")
    parser.add_argument("--dry-run", action="store_true", help="Preview actions without changing files")
    parser.add_argument("--force", action="store_true", help="Replace existing non-managed links/folders")
    parser.add_argument("--uninstall", action="store_true", help="Remove mounts and rules managed by this repo")

    args = parser.parse_args()
    skills = discover_skills()

    if args.status:
        print_status(skills)
        return 0

    if not args.rules_only:
        if args.agent in ("all", "antigravity"):
            mount_antigravity_skills(skills, dry_run=args.dry_run, uninstall=args.uninstall)
        if args.agent in ("all", "codex"):
            mount_link_based_agent(
                "codex",
                CODEX_SKILLS_DIR,
                skills,
                dry_run=args.dry_run,
                force=args.force,
                uninstall=args.uninstall,
            )
        if args.agent == "claude" or (args.agent == "all" and CLAUDE_DIR.exists()):
            mount_link_based_agent(
                "claude",
                CLAUDE_SKILLS_DIR,
                skills,
                dry_run=args.dry_run,
                force=args.force,
                uninstall=args.uninstall,
            )

    if not args.no_rules:
        if args.agent in ("all", "antigravity"):
            sync_antigravity_rule(dry_run=args.dry_run, uninstall=args.uninstall)
        if args.agent in ("all", "codex"):
            sync_codex_rule(dry_run=args.dry_run, uninstall=args.uninstall)

    print("\nDone.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
