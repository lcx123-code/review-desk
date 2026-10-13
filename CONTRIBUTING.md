# Contributing

Start with the development commands in README.en.md. Runtime code uses the Python standard library and an inline HTML/CSS/JavaScript panel. Node dependencies belong only to development tests.

Keep task-start snapshots intact when applying review feedback. Never report simulated host checks as actual Codex client acceptance. Include the relevant test results and client limitations in a pull request.

Do not commit review snapshots, local access tokens, demo state, personal installation settings, logs, or dependency directories. Add runtime assets to the packaging allowlist when introducing them.

For changes to the bridge or preview, manually test an embedded MCP host and standalone copy mode. Confirm feedback survives a connection failure and a changed revision invalidates earlier approval.
