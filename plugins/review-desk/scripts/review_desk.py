#!/usr/bin/env python3
"""Local review workflow, HTTP panel and newline-delimited MCP stdio server.

Python standard library only. No code execution, repository writes, or commits.
State is written only under the selected data directory and configuration path.
"""
from __future__ import annotations

import argparse
import contextlib
import difflib
import hashlib
import json
import os
import ipaddress
from pathlib import Path
import re
import secrets
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[1]
MAX_FILE = 1024 * 1024
MAX_TOTAL = 20 * 1024 * 1024
MAX_FILES = 2500
MAX_ROWS = 12000
UI_MIME = "text/html;profile=mcp-app"
UI_URI = "ui://review-desk/panel-v0.3.0.html"
VERSION = "0.3.0"
IDS = re.compile(r"^[a-f0-9]{24}$")


def digest(value):
    raw = value if isinstance(value, bytes) else json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    return hashlib.sha256(raw).hexdigest()


def now():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + "." + secrets.token_hex(6) + ".tmp")
    try:
        tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def data_path(project=None):
    configured = os.environ.get("REVIEW_DESK_DATA") or os.environ.get("PLUGIN_DATA")
    return Path(configured).resolve() if configured else Path(project or Path.cwd()).resolve() / ".codex" / "review-desk"


@contextlib.contextmanager
def lock_file(root):
    root.mkdir(parents=True, exist_ok=True)
    with (root / "state.lock").open("a+b") as stream:
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        if os.name == "nt":
            import msvcrt
            deadline = time.monotonic() + 15
            while True:
                try:
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    if time.monotonic() > deadline:
                        raise ValueError("Review state is busy; retry shortly.")
                    time.sleep(.05)
            try:
                yield
            finally:
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(stream, fcntl.LOCK_UN)


def project_root(path):
    root = Path(path).resolve()
    if not root.is_dir():
        raise ValueError("Project directory does not exist.")
    try:
        result = subprocess.run(["git", "-C", str(root), "rev-parse", "--show-toplevel"], capture_output=True, timeout=10)
        if result.returncode == 0:
            return Path(os.fsdecode(result.stdout).strip()).resolve(), True
    except FileNotFoundError:
        pass
    return root, False


def excluded(name):
    path = Path(name)
    parts = [x.lower() for x in path.parts]
    return (any(x in {".git", "node_modules", ".venv", "__pycache__"} for x in parts)
            or ".codex/review-desk" in name.replace("\\", "/").lower()
            or path.name.lower() == "review-desk.json"
            or path.name.lower() == ".env" or path.name.lower().startswith(".env.")
            or path.suffix.lower() in {".pem", ".key", ".p12", ".pfx"})


def safe_file(root, name):
    raw = Path(name)
    if raw.is_absolute() or ".." in raw.parts or not name or "\x00" in name:
        raise ValueError("File path must stay inside the project.")
    candidate = root / raw
    # Resolve the parent but do not follow a leaf symlink: snapshot records its
    # link target string rather than opening a file outside the project.
    if not candidate.parent.resolve().is_relative_to(root):
        raise ValueError("File path escapes the project.")
    return candidate


def snapshot(root, explicit=None):
    if explicit is None:
        result = subprocess.run(["git", "-C", str(root), "ls-files", "-z", "--cached", "--others", "--exclude-standard"], capture_output=True, timeout=15)
        if result.returncode:
            raise ValueError("Non-Git projects need an explicit files list.")
        names = sorted(set(os.fsdecode(n) for n in result.stdout.split(b"\0") if n))
    else:
        names = sorted(set(explicit))
    names = [n.replace("\\", "/") for n in names if not excluded(n)]
    if len(names) > MAX_FILES:
        raise ValueError(f"Project exceeds {MAX_FILES} files. Use an explicit files list to limit review scope.")
    files, total = {}, 0
    for name in names:
        path = safe_file(root, name)
        if path.is_symlink():
            files[name] = {"kind": "symlink", "hash": digest(os.readlink(path)), "size": 0}
            continue
        if not path.exists():
            continue
        if not path.is_file():
            continue
        size = path.stat().st_size
        # Hash large and binary files without storing their contents.
        hasher = hashlib.sha256()
        if size > MAX_FILE:
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(65536), b""):
                    hasher.update(chunk)
            files[name] = {"kind": "large", "hash": hasher.hexdigest(), "size": size}
            continue
        raw = path.read_bytes()
        item = {"kind": "binary", "hash": digest(raw), "size": len(raw)}
        if b"\0" not in raw:
            try:
                item.update(kind="text", text=raw.decode("utf-8"))
                total += len(raw)
            except UnicodeDecodeError:
                pass
        if total > MAX_TOTAL:
            raise ValueError("Text snapshot exceeds 20 MiB. Use an explicit files list to limit review scope.")
        files[name] = item
    return files


