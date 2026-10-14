import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "review_desk.py"
spec = importlib.util.spec_from_file_location("review_desk", SCRIPT)
desk = importlib.util.module_from_spec(spec)
spec.loader.exec_module(desk)


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "project"
        self.root.mkdir()
        self.store = desk.Store(Path(self.temp.name) / "data")
        self.write("app.py", "before\n")
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        subprocess.run(["git", "-C", str(self.root), "add", "app.py"], check=True)

    def write(self, name, value):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value, encoding="utf-8")

    def begin_change(self):
        s = self.store.begin(str(self.root))
        self.write("app.py", "after\n")
        return self.store.refresh(s["id"])

    def act(self, s, action, **kwargs):
        return self.store.mutate(s["id"], action, {"revision": s["revision"], **kwargs})

    def test_dirty_baseline_new_and_deleted_files(self):
        self.write("app.py", "user edit\n")
        self.write("keep.txt", "pre-existing untracked\n")
        s = self.store.begin(str(self.root))
        self.write("app.py", "user edit\nagent edit\n")
        self.write("new.ts", "export const value = 1;\n")
        (self.root / "keep.txt").unlink()
        view = self.store.refresh(s["id"])
        files = {f["path"]: f for f in view["files"]}
        self.assertEqual(files["app.py"]["deleted"], 0)
        self.assertEqual(files["app.py"]["added"], 1)
        self.assertEqual(files["new.ts"]["status"], "added")
        self.assertEqual(files["keep.txt"]["status"], "deleted")

    def test_unchanged_dirty_file_is_not_in_task_diff(self):
        self.write("user.txt", "pre-existing change\n")
        s = self.begin_change()
        self.assertEqual([f["path"] for f in s["files"]], ["app.py"])

    def test_line_comments_and_stale_approval(self):
        s = self.begin_change()
        s = self.act(s, "comment", text="fix this", target={"type": "code", "path": "app.py", "side": "new", "line": 1})
        with self.assertRaisesRegex(ValueError, "反馈"):
            self.act(s, "decision", decision="approved")
        s = self.act(s, "comment_status", comment_id=s["comments"][0]["id"], status="resolved")
        s = self.act(s, "decision", decision="approved")
        self.assertEqual(s["decision"]["value"], "approved")
        self.write("app.py", "another change\n")
        with self.assertRaisesRegex(ValueError, "更新"):
            self.act(s, "comment", text="old UI", target={"type": "general"})
        new = self.store.refresh(s["id"])
        self.assertIsNone(new["decision"])
        self.assertNotEqual(new["revision"], s["comments"][0]["revision"])

    def test_invalid_line_rejected_and_deleted_line_supported(self):
        s = self.begin_change()
        with self.assertRaisesRegex(ValueError, "line"):
            self.act(s, "comment", text="outside diff", target={"type": "code", "path": "app.py", "line": 900})
        s = self.act(s, "comment", text="keep original", target={"type": "code", "path": "app.py", "side": "old", "line": 1})
        self.assertEqual(s["comments"][0]["target"]["side"], "old")

    def test_checks_and_viewed_reset_on_change(self):
        s = self.begin_change()
        s = self.store.refresh(s["id"], checks=[{"name": "tests", "status": "passed"}])
        s = self.act(s, "viewed", path="app.py", viewed=True)
        self.write("app.py", "next\n")
        s = self.store.refresh(s["id"])
        self.assertEqual(s["checks"], [])
        self.assertEqual(s["viewed"], {})
        s = self.store.refresh(s["id"], checks=[{"name": "tests", "status": "failed"}])
        with self.assertRaisesRegex(ValueError, "失败"):
            self.act(s, "decision", decision="approved")

    def test_binary_changes_cannot_claim_full_approval(self):
        s = self.store.begin(str(self.root))
        (self.root / "image.bin").write_bytes(b"\0\x01\x02")
        s = self.store.refresh(s["id"])
        self.assertFalse(s["complete"])
        with self.assertRaisesRegex(ValueError, "完整"):
            self.act(s, "decision", decision="approved")

    def test_no_final_newline_is_visible(self):
        s = self.store.begin(str(self.root))
        self.write("app.py", "before")
        s = self.store.refresh(s["id"])
        self.assertEqual(s["stats"]["files"], 1)
        self.assertTrue(any(row.get("noNewline") for row in s["files"][0]["rows"]))

    def test_scope_excludes_state_and_secrets(self):
        self.write(".env", "SECRET=example\n")
        self.write(".env.local", "SECRET=local\n")
        self.write(".codex/review-desk/session.json", "{}")
        s = self.store.begin(str(self.root))
        baseline = self.store.load(s["id"])["baseline"]
        self.assertNotIn(".env", baseline)
        self.assertNotIn(".env.local", baseline)
        self.assertNotIn(".codex/review-desk/session.json", baseline)

    def test_explicit_scope_non_git_and_path_escape(self):
        plain = Path(self.temp.name) / "plain"
        plain.mkdir()
        with self.assertRaisesRegex(ValueError, "explicit"):
            self.store.begin(str(plain))
        s = self.store.begin(str(plain), files=["planned.txt"])
        (plain / "planned.txt").write_text("new", encoding="utf-8")
        s = self.store.refresh(s["id"])
        self.assertEqual(s["files"][0]["status"], "added")
        with self.assertRaisesRegex(ValueError, "inside"):
            self.store.begin(str(plain), files=["../project/app.py"])
        with self.assertRaises(ValueError):
            self.store.get("../../outside")

    def test_preview_feedback_roundtrip_and_preview_validation(self):
        s = self.begin_change()
        s = self.store.refresh(s["id"], preview_url="http://localhost:5173")
        s = self.act(s, "comment", text="按钮太小", target={"type": "preview", "element": "保存按钮", "viewport": "390 × 490"})
        s = self.act(s, "decision", decision="changes_requested")
        f = self.store.feedback(s["id"])
        self.assertIn("保存按钮", f["message"])
        self.assertIn("请求修改", f["message"])
        self.assertEqual(f["comments"][0]["target"]["url"], "http://localhost:5173")
        for url in ("javascript:alert(1)", "file:///test", "https://user:pass@example.com"):
            with self.assertRaises(ValueError):
                self.store.refresh(s["id"], preview_url=url)

    def test_local_http_rejects_missing_token_and_cross_origin_mutation(self):
        s = self.begin_change()
        panel = desk.PanelServer(self.store)
        panel.start()
        self.addCleanup(panel.close)
        base = f"http://127.0.0.1:{panel.port}"
        with self.assertRaises(HTTPError) as error:
            urlopen(base + "/api/review?session=" + s["id"])
        self.assertEqual(error.exception.code, 403)
        with urlopen(Request(base + "/api/review?session=" + s["id"], headers={"X-Review-Token": panel.token})) as result:
            self.assertEqual(json.load(result)["id"], s["id"])
        payload = json.dumps({"session_id": s["id"], "revision": s["revision"], "action": "comment", "text": "x"}).encode()
        req = Request(base + "/api/action", payload, headers={"Content-Type": "application/json", "X-Review-Token": panel.token, "Origin": "https://other.example"})
        with self.assertRaises(HTTPError) as error:
            urlopen(req)
        self.assertEqual(error.exception.code, 403)

    def test_mcp_stdio_handshake_tools_and_embedded_resource(self):
        process = subprocess.Popen([sys.executable, str(SCRIPT), "--data-dir", str(self.store.root), "mcp"],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8")
        def rpc(mid, method, params=None):
            process.stdin.write(json.dumps({"jsonrpc": "2.0", "id": mid, "method": method, "params": params or {}}, ensure_ascii=False) + "\n")
            process.stdin.flush()
            return json.loads(process.stdout.readline())["result"]
        try:
            initialized = rpc(1, "initialize", {"protocolVersion": "2025-11-25"})
            self.assertEqual(initialized["serverInfo"]["name"], "review-desk")
            tools = rpc(2, "tools/list")["tools"]
            open_tool = next(t for t in tools if t["name"] == "open_review")
            self.assertEqual(open_tool["_meta"]["ui"]["resourceUri"], desk.UI_URI)
            created = rpc(3, "tools/call", {"name": "begin_review", "arguments": {"project_path": str(self.root), "title": "</script><script>unsafe</script>"}})["structuredContent"]
            self.write("app.py", "changed\n")
            opened = rpc(4, "tools/call", {"name": "open_review", "arguments": {"session_id": created["id"]}})
            self.assertIn("fallbackUrl", opened["structuredContent"])
            resource = rpc(5, "resources/read", {"uri": desk.UI_URI})["contents"][0]
            self.assertEqual(resource["mimeType"], desk.UI_MIME)
            self.assertNotIn("</script><script>unsafe</script>", resource["text"])
            self.assertNotIn(created["id"], resource["text"])
            self.assertIn('"session": null', resource["text"])
            wrong = rpc(6, "tools/call", {"name": "get_feedback", "arguments": {"session_id": "invalid"}})
            self.assertTrue(wrong["isError"])
            launcher_tool = next(t for t in tools if t["name"] == "open_review_panel")
            self.assertEqual(launcher_tool["inputSchema"]["required"], [])
            self.assertEqual(launcher_tool["_meta"]["openai/ui"]["entrypoints"], [{"type": "thread"}])
            launcher = rpc(7, "tools/call", {"name": "open_review_panel"})["structuredContent"]
            self.assertTrue(launcher["launcher"])
            self.assertEqual(launcher["reviews"][0]["id"], created["id"])
            self.assertNotIn("files", launcher["reviews"][0])
            resource = rpc(8, "resources/read", {"uri": desk.UI_URI})["contents"][0]
            self.assertIn('"session": null', resource["text"])
            self.assertEqual(resource["_meta"]["ui"]["csp"]["frameDomains"], [])
        finally:
            process.stdin.close()
            process.wait(timeout=5)
            process.stdout.close()
            process.stderr.close()
            pointer = self.store.root / "server.json"
            if pointer.exists():
                info = json.loads(pointer.read_text())
                try:
                    with urlopen(Request("http://127.0.0.1:" + str(info["port"]) + "/health", headers={"X-Review-Token":info["token"]})) as response:
                        self.assertEqual(json.load(response)["version"], desk.VERSION)
                finally:
                    handle = None
                    if os.name == "nt":
                        import ctypes
                        ctypes.windll.kernel32.OpenProcess.restype = ctypes.c_void_p
                        handle = ctypes.windll.kernel32.OpenProcess(0x100000, False, info["pid"])
                    os.kill(info["pid"], 15)
                    if handle:
                        ctypes.windll.kernel32.WaitForSingleObject(ctypes.c_void_p(handle), 2000)
                        ctypes.windll.kernel32.CloseHandle(ctypes.c_void_p(handle))

    def test_task_list_does_not_expose_source_and_survives_bad_state(self):
        first = self.store.begin(str(self.root), title="First")
        second_root = Path(self.temp.name) / "other"
        second_root.mkdir()
        second = self.store.begin(str(second_root), title="Second", files=[])
        (self.store.root / "sessions" / ("f" * 24 + ".json")).write_text("invalid JSON", encoding="utf-8")
        reviews = self.store.list_reviews()["reviews"]
        self.assertEqual({r["id"] for r in reviews}, {first["id"], second["id"]})
        self.assertEqual({r["project"] for r in reviews}, {str(self.root.resolve()), str(second_root.resolve())})
        for review in reviews:
            self.assertNotIn("baseline", review)
            self.assertNotIn("current", review)
            self.assertNotIn("files", review)

    def test_approved_feedback_does_not_request_more_fixes(self):
        session = self.begin_change()
        self.store.mutate(session["id"], "decision", {"revision": session["revision"], "decision": "approved"})
        message = self.store.feedback(session["id"])["message"]
        self.assertIn("审查已通过", message)
        self.assertNotIn("修复后", message)

    def test_delivery_receipt_duplicate_retry_and_restart(self):
        s = self.begin_change()
        s = self.act(s, "comment", text="检查按钮", target={"type":"general"})
        prepared = self.store.prepare_feedback(s["id"])
        delivery = prepared["delivery"]
        self.assertIn("Delivery: " + delivery["id"], prepared["message"])
        self.assertEqual(self.store.prepare_feedback(s["id"])["delivery"]["id"], delivery["id"])
        self.store.record_delivery(s["id"], delivery["id"], "submitted")
        reloaded = desk.Store(self.store.root)
        self.assertEqual(reloaded.delivery_status(s["id"])["delivery"]["state"], "submitted")
        self.assertEqual(reloaded.feedback(s["id"])["delivery"]["state"], "submitted")
        with self.assertRaises(ValueError):
            reloaded.record_delivery(s["id"], delivery["id"], "received")
        reloaded.acknowledge_feedback(s["id"], delivery["id"], s["revision"])
        reloaded.record_delivery(s["id"], delivery["id"], "submitted")
        self.assertEqual(reloaded.delivery_status(s["id"])["delivery"]["state"], "received")
        s = self.act(s, "comment", text="还有另一条意见", target={"type":"general"})
        self.assertFalse(reloaded.delivery_status(s["id"])["delivery"]["current"])
        with self.assertRaises(ValueError):
            reloaded.acknowledge_feedback(s["id"], delivery["id"], s["revision"])
        newer = reloaded.prepare_feedback(s["id"])["delivery"]
        self.assertNotEqual(newer["id"], delivery["id"])
        reloaded.record_delivery(s["id"], newer["id"], "failed")
        self.assertEqual(reloaded.prepare_feedback(s["id"])["delivery"]["id"], newer["id"])
        self.assertEqual(reloaded.delivery_status(s["id"])["delivery"]["state"], "prepared")

    def test_delivery_rejects_new_code_and_mismatched_revision(self):
        s = self.begin_change()
        s = self.act(s, "comment", text="修改", target={"type":"general"})
        delivery = self.store.prepare_feedback(s["id"])["delivery"]
        with self.assertRaises(ValueError):
            self.store.acknowledge_feedback(s["id"], delivery["id"], "wrong")
        self.write("app.py", "externally changed\n")
        with self.assertRaises(ValueError):
            self.store.prepare_feedback(s["id"])
        self.store.refresh(s["id"])
        with self.assertRaises(ValueError):
            self.store.acknowledge_feedback(s["id"], delivery["id"], s["revision"])

    def test_preview_health_recovery_and_remote_probe_scope(self):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        import threading
        class Handler(BaseHTTPRequestHandler):
            def do_HEAD(self):
                self.send_response(200)
                self.end_headers()
            def log_message(self, *_):
                pass
        server = ThreadingHTTPServer(("127.0.0.1",0),Handler)
        threading.Thread(target=server.serve_forever,daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        url = "http://127.0.0.1:" + str(server.server_address[1])
        self.assertEqual(desk.probe_preview(url)["state"], "reachable")
        self.assertEqual(desk.probe_preview("https://unqueried.invalid/path")["state"], "external")
        self.assertEqual(desk.probe_preview("")["state"], "missing")
        s = self.begin_change()
        s = self.store.refresh(s["id"],preview_url="http://127.0.0.1:1")
        self.write(".codex/review-desk.json", json.dumps({"previewUrl":url}))
        status = self.store.check_preview(s["id"])
        self.assertTrue(status["recovered"])
        self.assertEqual(self.store.get(s["id"])["previewUrl"], url)


if __name__ == "__main__":
    unittest.main(verbosity=2)
