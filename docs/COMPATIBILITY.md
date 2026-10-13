# Compatibility and validation

## Verified locally — 2026-10-13

- 14 backend tests: task baselines, file scope, revisions, feedback, approvals, local HTTP access control, task listings, MCP handshake and UI resources.
- 23 frontend integration checks: a real local API with a simulated MCP host, review interactions, explicit task selection, pending filtering, duplicate-send protection, failed-send copy fallback, split/single/checks views, keyboard splitter limits, feedback drawer state, file search/navigation and actual code context.
- Frontend formatting check using Prettier 3.6.2.
- Real Edge browser: explicit task selection, code-comment persistence and navigation, resolved-comment filtering, embedded web preview, desktop/mobile widths, feedback drawer controls, keyboard splitter adjustment and 390px layout with no page overflow. Screenshots were visually inspected; no page JavaScript errors were reported.

Local backend execution used Python 3.9.13; the supported installation target remains Python 3.10+. Frontend execution used Node.js 24.16.0. CI definitions target Windows and Linux but remote CI has not run yet.

## Requires actual client acceptance

- Visibility of the `thread` extension entrypoint as a native button.
- Sending feedback into the actual current Codex conversation.
- Localhost preview embedding under the client's CSP and iframe restrictions.

The current Codex host has exposed Review Desk panel context during use, but that does not establish end-to-end feedback delivery or permanent button placement. The new v0.2.0 tool declarations require plugin reload before validation.

## Supported fallback

The standalone local UI saves feedback and offers copy/export. It cannot initiate a Codex turn. Client connection failures preserve saved comments and expose the same copy fallback.

Interactive previews target web projects. Non-web projects still receive code diffs and run instructions. Sites forbidding iframe embedding must be opened independently. Interface comments record position, description, and viewport dimensions rather than cross-origin DOM inspection.

Snapshots capture the task scope, including simultaneous edits by other processes during that task. They are bounded to 2,500 files and 20 MiB of text; oversized or binary data is represented by fingerprints and cannot receive complete UI approval.

## Client acceptance checklist

Install/reload the plugin, attach a small web project, run `$review-setup`, and request a task with `$review-changes`. Open the panel, add code and preview comments, send feedback, confirm that Codex receives it in the intended chat, then verify fixes by refreshing the same review session. Separately test the native entrypoint and disconnected copy mode.