def changed_files(before, after):
    changes = []
    for name in sorted(before.keys() | after.keys()):
        old, new = before.get(name), after.get(name)
        if old == new:
            continue
        kind = "added" if old is None else "deleted" if new is None else "modified"
        a, b = (old or {}).get("text", ""), (new or {}).get("text", "")
        textual = all(x is None or x["kind"] == "text" for x in (old, new))
        rows, added, deleted, limited = [], 0, 0, False
        if textual:
            # keepends preserves a final-newline-only edit.
            aa, bb = a.splitlines(keepends=True), b.splitlines(keepends=True)
            matcher = difflib.SequenceMatcher(None, aa, bb, autojunk=True)
            for group in matcher.get_grouped_opcodes(3):
                if len(rows) >= MAX_ROWS:
                    limited = True
                    break
                rows.append({"kind": "hunk", "text": f"@@ -{group[0][1]+1},{group[-1][2]-group[0][1]} +{group[0][3]+1},{group[-1][4]-group[0][3]} @@"})
                for tag, i1, i2, j1, j2 in group:
                    segments = [("context", aa, i1, i2, j1)] if tag == "equal" else []
                    if tag in ("delete", "replace"):
                        segments.append(("delete", aa, i1, i2, None))
                        deleted += i2-i1
                    if tag in ("insert", "replace"):
                        segments.append(("add", bb, j1, j2, None))
                        added += j2-j1
                    for rowkind, source, start, end, other in segments:
                        for index in range(start, end):
                            if len(rows) >= MAX_ROWS:
                                limited = True
                                break
                            rows.append({"kind": rowkind, "oldLine": index+1 if rowkind != "add" else None,
                                         "newLine": (index-start+other+1 if other is not None else index+1) if rowkind != "delete" else None,
                                         "text": source[index].rstrip("\r\n"), "noNewline": not source[index].endswith("\n")})
            if old and new and a == b and old != new:
                rows.append({"kind": "hunk", "text": "文件元数据已改变"})
        changes.append({"path": name, "status": kind, "added": added, "deleted": deleted, "rows": rows,
                        "textual": textual, "limited": limited,
                        "note": "差异过大，部分行未展示" if limited else "二进制、大文件或符号链接：仅记录内容指纹" if not textual else ""})
    return changes


