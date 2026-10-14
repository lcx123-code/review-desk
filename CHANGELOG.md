# Changelog

## 0.3.0 — 2026-10-14

- Add bounded loopback preview checks, explicit URL updates and recovery from project configuration.
- Persist feedback preparation, submission, uncertain outcomes and explicit agent receipt; prevent duplicate submission of unchanged payloads.
- Keep cached UI templates free of task snapshots and bind tasks through host notifications.
- Keep review fallback services alive across MCP connection changes; verify runtime version before reusing a service.
- Always retain a direct link and selectable/copyable address after host open acceptance, because acceptance does not prove navigation occurred.
- Verify native review-card expansion, real preview rendering by address, feedback queuing, agent receipt and revision-preserving fixes in an isolated Codex desktop task. Automatic preview opening remains a documented host compatibility issue.

- Route embedded previews through the host browser using MCP Apps `ui/open-link`, instead of creating an iframe blocked by the current Codex CSP.
- Preserve preview feedback in the same review task using a described element or region; do not invent viewport dimensions or coordinates for the host browser.
- Keep visible open actions when a frame fails, CSP blocks it, or the host rejects an open-link request; retain standalone iframe previews.
- Change the UI resource URI so reloaded clients can fetch the updated component.

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
