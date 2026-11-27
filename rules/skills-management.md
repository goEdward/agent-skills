---
trigger: model_decision
description: Manage, create, or update personal agent skills using the GitHub-synced multi-platform skill repository and keep secrets outside git.
---

# Personal Skill Management Rule

When creating, editing, or refactoring a personal reusable skill (for Antigravity, Codex, or Claude Code):

1. **Always place skills in the GitHub skill repository** under `<skills-repo>/skills/<skill-name>/` (one self-contained folder per skill) rather than scattering files directly inside `~/.gemini/config/skills/` or `~/.codex/skills/`.
   - Locate `<skills-repo>` dynamically via the loaded `sync-skills` skill path (its enclosing Git root), `~/.gemini/config/skills.json`, or `~/.config/agent-secrets/sync-skills.env` (`SKILLS_REPO_DIR`). Never hardcode a machine-specific absolute path inside repository files.
2. **Follow the repository folder layout**:
   - Generic entrypoint: `<skills-repo>/skills/<skill-name>/SKILL.md` (YAML frontmatter `name` must match `<skill-name>`, `description` non-empty, include `metadata.repository` & `metadata.source_path` so consumers can update directly via AI).
   - Supporting files: `README.md` (noting source repo & non-overwrite privacy policy), `skill.manifest.json` (with `repository` and `sourcePath`), `scripts/` (cross-platform Python/Node), `references/`, `templates/`.
   - Optional overrides (keep empty with `.gitkeep` when unused): `agents/{antigravity,codex,claude}/`, `platforms/{mac,win}/`.
3. **Separate privacy and secrets from Git**:
   - Never put API keys, tokens, personal emails, real usernames/local paths, or private business data inside `<skills-repo>`.
   - Put only field templates in `<skills-repo>/skills/<skill-name>/templates/config.example.env`, and instruct users/scripts to read real values from `~/.config/agent-secrets/<skill-name>.env` (outside the Git repository).
4. **Refresh mounts and prompt to sync**:
   - After adding or renaming a skill (or adding an agent override), run `python3 <skills-repo>/setup.py` (or `python <skills-repo>\setup.py` on Windows) so local agents pick it up immediately.
   - After finishing skill edits, ask the user whether to run `sync-skills` to audit privacy and push updates to GitHub (never push silently without confirmation).