class Store:
    def __init__(self, root):
        self.root = Path(root).resolve()

    def _path(self, sid):
        if not isinstance(sid, str) or not IDS.fullmatch(sid):
            raise ValueError("Invalid review session ID.")
        return self.root / "sessions" / (sid + ".json")

    def load(self, sid):
        path = self._path(sid)
        if not path.exists():
            raise ValueError("Review session not found.")
        return json.loads(path.read_text(encoding="utf-8"))

    def save(self, session):
        atomic_json(self._path(session["id"]), session)

    def begin(self, project_path, title="本次代码修改", files=None, preview_url=None):
        root, git = project_root(project_path)
        if not git and files is None:
            raise ValueError("Non-Git projects need an explicit files list, including planned new files.")
        if preview_url:
            validate_preview(preview_url)
        baseline = snapshot(root, files)
        session = {"id": secrets.token_hex(12), "project": str(root), "title": title[:200], "createdAt": now(),
                   "scope": files, "baseline": baseline, "current": baseline, "revision": digest(baseline),
                   "summary": "", "previewUrl": preview_url or "", "checks": [], "comments": [], "viewed": {},
                   "decision": None, "generation": 0}
        with lock_file(self.root):
            self.save(session)
        return self.view(session)

    def refresh(self, session_id, summary=None, preview_url=None, checks=None):
        if preview_url:
            validate_preview(preview_url)
        if checks is not None:
            if not isinstance(checks, list) or len(checks) > 100:
                raise ValueError("Checks must be a list of at most 100 actual validation results.")
            for check in checks:
                if not isinstance(check, dict) or check.get("status") not in {"passed", "failed", "not_run"} or not isinstance(check.get("name"), str):
                    raise ValueError("Each check needs name and passed/failed/not_run status.")
        with lock_file(self.root):
            s = self.load(session_id)
            current = snapshot(Path(s["project"]), s["scope"])
            revision = digest(current)
            if revision != s["revision"]:
                s["decision"], s["viewed"] = None, {}
                s["checks"] = []  # Old test results do not validate new code.
                s["generation"] += 1
            s.update(current=current, revision=revision, updatedAt=now())
            if summary is not None:
                s["summary"] = summary[:8000]
            if preview_url is not None:
                if preview_url != s.get("previewUrl"):
                    s["decision"] = None
                s["previewUrl"] = preview_url
            if checks is not None:
                s["checks"] = checks
            self.save(s)
        return self.view(s)

    def view(self, s):
        changes = changed_files(s["baseline"], s["current"])
        delivery = dict(s.get("deliveries", [])[-1]) if s.get("deliveries") else None
        if delivery:
            delivery["current"] = delivery["contentHash"] == feedback_hash(s)
        return {k: v for k, v in s.items() if k not in {"baseline", "current", "scope", "deliveries"}} | {
            "runtimeVersion": VERSION, "delivery": delivery,
            "files": changes, "stats": {"files": len(changes), "added": sum(c["added"] for c in changes), "deleted": sum(c["deleted"] for c in changes)},
            "complete": all(c["textual"] and not c["limited"] for c in changes)}

    def get(self, sid):
        with lock_file(self.root):
            return self.view(self.load(sid))

    def list_reviews(self):
        """List review identities without returning source snapshots or picking a project."""
        candidates = []
        for path in (self.root / "sessions").glob("*.json"):
            if IDS.fullmatch(path.stem):
                try:
                    candidates.append((path.stat().st_mtime_ns, path))
                except OSError:
                    continue
        reviews = []
        for _, path in sorted(candidates, reverse=True):
            try:
                session = self.load(path.stem)
                reviews.append({"id": session["id"], "title": session["title"],
                                "project": session["project"], "updatedAt": session.get("updatedAt", session["createdAt"]),
                                "previewUrl": session.get("previewUrl", ""),
                                "pendingComments": sum(c["status"] != "resolved" for c in session.get("comments", []))})
            except (ValueError, KeyError, TypeError, OSError):
                continue
            if len(reviews) >= 30:
                break
        return {"reviews": reviews}

    def mutate(self, sid, action, args):
        with lock_file(self.root):
            s = self.load(sid)
            # Check the actual files, not only the last rendered snapshot.
            current = snapshot(Path(s["project"]), s["scope"])
            if digest(current) != s["revision"] or args.get("revision") != s["revision"]:
                raise ValueError("代码已更新，请刷新审查面板后再操作。")
            view = self.view(s)
            if action == "comment":
                text = args.get("text", "")
                if not isinstance(text, str):
                    raise ValueError("评论必须为文本。")
                text = text.strip()
                if not text or len(text) > 4000:
                    raise ValueError("评论需为 1–4000 个字符。")
                target = args.get("target", {"type": "general"})
                if not isinstance(target, dict):
                    raise ValueError("Feedback target must be an object.")
                if target.get("type") not in {"general", "code", "preview"}:
                    raise ValueError("Unsupported feedback target.")
                if target["type"] == "code":
                    changed = next((f for f in view["files"] if f["path"] == target.get("path")), None)
                    side = target.get("side", "new")
                    key = "oldLine" if side == "old" else "newLine"
                    line = target.get("line")
                    if side not in {"old", "new"} or not changed or not isinstance(line, int) or not any(row.get(key) == line for row in changed["rows"]):
                        raise ValueError("Comment line must exist in the displayed diff.")
                    target = {"type": "code", "path": changed["path"], "side": side, "line": line}
                elif target["type"] == "preview":
                    target = {"type": "preview", "url": s["previewUrl"], "element": str(target.get("element", ""))[:300],
                              "viewport": str(target.get("viewport", ""))[:60]}
                else:
                    target = {"type": "general"}
                s["comments"].append({"id": secrets.token_hex(12), "text": text, "target": target,
                                      "revision": s["revision"], "status": "open", "createdAt": now()})
                s["decision"] = None
            elif action == "comment_status":
                status = args.get("status")
                if status not in {"open", "addressed", "resolved"}:
                    raise ValueError("Invalid comment status.")
                comment = next((c for c in s["comments"] if c["id"] == args.get("comment_id")), None)
                if not comment:
                    raise ValueError("Comment not found.")
                comment["status"] = status
                if status == "open":
                    s["decision"] = None
            elif action == "viewed":
                path = args.get("path")
                if path not in {f["path"] for f in view["files"]}:
                    raise ValueError("File is not in the current diff.")
                if args.get("viewed"):
                    s["viewed"][path] = s["revision"]
                else:
                    s["viewed"].pop(path, None)
            elif action == "decision":
                decision = args.get("decision")
                if decision not in {"approved", "changes_requested"}:
                    raise ValueError("Invalid decision.")
                if decision == "approved":
                    if not view["complete"]:
                        raise ValueError("存在未完整展示的差异，请先在编辑器中审查；面板不能记录完整通过。")
                    if any(c["status"] != "resolved" for c in s["comments"]):
                        raise ValueError("请先复查并解决反馈，再通过。")
                    if any(c["status"] == "failed" for c in s["checks"]):
                        raise ValueError("存在失败的验证项，请先处理。")
                s["decision"] = {"value": decision, "revision": s["revision"], "at": now()}
            else:
                raise ValueError("Unsupported review action.")
            self.save(s)
            return self.view(s)

    def feedback(self, sid):
        view = self.get(sid)
        lines = [f"请处理 Review Desk 审查反馈：{view['title']}", f"Session: {sid}", f"Revision: {view['revision']}"]
        if view["decision"]:
            lines.append("审查决定：" + ("通过" if view["decision"]["value"] == "approved" else "请求修改"))
        for c in view["comments"]:
            if c["status"] == "resolved":
                continue
            target = c["target"]
            label = "整体反馈"
            if target["type"] == "code":
                label = f"{target['path']}:{target['line']} ({target['side']})"
            elif target["type"] == "preview":
                label = f"界面 {target.get('element') or target.get('url')} [{target.get('viewport', '')}]"
            stale = " / 旧版本，请重新定位" if c["revision"] != view["revision"] else ""
            lines.extend([f"\n- {label} [{c['status']}{stale}] (comment {c['id']})", c["text"]])
        if view["decision"] and view["decision"]["value"] == "approved":
            lines.append("\n审查已通过。保留本任务记录，后续操作按用户当前指令执行。")
        else:
            lines.append("\n请读取该 session 的最新反馈和代码，修复后刷新同一 session，供我再次审查。")
        delivery = view.get("delivery")
        if delivery and delivery["current"]:
            lines.extend([f"\nDelivery: {delivery['id']}",
                          "读取反馈后，请调用 acknowledge_feedback 确认该 Delivery 和 Revision，再按反馈修复。"])
        return {"session_id": sid, "revision": view["revision"], "comments": view["comments"],
                "decision": view["decision"], "delivery": delivery, "message": "\n".join(lines)}

    def prepare_feedback(self, session_id):
        with lock_file(self.root):
            s = self.load(session_id)
            if digest(snapshot(Path(s["project"]), s["scope"])) != s["revision"]:
                raise ValueError("代码已更新，请先刷新审查再发送反馈。")
            if not s["decision"] and not any(c["status"] != "resolved" for c in s["comments"]):
                raise ValueError("先添加反馈或记录审查决定。")
            content_hash = feedback_hash(s)
            deliveries = s.setdefault("deliveries", [])
            latest = deliveries[-1] if deliveries else None
            if not latest or latest["contentHash"] != content_hash:
                deliveries.append({"id": secrets.token_hex(12), "revision": s["revision"],
                                   "contentHash": content_hash, "state": "prepared", "createdAt": now()})
                s["deliveries"] = deliveries[-20:]
            elif latest["state"] == "failed":
                latest.update(state="prepared", updatedAt=now())
            self.save(s)
        return self.feedback(session_id)

    def record_delivery(self, session_id, delivery_id, state):
        if state not in {"submitting", "submitted", "uncertain", "failed"}:
            raise ValueError("The panel cannot assert agent receipt.")
        with lock_file(self.root):
            s = self.load(session_id)
            delivery = next((d for d in s.get("deliveries", []) if d["id"] == delivery_id), None)
            if not delivery:
                raise ValueError("Unknown feedback delivery.")
            if delivery["contentHash"] != feedback_hash(s):
                raise ValueError("反馈已变化，请重新发送当前意见。")
            if delivery["state"] != "received":
                delivery.update(state=state, updatedAt=now())
            self.save(s)
        return self.view(s)

    def acknowledge_feedback(self, session_id, delivery_id, revision):
        """Agent-only receipt: the UI cannot claim that the agent read feedback."""
        with lock_file(self.root):
            s = self.load(session_id)
            delivery = next((d for d in s.get("deliveries", []) if d["id"] == delivery_id), None)
            if not delivery or delivery["revision"] != revision or s["revision"] != revision:
                raise ValueError("Feedback delivery or revision does not match.")
            if delivery["contentHash"] != feedback_hash(s):
                raise ValueError("反馈内容已变化，请读取当前反馈。")
            delivery.update(state="received", receivedAt=now())
            self.save(s)
        return self.view(s)

    def delivery_status(self, session_id):
        with lock_file(self.root):
            session = self.load(session_id)
            delivery = dict(session.get("deliveries", [])[-1]) if session.get("deliveries") else None
            if delivery:
                delivery["current"] = delivery["contentHash"] == feedback_hash(session)
        return {"session_id": session_id, "delivery": delivery, "runtimeVersion": VERSION}

    def check_preview(self, session_id):
        view = self.get(session_id)
        status = probe_preview(view.get("previewUrl", ""))
        # Recover only from the project's explicit configuration, never guessed ports.
        if status["state"] == "unavailable":
            try:
                config = json.loads((Path(view["project"]) / ".codex" / "review-desk.json").read_text(encoding="utf-8"))
                candidate = config.get("previewUrl")
                if candidate and candidate != view["previewUrl"]:
                    recovered = probe_preview(candidate)
                    if recovered["state"] == "reachable":
                        self.refresh(session_id, preview_url=candidate)
                        status = recovered | {"recovered": True}
            except (OSError, ValueError, TypeError):
                pass
        return status | {"session_id": session_id, "runtimeVersion": VERSION}


