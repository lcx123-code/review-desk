const fs = require("node:fs");
const assert = require("node:assert/strict");
const { JSDOM, VirtualConsole } = require("jsdom");
const path = require("node:path");
const os = require("node:os");
const { spawnSync } = require("node:child_process");
if (!process.argv[2]) {
  const fixture = require("node:child_process").spawn(
    process.env.REVIEW_DESK_PYTHON || "python",
    [
      "-u",
      path.join(__dirname, "../scripts/demo.py"),
      "--workspace",
      fs.mkdtempSync(path.join(os.tmpdir(), "review-desk-ui-")),
    ],
    { stdio: ["ignore", "pipe", "pipe"] },
  );
  let output = "",
    errors = "";
  const timer = setTimeout(() => {
    console.error("Demo did not start:", errors);
    fixture.kill();
    process.exitCode = 1;
  }, 15000);
  fixture.stderr.on("data", (chunk) => (errors += chunk));
  fixture.stdout.on("data", (chunk) => {
    output += chunk;
    const line = output.split("\n").find((value) => value.startsWith("{"));
    if (!line || !output.includes("\n")) return;
    clearTimeout(timer);
    fixture.stdout.removeAllListeners("data");
    const workspace = fixture.spawnargs.at(-1);
    const result = spawnSync(
      process.execPath,
      [__filename, path.join(workspace, "demo-info.json")],
      { stdio: "inherit" },
    );
    fixture.kill();
    process.exitCode = result.status ?? 1;
  });
  fixture.on("error", (error) => {
    clearTimeout(timer);
    console.error(error.message);
    process.exitCode = 1;
  });
  fixture.on("exit", () => clearTimeout(timer));
} else {
  const info = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
  const realUrl = new URL(info.url);
  const origin = realUrl.origin;
  const token = realUrl.searchParams.get("token");
  const sid = realUrl.searchParams.get("session");
  const errors = [];
  async function api(path, body) {
    const options = { headers: { "X-Review-Token": token } };
    if (body) {
      options.method = "POST";
      options.headers["Content-Type"] = "application/json";
      options.body = JSON.stringify(body);
    }
    const response = await fetch(origin + path, options);
    const result = await response.json();
    if (!response.ok) throw Error(result.error);
    return result;
  }
  async function waitFor(fn, label) {
    const end = Date.now() + 6000;
    while (Date.now() < end) {
      if (fn()) return;
      await new Promise((r) => setTimeout(r, 20));
    }
    throw Error("Timeout: " + label);
  }
  function click(w, id) {
    w.document.getElementById(id).click();
  }
  function input(w, id, value) {
    const el = w.document.getElementById(id);
    el.value = value;
    el.dispatchEvent(new w.Event("input", { bubbles: true }));
  }
  async function addComment(w, text) {
    input(w, "comment-text", text);
    w.document
      .getElementById("comment-form")
      .dispatchEvent(
        new w.Event("submit", { bubbles: true, cancelable: true }),
      );
    await waitFor(
      () =>
        w.document
          .getElementById("comment-dialog")
          .classList.contains("hidden"),
      "comment saved",
    );
  }
  function makeDom(
    html,
    embedded = false,
    denyMessage = false,
    denyOpen = false,
  ) {
    const console = new VirtualConsole();
    console.on("jsdomError", (error) => {
      if (!error.message.includes("navigation")) errors.push(error.message);
    });
    const sent = [];
    const opened = [];
    const dom = new JSDOM(html, {
      url: realUrl.href,
      runScripts: "dangerously",
      resources: "usable",
      pretendToBeVisual: true,
      virtualConsole: console,
      beforeParse(w) {
        w.fetch = (path, options) => fetch(new URL(path, realUrl), options);
        w.navigator.clipboard = { writeText: async () => {} };
        w.URL.createObjectURL = () => origin + "/export.json";
        w.URL.revokeObjectURL = () => {};
        if (embedded) {
          const parent = {
            postMessage: async (message) => {
              if (message.id === undefined) return;
              let result = {},
                error;
              try {
                if (message.method === "tools/call") {
                  const { name, arguments: args } = message.params;
                  let data;
                  if (name === "review_action")
                    data = await api("/api/action", args);
                  if (name === "get_feedback")
                    data = await api("/api/feedback?session=" + sid);
                  if (name === "prepare_feedback")
                    data = await api("/api/prepare-feedback", args);
                  if (name === "record_delivery")
                    data = await api("/api/delivery", args);
                  if (name === "get_delivery_status")
                    data = await api(
                      "/api/delivery-status?session=" + args.session_id,
                    );
                  if (name === "check_preview")
                    data = await api(
                      "/api/check-preview?session=" + args.session_id,
                    );
                  if (name === "refresh_review")
                    data = await api("/api/refresh", args);
                  if (name === "open_review")
                    data = await api("/api/refresh", args);
                  if (name === "list_reviews") data = await api("/api/reviews");
                  result = { structuredContent: data, content: [] };
                }
                if (message.method === "ui/message") {
                  if (denyMessage === "result") result = { isError: true };
                  else {
                    if (denyMessage)
                      throw Error("Host does not support sending");
                    sent.push(message.params);
                    result = {};
                  }
                }
                if (message.method === "ui/open-link") {
                  if (denyOpen === "rpc")
                    throw Error("Host open-link unavailable");
                  opened.push(message.params);
                  result = denyOpen ? { isError: true } : {};
                }
                if (message.method === "ui/initialize") {
                  assert.equal(message.params.appInfo.name, "review-desk");
                  result = {
                    protocolVersion: "2026-01-26",
                    hostInfo: { name: "test-host", version: "1" },
                    hostCapabilities: {},
                  };
                }
              } catch (e) {
                error = { message: e.message };
              }
              setTimeout(
                () =>
                  w.dispatchEvent(
                    new w.MessageEvent("message", {
                      data: { jsonrpc: "2.0", id: message.id, result, error },
                      source: parent,
                      origin: "https://host.example",
                    }),
                  ),
                0,
              );
            },
          };
          Object.defineProperty(w, "parent", { value: parent });
        }
      },
    });
    return { dom, sent, opened };
  }
  (async () => {
    const html = await (await fetch(info.url)).text();
    const { dom } = makeDom(html);
    const w = dom.window;
    await waitFor(
      () => w.document.querySelectorAll("#diff-rows tr").length > 1,
      "code diff loaded",
    );
    assert.equal(
      w.document.getElementById("file-count").textContent,
      "3 个文件",
    );
    assert.ok(w.document.getElementById("panes").classList.contains("split"));
    assert.ok(
      !w.document.getElementById("code-pane").classList.contains("hidden"),
    );
    assert.ok(
      !w.document.getElementById("preview-pane").classList.contains("hidden"),
    );
    click(w, "split-view");
    await waitFor(
      () =>
        w.document.getElementById("preview-pane").classList.contains("hidden"),
      "single code view",
    );
    click(w, "tab-preview");
    await waitFor(
      () => w.document.getElementById("code-pane").classList.contains("hidden"),
      "single preview view",
    );
    click(w, "tab-checks");
    await waitFor(
      () =>
        !w.document.getElementById("checks-pane").classList.contains("hidden"),
      "checks view",
    );
    assert.ok(
      w.document.getElementById("preview-pane").classList.contains("hidden"),
    );
    click(w, "tab-code");
    click(w, "split-view");
    await waitFor(
      () => w.document.getElementById("panes").classList.contains("split"),
      "split restored",
    );
    const separator = w.document.getElementById("split-divider");
    separator.dispatchEvent(
      new w.KeyboardEvent("keydown", { key: "ArrowRight", bubbles: true }),
    );
    assert.equal(separator.getAttribute("aria-valuenow"), "60");
    separator.dispatchEvent(
      new w.KeyboardEvent("keydown", { key: "End", bubbles: true }),
    );
    assert.equal(separator.getAttribute("aria-valuenow"), "70");
    separator.dispatchEvent(
      new w.KeyboardEvent("keydown", { key: "Home", bubbles: true }),
    );
    assert.equal(separator.getAttribute("aria-valuenow"), "35");
    click(w, "feedback-close");
    await waitFor(
      () =>
        !w.document
          .getElementById("workspace")
          .classList.contains("feedback-open"),
      "feedback collapsed",
    );
    assert.equal(
      w.document
        .getElementById("feedback-toggle")
        .getAttribute("aria-expanded"),
      "false",
    );
    click(w, "feedback-toggle");
    await waitFor(
      () =>
        w.document
          .getElementById("workspace")
          .classList.contains("feedback-open"),
      "feedback reopened",
    );
    click(w, "file-picker-toggle");
    await waitFor(
      () =>
        !w.document.getElementById("file-picker").classList.contains("hidden"),
      "file picker opened",
    );
    input(w, "file-search", "timer.js");
    const timerFile = [...w.document.querySelectorAll("#file-list .file")].find(
      (file) => file.title === "timer.js",
    );
    assert.ok(timerFile && !timerFile.classList.contains("hidden"));
    assert.equal(
      w.document.querySelectorAll("#file-list .file:not(.hidden)").length,
      1,
    );
    timerFile.click();
    assert.equal(
      w.document.getElementById("file-path").textContent,
      "timer.js",
    );
    assert.ok(
      w.document.getElementById("file-picker").classList.contains("hidden"),
    );
    assert.ok(
      w.document.getElementById("diff-rows").textContent.includes("pause()"),
    );
    const codeRow = w.document.querySelector("#diff-rows tr.add");
    codeRow.click();
    await waitFor(
      () =>
        !w.document
          .getElementById("comment-dialog")
          .classList.contains("hidden"),
      "comment dialog",
    );
    await addComment(w, "这里需要处理空状态");
    assert.ok(
      w.document
        .getElementById("comments")
        .textContent.includes("这里需要处理空状态"),
    );
    assert.ok(
      w.document.querySelector(".comment-snippet").textContent.length > 0,
    );
    click(w, "mark-viewed");
    await waitFor(
      () =>
        w.document.getElementById("mark-viewed").textContent.includes("已看"),
      "viewed marker",
    );
    click(w, "approve");
    await waitFor(
      () => w.document.getElementById("toast").textContent.includes("解决反馈"),
      "approval guarded",
    );
    w.document.querySelector(".comment-meta button").click();
    await waitFor(
      () => w.document.querySelector(".comment.resolved"),
      "comment resolved",
    );
    click(w, "tab-preview");
    await waitFor(
      () =>
        !w.document.getElementById("preview-pane").classList.contains("hidden"),
      "preview tab",
    );
    assert.equal(
      w.document.getElementById("preview-iframe").getAttribute("src"),
      info.previewUrl,
    );
    click(w, "mobile");
    await waitFor(
      () =>
        w.document.getElementById("preview-frame").classList.contains("mobile"),
      "mobile viewport",
    );
    click(w, "pick");
    await waitFor(
      () =>
        w.document
          .getElementById("preview-frame")
          .classList.contains("picking"),
      "pick mode",
    );
    const overlay = w.document.getElementById("pick-overlay");
    overlay.getBoundingClientRect = () => ({
      left: 0,
      top: 0,
      width: 390,
      height: 490,
    });
    overlay.dispatchEvent(
      new w.MouseEvent("click", { clientX: 195, clientY: 245, bubbles: true }),
    );
    input(w, "element", "开始按钮");
    await addComment(w, "手机上按钮需要再大一点");
    click(w, "send");
    await waitFor(
      () =>
        !w.document.getElementById("copy-dialog").classList.contains("hidden"),
      "copy feedback dialog",
    );
    assert.ok(
      w.document.getElementById("copy-text").value.includes("开始按钮"),
    );
    click(w, "copy-close");
    click(w, "request-changes");
    await waitFor(
      () => w.document.getElementById("decision").textContent === "请求修改",
      "change decision",
    );
    // The real preview's script executes under JSDOM; this verifies behavior, not pixels.
    await waitFor(
      () =>
        w.document
          .getElementById("preview-iframe")
          .contentDocument?.getElementById("start") &&
        w.document.getElementById("preview-iframe").contentDocument
          ?.readyState === "complete",
      "preview document loaded",
    );
    const preview = w.document.getElementById("preview-iframe").contentWindow;
    preview.document.getElementById("start").click();
    assert.equal(preview.document.getElementById("start").textContent, "暂停");
    preview.document.getElementById("start").click();
    assert.equal(
      preview.document.getElementById("start").textContent,
      "继续专注",
    );
    preview.document.getElementById("reset").click();
    assert.equal(preview.document.getElementById("time").textContent, "25:00");
    const state = await api("/api/review?session=" + sid);
    let embeddedHtml = html.replace(
      /\/\*REVIEW_BOOT\*\/\s*null/,
      JSON.stringify({ embedded: true, session: state }).replaceAll(
        "<",
        "\\u003c",
      ),
    );
    const { dom: app, sent, opened } = makeDom(embeddedHtml, true);
    const aw = app.window;
    await waitFor(
      () =>
        aw.document
          .getElementById("bridge-note")
          .textContent.includes("反馈将提交到当前对话"),
      "host initialized",
    );
    assert.equal(
      aw.document.getElementById("preview-iframe").hasAttribute("src"),
      false,
    );
    assert.ok(
      aw.document.getElementById("preview-canvas").classList.contains("hidden"),
    );
    assert.ok(
      !aw.document
        .getElementById("preview-actions")
        .classList.contains("hidden"),
    );
    assert.equal(aw.document.getElementById("mobile").disabled, true);
    click(aw, "preview-launch");
    await waitFor(() => opened.length === 1, "host browser open-link");
    assert.equal(opened[0].url, info.previewUrl);
    assert.ok(
      !aw.document
        .getElementById("preview-open-fallback")
        .classList.contains("hidden"),
      "a host acceptance without navigation must retain an actionable fallback",
    );
    click(aw, "preview-copy-url");
    await waitFor(
      () => aw.document.getElementById("toast").textContent.includes("复制"),
      "preview address copy fallback",
    );
    const previewAddress = aw.document.getElementById("preview-copy-address");
    assert.equal(previewAddress.value, info.previewUrl);
    assert.equal(previewAddress.selectionEnd, info.previewUrl.length);
    click(aw, "preview-comment");
    await waitFor(
      () =>
        !aw.document
          .getElementById("comment-dialog")
          .classList.contains("hidden"),
      "external preview comment",
    );
    input(aw, "element", "运行页面右下角的重置按钮");
    await addComment(aw, "浏览器预览意见仍属于当前任务");
    const externalComment = (
      await api("/api/feedback?session=" + sid)
    ).comments.find((c) => c.text === "浏览器预览意见仍属于当前任务");
    assert.equal(externalComment.target.type, "preview");
    assert.equal(externalComment.target.url, info.previewUrl);
    assert.equal(externalComment.target.element, "运行页面右下角的重置按钮");
    assert.equal(externalComment.target.viewport, "");
    click(aw, "send");
    click(aw, "send");
    await waitFor(() => sent.length === 1, "MCP feedback sent");
    assert.equal(sent[0].role, "user");
    assert.ok(sent[0].content[0].text.includes(sid));
    assert.ok(sent[0].content[0].text.includes("手机上按钮"));
    await waitFor(
      () =>
        aw.document
          .getElementById("delivery-status")
          .textContent.includes("等待 Codex"),
      "submission is not receipt",
    );
    click(aw, "send");
    await new Promise((resolve) => setTimeout(resolve, 100));
    assert.equal(sent.length, 1);
    const receivedFeedback = await api("/api/feedback?session=" + sid);
    const ack = spawnSync(
      process.env.REVIEW_DESK_PYTHON || "python",
      [
        path.join(__dirname, "../scripts/review_desk.py"),
        "--data-dir",
        info.dataDir,
        "acknowledge",
        sid,
        receivedFeedback.delivery.id,
        "--revision",
        receivedFeedback.revision,
      ],
      { encoding: "utf8" },
    );
    assert.equal(ack.status, 0, ack.stderr);
    await waitFor(
      () =>
        aw.document
          .getElementById("delivery-status")
          .textContent.includes("Codex 已接收"),
      "agent receipt appears in panel",
    );
    click(aw, "general-comment");
    await waitFor(
      () =>
        !aw.document
          .getElementById("comment-dialog")
          .classList.contains("hidden"),
      "embedded comment dialog",
    );
    await addComment(aw, "内嵌面板反馈也能保存");
    assert.ok(
      (await api("/api/feedback?session=" + sid)).message.includes(
        "内嵌面板反馈",
      ),
    );
    click(aw, "task-switch");
    await waitFor(
      () => aw.document.querySelector(".review-task"),
      "task chooser",
    );
    aw.document.querySelector(".review-task").click();
    await waitFor(
      () =>
        aw.document.getElementById("task-dialog").classList.contains("hidden"),
      "explicit task selected",
    );
    click(aw, "filter-open");
    await waitFor(
      () => aw.document.querySelectorAll(".comment.resolved").length === 0,
      "pending filter",
    );
    click(aw, "filter-all");
    await waitFor(
      () => aw.document.querySelector(".comment.resolved"),
      "all feedback history",
    );
    const { dom: unavailable } = makeDom(embeddedHtml, true, true);
    const uw = unavailable.window;
    await waitFor(
      () =>
        uw.document
          .getElementById("connection-title")
          .textContent.includes("已连接"),
      "unavailable host initialized",
    );
    click(uw, "send");
    await waitFor(
      () =>
        !uw.document.getElementById("copy-dialog").classList.contains("hidden"),
      "failed send falls back to copy",
    );
    assert.ok(
      uw.document.getElementById("copy-text").value.includes("手机上按钮"),
    );
    assert.ok(
      uw.document
        .getElementById("connection-title")
        .textContent.includes("不可用"),
    );
    unavailable.window.close();
    const { dom: rejectedMessage } = makeDom(embeddedHtml, true, "result");
    await waitFor(
      () =>
        rejectedMessage.window.document
          .getElementById("connection-title")
          .textContent.includes("已连接"),
      "message-rejected host initialized",
    );
    click(rejectedMessage.window, "send");
    await waitFor(
      () =>
        !rejectedMessage.window.document
          .getElementById("copy-dialog")
          .classList.contains("hidden"),
      "isError submission falls back to copy",
    );
    assert.equal(
      (await api("/api/delivery-status?session=" + sid)).delivery.state,
      "failed",
    );
    rejectedMessage.window.close();
    for (const denial of [true, "rpc"]) {
      const { dom: refused } = makeDom(embeddedHtml, true, false, denial);
      const rw = refused.window;
      await waitFor(
        () =>
          rw.document
            .getElementById("connection-title")
            .textContent.includes("已连接"),
        "refused-link host initialized",
      );
      click(rw, "preview-launch");
      await waitFor(
        () =>
          rw.document
            .getElementById("preview-state-description")
            .textContent.includes("未能"),
        "host rejected open-link offers direct link",
      );
      assert.equal(
        rw.document.getElementById("preview-open-direct").href,
        info.previewUrl + "/",
      );
      assert.ok(
        rw.document
          .getElementById("preview-state-description")
          .textContent.includes("未能"),
      );
      assert.equal(
        rw.document.getElementById("preview-iframe").hasAttribute("src"),
        false,
      );
      refused.window.close();
    }
    const violation = new w.Event("securitypolicyviolation");
    Object.assign(violation, {
      effectiveDirective: "frame-src",
      blockedURI: info.previewUrl,
    });
    w.document.dispatchEvent(violation);
    assert.ok(
      w.document.getElementById("preview-canvas").classList.contains("hidden"),
    );
    assert.equal(
      w.document.getElementById("preview-iframe").hasAttribute("src"),
      false,
    );
    assert.ok(
      w.document
        .getElementById("preview-state-description")
        .textContent.includes("无法直接显示"),
    );
    click(w, "refresh");
    await waitFor(
      () => w.document.getElementById("toast").textContent.includes("已读取"),
      "refresh after preview failure",
    );
    assert.equal(
      w.document.getElementById("preview-iframe").hasAttribute("src"),
      false,
    );
    assert.ok(
      !w.document
        .getElementById("preview-actions")
        .classList.contains("hidden"),
    );
    click(w, "preview-address-edit");
    await waitFor(
      () =>
        !w.document
          .getElementById("preview-address-dialog")
          .classList.contains("hidden"),
      "edit preview address",
    );
    w.document.getElementById("preview-address").value = "http://127.0.0.1:1";
    w.document
      .getElementById("preview-address-form")
      .dispatchEvent(
        new w.Event("submit", { bubbles: true, cancelable: true }),
      );
    await waitFor(
      () =>
        w.document
          .getElementById("preview-health")
          .textContent.includes("未响应"),
      "dead preview has actionable state",
    );
    assert.ok(
      w.document
        .getElementById("preview-state-description")
        .textContent.includes("启动项目"),
    );
    click(w, "preview-address-edit");
    await waitFor(
      () =>
        !w.document
          .getElementById("preview-address-dialog")
          .classList.contains("hidden"),
      "edit recovered address",
    );
    w.document.getElementById("preview-address").value = info.previewUrl;
    w.document
      .getElementById("preview-address-form")
      .dispatchEvent(
        new w.Event("submit", { bubbles: true, cancelable: true }),
      );
    await waitFor(
      () =>
        w.document
          .getElementById("preview-health")
          .textContent.includes("在线"),
      "updated preview recovers",
    );
    const staticHtml = html.replace(
      /\/\*REVIEW_BOOT\*\/\s*null/,
      JSON.stringify({ embedded: true, session: null, version: "0.3.0" }),
    );
    const { dom: staticApp } = makeDom(staticHtml, true);
    staticApp.window.dispatchEvent(
      new staticApp.window.MessageEvent("message", {
        source: staticApp.window.parent,
        origin: "https://host.example",
        data: {
          jsonrpc: "2.0",
          method: "ui/notifications/tool-result",
          params: { structuredContent: state },
        },
      }),
    );
    await waitFor(
      () =>
        staticApp.window.document.getElementById("title").textContent ===
        state.title,
      "cached template binds incoming task result",
    );
    assert.ok(
      staticApp.window.document
        .getElementById("task-dialog")
        .classList.contains("hidden"),
    );
    staticApp.window.close();
    assert.deepEqual(errors, []);
    app.window.close();
    dom.window.close();
    console.log(
      JSON.stringify(
        {
          passed: true,
          checks: [
            "diff loads",
            "code comment persistence",
            "viewed marker",
            "approval guard",
            "comment resolution",
            "live preview tab",
            "mobile width switch",
            "preview position feedback",
            "standalone copy feedback",
            "change request",
            "preview start/pause/reset",
            "MCP initialization",
            "MCP feedback message",
            "embedded comment persistence",
            "duplicate send guarded",
            "task chooser selection",
            "pending comment filter",
            "failed send preserves feedback with copy fallback",
            "split and single view with checks switching",
            "keyboard splitter bounds",
            "feedback drawer collapse and reopen",
            "searchable file picker navigation",
            "real code context in feedback cards",
            "embedded HTTP preview uses host browser without iframe request",
            "host open-link carries exact preview URL",
            "accepted open request retains direct link and selectable preview address",
            "external preview comments retain task URL without invented coordinates",
            "host RPC error and isError response expose direct open fallback",
            "frame CSP failure survives refresh with visible open action",
            "host submission is not agent receipt and cannot resend unchanged payload",
            "agent acknowledgement updates the panel from durable delivery status",
            "message isError response does not claim delivery",
            "dead preview address can be updated and recover in the same task",
            "immutable embedded template binds task notification without chooser race",
          ],
          limitations:
            "JSDOM does not validate browser pixels, native Codex embedding or actual iframe policy.",
        },
        null,
        2,
      ),
    );
  })().catch((error) => {
    console.error(error.stack);
    process.exit(1);
  });
}
