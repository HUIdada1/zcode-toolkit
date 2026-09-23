/**
 * ZCode 输入框「增强提示词」按钮 —— 润色草稿
 * ============================================
 * 在输入框工具栏注入一个**纯图标**按钮（不显示任何文案）：
 *   点击 → 取当前草稿 → 经 preload 桥 / main handler 用**界面当前选中的模型**调一次补全 →
 *   把结果写回输入框，图标临时变成「撤销」可一键还原。
 *
 * 三种状态只靠图标 + 悬浮提示区分（不占宽度、不出现中文文本）：
 *   待机  ✦ 星芒图标   title「增强提示词」
 *   进行中 ◌ 旋转图标   title「增强中…」
 *   可还原 ↺ 撤销图标   title「恢复原文」
 *
 * 依赖：由 zcode_patcher.py --enhance-prompt 注入，配套 preload 桥（enhancePrompt）与
 *      main handler（zcode:enhance-prompt）。缺桥时按钮自动隐藏，不报错。
 *
 * 安全：只读输入框内容、只写输入框与自己的按钮；不额外发网络请求（请求在主进程侧发起）；
 *      异常静默，找不到输入框时自清理。诊断：window.__zenhanceDiag
 */
(() => {
  if (window.__zenhance) return;
  window.__zenhance = true;

  const MARK = "data-zenhance";
  const BTN_ID = "zcode-enhance-prompt-btn";
  const STYLE_ID = "zenhance-style";
  const diag = (window.__zenhanceDiag = window.__zenhanceDiag || {});
  diag.scriptVersion = "1.2";

  const TIP_IDLE = "增强提示词";
  const TIP_BUSY = "增强中…";
  const TIP_REVERT = "恢复原文";
  const REVERT_WINDOW_MS = 20000;

  const COMPOSER_INPUT_SELECTORS = [
    "[data-testid='v4-composer-input']",
    "[data-testid*='composer-input']",
    "textarea[data-testid]",
    "form textarea",
    "textarea",
    "[contenteditable='true']",
  ];

  // ---------- 图标（纯 SVG，无文案） ----------
  const ICONS = {
    // 星芒：增强
    idle: '<path fill="currentColor" d="M12 2.6l1.75 4.9 4.9 1.75-4.9 1.75L12 15.9l-1.75-4.9L5.35 9.25l4.9-1.75z"/>'
      + '<path fill="currentColor" d="M18.6 14.2l.85 2.3 2.3.85-2.3.85-.85 2.3-.85-2.3-2.3-.85 2.3-.85z"/>',
    // 旋转：进行中
    busy: '<circle cx="12" cy="12" r="8.5" fill="none" stroke="currentColor" stroke-width="2.6" '
      + 'stroke-opacity="0.25"></circle>'
      + '<path d="M12 3.5a8.5 8.5 0 0 1 8.5 8.5" fill="none" stroke="currentColor" stroke-width="2.6" '
      + 'stroke-linecap="round"></path>',
    // 撤销箭头：恢复原文
    revert: '<path fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" '
      + 'stroke-linejoin="round" d="M4.5 9.5h9a5 5 0 0 1 0 10H9"></path>'
      + '<path fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" '
      + 'stroke-linejoin="round" d="M8 5.5l-3.5 4 3.5 4"></path>',
  };

  function ensureStyle() {
    if (document.getElementById(STYLE_ID)) return;
    try {
      const st = document.createElement("style");
      st.id = STYLE_ID;
      st.textContent = [
        "#" + BTN_ID + "{display:inline-flex;align-items:center;justify-content:center;width:28px;height:28px;",
        "padding:0;border-radius:8px;border:0.5px solid transparent;background:transparent;cursor:pointer;",
        "color:var(--color-foreground-subtle,#5b6470);transition:background .15s,color .15s,opacity .15s}",
        "#" + BTN_ID + ":hover{background:rgba(127,127,127,.14);color:var(--color-foreground,#1b1f24)}",
        "#" + BTN_ID + "[disabled]{opacity:.55;cursor:default}",
        "#" + BTN_ID + "[data-mode='revert']{color:var(--color-warning,#b45309)}",
        "#" + BTN_ID + " svg{width:14px;height:14px;display:block}",
        "#" + BTN_ID + " svg.zenhance-spin{animation:zenhanceSpin .9s linear infinite;transform-origin:50% 50%}",
        "@keyframes zenhanceSpin{to{transform:rotate(360deg)}}",
        ".zenhance-toast{position:fixed;left:50%;bottom:96px;transform:translateX(-50%);z-index:2147483000;",
        "background:var(--color-background,#fff);color:var(--color-foreground,#1b1f24);",
        "border:1px solid rgba(127,127,127,.28);border-radius:10px;padding:8px 14px;font-size:12px;",
        "box-shadow:0 8px 24px rgba(0,0,0,.18);max-width:72vw;white-space:pre-wrap}",
      ].join("");
      document.head.appendChild(st);
    } catch (err) { /* 静默 */ }
  }

  function toast(msg, ms) {
    try {
      const old = document.querySelector(".zenhance-toast");
      if (old) old.remove();
      const el = document.createElement("div");
      el.className = "zenhance-toast";
      el.textContent = String(msg);
      document.body.appendChild(el);
      setTimeout(() => el.remove(), ms || 3600);
    } catch (err) { /* 静默 */ }
  }

  // ---------- 输入框定位 / 读写 ----------
  function findInput() {
    for (const sel of COMPOSER_INPUT_SELECTORS) {
      let els = [];
      try { els = Array.from(document.querySelectorAll(sel)); } catch (err) { continue; }
      const vis = els.filter((e) => e.offsetParent != null);
      const pool = vis.length ? vis : els;
      if (pool.length) {
        pool.sort((a, b) => b.getBoundingClientRect().top - a.getBoundingClientRect().top);
        return pool[0];
      }
    }
    return null;
  }

  function readText(el) {
    if (!el) return "";
    if (typeof el.value === "string") return el.value;
    return el.innerText || el.textContent || "";
  }

  /** 写回受控输入框：优先 execCommand('insertText')（textarea 与 contenteditable 都能触发
   *  React 受控更新），失败退回「原生 setter + input 事件」。 */
  function writeText(el, text) {
    if (!el) return false;
    try { el.focus(); } catch (err) { /* ignore */ }
    try {
      if (typeof el.select === "function") el.select();
      else if (typeof el.setSelectionRange === "function") el.setSelectionRange(0, readText(el).length);
      else {
        const range = document.createRange();
        range.selectNodeContents(el);
        const sel = window.getSelection();
        sel.removeAllRanges();
        sel.addRange(range);
      }
      if (document.execCommand && document.execCommand("insertText", false, text)) return true;
    } catch (err) { /* 落到兜底 */ }
    try {
      const proto = el instanceof HTMLTextAreaElement
        ? HTMLTextAreaElement.prototype
        : (el instanceof HTMLInputElement ? HTMLInputElement.prototype : null);
      const setter = proto && Object.getOwnPropertyDescriptor(proto, "value")?.set;
      if (setter) setter.call(el, text);
      else el.textContent = text;
      el.dispatchEvent(new Event("input", { bubbles: true }));
      return true;
    } catch (err) {
      return false;
    }
  }

  /** 当前选中的模型：优先读模型按钮上的 data-model-current-value（形如 providerId/modelId），
   *  读不到就退化为该按钮的显示文案（主进程会按 modelId / name 反查配置）。
   *  注意：同一页面可能有多个带该属性的节点（弹窗、工作流设置面板等），必须挑
   *  **可见的、最靠下的**那个 —— 否则会把后台面板里的模型当成本会话选的模型，
   *  这正是「本机能润色、别人报 Model is unavailable」的一个直接来源。 */
  function currentModel() {
    let value = "", label = "";
    try {
      const all = Array.from(document.querySelectorAll("[data-model-current-value]"));
      if (all.length) {
        const vis = all.filter((e) => e.offsetParent != null);
        const pool = vis.length ? vis : all;
        pool.sort((a, b) => b.getBoundingClientRect().top - a.getBoundingClientRect().top);
        const el = pool[0];
        value = String(el.getAttribute("data-model-current-value") || "").trim();
        // 显示名取该节点里最长的可见文本（模型名 + 可能的连接方式后缀）
        const t = String(el.textContent || "").replace(/\s+/g, " ").trim();
        label = t;
        diag.modelCandidates = all.length;
      }
    } catch (err) { /* ignore */ }
    return { value, label };
  }

  // ---------- 按钮 ----------
  let btn = null;
  let busy = false;
  let original = null;
  let revertTimer = null;

  function setButton(mode) {
    if (!btn) return;
    const tip = mode === "busy" ? TIP_BUSY : (mode === "revert" ? TIP_REVERT : TIP_IDLE);
    btn.setAttribute("data-mode", mode);
    btn.setAttribute("title", tip);
    btn.setAttribute("aria-label", tip);
    btn.disabled = busy;
    btn.innerHTML = "";
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("aria-hidden", "true");
    if (mode === "busy") svg.setAttribute("class", "zenhance-spin");
    svg.innerHTML = ICONS[mode] || ICONS.idle;
    btn.appendChild(svg);
  }

  function armRevert() {
    clearTimeout(revertTimer);
    setButton("revert");
    revertTimer = setTimeout(() => {
      original = null;
      setButton("idle");
    }, REVERT_WINDOW_MS);
  }

  async function enhance() {
    const el = findInput();
    if (!el) { toast("没找到输入框"); return; }
    const text = readText(el).trim();
    if (!text) { toast("请先输入要增强的内容"); return; }

    const api = window.zcode;
    if (!api || typeof api.enhancePrompt !== "function") {
      toast("通信桥不可用：请重跑 --enhance-prompt 注入后重启 ZCode");
      return;
    }

    busy = true;
    setButton("busy");
    const m = currentModel();
    diag.lastRequest = { modelValue: m.value, modelLabel: m.label, chars: text.length };
    try {
      const res = await api.enhancePrompt(text, m.value, m.label);
      diag.lastResult = res && res.success
        ? { model: res.model, provider: res.provider, how: res.how, chars: String(res.text || "").length }
        : { code: (res && res.code) || "unknown", error: String((res && res.error) || "未知错误") };
      if (!res || !res.success) {
        // 「模型不可用」是配置问题，不是网络问题 —— 给出可执行的下一步，而不是甩一个 HTTP 400
        const tip = String((res && res.tip) || "");
        const head = "增强失败：" + String((res && res.error) || "未知错误");
        const body = tip ? ("\n→ " + tip) : "";
        toast(head + body, tip ? 9000 : 5200);
        if (res && (res.code === "model" || res.code === "auth" || res.code === "no-model")) {
          console.warn("[zcode-enhance] 配置类失败", res);
        }
        return;
      }
      const enhanced = String(res.text || "").trim();
      if (!enhanced) { toast("增强失败：模型返回空内容", 5200); return; }
      original = text;
      if (!writeText(el, enhanced)) {
        toast("已生成结果但写回输入框失败，请手动复制：\n" + enhanced.slice(0, 200), 8000);
        return;
      }
      armRevert();
      toast("已用「" + (res.model || "当前模型") + "」增强");
    } catch (err) {
      diag.lastResult = { code: "exception", error: String(err) };
      toast("增强异常：" + String((err && err.message) || err), 5200);
    } finally {
      busy = false;
      if (btn) {
        if (btn.getAttribute("data-mode") === "revert") btn.disabled = false;
        else setButton("idle");
      }
    }
  }

  function onRevert() {
    const el = findInput();
    if (el && original != null) writeText(el, original);
    original = null;
    clearTimeout(revertTimer);
    setButton("idle");
  }

  function ensureButton() {
    ensureStyle();
    const input = findInput();
    if (!input) { if (btn) { btn.remove(); btn = null; } diag.hiddenReason = "未找到输入框"; return; }
    let host = null;
    try {
      const send = document.querySelector("[data-testid='v4-composer-send']");
      if (send && send.parentElement) host = send.parentElement;
    } catch (err) { /* ignore */ }
    if (!host) {
      const card = input.closest("[data-testid='v4-composer']") || input.parentElement;
      host = card && (card.querySelector("div") || card);
    }
    if (!host) { diag.hiddenReason = "未找到工具栏行"; return; }

    if (btn && btn.isConnected) {
      if (btn.parentElement !== host) host.insertBefore(btn, host.firstChild);
      return;
    }
    btn = document.createElement("button");
    btn.id = BTN_ID;
    btn.type = "button";
    btn.setAttribute(MARK, "1");
    btn.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      if (busy) return;
      if (btn.getAttribute("data-mode") === "revert") onRevert();
      else enhance();
    });
    host.insertBefore(btn, host.firstChild);
    setButton("idle");
    diag.hiddenReason = null;
    diag.buttonAttached = true;
  }

  function start() {
    ensureStyle();
    let timer = null;
    const schedule = () => {
      if (timer) return;
      timer = setTimeout(() => { timer = null; if (!document.hidden) ensureButton(); }, 300);
    };
    try {
      new MutationObserver(schedule).observe(document.body, { childList: true, subtree: true });
    } catch (err) { /* 静默 */ }
    setInterval(() => { if (!document.hidden) ensureButton(); }, 2000);
    ensureButton();
  }

  if (document.body) start();
  else document.addEventListener("DOMContentLoaded", start);

  setTimeout(() => {
    if (!diag.buttonAttached) console.warn("[zcode-enhance] 按钮未挂载，window.__zenhanceDiag =", diag);
    else console.info("[zcode-enhance] 已就绪，window.__zenhanceDiag =", diag);
  }, 5000);
})();