def feedback_hash(session):
    return digest({"id": session["id"], "revision": session["revision"], "title": session["title"],
                   "comments": [c for c in session["comments"] if c["status"] != "resolved"],
                   "decision": session["decision"]})


def probe_preview(url):
    from urllib.request import Request, build_opener, ProxyHandler, HTTPRedirectHandler
    from urllib.error import HTTPError
    status = {"url": url, "checkedAt": now(), "state": "missing"}
    if not url:
        return status
    validate_preview(url)
    parsed = urlsplit(url)
    try:
        local = parsed.hostname == "localhost" or ipaddress.ip_address(parsed.hostname).is_loopback
    except ValueError:
        local = False
    if not local:
        return status | {"state": "external"}
    class NoRedirect(HTTPRedirectHandler):
        def redirect_request(self, *_):
            return None
    opener = build_opener(ProxyHandler({}), NoRedirect())
    for method in ("HEAD", "GET"):
        try:
            with opener.open(Request(url, method=method), timeout=1.5) as response:
                return status | {"state": "reachable", "httpStatus": response.status}
        except HTTPError as error:
            code = error.code
            error.close()
            if code in {405, 501} and method == "HEAD":
                continue
            return status | {"state": "reachable" if code < 400 or code in {401, 403} else "unavailable", "httpStatus": code}
        except (OSError, ValueError):
            return status | {"state": "unavailable"}
    return status | {"state": "unavailable"}


