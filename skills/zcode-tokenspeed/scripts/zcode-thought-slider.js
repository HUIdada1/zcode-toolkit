/**
 * ZCode 思考强度滑条 —— Codex 风格:点击「思考」入口弹出拖拽条(带动效)
 * ====================================================================
 * 工具栏交互(替代原生「思考级别」下拉):
 *   [ 思考 · high ▁▃▆ ]  ←常驻入口:当前档名 + 迷你电量条
 *        └─点击→ 弹出面板(180ms 弹出动效):
 *          ┌────────────────────────┐
 *          │ 思考强度            high │
 *          │ ●——●——○———● │ ←吸附拖拽条,支持 ←/→ 微调
 *          └────────────────────────┘
 *   点外部 / Esc 收起;原生下拉经 CSS 隐藏(单档位固定徽章除外),状态仍双向同步。
 *
 * 数据源(全部只读 DOM,零协议逆向):
 *   - 状态探针:V4ComposerToolbar 渲染的隐藏 span(className:"hidden"),
 *     data-thought=当前档位,data-thought-levels=可用档位(逗号分隔);
 *     React 随会话实时更新,原生操作(含 t 键循环切档)自动回流到入口与面板。
 *   - 入口锚点:原生档位触发器 [data-composer-thought-control](隐藏后仅作定位基准)。
 *
 * 写路径(按优先级):
 *   1) React fiber:沿 __reactFiber$ return 链找到 onValueChange(option.type==='select')
 *      直调——等价于用户点选菜单项,原生继续走 session/setThoughtLevel 会话 RPC。
 *   2) 降级:模拟点击触发器打开 Radix 菜单,按 options 顺序点第 index 个
 *      [role="option"](档位显示名是 i18n 文案,按序号而非文本定位)。
 *
 * 安全:只读状态、只写自己的元素与独立 <style>;异常静默;探针/锚点消失自清理,
 *   不调用应用内部模块、不发协议帧、不碰 ServicePort。
 * 回滚:python zcode_patcher.py --thought-slider --revert,或 restore_clean.py --latest。
 */
