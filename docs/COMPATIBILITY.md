# Compatibility and validation

## v0.3.0 local validation — 2026-10-15

- 17 backend tests: task baselines, file scope, revisions, feedback, approvals, access control, MCP resources, preview health/recovery, delivery persistence and detached fallback services.
- 34 frontend integration checks using a real local API and a simulated MCP host: review interactions, task selection, responsive layouts, feedback state transitions, explicit agent acknowledgement, rejection/timeout handling, preview recovery and visible fallback after accepted open requests.
- Frontend formatting with Prettier 3.6.2.

Backend execution used Python 3.9.13; the supported installation target remains Python 3.10+. Frontend execution used Node.js 24.16.0. Automated bridge checks do not establish native client behavior. [The prior v0.2.0 CI run](https://github.com/lcx123-code/review-desk/actions/runs/38365564750) passed on Windows/Linux with Python 3.10/3.12 and Node.js 22; it does not validate v0.3.0.

## Native Codex desktop acceptance — 2026-10-15

Tested on Windows with Codex 26.1007.9446.0, the installed v0.3.0 MCP runtime, and an isolated timer project:

- An actual review card expanded into a Review Desk tab beside the existing conversation.
- Preview health detected a stopped service, then reported the same address online after its server started.
- Pasting the loopback address into a Codex browser tab rendered the actual timer. The review and browser tabs could be switched without leaving Codex.
- An interface comment was saved through the native panel, and the send button placed its delivery into the current conversation queue during an active turn.
- The agent read the exact saved delivery and explicitly acknowledged it. The native panel changed from waiting for receipt to received.
- The agent applied the isolated comment, refreshed the same review session, marked the comment addressed, and verified the changed heading in the actual browser tab. The original baseline stayed intact.

This acceptance did not prove automatic processing of a queued message while the agent is busy: the agent explicitly fetched its feedback for the test. It also did not verify a permanent thread-extension button.

## Known preview-open limitation

The tested client's `ui/open-link` request returned success, but no automatic switch to the running page was observed. Review Desk therefore does not label that response as a rendered preview. Its embedded preview always exposes a direct link, a copy button and a selectable address, including after an accepted request. If nothing opens, paste that address into a new Codex browser tab.

The previous Codex 26.1007.2314.0 origin sanitizer removed HTTP iframe origins. Actual client logs showed `frame-src 'none'` and `ERR_BLOCKED_BY_CSP` for loopback HTTP previews. Embedded previews consequently use the host browser rather than requesting an iframe. Browser placement is controlled by the host; the plugin cannot force a native tab through the documented open-link request. See the [official UI reference](https://developers.openai.com/plugins/reference).

Standalone browser previews retain iframe rendering and position marking. A frame load event alone does not establish successful project rendering. Sites that forbid embedding must be viewed independently. Embedded interface feedback describes an element or region without fabricated coordinates or viewport dimensions.

## Feedback and service recovery

Feedback states distinguish saved, prepared, submitting, submitted, uncertain, failed and agent-received. An unchanged submitted or uncertain delivery cannot be resent blindly. Receipt requires the exact delivery ID, revision and payload; reading feedback alone does not assert receipt. Changed code resets approval.

Preview checks are bounded and probe loopback addresses only. External sites are checked by the browser. Recovery uses the project's explicit `.codex/review-desk.json` preview URL; it does not guess ports or execute project commands. The fallback review server persists separately from the MCP connection, and its runtime version is checked before reuse.

The embedded UI resource is an immutable template without a task snapshot. Task results and explicit selection bind the current review. Reload the plugin or start a new conversation when its advertised UI resource/runtime is stale.

## Supported fallback and scope

The standalone local UI saves feedback and offers copy/export; it cannot initiate a Codex turn. Client connection failures preserve saved comments and expose the same copy fallback. Non-web projects support code diffs and run instructions.

Snapshots capture the explicit task scope, including simultaneous edits by other processes. They are bounded to 2,500 files and 20 MiB of text; oversized or binary data is fingerprinted and cannot receive complete UI approval. Review state and source snapshots remain local and are excluded from release packages.
