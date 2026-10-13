"""Build a deterministic, allowlisted local plugin release using stdlib only."""
import hashlib
import json
from pathlib import Path
import zipfile


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "review-desk"
VERSION = json.loads((PLUGIN / "plugin.json").read_text(encoding="utf-8"))["version"]
ROOT_FILES = [
    "README.md", "README.en.md", "LICENSE", "CHANGELOG.md", "CONTRIBUTING.md",
    "Start-Demo.ps1", ".agents/plugins/marketplace.json", "docs/COMPATIBILITY.md",
    "docs/screenshots/code-review.png", "docs/screenshots/live-preview.png",
    "scripts/package_release.py",
]
PLUGIN_FILES = [
    "plugin.json", ".codex-plugin/plugin.json", "mcp.json", ".mcp.json",
    "hooks/hooks.json", "scripts/review_desk.py", "scripts/demo.py", "web/panel.html",
    "skills/review-setup/SKILL.md", "skills/review-setup/agents/openai.yaml",
    "skills/review-changes/SKILL.md", "skills/review-changes/agents/openai.yaml",
    "tests/test_review_desk.py", "tests/test_panel.cjs", "package.json", "package-lock.json",
]


def main():
    paths = ROOT_FILES + ["plugins/review-desk/" + name for name in PLUGIN_FILES]
    for relative in paths:
        path = ROOT / relative
        if not path.is_file() or not path.resolve().is_relative_to(ROOT.resolve()):
            raise ValueError("Missing or out-of-scope release file: " + relative)
    output = ROOT / "dist"
    output.mkdir(exist_ok=True)
    archive = output / ("review-desk-v" + VERSION + ".zip")
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for relative in sorted(paths):
            info = zipfile.ZipInfo("review-desk/" + relative, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            bundle.writestr(info, (ROOT / relative).read_bytes())
    checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
    (output / "SHA256SUMS").write_text(checksum + "  " + archive.name + "\n", encoding="utf-8")
    print(json.dumps({"archive": str(archive), "files": len(paths), "sha256": checksum}))


if __name__ == "__main__":
    main()
