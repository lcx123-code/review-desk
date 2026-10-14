---
name: review-setup
description: Configure the local Review Desk plugin for a coding project, including review snapshots, an optional live web preview URL, and opt-in lifecycle hooks.
---

# Set up Review Desk

Identify the user's coding project from the attached workspace. Validate Python 3.10+ and Git. Prefer the review-desk MCP tools; the bundled CLI is `../../scripts/review_desk.py` relative to this skill directory.

Run `python <script> configure --project <absolute-project-path>`. This writes `.codex/review-desk.json` with `enabled: true` so the bundled UserPromptSubmit and Stop hooks can snapshot and refresh that project. If already configured, preserve its preferences; supply `--preview-url <url>` only when a real running web server URL is known. The configuration does not launch project commands. In a non-Git project add an explicit list of project-relative paths under `files`, including planned additions.

For automatic hook execution the user must review/trust the bundled definitions through Codex's hook UI (or `/hooks` in the CLI); installation alone does not trust them. Do not bypass the trust flow. Until hooks are available, the review-changes skill can explicitly run begin/refresh/open tools.

Tell the user to invoke `$review-changes` with their coding task. Code review works for local text files; live interactive previews target Web projects. If their client renders MCP Apps, `open_review` supplies the embedded panel. Otherwise the local URL supplies the same review UI and copy/export feedback. Do not describe a local MCP server as a hosted public plugin or claim it is visible in the current chat before it is loaded.

For v0.3, keep the configured preview URL current so `check_preview` can recover changed ports. Use `open_review_panel` for the explicit task picker and verify the displayed runtime version after updating the plugin. A host's accepted message is submission, not proof that Codex read feedback; the agent confirms receipt using the delivery ID after reading it.
