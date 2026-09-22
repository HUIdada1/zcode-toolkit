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
  const FILL = "#4ade80";           // 兜底色(实际按档位着色,见 effortColor)
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
@keyframes zsliderGlow{0%,100%{box-shadow:0 0 6px 1px rgba(125,211,252,.4)}50%{box-shadow:0 0 14px 3px rgba(125,211,252,.75)}}
@keyframes zsliderRipple{from{opacity:.75;transform:translate(-50%,-50%) scale(.4)}to{opacity:0;transform:translate(-50%,-50%) scale(2.4)}}
/* ---- 视觉规格：渐变轨道 / 拖尾光斑 / 拖拽增辉 / max 呼吸 ---- */
.zslider-panel{border:1px solid rgba(127,127,127,.22)!important;border-radius:14px!important;box-shadow:0 14px 42px rgba(0,0,0,.38),0 3px 10px rgba(0,0,0,.16)!important;backdrop-filter:blur(8px)}
.zslider-rail{background:linear-gradient(100deg,#03040a 0%,#071126 22%,#101d4c 45%,#302262 70%,#5d35a0 100%)!important;box-shadow:inset 0 1px 0 rgba(189,199,255,.15),inset 0 -1px 0 rgba(0,0,0,.55),0 3px 10px rgba(12,17,55,.34)!important}
.zslider-flare{position:absolute;top:50%;left:var(--zp,0%);width:64px;height:40px;border-radius:50%;transform:translate(-100%,-50%);background:radial-gradient(ellipse at 100% 50%,rgba(255,255,255,.95) 0 4%,rgba(188,189,255,.8) 11%,rgba(106,87,255,.5) 28%,rgba(105,31,255,.2) 49%,transparent 74%);filter:blur(2px) saturate(1.25);mix-blend-mode:screen;transition:left 70ms linear,filter 140ms ease;pointer-events:none}
.zslider-track.is-dragging .zslider-flare{filter:blur(1.5px) saturate(1.6) brightness(1.42);transition:none}
.zslider-track.is-dragging .zslider-fill{filter:saturate(1.45) brightness(1.28)}
.zslider-track[data-top] .zslider-rail{animation:zsliderBreathe 1.9s ease-in-out infinite}
@keyframes zsliderBreathe{0%,100%{filter:brightness(1) saturate(1)}50%{filter:brightness(1.3) saturate(1.2)}}
@media (prefers-color-scheme: light){
  .zslider-rail{background:linear-gradient(90deg,#eef3ff 0%,#dfe9ff 100%)!important;box-shadow:inset 0 1px 0 rgba(255,255,255,.9),inset 0 0 0 1px rgba(77,111,255,.14),0 2px 8px rgba(31,58,147,.12)!important}
  .zslider-flare{background:radial-gradient(ellipse at 100% 50%,rgba(255,255,255,.9) 0 5%,rgba(141,166,255,.65) 16%,rgba(91,120,255,.4) 36%,rgba(91,63,255,.16) 55%,transparent 74%)}
}
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
    return { levels, cur, el };                  // el:探针 span,同时是工具栏行的定位锚
  }

  // 从某 DOM 元素的 React fiber 向下遍历子树,找持有思考档位 select props 的组件 fiber(jU)。
  // 注意 3.14.1 默认(非紧凑)模式下触发器不带 data-composer-thought-control 属性,
  // DOM 选择器拿不到它,必须走 fiber;探针 span 与 jU 是兄弟,所以从父行 DOM 反查。
  function findThoughtFiber(rootEl) {
    const root = fiberOf(rootEl);
    if (!root) return null;
    const stack = [root];
    while (stack.length) {
      const cur = stack.pop();
      if (!cur) continue;
      const p = cur.memoizedProps;
      if (p && p.option && p.option.type === "select" && Array.isArray(p.option.options)
          && typeof p.onValueChange === "function"
          && p.option.options.some((o) => o && typeof o.value === "string")) {
        return cur;
      }
      stack.push(cur.child, cur.sibling);
    }
    return null;
  }

  // 原生触发器 BUTTON:jU 子树里的第一个 button(单档位固定徽章分支无 BUTTON,返回 null)
  function anchorTrigger() {
    try {
      const span = document.querySelector("[data-thought][data-thought-levels]");
      if (!span || !span.parentElement) return null;
      const ju = findThoughtFiber(span.parentElement);
      if (!ju) return null;
      const stack = [ju.child];
      while (stack.length) {
        const cur = stack.pop();
        if (!cur) continue;
        const el = cur.stateNode;
        if (el && el.tagName === "BUTTON" && el.isConnected) return el;
        stack.push(cur.child, cur.sibling);
      }
      return null;
    } catch (err) { return null; }
  }

  // ---------- 写路径 ①: React fiber 直调 onValueChange ----------
  function fiberOf(el) {
    for (const k in el) {
      if (k.startsWith("__reactFiber$")) return el[k];
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

  // 写路径 ①的取值:从探针 span 父行反查 jU fiber,直接拿 onValueChange(不依赖触发器 DOM)
  function thoughtCommit() {
    try {
      const span = document.querySelector("[data-thought][data-thought-levels]");
      if (!span || !span.parentElement) return null;
      const ju = findThoughtFiber(span.parentElement);
      return ju ? ju.memoizedProps.onValueChange : null;
    } catch (err) { return null; }
  }

  async function commitLevel(value, index) {
    const commit = thoughtCommit();
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
    entryMini.firstChild.style.background = state.effortColor || "#4d9dff";
  }

  function ensureEntry(trig, span) {
    // 插入锚:原生触发器 BUTTON 优先;3.14.1 默认模式拿不到触发器时退到探针 span(同一工具栏行)
    const anchor = (trig && trig.parentElement) ? trig : ((span && span.parentElement) ? span : null);
    if (entry && entry.isConnected) {
      if (anchor && entry.previousElementSibling !== anchor) {
        anchor.insertAdjacentElement("afterend", entry);
      }
      return true;
    }
    if (!anchor) return false;
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
      position: "relative", width: "5px", height: "14px",
      borderRadius: "3px", overflow: "hidden",
      background: "rgba(127,127,127,0.25)",
    });
    const miniFill = document.createElement("span");
    Object.assign(miniFill.style, {
      position: "absolute", left: "0", bottom: "0", width: "100%",
      borderRadius: "2px", background: "#4d9dff", height: "0",
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
    anchor.insertAdjacentElement("afterend", entry);
    return true;
  }

  // ---------- 弹出面板(滑条) ----------
  let panel = null;
  let track = null;
  let rail = null;
  let fillEl = null;
  let flareEl = null;
  let dots = [];
  let labelEl = null;
  let state = { levels: [], cur: "", key: "", drag: false, effortColor: "#4d9dff" };
  let closeTimer = null;

  const EASE = "cubic-bezier(0.34,1.56,0.64,1)";   // 弹性过冲曲线(width 过冲部分被裁剪层裁住)

  // ---------- 八帧奔跑小人(滑块按钮) ----------
  // 参数化火柴人跑步循环:大腿按正弦摆动、后摆相膝弯大、手臂与对侧腿同相,身体随步频轻微起伏。
  // 拖动越快 rate 越高(帧/秒),松手后以固定减速度自然停下。
  const runner = { el: null, svg: null, tail: null, frame: 0, progress: 0, raf: 0, last: 0 };
  const IDLE_FRAME_MS = 90;     // 静止循环 720ms / 8 帧(dsh 规格)
  const DRAG_FRAME_MS = 52.5;   // 拖拽循环 420ms / 8 帧
  const REDUCED = typeof matchMedia === "function" && matchMedia("(prefers-reduced-motion: reduce)").matches;

  function pt(o, deg, len) {   // deg: 90=竖直向下, 正角向前(右)
    const r = deg * Math.PI / 180;
    return [o[0] + len * Math.sin(r), o[1] + len * Math.cos(r)];
  }
  const f1 = (n) => Number(n).toFixed(1);
  function seg(a, b, extra) {
    return `<line x1="${f1(a[0])}" y1="${f1(a[1])}" x2="${f1(b[0])}" y2="${f1(b[1])}" stroke="#fff" stroke-opacity="${(extra && extra.op) || 0.95}" stroke-width="${(extra && extra.w) || 2.4}" stroke-linecap="round"/>`;
  }

  function runnerSVG(frame) {
    const t = frame / 8 * 2 * Math.PI;
    const bob = Math.abs(Math.sin(t)) * 1.2;                    // 跑步身体起伏
    const hip = [11.6, 14.4 - bob];
    const sh = [12.2, 8.4 - bob];                               // 肩(躯干微前倾)
    const parts = [`<circle cx="${f1(12.9)}" cy="${f1(5.6 - bob)}" r="2.5" fill="#fff"/>`];
    parts.push(seg([hip[0] + 0.4, hip[1]], sh, { op: 0.95 }));  // 躯干
    for (const ph of [t, t + Math.PI]) {                        // 两腿相位差 π
      const thigh = 42 * Math.sin(ph);                          // 大腿摆角
      const bend = 18 + 34 * Math.max(0, -Math.sin(ph));        // 后摆相膝弯更大
      const knee = pt(hip, 90 + thigh, 5.4);
      const foot = pt(knee, 90 + thigh + bend, 5.2);
      parts.push(seg(hip, knee, { op: 0.8 }), seg(knee, foot));
    }
    for (const ph of [t + Math.PI, t]) {                        // 手臂与对侧腿同相
      const swing = 34 * Math.sin(ph);
      const elbow = pt(sh, 90 + swing - 14, 4.2);               // 上臂略张
      const hand = pt(elbow, 90 + swing + 26, 4.0);             // 前臂前摆
      parts.push(seg(sh, elbow, { op: 0.7, w: 2.0 }), seg(elbow, hand, { op: 0.7, w: 2.0 }));
    }
    return `<g>${parts.join("")}</g>`;
  }

  function runnerRender() {
    if (!runner.svg) return;   // 面板未打开时 svg 不存在,sync 的换档 kick 不应渲染
    runner.svg.innerHTML = runnerSVG(runner.frame);
  }

  function runnerLoop(ts) {
    if (!runner.raf) return;
    const dt = runner.last ? Math.min(100, ts - runner.last) : 16;
    runner.last = ts;
    const step = state.drag ? DRAG_FRAME_MS : IDLE_FRAME_MS;   // 拖拽 420ms 循环,静止 720ms 循环
    runner.progress += dt / step;
    const f = Math.floor(runner.progress) % 8;
    if (f !== runner.frame) { runner.frame = f; runnerRender(); }
    runner.raf = requestAnimationFrame(runnerLoop);
  }

  function runnerSetMode() {
    if (REDUCED || !runner.el || !runner.svg) return;   // 减少动态效果:冻结在站立帧(dsh 规格)
    if (!runner.raf) { runner.last = 0; runner.raf = requestAnimationFrame(runnerLoop); }
  }

  function runnerStop() {
    if (runner.raf) cancelAnimationFrame(runner.raf);
    runner.raf = 0; runner.rate = 0; runner.last = 0;
  }

  // 辐射特效:换档确认时从滑块发射一圈涟漪
  function rippleAt() {
    if (!runner.el || !runner.el.parentElement) return;
    const r = document.createElement("div");
    Object.assign(r.style, {
      position: "absolute", left: runner.el.style.left, top: "50%",
      width: "36px", height: "36px", borderRadius: "50%",
      border: "1.5px solid " + (state.effortColor || FILL), pointerEvents: "none",
      animation: "zsliderRipple .5s ease-out forwards",
    });
    runner.el.parentElement.appendChild(r);
    setTimeout(() => r.remove(), 520);
  }

  // 档位着色(dsh 规格):低/中蓝 → 高紫罗兰 → max 亮蓝并泛光脉冲
  function effortColor(idx, n) {
    const ratio = (idx >= 0 && n > 1) ? idx / (n - 1) : 0;
    if (ratio >= 0.999) return { color: "#7dd3fc", glow: "0 0 10px 2px rgba(125,211,252,.65)", pulse: true };
    if (ratio >= 0.6) return { color: "#a78bfa", glow: "0 0 8px rgba(167,139,250,.5)", pulse: false };
    return { color: "#4d9dff", glow: "0 0 6px rgba(77,157,255,.45)", pulse: false };
  }

  function setFill(idx, n) {
    const pct = n > 1 ? (idx / (n - 1)) * 100 : 0;
    fillEl.style.width = pct + "%";
    if (runner.el) runner.el.style.left = pct + "%";   // 滑块独立于裁剪层,过冲时小人悬浮在端点外
    // 光斑锚点与 max 档呼吸(dsh 规格):--zp 驱动 .zslider-flare,data-top 触发轨道呼吸
    track.style.setProperty("--zp", pct + "%");
    if (idx >= 0 && n > 1 && idx === n - 1) track.setAttribute("data-top", "1");
    else track.removeAttribute("data-top");
    const ec = effortColor(idx, n);
    state.effortColor = ec.color;
    fillEl.style.background = ec.color;
    fillEl.style.animation = ec.pulse ? "zsliderGlow 1.6s ease-in-out infinite" : "none";
    if (runner.el) runner.el.style.filter = `drop-shadow(${ec.glow})`;
    dots.forEach((d, i) => {
      const active = i <= idx;
      d.style.background = active ? ec.color : DOT_IDLE;
      d.style.width = d.style.height = (i === idx ? 9 : 6) + "px";
      d.style.boxShadow = active ? `0 0 6px ${ec.color}66` : "none";
      d.style.border = active ? "none" : "1px solid rgba(127,127,127,.35)";
    });
  }

  function closePanel(animate) {
    if (!panel) return;
    const p = panel;
    panel = null;
    runnerStop();   // 面板关闭即停小人动画(rAF 引用旧节点无意义)
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
    p.className = "zslider-panel";
    p.tabIndex = -1;
    Object.assign(p.style, {
      position: "fixed", zIndex: "2147483000",
      width: "236px", padding: "10px 12px 12px",
      background: "var(--color-background, #1e1e1e)",
      color: "var(--color-foreground, #e8e8e8)",
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
    track.className = "zslider-track";
    Object.assign(track.style, {
      position: "relative", height: "26px",
      cursor: "pointer", touchAction: "none",
    });
    // 轨道底(dsh 规格:深蓝→紫渐变,浅色主题由 CSS 媒体查询切换)
    rail = document.createElement("div");
    rail.className = "zslider-rail";
    Object.assign(rail.style, {
      position: "absolute", left: "0", right: "0", top: "10px", height: "6px",
      borderRadius: "3px",
    });
    track.appendChild(rail);
    // 填充条裁剪层:弹性过冲曲线会让 width 短暂超过 100%,必须裁住否则溢出轨道
    const clip = document.createElement("div");
    Object.assign(clip.style, {
      position: "absolute", left: "0", right: "0", top: "10px", height: "6px",
      borderRadius: "3px", overflow: "hidden",
    });
    fillEl = document.createElement("div");
    fillEl.className = "zslider-fill";
    Object.assign(fillEl.style, {
      position: "absolute", left: "0", top: "0", height: "100%",
      borderRadius: "3px", background: FILL, width: "0",
      // 弹性扫入:打开面板/换档时从旧值弹到新值;拖拽中临时禁用(跟手)
      transition: `width .45s ${EASE}`,
    });
    clip.appendChild(fillEl);
    // 拖尾光斑(dsh 规格):纯白核心向蓝紫扩散,贴着滑块左侧,screen 混合
    flareEl = document.createElement("div");
    flareEl.className = "zslider-flare";
    clip.appendChild(flareEl);
    track.appendChild(clip);
    // 滑块按钮:八帧奔跑小人,独立于裁剪层悬浮。
    // 静止停在站立帧;拖动/换档时按速度播放跑步循环,松手自然减速停下。
    runner.el = document.createElement("div");
    Object.assign(runner.el.style, {
      position: "absolute", left: "0%", top: "50%",
      transform: "translate(-50%,-58%)",
      width: "30px", height: "30px",
      pointerEvents: "none",
      transition: `left .45s ${EASE}`,
      filter: "drop-shadow(0 0 5px rgba(77,157,255,0.5))",
    });
    runner.svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    runner.svg.setAttribute("viewBox", "0 0 24 24");
    Object.assign(runner.svg.style, { width: "100%", height: "100%", display: "block" });
    runner.el.appendChild(runner.svg);
    // 拖动尾迹:特效裁剪到滑块左侧(dsh 规格)
    runner.tail = document.createElement("div");
    Object.assign(runner.tail.style, {
      position: "absolute", right: "80%", top: "50%",
      transform: "translateY(-50%)",
      width: "16px", height: "3px", borderRadius: "2px",
      background: "linear-gradient(to left, rgba(255,255,255,.85), rgba(255,255,255,0))",
      opacity: "0", transition: "opacity .18s",
      pointerEvents: "none",
    });
    runner.el.appendChild(runner.tail);
    runnerRender();
    track.appendChild(runner.el);
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
    let lastMoveX = null;
    track.addEventListener("pointerdown", (e) => {
      e.preventDefault();
      state.drag = true;
      lastMoveX = e.clientX;
      track.classList.add("is-dragging");   // 拖拽增辉:光斑/填充提亮,滑块放大
      fillEl.style.transition = "none";   // 拖拽跟手,不用弹性
      if (runner.tail) runner.tail.style.opacity = ".55";
      try { track.setPointerCapture(e.pointerId); } catch (err) { /* ignore */ }
      preview(idxFromEvent(e));
    });
    track.addEventListener("pointermove", (e) => {
      if (!state.drag) return;
      preview(idxFromEvent(e));
      lastMoveX = e.clientX;
    });
    const finish = (e) => {
      if (!state.drag) return;
      state.drag = false;
      lastMoveX = null;
      track.classList.remove("is-dragging");
      if (runner.tail) runner.tail.style.opacity = "0";
      fillEl.style.transition = `width .45s ${EASE}`;   // 松手回弹,小人回到 720ms 待机循环
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
      lastMoveX = null;
      track.classList.remove("is-dragging");
      if (runner.tail) runner.tail.style.opacity = "0";
      fillEl.style.transition = `width .45s ${EASE}`;
      sync();
    });

    // ←/→ 微调(面板聚焦时;小人随按键跑动)
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
    runnerSetMode();   // 打开面板即启动待机循环(720ms);拖拽时由 state.drag 切 420ms
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
  // ---------- 可视化诊断:探针/fiber 断点直接显示在输入框下方(截图即可排障) ----------
  let diagEl = null;
  let diagLast = "";
  function collectDiag() {
    const out = [];
    const span = document.querySelector("[data-thought][data-thought-levels]");
    out.push("探针" + (span ? "✓" : "✗"));
    if (span) {
      out.push("档位[" + ((span.getAttribute("data-thought-levels") || "") || "空") + "]");
      let ju = null;
      try { ju = span.parentElement ? findThoughtFiber(span.parentElement) : null; } catch (err) { out.push("fiber异常"); }
      out.push("fiber" + (ju ? "✓" : "✗"));
    } else {
      out.push("data-thought×" + document.querySelectorAll("[data-thought]").length);
      out.push("composer×" + document.querySelectorAll("[data-testid='v4-composer']").length);
      out.push("触发器×" + document.querySelectorAll("[data-composer-thought-control]").length);
    }
    out.push("入口" + (entry && entry.isConnected ? "✓" : "✗"));
    return out.join(" · ");
  }
  function updateDiag(msg) {
    if (!msg) {
      if (diagEl) { diagEl.remove(); diagEl = null; }
      diagLast = "";
      return;
    }
    if (msg === diagLast && diagEl && diagEl.isConnected) return;   // 未变化零 DOM 写
    diagLast = msg;
    try {
      const card = document.querySelector("[data-testid='v4-composer']");
      if (!card || !card.parentElement) return;
      if (!diagEl || !diagEl.isConnected) {
        diagEl = document.createElement("div");
        diagEl.setAttribute("data-zslider-diag", "1");
        Object.assign(diagEl.style, {
          fontSize: "11px", color: "#e0983a", textAlign: "center",
          marginTop: "4px", userSelect: "none",
          whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis",
        });
        card.insertAdjacentElement("afterend", diagEl);
      }
      diagEl.textContent = "⧗ 思考条诊断:" + msg;
    } catch (err) { /* 静默 */ }
  }

  function sync() {
    try {
      ensureStyle();
      const p = probe();
      if (!p) {
        closePanel(false);
        if (entry) entry.remove();
        entry = null;
        updateDiag(collectDiag());
        return;
      }
      updateDiag(null);
      const trig = anchorTrigger();
      if (!ensureEntry(trig, p.el)) {
        updateDiag("锚点缺失:触发器" + (trig ? "✓" : "✗") + " 探针父行无父级");
        return;
      }
      // 隐藏原生下拉:触发器由 fiber 反查后直接置 display:none,
      // React 重建该元素时会在下一轮 sync 重新隐藏(紧凑模式的 CSS 规则仍作兜底)
      if (trig && trig.style.display !== "none") trig.style.display = "none";
      state.levels = p.levels;
      state.cur = p.cur;
      const key = p.levels.join(",") + "|" + p.cur;
      if (state.key !== key) {
        state.key = key;
        const idx = p.levels.indexOf(p.cur);
        entryName.textContent = p.cur;
        entryName.style.color = idx >= 0 ? "" : "rgba(233,99,99,0.9)";   // 未知档位标红提示
        setMini(idx >= 0 ? idx : 0, p.levels.length);
        // 换档确认:确保小人循环在跑 + 滑块处发射涟漪 + 档名脉冲
        if (state.cur && panel) runnerSetMode();
        if (panel) rippleAt();
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
  window.__zsliderDiag = collectDiag;
  setTimeout(() => {
    window.__zsliderDiag = collectDiag();
    if (probe()) console.info("[zslider] 已就绪:", probe().levels.join("/"), "当前", probe().cur || "(未设)");
    else console.info("[zslider] 探针未命中:", window.__zsliderDiag, "(输入框下方应显示诊断条)");
  }, 5000);
})();
