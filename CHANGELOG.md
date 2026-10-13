# Changelog

## 0.2.0 — 2026-10-13

- Add an explicit review-task picker and a thread extension entrypoint declaration.
- Show conversation bridge status and retain a copy fallback on send failure.
- Prevent duplicate feedback submissions and empty feedback messages.
- Add pending-feedback filtering and navigation to code/preview comment locations.
- Refresh the UI with neutral surfaces and deep green accents, syntax highlighting, search, and review progress.
- Show code and running preview side by side with a draggable and keyboard-accessible divider.
- Replace the permanent file sidebar with a searchable dropdown; add a collapsible feedback drawer with actual code context.
- Add a portable frontend integration runner, backend coverage, and release packaging.

Native entrypoint visibility and sending feedback to an actual current Codex chat still require client acceptance testing. Automated bridge checks use a simulated host.

## 0.1.0 — 2026-10-12

- Local task-start snapshots, before/after diffs, web previews, and revision-aware feedback.
- MCP Apps panel, token-protected loopback fallback, optional project hooks, and skills.