def validate_preview(url):
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Preview must be an HTTP(S) URL without credentials.")
    # Avoid confusing fragments/query tokens with host policy declarations.
    try:
        parsed.port
    except ValueError:
        raise ValueError("Invalid preview port.")
    return f"{parsed.scheme}://{parsed.netloc}"


class PanelServer:
    def __init__(self, store, port=0):
        self.store, self.token = store, secrets.token_urlsafe(32)
        self.last_used = time.monotonic()
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def reply(self, status, data, mime="application/json; charset=utf-8"):
                raw = json.dumps(data, ensure_ascii=False).encode() if not isinstance(data, bytes) else data
                self.send_response(status)
                self.send_header("Content-Type", mime)
                self.send_header("Content-Length", str(len(raw)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Referrer-Policy", "no-referrer")
                self.end_headers()
                self.wfile.write(raw)

            def authorize(self, mutation=False):
                host = self.headers.get("Host", "")
                valid_hosts = {f"127.0.0.1:{owner.port}", f"localhost:{owner.port}"}
                if host not in valid_hosts:
                    raise PermissionError("Invalid local host.")
                query = parse_qs(urlsplit(self.path).query)
                token = query.get("token", [""])[0] or self.headers.get("X-Review-Token", "")
                if not secrets.compare_digest(token, owner.token):
                    raise PermissionError("Missing or invalid review access token.")
                if mutation:
                    origin = self.headers.get("Origin")
                    if origin and origin not in {f"http://{h}" for h in valid_hosts}:
                        raise PermissionError("Cross-origin write rejected.")
                    if "application/json" not in self.headers.get("Content-Type", ""):
                        raise ValueError("Expected application/json.")
                owner.last_used = time.monotonic()
                return urlsplit(self.path).path, query

            def do_GET(self):
                try:
                    path, query = self.authorize()
                    sid = query.get("session", [""])[0]
                    if path == "/health":
                        self.reply(200, {"ok": True, "version": VERSION})
                    elif path == "/":
                        self.reply(200, (ROOT / "web" / "panel.html").read_bytes(), "text/html; charset=utf-8")
                    elif path == "/api/review":
                        self.reply(200, store.get(sid))
                    elif path == "/api/feedback":
                        self.reply(200, store.feedback(sid))
                    elif path == "/api/reviews":
                        self.reply(200, store.list_reviews())
                    elif path == "/api/check-preview":
                        self.reply(200, store.check_preview(sid))
                    elif path == "/api/delivery-status":
                        self.reply(200, store.delivery_status(sid))
                    else:
                        self.reply(404, {"error": "Not found"})
                except PermissionError as exc:
                    self.reply(403, {"error": str(exc)})
                except (ValueError, OSError) as exc:
                    self.reply(400, {"error": str(exc)})

            def do_POST(self):
                try:
                    path, _ = self.authorize(True)
                    length = int(self.headers.get("Content-Length", "0"))
                    if not 0 < length < 32000:
                        raise ValueError("Invalid request size.")
                    body = json.loads(self.rfile.read(length))
                    if path == "/api/action":
                        value = store.mutate(body["session_id"], body["action"], body)
                    elif path == "/api/refresh":
                        value = store.refresh(body["session_id"], preview_url=body.get("preview_url"))
                    elif path == "/api/prepare-feedback":
                        value = store.prepare_feedback(body["session_id"])
                    elif path == "/api/delivery":
                        value = store.record_delivery(body["session_id"], body["delivery_id"], body["state"])
                    else:
                        self.reply(404, {"error": "Not found"})
                        return
                    self.reply(200, value)
                except PermissionError as exc:
                    self.reply(403, {"error": str(exc)})
                except (ValueError, KeyError, TypeError, OSError) as exc:
                    self.reply(400, {"error": str(exc)})

        self.http = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        self.http.daemon_threads = True
        self.port = self.http.server_address[1]

    def url(self, sid):
        return f"http://127.0.0.1:{self.port}/?session={sid}&token={self.token}"

    def start(self):
        threading.Thread(target=self.http.serve_forever, daemon=True).start()

    def close(self):
        self.http.shutdown()
        self.http.server_close()


def ensure_panel(store):
    """Reuse or launch a hidden local fallback server. No project commands run."""
    from urllib.request import Request, urlopen
    pointer = store.root / "server.json"
    with lock_file(store.root):
        if pointer.exists():
            try:
                info = json.loads(pointer.read_text(encoding="utf-8"))
                request = Request(f"http://127.0.0.1:{int(info['port'])}/health", headers={"X-Review-Token": info["token"]})
                with urlopen(request, timeout=.7) as response:
                    if response.status == 200 and json.load(response).get("version") == VERSION:
                        return info
            except (OSError, ValueError, KeyError):
                pass
        log = (store.root / "server.log").open("ab")
        kwargs = {"creationflags": subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS} if os.name == "nt" else {"start_new_session": True}
        try:
            subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--data-dir", str(store.root), "serve"],
                             stdin=subprocess.DEVNULL, stdout=log, stderr=log, **kwargs)
        finally:
            log.close()
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            if pointer.exists():
                info = json.loads(pointer.read_text(encoding="utf-8"))
                try:
                    with urlopen(Request(f"http://127.0.0.1:{int(info['port'])}/health", headers={"X-Review-Token": info["token"]}), timeout=.5) as response:
                        if response.status == 200 and json.load(response).get("version") == VERSION:
                            return info
                except OSError:
                    pass
            time.sleep(.1)
    raise ValueError("Could not start local review panel. Check server.log.")


def fallback_url(store, sid):
    info = ensure_panel(store)
    return f"http://127.0.0.1:{info['port']}/?session={sid}&token={info['token']}"


def resource_uri(view):
    return UI_URI


def tool_schema(name, description, properties, required=(), read=False):
    return {"name": name, "description": description, "inputSchema": {"type": "object", "properties": properties,
            "required": list(required), "additionalProperties": False},
            "annotations": {"readOnlyHint": read, "destructiveHint": False, "openWorldHint": False}}


STRING = {"type": "string"}
SID = {"session_id": STRING}
TOOLS = [
    tool_schema("open_review_panel", "Open the Review Desk task chooser beside the conversation. User selects a project/task explicitly; no project is automatically selected.", {}, read=True),
    tool_schema("list_reviews", "List recent review tasks and project identities without source snapshots.", {}, read=True),
    tool_schema("begin_review", "Capture task-start baseline BEFORE editing code. Return session ID to reuse for review and feedback.",
                {"project_path": STRING, "title": STRING, "files": {"type": "array", "items": STRING}, "preview_url": STRING}, ("project_path",)),
    tool_schema("refresh_review", "Refresh a task's code diff and actual validation results. Preserve baseline; changed code invalidates approval and old checks.",
                SID | {"summary": STRING, "preview_url": STRING, "checks": {"type": "array", "items": {"type": "object", "properties": {"name": STRING, "status": {"type": "string", "enum": ["passed", "failed", "not_run"]}}, "required": ["name", "status"], "additionalProperties": False}}}, ("session_id",)),
    tool_schema("open_review", "Open human review panel with code diff, live Web preview, comments and feedback submission. Also returns local browser fallback URL.", SID, ("session_id",), True),
    tool_schema("get_feedback", "Read saved human review comments and decision for the given task session.", SID, ("session_id",), True),
    tool_schema("check_preview", "Check the local preview and recover its address from explicit project configuration. Does not execute project commands.", SID, ("session_id",)),
    tool_schema("prepare_feedback", "Prepare a durable feedback delivery for the current task and revision.", SID, ("session_id",)),
    tool_schema("record_delivery", "Record host submission or failure; cannot assert agent receipt.", SID | {"delivery_id": STRING, "state": {"type": "string", "enum": ["submitting", "submitted", "uncertain", "failed"]}}, ("session_id", "delivery_id", "state")),
    tool_schema("get_delivery_status", "Read the durable feedback delivery status without scanning project files.", SID, ("session_id",), True),
    tool_schema("acknowledge_feedback", "Confirm receipt only after the agent has read this exact feedback delivery and revision.", SID | {"delivery_id": STRING, "revision": STRING}, ("session_id", "delivery_id", "revision")),
    tool_schema("review_action", "Save a user comment, viewed marker or explicit review decision. Never commits or executes project code.",
                SID | {"revision": STRING, "action": {"type": "string", "enum": ["comment", "comment_status", "viewed", "decision"]}, "text": STRING,
                       "target": {"type": "object"}, "comment_id": STRING, "status": {"type": "string", "enum": ["open", "addressed", "resolved"]},
                       "path": STRING, "viewed": {"type": "boolean"}, "decision": {"type": "string", "enum": ["approved", "changes_requested"]}}, ("session_id", "revision", "action"))]
next(t for t in TOOLS if t["name"] == "open_review")["_meta"] = {
    "ui": {"resourceUri": UI_URI}, "openai/outputTemplate": UI_URI}
next(t for t in TOOLS if t["name"] == "open_review_panel")["_meta"] = {
    "ui": {"resourceUri": UI_URI}, "openai/outputTemplate": UI_URI,
    "openai/ui": {"entrypoints": [{"type": "thread"}]}}
for name in ("get_feedback", "acknowledge_feedback"):
    next(t for t in TOOLS if t["name"] == name)["_meta"] = {"ui": {"visibility": ["model"]}}


def mcp(store):
    panel = None
    last_view = None

    def result(value):
        return {"content": [{"type": "text", "text": json.dumps(value, ensure_ascii=False)}], "structuredContent": value}

    def dispatch(method, params):
        nonlocal panel, last_view
        if method == "initialize":
            supported = {"2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25"}
            requested = params.get("protocolVersion")
            return {"protocolVersion": requested if requested in supported else "2025-11-25",
                    "capabilities": {"tools": {}, "resources": {}}, "serverInfo": {"name": "review-desk", "version": VERSION},
                    "instructions": "Capture begin_review before editing; refresh/open after tests. Reuse the session for review feedback."}
        if method == "ping":
            return {}
        if method == "tools/list":
            return {"tools": TOOLS}
        if method == "resources/list":
            return {"resources": [{"uri": UI_URI, "name": "Review Desk", "mimeType": UI_MIME}]}
        if method == "resources/templates/list":
            return {"resourceTemplates": []}
        if method == "resources/read":
            uri = params["uri"]
            if uri != UI_URI:
                raise ValueError("Unknown review UI resource.")
            view = last_view
            launcher = bool(view and view.get("launcher"))
            origins = []  # Embedded previews use the host browser; this template is immutable.
            html = (ROOT / "web" / "panel.html").read_text(encoding="utf-8")
            # Cached resources must not contain a task-specific boot snapshot.
            boot = json.dumps({"embedded": True, "session": None, "version": VERSION})
            html = re.sub(r"/\*REVIEW_BOOT\*/\s*null", lambda _: boot, html, count=1)
            csp = {"connectDomains": [], "resourceDomains": [], "frameDomains": origins}
            return {"contents": [{"uri": uri, "mimeType": UI_MIME, "text": html,
                                  "_meta": {"ui": {"prefersBorder": True, "csp": csp},
                                            "openai/widgetCSP": {"connect_domains": [], "resource_domains": [], "frame_domains": origins}}}]}
        if method == "tools/call":
            name, args = params["name"], params.get("arguments", {})
            schema = next((t["inputSchema"] for t in TOOLS if t["name"] == name), None)
            if schema is None:
                raise ValueError("Unknown tool.")
            if not isinstance(args, dict) or set(args) - schema["properties"].keys() or any(k not in args for k in schema["required"]):
                raise ValueError("Invalid tool arguments.")
            for key, val in args.items():
                kind = schema["properties"][key].get("type")
                expected = {"string": str, "array": list, "object": dict, "boolean": bool}.get(kind)
                if expected and not isinstance(val, expected):
                    raise ValueError(f"Invalid argument type: {key}.")
            if name == "list_reviews":
                value = store.list_reviews()
            elif name == "open_review_panel":
                value = {"launcher": True, **store.list_reviews(), "version": VERSION}
                last_view = value
            elif name == "begin_review":
                value = store.begin(**args)
            elif name == "refresh_review":
                value = store.refresh(**args)
            elif name == "get_feedback":
                value = store.feedback(args["session_id"])
            elif name == "check_preview":
                value = store.check_preview(**args)
            elif name == "prepare_feedback":
                value = store.prepare_feedback(**args)
            elif name == "record_delivery":
                value = store.record_delivery(**args)
            elif name == "get_delivery_status":
                value = store.delivery_status(**args)
            elif name == "acknowledge_feedback":
                value = store.acknowledge_feedback(**args)
            elif name == "review_action":
                value = store.mutate(args["session_id"], args["action"], args)
            else:
                value = store.refresh(args["session_id"])
                value["fallbackUrl"] = fallback_url(store, value["id"])
                last_view = value
            output = result(value)
            if name in {"open_review", "open_review_panel"}:
                output["_meta"] = {"ui": {"resourceUri": resource_uri(value)}, "openai/outputTemplate": resource_uri(value)}
            return output
        raise ValueError("Unsupported MCP method.")

    try:
        for line in sys.stdin:
            msg = None
            try:
                msg = json.loads(line)
                if "id" not in msg:
                    continue
                value = dispatch(msg["method"], msg.get("params", {}))
                response = {"jsonrpc": "2.0", "id": msg["id"], "result": value}
            except (ValueError, KeyError, TypeError, OSError, subprocess.SubprocessError) as exc:
                if isinstance(msg, dict) and msg.get("method") == "tools/call":
                    response = {"jsonrpc": "2.0", "id": msg.get("id"), "result": {"isError": True, "content": [{"type": "text", "text": str(exc)}]}}
                else:
                    response = {"jsonrpc": "2.0", "id": msg.get("id") if isinstance(msg, dict) else None, "error": {"code": -32602, "message": str(exc)}}
            print(json.dumps(response, ensure_ascii=False), flush=True)
    finally:
        if panel:
            panel.close()


def hook(mode):
    event = json.load(sys.stdin)
    root, _ = project_root(event.get("cwd", str(Path.cwd())))
    config_path = root / ".codex" / "review-desk.json"
    if not config_path.exists():
        return {}
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if not config.get("enabled"):
        return {}
    store = Store(data_path(root))
    # Isolate turns across simultaneous Codex conversations in the same repo.
    key = digest([event.get("session_id", "default"), event.get("turn_id", "default")])[:24]
    pointer = store.root / "turns" / (key + ".json")
    if mode == "begin":
        if pointer.exists():
            sid = json.loads(pointer.read_text(encoding="utf-8"))["session_id"]
        else:
            s = store.begin(str(root), "Codex 本轮修改", config.get("files"), config.get("previewUrl"))
            sid = s["id"]
            atomic_json(pointer, {"session_id": sid})
        context = f"Review Desk baseline for this turn: {sid}. Reuse this session. After coding and checks call refresh_review then open_review. Data directory: {store.root}."
        return {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": context}}
    if not pointer.exists():
        return {}
    sid = json.loads(pointer.read_text(encoding="utf-8"))["session_id"]
    s = store.refresh(sid)
    if not s["files"]:
        return {}
    url = fallback_url(store, sid)
    return {"systemMessage": f"Review Desk：本轮 {s['stats']['files']} 个文件有改动。审查入口：{url}"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("mcp")
    serve = commands.add_parser("serve")
    serve.add_argument("--port", type=int, default=0)
    begin = commands.add_parser("begin")
    begin.add_argument("--project", required=True)
    begin.add_argument("--title", default="本次代码修改")
    begin.add_argument("--files", nargs="*")
    begin.add_argument("--preview-url")
    refresh = commands.add_parser("refresh")
    refresh.add_argument("session_id")
    refresh.add_argument("--summary")
    refresh.add_argument("--preview-url")
    refresh.add_argument("--checks-json", help='Actual results, e.g. [{"name":"tests","status":"passed"}]')
    for name in ("open", "feedback"):
        child = commands.add_parser(name)
        child.add_argument("session_id")
    ack = commands.add_parser("acknowledge")
    ack.add_argument("session_id")
    ack.add_argument("delivery_id")
    ack.add_argument("--revision", required=True)
    configure = commands.add_parser("configure")
    configure.add_argument("--project", required=True)
    configure.add_argument("--preview-url")
    configure.add_argument("--files", nargs="*")
    h = commands.add_parser("hook")
    h.add_argument("mode", choices=["begin", "finish"])
    args = parser.parse_args()
    if args.command == "hook":
        try:
            value = hook(args.mode)
        except Exception as exc:
            # Hooks never block a user's turn on a panel failure.
            value = {"systemMessage": f"Review Desk 未生成审查面板：{exc}"}
        print(json.dumps(value, ensure_ascii=False))
        return
    store = Store(args.data_dir or data_path(getattr(args, "project", None)))
    if args.command == "mcp":
        mcp(store)
        return
    if args.command == "serve":
        panel = PanelServer(store, args.port)
        atomic_json(store.root / "server.json", {"port": panel.port, "token": panel.token, "pid": os.getpid()})
        panel.start()
        try:
            while time.monotonic() - panel.last_used < 7200:
                time.sleep(10)
        except KeyboardInterrupt:
            pass
        finally:
            panel.close()
        return
    if args.command == "configure":
        root, _ = project_root(args.project)
        path = root / ".codex" / "review-desk.json"
        config = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        config["enabled"] = True
        if args.preview_url:
            validate_preview(args.preview_url)
            config["previewUrl"] = args.preview_url
        if args.files is not None:
            config["files"] = args.files
        atomic_json(path, config)
        value = {"config": str(path), "enabled": True, "next": "Review and trust the bundled hooks in Codex, or invoke $review-changes explicitly."}
    elif args.command == "begin":
        value = store.begin(args.project, args.title, args.files, args.preview_url)
    elif args.command == "refresh":
        value = store.refresh(args.session_id, args.summary, args.preview_url, json.loads(args.checks_json) if args.checks_json else None)
    elif args.command == "feedback":
        value = store.feedback(args.session_id)
    elif args.command == "acknowledge":
        value = store.acknowledge_feedback(args.session_id, args.delivery_id, args.revision)
    else:
        value = store.refresh(args.session_id)
        value["fallbackUrl"] = fallback_url(store, args.session_id)
    print(json.dumps(value, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stdin, "reconfigure"):
        sys.stdin.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
    try:
        main()
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        sys.exit(1)
