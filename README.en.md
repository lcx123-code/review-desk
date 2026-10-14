# Review Desk

Version 0.3.0 adds preview health checks, address recovery and durable feedback receipts. Embedded previews request the host browser and always retain a direct link and selectable address. In the tested Codex client, an accepted open request did not automatically switch to the page; pasting the address into a Codex browser tab rendered the actual project. See [compatibility and validation](docs/COMPATIBILITY.md).

[中文](README.md) · [Compatibility and validation](docs/COMPATIBILITY.md)

[Repository](https://github.com/lcx123-code/review-desk) · [Download v0.3.0](https://github.com/lcx123-code/review-desk/releases/tag/v0.3.0)

Review a Codex coding task with a before/after diff, an interactive web preview, and feedback you can send back to the conversation. A task-start snapshot preserves changes that already existed before the task.

![Code review](docs/screenshots/code-review.png)

Version 0.2.0 pairs a neutral workspace with deep green accents and a charcoal editor. Code and web preview can appear side by side with a draggable, keyboard-accessible divider. Files use a searchable dropdown; feedback lives in a collapsible drawer with actual code context. Smaller windows use a single view.

It also adds explicit task selection, connection status, pending-feedback filters, comment navigation, duplicate-send protection, and a copy fallback when the conversation bridge fails. The workspace includes syntax highlighting, review progress, desktop/mobile preview widths, and revision-aware comments and approvals.

## Try the demo

Requires Python 3.10+. From the repository root:

```sh
python plugins/review-desk/scripts/demo.py --workspace demo-work
```

Open the printed local URL. Inspect the diff, try the timer, and add feedback. Press Ctrl+C to stop. The demo creates an isolated example instead of editing your project.

## Install

Requires Git, Python 3.10+, and a Codex client supporting local plugins. Extract the release archive or clone the repository, then register the marketplace from its root:

```sh
codex plugin marketplace add .
```

Restart Codex, install **Review Desk** from **Review Desk 本地插件**, and run `$review-setup` in a new chat attached to your coding project. Then request:

```text
Use $review-changes to implement this task. Let me review the code and running web preview afterward.
```

Clients supporting MCP Apps can render the review beside the conversation. `open_review_panel` declares a thread extension entrypoint and opens an explicit task picker. A permanent native button depends on client support and remains unverified. See [compatibility](docs/COMPATIBILITY.md).

If your Python command is `python3`, update `command` in both plugin MCP config files. Automatic hooks are optional and require the client's hook trust flow. Explicit `$review-changes` works without trusted hooks.

## Feedback loop

`begin_review` → edit and validate → `refresh_review` → `open_review` → human comments → `get_feedback` → fixes → refresh the same session.

The embedded panel submits feedback to the current conversation when the bridge is available. During an active turn it can enter the message queue. Only an agent that reads the exact delivery and revision can confirm receipt through `acknowledge_feedback`; submission alone does not imply receipt. A standalone browser saves feedback and offers copy/export. Changed revisions invalidate old approvals. Binary, oversized, and truncated files cannot receive a complete approval through the panel.

Web previews use your project's development server. The plugin does not run project commands or commit/push code. Review snapshots include source code and must remain local. The local server binds to loopback and requires a random URL token.

## Development

```sh
python plugins/review-desk/tests/test_review_desk.py
cd plugins/review-desk
npm ci
node tests/test_panel.cjs
node node_modules/prettier/bin/prettier.cjs --check web/panel.html tests/test_panel.cjs
```

Set `REVIEW_DESK_PYTHON` if the test runner needs a different Python executable. The frontend tests start an isolated real API with a simulated MCP host. Node.js dependencies are development-only.

Run `python scripts/package_release.py` from the repository root to create a versioned archive and SHA-256 checksum in `dist/`. Packaging uses an explicit allowlist.

Licensed under [MIT](LICENSE).
