/**
 * ZCode 输入框「增强提示词」按钮 —— 润色草稿（与 WorkBuddy 的 input.enhance 同款交互）
 * ==================================================================================
 * 在输入框工具栏注入一个「增强提示词」按钮：
 *   点击 → 取当前草稿 → 经 preload 桥 / main handler 用**当前选中的模型**调一次补全 →
 *   把结果写回输入框，并临时把按钮变成「恢复原文」可一键还原。
 *
 * 与 WorkBuddy 的对应关系：
 *   input.enhance.title    「增强提示词」
 *   input.enhance.enhancing「增强中...」
 *   input.enhance.revert   「恢复原文」
 *   提示词模板取自其 DEFAULT_ENHANCE_PROMPT_SYSTEM / _USER_TEMPLATE 的要点（见 zcode_patcher.py
 *   的 _ENHANCE_SYSTEM_PROMPT / _ENHANCE_USER_TEMPLATE）。
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
  const diag = (window.__zenhanceDiag = window.__zenhanceDiag || {});
  diag.scriptVersion = "1.0";

  const LABEL_IDLE = "增强提示词";
  const LABEL_BUSY = "增强中...";
  const LABEL_REVERT = "恢复原文";
  const REVERT_WINDOW_MS = 20000;

  const COMPOSER_INPUT_SELECTORS = [
    "[data-testid='v4-composer-input']",
    "[data-testid*='composer-input']",
    "textarea[data-testid]",
    "form textarea",
    "textarea",
    "[contenteditable='true']",
  ];

  // ---------- 样式 ----------
  const STYLE_ID = "zenhance-style";
  function ensureStyle() {
    if (document.getElementById(STYLE_ID)) return;
    try {
      const st = document.createElement("style");
      st.id = STYLE_ID;
      st.textContent = [
        "#" + BTN_ID + "{display:inline-flex;align-items:center;gap:4px;height:28px;padding:0 8px;",
        "border-radius:8px;border:0.5px solid transparent;background:transparent;cursor:pointer;",
        "font-size:12px;line-height:1;white-space:nowrap;color:var(--color-foreground-subtle,#5b6470);",
        "transition:background .15s,color .15s,opacity .15s}",
        "#" + BTN_ID + ":hover{background:rgba(127,127,127,.14);color:var(--color-foreground,#1b1f24)}",
        "#" + BTN_ID + "[disabled]{opacity:.6;cursor:default}",
        "#" + BTN_ID + "[data-mode='revert']{color:var(--color-warning,#b45309)}",
        "." + "zenhance-spin{animation:zenhanceSpin .9s linear infinite;transform-origin:50% 50%}",
        "@keyframes zenhanceSpin{to{transform:rotate(360deg)}}",
        ".zenhance-toast{position:fixed;left:50%;bottom:96px;transform:translateX(-50%);z-index:2147483000;",
        "background:var(--color-background,#fff);color:var(--color-foreground,#1b1f24);",
        "border:1px solid rgba(127,127,127,.28);border-radius:10px;padding:8px 14px;font-size:12px;",
        "box-shadow:0 8px 24px rgba(0,0,0,.18);max-width:70vw}",
      ].join("");
      document.head.appendChild(st);
    } catch (err) { /* 静默 */ }
  }

  function toast(msg) {
    try {
      const old = document.querySelector(".zenhance-toast");
      if (old) old.remove();
      const el = document.createElement("div");
      el.className = "zenhance-toast";
      el.textContent = msg;
      document.body.appendChild(el);
      setTimeout(() => el.remove(), 3200);
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
        // 取最靠近视口底部的那个（composer 在底部）
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

  /** 把文本写回受控输入框：优先用 execCommand('insertText')（textarea 与 contenteditable 都能
   *  触发 React 的受控更新），失败再退回「原生 setter + input 事件」。 */
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
    } catch (err) { /* 落到下面的兜底 */ }
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

  /** 当前选中的模型（模型选择按钮上的 data-model-current-value，形如 providerId/modelId） */
  function currentModelValue() {
    try {
      const el = document.querySelector("[data-model-current-value]");
      return el ? String(el.getAttribute("data-model-current-value") || "") : "";
    } catch (err) {
      return "";
    }
  }

  // ---------- 按钮 ----------
  let btn = null;
  let busy = false;
  let original = null;          // 增强前的原文，用于「恢复原文」
  let revertTimer = null;

  function setButton(mode, label) {
    if (!btn) return;
    btn.setAttribute("data-mode", mode);
    btn.disabled = busy;
    btn.title = mode === "revert" ? "把输入框恢复成增强前的内容" : "用当前选中的模型润色这段输入";
    btn.innerHTML = "";
    const icon = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    icon.setAttribute("viewBox", "0 0 24 24");
    icon.setAttribute("width", "13");
    icon.setAttribute("height", "13");
    icon.setAttribute("aria-hidden", "true");
    if (busy) {
      icon.setAttribute("class", "zenhance-spin");
      icon.innerHTML = '<circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" '
        + 'stroke-width="2.5" stroke-opacity="0.25"></circle>'
        + '<path d="M12 3a9 9 0 0 1 9 9" fill="none" stroke="currentColor" stroke-width="2.5" '
        + 'stroke-linecap="round"></path>';
    } else {
      icon.innerHTML = '<path fill="currentColor" d="M12 2l1.9 5.3L19 9l-5.1 1.7L12 16l-1.9-5.3L5 9l5.1-1.7z"/>'
        + '<path fill="currentColor" d="M18.5 14l.9 2.4 2.4.9-2.4.9-.9 2.4-.9-2.4-2.4-.9 2.4-.9z"/>';
    }
    const span = document.createElement("span");
    span.textContent = label;
    btn.appendChild(icon);
    btn.appendChild(span);
  }

  function armRevert() {
    clearTimeout(revertTimer);
    setButton("revert", LABEL_REVERT);
    revertTimer = setTimeout(() => {
      original = null;
      setButton("idle", LABEL_IDLE);
    }, REVERT_WINDOW_MS);
  }

  async function enhance() {
    const el = findInput();
    if (!el) return;
    const text = readText(el).trim();
    if (!text) { toast("请先输入要增强的内容"); return; }
    const api = window.zcode;
    if (!api || typeof api.enhancePrompt !== "function") {
      toast("通信桥不可用：请重跑 --enhance-prompt 注入后重启 ZCode");
      return;
    }

    busy = true;
    setButton("busy", LABEL_BUSY);
    const modelValue = currentModelValue();
    diag.lastRequest = { modelValue, chars: text.length, at: new Date().toISOString() };
    try {
      const res = await api.enhancePrompt(text, modelValue);
      diag.lastResult = res && (res.success ? { model: res.model, chars: String(res.text || "").length }
        : { error: String(res && res.error || "unknown") });
      if (!res || !res.success) {
        toast("增强失败：" + String((res && res.error) || "未知错误"));
        return;
      }
      const enhanced = String(res.text || "").trim();
      if (!enhanced) { toast("增强失败：模型返回空内容"); return; }
      original = text;
      if (!writeText(el, enhanced)) { toast("增强成功但写回输入框失败（请手动复制）"); return; }
      armRevert();
      toast("已增强（模型：" + (res.model || "当前模型") + "）");
    } catch (err) {
      diag.lastResult = { error: String(err) };
      toast("增强异常：" + String(err && err.message || err));
    } finally {
      busy = false;
      if (btn && btn.getAttribute("data-mode") !== "revert") setButton("idle", LABEL_IDLE);
      else if (btn) btn.disabled = false;
    }
  }

  function onRevert() {
    const el = findInput();
    if (el && original != null) writeText(el, original);
    original = null;
    clearTimeout(revertTimer);
    setButton("idle", LABEL_IDLE);
    toast("已恢复原文");
  }

  function ensureButton() {
    ensureStyle();
    const input = findInput();
    if (!input) { if (btn) { btn.remove(); btn = null; } diag.hiddenReason = "未找到输入框"; return; }
    // 挂在发送按钮所在的那一行（找不到就退到输入框的父级容器）
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
    setButton("idle", LABEL_IDLE);
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
    if (!diag.buttonAttached) {
      console.warn("[zcode-enhance] 按钮未挂载，window.__zenhanceDiag =", diag);
    } else {
      console.info("[zcode-enhance] 已就绪，window.__zenhanceDiag =", diag);
    }
  }, 5000);
})();