(() => {
  if (window.__zslider) return;
  window.__zslider = true;
  const MARK = "data-zslider";
  const FILL = "#4ade80";           // 与 TPS 胶囊绿点同色系
  const TRACK = "rgba(127,127,127,0.30)";
  const DOT_IDLE = "rgba(127,127,127,0.55)";
  const ACCENT = "var(--color-warning, #e0983a)";

  // ---------- 样式:隐藏原生下拉(单档位固定徽章除外)+ 面板动效 ----------
  const STYLE_ID = "zslider-style";
  function ensureStyle() {
    if (document.getElementById(STYLE_ID)) return;
    const st = document.createElement("style");
    st.id = STYLE_ID;
    st.textContent = `
[data-composer-thought-control]:not([data-thought-level-fixed="true"]){display:none!important}
@keyframes zsliderIn{from{opacity:0;transform:translateY(10px) scale(.85);filter:blur(6px)}60%{filter:blur(0)}to{opacity:1;transform:translateY(0) scale(1);filter:blur(0)}}
@keyframes zsliderOut{from{opacity:1;transform:translateY(0) scale(1);filter:blur(0)}to{opacity:0;transform:translateY(5px) scale(.96);filter:blur(4px)}}
@keyframes zsliderDot{from{transform:translate(-50%,-50%) scale(0)}to{transform:translate(-50%,-50%) scale(1)}}
@keyframes zsliderPulse{0%{transform:scale(1)}40%{transform:scale(1.28)}100%{transform:scale(1)}}
`;
    document.head.appendChild(st);
  }

  // ---------- 状态探针 ----------
  function probe() {
    let el = null;
    try { el = document.querySelector("[data-thought][data-thought-levels]"); } catch (err) { /* ignore */ }
    if (!el) return null;
    const levels = (el.getAttribute("data-thought-levels") || "")
      .split(",").map((s) => s.trim()).filter(Boolean);
    const cur = (el.getAttribute("data-thought") || "").trim();
    if (levels.length < 2) return null;          // 无档位/单档位:入口与滑条无意义(原生显示固定徽章)
    return { levels, cur };
  }

  // 原生触发器(已被 CSS 隐藏,仅作插入定位基准;不看可见性——display:none 的元素 offsetParent 为 null)
  function anchorTrigger() {
    try {
      return document.querySelector('[data-composer-thought-control]:not([data-thought-level-fixed="true"])');
    } catch (err) { return null; }
  }

  // ---------- 写路径 ①: React fiber 直调 onValueChange ----------
  function fiberOf(el) {
    for (const k in el) {
      if (k.startsWith("__reactFiber$")) return el[k];
    }
    return null;
  }

  function findThoughtCommit(startEl) {
    let f = fiberOf(startEl);
    for (let i = 0; f && i < 40; i++, f = f.return) {
      const p = f.memoizedProps;
      if (p && typeof p.onValueChange === "function" && p.option
          && p.option.type === "select" && Array.isArray(p.option.options)
          && p.option.options.some((o) => o && typeof o.value === "string")) {
        return p.onValueChange;
      }
    }
    return null;
  }

  // ---------- 写路径 ②: 模拟开菜单按序号点选(触发器虽 display:none,事件派发仍有效) ----------
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  async function commitViaMenu(targetIndex) {
    const trig = anchorTrigger();
    if (!trig) return false;
    const o0 = { bubbles: true, cancelable: true, button: 0 };
    try {
      trig.dispatchEvent(new PointerEvent("pointerdown", { ...o0, pointerType: "mouse", isPrimary: true }));
      trig.dispatchEvent(new MouseEvent("mousedown", o0));
      trig.dispatchEvent(new PointerEvent("pointerup", { ...o0, pointerType: "mouse", isPrimary: true }));
      trig.dispatchEvent(new MouseEvent("mouseup", o0));
      trig.dispatchEvent(new MouseEvent("click", o0));
    } catch (err) { /* PointerEvent 不可用时忽略 */ }
    for (let i = 0; i < 24; i++) {          // 最多等 1.2s 的菜单 portal 渲染
      await sleep(50);
      const opts = Array.from(document.querySelectorAll("[role='option']"))
        .filter((e) => e.getBoundingClientRect().height > 0);
      const it = opts[targetIndex];
      if (!it) continue;
      const r = it.getBoundingClientRect();
      const o = { bubbles: true, cancelable: true, button: 0,
                  clientX: r.left + r.width / 2, clientY: r.top + r.height / 2 };
      try {
        it.dispatchEvent(new PointerEvent("pointerdown", { ...o, pointerType: "mouse", isPrimary: true }));
        it.dispatchEvent(new PointerEvent("pointerup", { ...o, pointerType: "mouse", isPrimary: true }));
        it.dispatchEvent(new MouseEvent("click", o));
      } catch (err) { /* ignore */ }
      return true;
    }
    return false;
  }

  async function commitLevel(value, index) {
    const trig = anchorTrigger();
    const commit = trig ? findThoughtCommit(trig) : null;
    if (commit) {
      try { commit(value); return true; } catch (err) { /* 落到降级 */ }
    }
    return commitViaMenu(index);
  }

  // ---------- 常驻入口 ----------
  let entry = null;
  let entryName = null;
  let entryMini = null;

  function setMini(idx, n) {
    if (!entryMini) return;
    const pct = n > 1 ? (idx / (n - 1)) * 100 : 0;
    entryMini.firstChild.style.height = Math.max(8, pct) + "%";
  }

  function ensureEntry(trig) {
    if (entry && entry.isConnected) {
      if (trig && entry.previousElementSibling !== trig && trig.parentElement) {
        trig.insertAdjacentElement("afterend", entry);
      }
      return true;
    }
    if (!trig || !trig.parentElement) return false;
    entry = document.createElement("div");
    entry.setAttribute(MARK + "-entry", "1");
    Object.assign(entry.style, {
      display: "inline-flex", alignItems: "center", gap: "5px",
      height: "28px", padding: "0 8px", borderRadius: "8px",
      cursor: "pointer", userSelect: "none", flex: "0 0 auto",
      fontSize: "12px", whiteSpace: "nowrap",
      color: "var(--color-foreground-subtle, #9a9a9a)",
    });
    entry.title = "思考强度 · 点击调整";
    entry.addEventListener("pointerenter", () => {
      entry.style.background = "rgba(127,127,127,0.14)";
      entry.style.color = "var(--color-foreground, #e8e8e8)";
    });
    entry.addEventListener("pointerleave", () => {
      entry.style.background = "transparent";
      entry.style.color = "var(--color-foreground-subtle, #9a9a9a)";
    });
    entry.addEventListener("click", (e) => {
      e.stopPropagation();
      togglePanel();
    });

    const t1 = document.createElement("span");
    t1.textContent = "思考";
    entryName = document.createElement("span");
    entryName.textContent = "";
    // 迷你电量条(原生触发器同款造型:2px 宽竖条,底部向上填充)
    entryMini = document.createElement("span");
    Object.assign(entryMini.style, {
      position: "relative", width: "3px", height: "12px",
      borderRadius: "2px", overflow: "hidden",
      background: "rgba(127,127,127,0.25)",
    });
    const miniFill = document.createElement("span");
    Object.assign(miniFill.style, {
      position: "absolute", left: "0", bottom: "0", width: "100%",
      borderRadius: "2px", background: FILL, height: "0",
      transition: "height .25s cubic-bezier(0.34,1.56,0.64,1)",
    });
    entryMini.appendChild(miniFill);
    const chev = document.createElement("span");
    chev.textContent = "▾";
    chev.style.fontSize = "9px";
    chev.style.opacity = "0.7";
    entry.appendChild(t1);
    entry.appendChild(entryName);
    entry.appendChild(entryMini);
    entry.appendChild(chev);
    trig.insertAdjacentElement("afterend", entry);
    return true;
  }

  // ---------- 弹出面板(滑条) ----------
  let panel = null;
  let track = null;
  let rail = null;
  let fillEl = null;
  let glowEl = null;
  let dots = [];
  let labelEl = null;
  let state = { levels: [], cur: "", key: "", drag: false };
  let closeTimer = null;

  const EASE = "cubic-bezier(0.34,1.56,0.64,1)";   // 弹性过冲曲线(width 过冲部分被裁剪层裁住)

  function setFill(idx, n) {
    const pct = n > 1 ? (idx / (n - 1)) * 100 : 0;
    fillEl.style.width = pct + "%";
    if (glowEl) glowEl.style.left = pct + "%";   // 光点独立于裁剪层,过冲时光晕悬浮在端点外
    dots.forEach((d, i) => {
      d.style.background = i <= idx ? FILL : DOT_IDLE;
      d.style.width = d.style.height = (i === idx ? 9 : 6) + "px";
    });
  }

  function closePanel(animate) {
    if (!panel) return;
    const p = panel;
    panel = null;
    document.removeEventListener("pointerdown", onDocDown, true);
    document.removeEventListener("keydown", onDocKey, true);
    if (animate) {
      p.style.animation = "zsliderOut 140ms ease-in forwards";
      clearTimeout(closeTimer);
      closeTimer = setTimeout(() => p.remove(), 140);
    } else {
      p.remove();
    }
  }

  function onDocDown(e) {
    if (!panel) return;
    if (panel.contains(e.target) || (entry && entry.contains(e.target))) return;
    closePanel(true);
  }
  function onDocKey(e) {
    if (e.key === "Escape" && panel) { e.stopPropagation(); closePanel(true); }
  }

  function togglePanel() {
    if (panel) { closePanel(true); return; }
    if (!entry || !state.levels.length) return;
    closePanel(false);
    const p = document.createElement("div");
    p.setAttribute(MARK + "-panel", "1");
    p.tabIndex = -1;
    Object.assign(p.style, {
      position: "fixed", zIndex: "2147483000",
      width: "236px", padding: "10px 12px 12px",
      borderRadius: "12px",
      background: "var(--color-background, #1e1e1e)",
      color: "var(--color-foreground, #e8e8e8)",
      border: "1px solid rgba(127,127,127,0.28)",
      boxShadow: "0 10px 32px rgba(0,0,0,0.38)",
      transformOrigin: "bottom left",
      animation: "none",   // 先无动画完成定位测量(scale 状态会污染 getBoundingClientRect),定位后再启动
      userSelect: "none",
    });

    // 标题行:左「思考强度」、右当前档名
    const head = document.createElement("div");
    Object.assign(head.style, {
      display: "flex", justifyContent: "space-between", alignItems: "center",
      marginBottom: "8px",
    });
    const t = document.createElement("span");
    t.textContent = "思考强度";
    t.style.cssText = "font-size:12px;color:var(--color-foreground-subtle,#9a9a9a);";
    labelEl = document.createElement("span");
    labelEl.style.cssText = `font-size:12px;font-weight:600;color:${ACCENT};font-variant-numeric:tabular-nums;display:inline-block;transform-origin:right center;`;
    head.appendChild(t);
    head.appendChild(labelEl);
    p.appendChild(head);

    // 滑条行
    track = document.createElement("div");
    Object.assign(track.style, {
      position: "relative", height: "18px",
      cursor: "pointer", touchAction: "none",
    });
    // 轨道底
    rail = document.createElement("div");
    Object.assign(rail.style, {
      position: "absolute", left: "0", right: "0", top: "7px", height: "4px",
      borderRadius: "2px", background: TRACK,
    });
    track.appendChild(rail);
    // 填充条裁剪层:弹性过冲曲线会让 width 短暂超过 100%,必须裁住否则溢出轨道
    const clip = document.createElement("div");
    Object.assign(clip.style, {
      position: "absolute", left: "0", right: "0", top: "7px", height: "4px",
      borderRadius: "2px", overflow: "hidden",
    });
    fillEl = document.createElement("div");
    Object.assign(fillEl.style, {
      position: "absolute", left: "0", top: "0", height: "100%",
      borderRadius: "2px", background: FILL, width: "0",
      // 弹性扫入:打开面板/换档时从旧值弹到新值;拖拽中临时禁用(跟手)
      transition: `width .45s ${EASE}`,
    });
    clip.appendChild(fillEl);
    track.appendChild(clip);
    // 末端光点:独立于裁剪层悬浮,过冲时光晕悬在端点外,视觉更活
    glowEl = document.createElement("div");
    Object.assign(glowEl.style, {
      position: "absolute", left: "0%", top: "50%",
      transform: "translate(-50%,-50%)",
      width: "8px", height: "8px", borderRadius: "50%",
      background: "rgba(255,255,255,0.92)",
      boxShadow: "0 0 8px 2px rgba(74,222,128,0.55)",
      pointerEvents: "none",
      transition: `left .45s ${EASE}`,
    });
    track.appendChild(glowEl);
    p.appendChild(track);

    // 刻度点:级联弹入(stagger)+ 尺寸/颜色平滑过渡;挂 track 层避免被裁剪层裁掉
    dots = state.levels.map((lv, i) => {
      const d = document.createElement("div");
      d.title = lv;
      Object.assign(d.style, {
        position: "absolute", top: "50%",
        transform: "translate(-50%,-50%)",
        width: "6px", height: "6px", borderRadius: "50%",
        background: DOT_IDLE, pointerEvents: "none",
        transition: "width .2s, height .2s, background .2s",
        animation: `zsliderDot .35s cubic-bezier(.34,1.56,.64,1) ${40 + i * 35}ms backwards`,
      });
      d.style.left = (state.levels.length > 1 ? (i / (state.levels.length - 1)) * 100 : 0) + "%";
      track.appendChild(d);
      return d;
    });

    // 定位:入口上方左对齐;越界时收敛到视口内,上方放不下落到底部
    document.body.appendChild(p);
    const er = entry.getBoundingClientRect();
    const pr = p.getBoundingClientRect();
    let left = er.left;
    let top = er.top - pr.height - 8;
    if (left + pr.width > window.innerWidth - 8) left = window.innerWidth - pr.width - 8;
    if (top < 8) top = er.bottom + 8;          // 上方放不下 → 入口下方
    if (top + pr.height > window.innerHeight - 8) top = Math.max(8, window.innerHeight - pr.height - 8);
    if (top > er.bottom + 8) p.style.transformOrigin = "top left";   // 从下方弹出时原点换边
    p.style.left = Math.max(8, left) + "px";
    p.style.top = top + "px";
    // 定位就绪后启动弹出动效
    p.style.animation = "zsliderIn 260ms cubic-bezier(0.2,0.9,0.25,1.15)";

    // 拖拽/点击:指针横向位置 → 最近档位,松手提交
    const idxFromEvent = (e) => {
      const r = track.getBoundingClientRect();
      const ratio = Math.max(0, Math.min(1, (e.clientX - r.left) / Math.max(1, r.width)));
      const n = state.levels.length;
      return Math.max(0, Math.min(n - 1, Math.round(ratio * (n - 1))));
    };
    const preview = (idx) => {
      setFill(idx, state.levels.length);
      labelEl.textContent = state.levels[idx] ?? "";
    };
    track.addEventListener("pointerdown", (e) => {
      e.preventDefault();
      state.drag = true;
      fillEl.style.transition = "none";   // 拖拽跟手,不用弹性
      try { track.setPointerCapture(e.pointerId); } catch (err) { /* ignore */ }
      preview(idxFromEvent(e));
    });
    track.addEventListener("pointermove", (e) => {
      if (state.drag) preview(idxFromEvent(e));
    });
    const finish = (e) => {
      if (!state.drag) return;
      state.drag = false;
      fillEl.style.transition = `width .45s ${EASE}`;   // 松手回弹
      try { track.releasePointerCapture(e.pointerId); } catch (err) { /* ignore */ }
      const idx = idxFromEvent(e);
      const value = state.levels[idx];
      if (value && value !== state.cur) {
        commitLevel(value, idx).then((ok) => {
          if (!ok) console.warn("[zslider] 档位提交失败(fiber 与菜单路径均未命中)");
        });
      }
      sync();   // 以真实状态回填(提交失败时弹回)
    };
    track.addEventListener("pointerup", finish);
    track.addEventListener("pointercancel", (e) => {
      state.drag = false;
      fillEl.style.transition = `width .45s ${EASE}`;
      sync();
    });

    // ←/→ 微调(面板聚焦时)
    p.addEventListener("keydown", (e) => {
      const n = state.levels.length;
      if (!n) return;
      let idx = state.levels.indexOf(state.cur);
      if (e.key === "ArrowRight") idx = Math.min(n - 1, idx + 1);
      else if (e.key === "ArrowLeft") idx = Math.max(0, idx - 1);
      else return;
      e.preventDefault();
      const value = state.levels[idx];
      setFill(idx, n);
      labelEl.textContent = value;
      if (value && value !== state.cur) commitLevel(value, idx);
    });

    panel = p;
    refreshPanel();
    document.addEventListener("pointerdown", onDocDown, true);
    document.addEventListener("keydown", onDocKey, true);
    try { p.focus(); } catch (err) { /* ignore */ }
  }

  function refreshPanel() {
    if (!panel) return;
    const n = state.levels.length;
    if (!n) { closePanel(false); return; }
    const idx = state.levels.indexOf(state.cur);
    const shown = idx >= 0 ? idx : 0;
    if (!state.drag) {
      setFill(idx >= 0 ? idx : 0, n);
      if (idx < 0) fillEl.style.width = "0";
      labelEl.textContent = state.cur;
    }
  }

  // ---------- 同步(入口常驻刷新;面板打开时实时跟随) ----------
  function sync() {
    try {
      ensureStyle();
      const p = probe();
      if (!p) {
        closePanel(false);
        if (entry) entry.remove();
        entry = null;
        return;
      }
      const trig = anchorTrigger();
      if (!ensureEntry(trig)) return;
      state.levels = p.levels;
      state.cur = p.cur;
      const key = p.levels.join(",") + "|" + p.cur;
      if (state.key !== key) {
        state.key = key;
        const idx = p.levels.indexOf(p.cur);
        entryName.textContent = p.cur;
        entryName.style.color = idx >= 0 ? "" : "rgba(233,99,99,0.9)";   // 未知档位标红提示
        setMini(idx >= 0 ? idx : 0, p.levels.length);
        // 面板打开时档名脉冲一下,呼应填充弹性回弹
        if (panel && labelEl) {
          labelEl.style.animation = "none";
          void labelEl.offsetHeight;   // 强制回流重触发动画
          labelEl.style.animation = "zsliderPulse .35s cubic-bezier(.34,1.56,.64,1)";
        }
      }
      refreshPanel();
    } catch (err) { /* 静默 */ }
  }

  function start() {
    setInterval(sync, 1000);
    try {
      let lastSync = 0;
      const mo = new MutationObserver(() => {
        const now = performance.now();
        if (now - lastSync > 120) { lastSync = now; sync(); }
      });
      mo.observe(document.body, { childList: true, subtree: true, attributes: true, attributeFilter: ["data-thought", "data-thought-levels"] });
    } catch (err) { /* 静默 */ }
    sync();
  }
  if (document.body) start();
  else document.addEventListener("DOMContentLoaded", start);

  // 自检:加载 5s 后探针状态打到 console(排障用;无档位模型静默属预期)
  setTimeout(() => {
    const p = probe();
    if (p) console.info("[zslider] 已就绪:", p.levels.join("/"), "当前", p.cur || "(未设)");
    else console.info("[zslider] 探针未命中或模型无思考档位,入口隐藏");
  }, 5000);
})();
