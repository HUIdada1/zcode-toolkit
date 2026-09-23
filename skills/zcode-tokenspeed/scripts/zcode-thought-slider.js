/**
 * ZCode 思考强度滑条 —— dsh-reasoning-effort 同款拖动条
 * ====================================================================
 * 工具栏交互:
 *   [ 思考 · high ▁ ]  ←常驻入口:当前档名 + 迷你电量条
 *        └─点击→ 弹出面板(260ms 弹出动效):
 *          ┌────────────────────────────┐
 *          │ 思考强度               high │
 *          │ [●▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒]      │ ← 整条胶囊轨道 + 白色圆形旋钮
 *          └────────────────────────────┘
 *   点外部 / Esc 收起;原生下拉经 CSS 隐藏(单档位固定徽章除外),状态仍双向同步。
 *
 * 轨道样式 1:1 复刻 HanaAyane/dsh-reasoning-effort(styles.ts,MIT):
 *   .re-effort(32px 行) > .re-effort-slider(30px 胶囊,--re-progress 百分比驱动):
 *     .re-effort-track   深色:暗夜蓝→紫渐变 + 内高光/外投影;
 *                        浅色:浅蓝底 + ::before 进度填充(width = --re-progress)
 *     .re-effort-fx > canvas.re-effort-canvas  像素辐射特效(drawRadiation,
 *                        4px 像素格能量柱 + 拖尾粒子 + 旋钮处辉光,拖拽加速增辉)
 *                   > span.re-effort-flare   旋钮处拖尾光斑(伪元素十字辉光)
 *     span.re-effort-knob  白色圆形旋钮(28px,clamp 贴边)
 *   状态类(与上游同款):is-dragging 跟手增辉 / is-busy 提交中半透明 /
 *   is-error 未知档位描边 / slider[data-top] 顶端呼吸动画。
 *   主题:panel.zs-dark ↔ 上游 body[data-ds-dark-theme];canvas isDark 逐帧读取。
 *
 * 拖拽手感(同上游):连续跟手(raw 浮点,旋钮/光斑/canvas 实时跟随),
 *   松手 Math.round 吸附最近档位并提交;提交期间 is-busy,失败回弹到原档位。
 *
 * 数据源(全部只读 DOM,零协议逆向):
 *   - 状态探针:V4ComposerToolbar 渲染的隐藏 span(className:"hidden"),
 *     data-thought=当前档位,data-thought-levels=可用档位(逗号分隔);
 *     React 随会话实时更新,原生操作(含 t 键循环切档)自动回流到入口与面板。
 *   - 入口锚点:原生档位触发器 [data-composer-thought-control](隐藏后仅作定位基准)。
 *   - 生成中判定:停止按钮 → 运行中 live tail → 推理流,见 THINK_SELECTORS。
 *
 * 写路径(按优先级):
 *   1) React fiber:沿 __reactFiber$ return 链找到 onValueChange(option.type==='select')
 *      直调——等价于用户点选菜单项,原生继续走 session/setThoughtLevel 会话 RPC。
 *   2) 降级:模拟点击触发器打开 Radix 菜单,按 options 顺序点第 index 个
 *      [role="option"](档位显示名是 i18n 文案,按序号而非文本定位)。
 *
 * 调试接口:window.__zsliderCtl(config / setThinking / setSegments(已废弃,兼容保留) /
 *   refresh / diag / state),渲染层诊断:window.__zsliderDiag(5s 后由函数变成字符串)。
 *
 * 安全:只读状态、只写自己的元素与独立 <style>;异常静默;探针/锚点消失自清理,
 *   不调用应用内部模块、不发协议帧、不碰 ServicePort。
 * 回滚:python zcode_patcher.py --thought-slider --revert,或 restore_clean.py --latest。
 */
(() => {
  if (window.__zslider) return;
  window.__zslider = true;
  const MARK = "data-zslider";
  const ACCENT = "var(--color-warning, var(--zs-accent, #e0983a))";

  // ---------- 可调参数（对外暴露为 window.__zsliderCtl.config，运行时可改） ----------
  const CONFIG = {
    thinking: null,          // null = 自动检测；true / false = 手动锁定（setThinking(null) 恢复自动）
    autoDetectThinking: true,
    segments: 0,             // 已废弃:新版为连续轨道,段数概念移除;字段仅为 __zsliderCtl 兼容保留
  };

  // ---------- 样式:隐藏原生下拉(单档位固定徽章除外)+ 面板动效 + 上游同款拖动条 ----------
  // 主题判定与 TPS 同构(应用主题类 → 系统偏好 → 兜底)。
  const STYLE_ID = "zslider-style";
  function isDarkTheme() {
    try {
      const d = document.documentElement;
      const b = document.body;
      if ((b && b.classList.contains("dark")) || d.classList.contains("dark")) return true;
      if (b && b.getAttribute("data-theme") === "dark") return true;
      if (d.getAttribute("data-theme") === "dark") return true;
      if ((b && b.classList.contains("light")) || d.classList.contains("light")
          || d.getAttribute("data-theme") === "light") return false;
      return !!(window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches);
    } catch (err) { return false; }
  }
  function ensureStyle() {
    if (document.getElementById(STYLE_ID)) return;
    const st = document.createElement("style");
    st.id = STYLE_ID;
    // 主题变量块:面板 chrome 用;暗色值只列颜色类,尺寸类仍在 :root 上(继承下来即可)。
    const VARS_LIGHT = "{"
      + "--zs-bg:#ffffff;--zs-fg:#1b1f24;--zs-dim:#5b6470;--zs-accent:#b45309;"
      + "--zs-glass:rgba(255,255,255,.75);--zs-border:rgba(15,23,42,.10);--zs-chip:rgba(15,23,42,.06);"
      + "--zs-shadow:0 12px 30px rgba(15,23,42,.16),0 2px 8px rgba(15,23,42,.10);"
      + "--zs-r-panel:14px}";
    const VARS_DARK = "{"
      + "--zs-bg:#191c22;--zs-fg:#e9ecf1;--zs-dim:#9aa3b2;--zs-accent:#e0983a;"
      + "--zs-glass:rgba(24,27,34,.66);--zs-border:rgba(255,255,255,.16);--zs-chip:rgba(255,255,255,.08);"
      + "--zs-shadow:0 14px 42px rgba(0,0,0,.45),0 3px 10px rgba(0,0,0,.22)}";
    st.textContent = [
      '[data-composer-thought-control]:not([data-thought-level-fixed="true"]){display:none!important}',
      ":root" + VARS_LIGHT,
      ".dark,html.dark,body.dark,[data-theme='dark']" + VARS_DARK,
      "@media (prefers-color-scheme: dark){:root:not(.light):not([data-theme='light'])" + VARS_DARK + "}",
      "/* 面板:圆角/描边/阴影统一走变量,叠一层自上而下的玻璃渐变;outline:none 压掉 focus() 的系统色焦点环 */",
      ".zslider-panel{border:1px solid var(--zs-border)!important;border-radius:var(--zs-r-panel)!important;"
        + "box-shadow:var(--zs-shadow)!important;outline:none!important;"
        + "background-image:linear-gradient(180deg,var(--zs-glass),transparent 62%);"
        + "backdrop-filter:blur(10px) saturate(1.1)}",
      // ===== 拖动条:1:1 复刻 dsh-reasoning-effort/src/client/styles.ts =====
      // (上游主题条件 body[data-ds-dark-theme] / body:not(...) 在此映射为
      //  panel.zs-dark / panel:not(.zs-dark);上游走隐藏 range input 的 cursor:grab
      //  在此落在 .re-effort 行上,其余数值逐字保留)
      ".re-effort{display:flex;align-items:center;width:100%;min-width:0;height:32px;"
        + "color:#9aa3b2;user-select:none;box-sizing:border-box;cursor:grab;touch-action:none}",
      ".re-effort.is-dragging{cursor:grabbing}",
      ".re-effort-slider{--re-progress:50%;position:relative;width:100%;height:30px;flex:1 1 auto;"
        + "border-radius:999px;isolation:isolate;transition:filter 180ms ease}",
      ".re-effort-track{position:absolute;inset:0;overflow:hidden;border-radius:inherit;"
        + "background:linear-gradient(100deg,#03040a 0%,#071126 22%,#101d4c 45%,#302262 70%,#5d35a0 100%);"
        + "box-shadow:inset 0 1px 0 rgba(189,199,255,.15),inset 0 -1px 0 rgba(0,0,0,.55),"
        + "0 3px 10px rgba(12,17,55,.34)}",
      ".re-effort-track::after{content:'';position:absolute;inset:0;"
        + "background:radial-gradient(circle at 18% 45%,rgba(82,130,255,.12),transparent 24%),"
        + "linear-gradient(90deg,rgba(0,0,0,.28),transparent 42%,rgba(168,113,255,.12));pointer-events:none}",
      ".re-effort-fx{position:absolute;z-index:1;inset:0;overflow:hidden;border-radius:inherit;pointer-events:none}",
      ".re-effort-canvas{position:absolute;z-index:2;inset:0;width:100%;height:100%;opacity:1;"
        + "image-rendering:pixelated;mix-blend-mode:screen;transition:filter 140ms ease}",
      ".re-effort-flare{position:absolute;z-index:3;top:50%;left:var(--re-progress);width:78px;height:46px;"
        + "border-radius:50%;"
        + "background:radial-gradient(ellipse at 100% 50%,rgba(255,255,255,.96) 0 4%,rgba(188,189,255,.8) 11%,"
        + "rgba(106,87,255,.5) 28%,rgba(105,31,255,.2) 49%,transparent 74%);"
        + "filter:blur(2px) saturate(1.25);mix-blend-mode:screen;transform:translate(-100%,-50%);"
        + "transition:left 70ms linear,filter 140ms ease;pointer-events:none}",
      ".re-effort-flare::before,.re-effort-flare::after{content:'';position:absolute;"
        + "inset:50% auto auto 100%;border-radius:999px;transform:translate(-50%,-50%)}",
      ".re-effort-flare::before{width:52px;height:1px;"
        + "background:linear-gradient(90deg,transparent,rgba(100,160,255,.42),#f1ecff,rgba(193,82,255,.65),transparent);"
        + "box-shadow:0 0 7px #9b7cff,0 0 13px rgba(72,132,255,.64)}",
      ".re-effort-flare::after{width:1px;height:20px;"
        + "background:linear-gradient(180deg,transparent,rgba(196,190,255,.84),transparent);"
        + "box-shadow:0 0 7px #9c7cff}",
      ".re-effort-knob{position:absolute;z-index:4;top:50%;"
        + "left:clamp(14px,var(--re-progress),calc(100% - 14px));"
        + "width:28px;height:28px;border:1px solid rgba(255,255,255,.94);border-radius:50%;background:#fff;"
        + "box-shadow:0 0 0 2px rgba(92,105,255,.12),0 0 14px rgba(121,82,255,.48),0 2px 7px rgba(0,0,0,.3);"
        + "transform:translate(-50%,-50%);"
        + "transition:left 190ms cubic-bezier(.22,1,.36,1),transform 160ms ease,box-shadow 180ms ease;"
        + "pointer-events:none}",
      ".re-effort.is-dragging .re-effort-canvas{filter:saturate(1.45) brightness(1.28) contrast(1.06)}",
      ".re-effort.is-dragging .re-effort-flare{filter:blur(1.5px) saturate(1.6) brightness(1.42);transition:none}",
      ".re-effort.is-dragging .re-effort-knob{transform:translate(-50%,-50%) scale(1.07);transition:none;"
        + "box-shadow:0 0 0 3px rgba(113,115,255,.25),0 0 20px rgba(74,145,255,.86),"
        + "0 0 31px rgba(171,53,255,.66),0 3px 8px rgba(0,0,0,.32)}",
      ".re-effort-slider[data-top] .re-effort-track{animation:re-effort-dark-breathe 1.9s ease-in-out infinite}",
      ".re-effort-slider[data-top] .re-effort-knob{box-shadow:"
        + "0 0 0 3px rgba(119,99,255,.18),0 0 22px rgba(135,78,255,.76),"
        + "0 0 34px rgba(53,121,255,.34),0 3px 8px rgba(0,0,0,.3)}",
      ".re-effort.is-error .re-effort-slider{outline:1px solid #e5484d;outline-offset:2px}",
      ".re-effort.is-busy{opacity:.72}",
      // 浅色主题分支(逐字对应上游 body:not([data-ds-dark-theme]) 块)
      ".zslider-panel:not(.zs-dark) .re-effort-track{background:#e5f0ff;"
        + "box-shadow:inset 0 1px 0 rgba(255,255,255,.9),inset 0 0 0 1px rgba(80,133,194,.14),"
        + "0 3px 10px rgba(48,101,165,.13)}",
      ".zslider-panel:not(.zs-dark) .re-effort-track::before{content:'';position:absolute;z-index:0;"
        + "inset:0 auto 0 0;width:var(--re-progress);border-radius:inherit;"
        + "background:linear-gradient(90deg,#fff 0%,#e2f0ff 20%,#a8d0fb 57%,#438fdf 100%);"
        + "transition:width 190ms cubic-bezier(.22,1,.36,1)}",
      ".zslider-panel:not(.zs-dark) .re-effort-slider[data-top] .re-effort-track::before{"
        + "background:linear-gradient(90deg,#fff 0%,#d7eaff 18%,#75afea 54%,#0751ad 100%)}",
      ".zslider-panel:not(.zs-dark) .re-effort.is-dragging .re-effort-track::before{transition:none}",
      ".zslider-panel:not(.zs-dark) .re-effort-track::after{z-index:1;"
        + "background:linear-gradient(90deg,rgba(255,255,255,.48),transparent 34%,rgba(23,101,201,.07))}",
      ".zslider-panel:not(.zs-dark) .re-effort-canvas{opacity:.78;mix-blend-mode:multiply}",
      ".zslider-panel:not(.zs-dark) .re-effort-flare{"
        + "background:radial-gradient(ellipse at 100% 50%,rgba(255,255,255,.98) 0 5%,rgba(204,231,255,.88) 13%,"
        + "rgba(91,162,241,.48) 31%,rgba(37,111,207,.16) 53%,transparent 75%);"
        + "filter:blur(2px) saturate(1.12)}",
      ".zslider-panel:not(.zs-dark) .re-effort-flare::before{"
        + "background:linear-gradient(90deg,transparent,rgba(116,177,244,.34),#fff,rgba(66,139,225,.58),transparent);"
        + "box-shadow:0 0 7px rgba(58,133,222,.5),0 0 13px rgba(104,176,255,.38)}",
      ".zslider-panel:not(.zs-dark) .re-effort-flare::after{"
        + "background:linear-gradient(180deg,transparent,rgba(255,255,255,.94),transparent);"
        + "box-shadow:0 0 7px rgba(64,137,224,.44)}",
      ".zslider-panel:not(.zs-dark) .re-effort-knob{border-color:rgba(126,160,197,.32);"
        + "box-shadow:0 0 0 2px rgba(58,124,207,.09),0 0 13px rgba(48,118,207,.3),0 3px 8px rgba(39,77,119,.18)}",
      ".zslider-panel:not(.zs-dark) .re-effort-slider[data-top] .re-effort-track{"
        + "animation-name:re-effort-light-breathe}",
      ".zslider-panel:not(.zs-dark) .re-effort-slider[data-top] .re-effort-knob,"
        + ".zslider-panel:not(.zs-dark) .re-effort.is-dragging .re-effort-knob{box-shadow:"
        + "0 0 0 3px rgba(36,105,192,.15),0 0 20px rgba(25,100,201,.45),0 3px 8px rgba(39,77,119,.18)}",
      "@keyframes re-effort-dark-breathe{0%,100%{box-shadow:inset 0 1px 0 rgba(196,204,255,.16),"
        + "0 3px 10px rgba(18,25,72,.4)}50%{box-shadow:inset 0 1px 0 rgba(220,214,255,.24),"
        + "0 0 21px rgba(111,66,255,.5)}}",
      "@keyframes re-effort-light-breathe{0%,100%{box-shadow:inset 0 1px 0 rgba(255,255,255,.9),"
        + "inset 0 0 0 1px rgba(67,124,193,.16),0 3px 10px rgba(48,101,165,.13)}"
        + "50%{box-shadow:inset 0 1px 0 rgba(255,255,255,.96),inset 0 0 0 1px rgba(31,102,190,.22),"
        + "0 0 19px rgba(31,105,201,.24)}}",
      // ===== 面板 chrome(本插件自有,保留) =====
      "@keyframes zsliderIn{from{opacity:0;transform:translateY(10px) scale(.85);filter:blur(6px)}60%{filter:blur(0)}to{opacity:1;transform:translateY(0) scale(1);filter:blur(0)}}",
      "@keyframes zsliderOut{from{opacity:1;transform:translateY(0) scale(1);filter:blur(0)}to{opacity:0;transform:translateY(5px) scale(.96);filter:blur(4px)}}",
      "@keyframes zsliderPulse{0%{transform:scale(1)}40%{transform:scale(1.28)}100%{transform:scale(1)}}",
      "@media (max-width:420px){:root{--zs-r-panel:12px}}",
      "/* 减弱动效:上游同款(呼吸动画/跟手过渡全关)+ 面板动画全关 */",
      "@media (prefers-reduced-motion: reduce){"
        + ".re-effort-slider[data-top] .re-effort-track{animation:none}"
        + ".re-effort-knob,.re-effort-flare,.zslider-panel:not(.zs-dark) .re-effort-track::before{transition:none}"
        + ".zslider-panel,.zslider-panel *{animation:none!important}}",
    ].join("\n");
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
    entryMini.firstChild.style.background = effortColor(idx, n).color;
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
      color: "var(--color-foreground-subtle, var(--zs-dim, #9a9a9a))",
    });
    entry.title = "思考强度 · 点击调整";
    entry.addEventListener("pointerenter", () => {
      entry.style.background = "rgba(127,127,127,0.14)";
      entry.style.color = "var(--color-foreground, var(--zs-fg, #e8e8e8))";
    });
    entry.addEventListener("pointerleave", () => {
      entry.style.background = "transparent";
      entry.style.color = "var(--color-foreground-subtle, var(--zs-dim, #9a9a9a))";
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
      borderRadius: "999px", overflow: "hidden",
      background: "rgba(127,127,127,0.28)",
    });
    const miniFill = document.createElement("span");
    Object.assign(miniFill.style, {
      position: "absolute", left: "0", bottom: "0", width: "100%",
      borderRadius: "999px", background: "#4d9dff", height: "0",
      // 竖条走高度过渡,过冲在竖直方向不撞裁剪边界,弹性手感保留
      transition: "height .28s cubic-bezier(0.34,1.56,0.64,1)",
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

  // ---------- 弹出面板(拖动条) ----------
  let panel = null;
  let track = null;          // .re-effort 行(承载 is-dragging/is-busy/is-error)
  let sliderEl = null;       // .re-effort-slider(--re-progress / data-top 落在这里)
  let canvasEl = null;       // .re-effort-canvas(像素辐射特效)
  let labelEl = null;
  let state = { levels: [], cur: "", key: "", drag: false, thinking: false, preview: 0, busy: false };
  let closeTimer = null;
  // canvas 特效状态(与上游 RadiationState 同构):progress=旋钮比例(0~1 连续),dragging=拖拽加速
  let radiation = { progress: 0.5, dragging: false };
  let fxRaf = 0;
  let fxResizeObserver = null;
  let fxRedraw = null;       // reduced-motion 时 preview 变化手动补一帧

  // 上游 drawRadiation 逐字移植(src/client/index.tsx):
  // 以旋钮为原点向左铺 4px 像素格能量柱(三组正弦叠加+噪点颗粒),
  // 外加 14 条拖尾粒子流与旋钮处径向辉光;深/浅色两套配色。
  function drawRadiation(context, width, height, time, st) {
    const origin = st.progress * width;
    const isDark = panel ? panel.classList.contains("zs-dark") : isDarkTheme();
    const cell = 4;
    const speed = st.dragging ? 2.8 : 1;

    context.clearRect(0, 0, width, height);
    if (origin <= 0) return;

    context.save();
    context.beginPath();
    context.rect(0, 0, origin, height);
    context.clip();

    for (let x = 0; x < origin; x += cell) {
      const delta = x + cell * 0.5 - origin;
      const distance = Math.abs(delta);
      const phaseA = distance / 10 - time * 0.0074 * speed;
      const phaseB = distance / 23 - time * 0.0041 * speed + 1.7;
      const phaseC = distance / 40 - time * 0.0022 * speed + 3.4;
      const sinA = Math.max(0, Math.sin(phaseA));
      const sinB = Math.max(0, Math.sin(phaseB));
      const sinC = Math.max(0, Math.sin(phaseC));
      const waveA = Math.pow(sinA, 2.6);
      const waveB = Math.pow(sinB, 3.2);
      const waveC = Math.pow(sinC, 4);
      const crest = Math.pow(sinA, 15) + Math.pow(sinB, 18) * 0.78;
      const wave = Math.min(1, waveA * 0.76 + waveB * 0.58 + waveC * 0.32);
      const trail = 0.38 + 0.62 * Math.exp(-distance / Math.max(55, width * 0.72));
      const pillar = Math.pow(Math.max(0, Math.sin(x / 20 + time * 0.0016)), 3) * 0.27;
      const columnEnergy = trail * (wave * 1.04 + pillar + crest * 0.32);

      if (columnEnergy > 0.012) {
        const nearness = Math.max(0, 1 - distance / Math.max(1, width * 0.78));
        const red = isDark
          ? Math.round(42 + 124 * nearness + 75 * wave)
          : Math.round(28 + 58 * nearness + 15 * wave);
        const green = isDark
          ? Math.round(56 + 58 * nearness + 44 * crest)
          : Math.round(88 + 72 * nearness + 30 * crest);
        const blue = isDark
          ? Math.round(175 + 72 * nearness + 8 * wave)
          : Math.round(182 + 62 * nearness);
        const alpha = isDark
          ? Math.min(0.88, columnEnergy * 0.72)
          : Math.min(0.62, columnEnergy * 0.54);
        context.fillStyle = "rgba(" + red + ", " + green + ", " + blue + ", " + alpha + ")";
        context.fillRect(x, 0, cell - 1, height);
      }

      for (let y = 0; y < height; y += cell) {
        const deltaY = y + cell * 0.5 - height * 0.5;
        const radial = Math.hypot(delta / 38, deltaY / 11);
        const halo = Math.exp(-radial * 0.96) * 1.08;
        const verticalShape = 0.58 + 0.42 * Math.cos((deltaY / height) * Math.PI);
        const grain = 0.72 + 0.28 * Math.sin(x * 0.73 + y * 1.31 + time * 0.006);
        const alpha = Math.min(0.96, (columnEnergy * 0.88 + halo + crest * 0.19) * verticalShape * grain);
        if (alpha < 0.035) continue;

        const hot = Math.max(0, 1 - radial / 2.4);
        const red = isDark
          ? Math.round(54 + 148 * hot + 42 * wave + 35 * crest)
          : Math.round(25 + 72 * hot + 12 * wave);
        const green = isDark
          ? Math.round(68 + 78 * hot + 46 * crest)
          : Math.round(98 + 72 * hot + 24 * crest);
        const blue = isDark
          ? Math.round(186 + 64 * hot)
          : Math.round(194 + 56 * hot);
        context.fillStyle = "rgba(" + red + ", " + green + ", " + blue + ", " + (isDark ? alpha : alpha * 0.72) + ")";
        context.fillRect(x, y, cell - 1, cell - 1);
      }
    }

    for (let i = 0; i < 14; i += 1) {
      const travel = (time * (st.dragging ? 0.16 : 0.065) * (0.78 + (i % 5) * 0.09) + i * 23) % Math.max(30, origin + 64);
      const particleX = origin - travel;
      if (particleX < -24 || particleX > width + 16) continue;
      const particleY = 3 + ((i * 13 + Math.sin(time * 0.003 + i) * 5) % Math.max(7, height - 6));
      const length = 4 + (i % 4) * 4 + (st.dragging ? 6 : 0);
      const alpha = 0.28 + (i % 5) * 0.1;
      const streak = context.createLinearGradient(particleX, 0, particleX + length, 0);
      streak.addColorStop(0, isDark ? "rgba(72,118,255,0)" : "rgba(24,94,184,0)");
      streak.addColorStop(0.68, isDark ? "rgba(112,135,255," + alpha + ")" : "rgba(36,108,202," + alpha * 0.72 + ")");
      streak.addColorStop(1, isDark ? "rgba(236,222,255," + Math.min(1, alpha + 0.26) + ")" : "rgba(103,175,248," + Math.min(0.82, alpha + 0.18) + ")");
      context.fillStyle = streak;
      context.fillRect(particleX, particleY, length, i % 3 === 0 ? 2 : 1);
    }

    const glow = context.createRadialGradient(origin, height / 2, 0, origin, height / 2, 24);
    glow.addColorStop(0, isDark ? "rgba(255,255,255,.82)" : "rgba(255,255,255,.86)");
    glow.addColorStop(0.14, isDark ? "rgba(183,190,255,.54)" : "rgba(162,210,255,.48)");
    glow.addColorStop(0.44, isDark ? "rgba(103,74,255,.28)" : "rgba(37,112,207,.22)");
    glow.addColorStop(1, isDark ? "rgba(86,31,210,0)" : "rgba(25,91,181,0)");
    context.fillStyle = glow;
    context.fillRect(origin - 26, 0, 52, height);
    context.restore();
  }

  // canvas 生命周期:面板打开期间 rAF 连续重绘;reduced-motion 只画一帧(preview 变化补帧)
  function initCanvasFx() {
    try {
      if (!canvasEl) return;
      const context = canvasEl.getContext("2d");
      if (!context) return;
      let width = 1;
      let height = 1;
      const reduced = (typeof window.matchMedia === "function")
        ? window.matchMedia("(prefers-reduced-motion: reduce)") : null;

      const resize = () => {
        const bounds = canvasEl.getBoundingClientRect();
        const ratio = Math.min(window.devicePixelRatio || 1, 2);
        width = Math.max(1, bounds.width);
        height = Math.max(1, bounds.height);
        canvasEl.width = Math.max(1, Math.round(width * ratio));
        canvasEl.height = Math.max(1, Math.round(height * ratio));
        context.setTransform(ratio, 0, 0, ratio, 0, 0);
      };
      const draw = (time) => {
        drawRadiation(context, width, height, time || (window.performance && performance.now ? performance.now() : 0), radiation);
      };
      const loop = (time) => {
        draw(time);
        fxRaf = window.requestAnimationFrame(loop);
      };

      resize();
      draw();
      if (!(reduced && reduced.matches)) fxRaf = window.requestAnimationFrame(loop);
      fxRedraw = () => { if (!fxRaf) draw(); };
      if (typeof ResizeObserver === "function") {
        fxResizeObserver = new ResizeObserver(() => { resize(); draw(); });
        fxResizeObserver.observe(canvasEl);
      }
    } catch (err) { /* canvas 不可用时滑条其余部分照常工作 */ }
  }

  function stopCanvasFx() {
    if (fxRaf) { try { window.cancelAnimationFrame(fxRaf); } catch (err) { /* ignore */ } fxRaf = 0; }
    if (fxResizeObserver) { try { fxResizeObserver.disconnect(); } catch (err) { /* ignore */ } fxResizeObserver = null; }
    fxRedraw = null;
  }

  // ---------- 思考中/空闲：检测（保留给入口/诊断;上游样式无"思考中流动"态） ----------
  // 自动检测，按可靠性排序（①② 来自 3.14.3 renderer bundle 实证，非猜测）：
  //   ① 停止按钮在场 —— 客户端把「停止生成」与「发送」做成同一按钮位的**互斥渲染**，
  //      aria-label 取 i18n `chat.stop`（中文「停止生成」/ 英文「Stop generating」）。
  //      停止按钮出现 ⇔ 正在生成，语义最准。
  //   ② 运行中的 live tail —— 会话区里正在跑的轮次容器 [data-v4-running-live-tail]。
  //   ③ 推理文本正在流式输出 [data-reasoning-streaming-line]（更窄，仅 reasoning 流期间）。
  //   ④ 兜底 testid：当前版本里不存在（已扫全量 renderer 确认），留给未来版本。
  // 手动覆盖：CONFIG.thinking = true/false，或 __zsliderCtl.setThinking(v)；传 null 恢复自动。
  const THINK_SELECTORS = [
    "button[aria-label*='停止']",
    "button[aria-label^='Stop']",
    "[title*='停止生成']",
    "[title^='Stop generating']",
    "[data-v4-running-live-tail]",
    "[data-reasoning-streaming-line]",
    "[data-testid*='stop-generat']",
    "[data-testid*='stop-button']",
  ];
  // 可见性用 getClientRects()：offsetParent 对 position:fixed 元素恒为 null，会漏判
  function isRendered(el) {
    try { return el.getClientRects().length > 0; } catch (err) { return false; }
  }
  // 检测结果 250ms 复用：流式期间 DOM 变动极密，getClientRects 会强制布局，
  // 每轮都全量探测（8 个选择器）代价偏高；250ms 的滞后对动效不可感知。
  let thinkCache = { at: 0, val: false };
  function detectThinking() {
    if (CONFIG.thinking != null) return !!CONFIG.thinking;
    if (!CONFIG.autoDetectThinking) return false;
    const now = (typeof performance === "object" && performance.now) ? performance.now() : Date.now();
    if (now - thinkCache.at < 250) return thinkCache.val;
    let val = false;
    for (const sel of THINK_SELECTORS) {
      let el = null;
      try { el = document.querySelector(sel); } catch (err) { continue; }
      if (el && isRendered(el)) { val = true; break; }
    }
    thinkCache = { at: now, val };
    return val;
  }
  function setThinking(v) {
    const b = !!v;
    if (state.thinking === b) return;
    state.thinking = b;
    if (track && track.isConnected) track.dataset.thinking = b ? "1" : "0";
  }

  // 档位着色(仅迷你电量条用;拖动条本体色彩由上游同款 CSS/canvas 负责):
  // 低/中蓝 → 高紫罗兰 → max 亮蓝
  function effortColor(idx, n) {
    const ratio = (idx >= 0 && n > 1) ? idx / (n - 1) : 0;
    if (ratio >= 0.999) return { color: "#7dd3fc" };
    if (ratio >= 0.6) return { color: "#a78bfa" };
    return { color: "#4d9dff" };
  }

  // ---------- 预览(连续跟手):raw ∈ [0, n-1] 浮点,驱动 --re-progress / 光斑 / canvas ----------
  const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
  function preview(raw) {
    const n = state.levels.length;
    const r = clamp(raw, 0, Math.max(0, n - 1));
    state.preview = r;
    const pct = n > 1 ? (r / (n - 1)) * 100 : 0;
    if (sliderEl) sliderEl.style.setProperty("--re-progress", pct + "%");
    radiation.progress = n > 1 ? r / (n - 1) : 0;
    if (labelEl) labelEl.textContent = state.levels[Math.round(r)] ?? "";
    if (fxRedraw) fxRedraw();   // reduced-motion 时手动补一帧
  }

  function closePanel(animate) {
    if (!panel) return;
    const p = panel;
    panel = null;
    stopCanvasFx();
    track = null;
    sliderEl = null;
    canvasEl = null;
    window.removeEventListener("resize", onViewportChange);
    window.removeEventListener("scroll", onViewportChange, true);
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
    p.className = "zslider-panel" + (isDarkTheme() ? " zs-dark" : "");
    p.tabIndex = -1;
    Object.assign(p.style, {
      position: "fixed", zIndex: "2147483000",
      // 响应式:窄窗口收缩到视口内
      width: "min(236px, calc(100vw - 24px))",
      boxSizing: "border-box", padding: "12px 14px 14px",
      background: "var(--color-background, var(--zs-bg, #1e1e1e))",
      color: "var(--color-foreground, var(--zs-fg, #e8e8e8))",
      transformOrigin: "bottom left",
      animation: "none",   // 先无动画完成定位测量(scale 状态会污染 getBoundingClientRect),定位后再启动
      userSelect: "none",
    });

    // 标题行:左「思考强度」、右当前档名
    const head = document.createElement("div");
    Object.assign(head.style, {
      display: "flex", justifyContent: "space-between", alignItems: "center",
      marginBottom: "10px", gap: "8px",
    });
    const t = document.createElement("span");
    t.textContent = "思考强度";
    t.style.cssText = "font-size:12px;color:var(--color-foreground-subtle,var(--zs-dim,#9a9a9a));white-space:nowrap;";
    labelEl = document.createElement("span");
    // 档名做成胶囊:给数值一个容器,换档脉冲时不再像散落的文字
    labelEl.style.cssText = "font-size:12px;font-weight:600;color:" + ACCENT
      + ";font-variant-numeric:tabular-nums;display:inline-block;transform-origin:right center;"
      + "padding:2px 8px;border-radius:999px;background:var(--zs-chip);line-height:1.3;white-space:nowrap;";
    head.appendChild(t);
    head.appendChild(labelEl);
    p.appendChild(head);

    // 拖动条行 = 上游同款 .re-effort 结构(track + fx(canvas+flare) + knob)
    track = document.createElement("div");
    track.className = "re-effort";
    sliderEl = document.createElement("div");
    sliderEl.className = "re-effort-slider";
    const trackBg = document.createElement("div");
    trackBg.className = "re-effort-track";
    const fx = document.createElement("div");
    fx.className = "re-effort-fx";
    canvasEl = document.createElement("canvas");
    canvasEl.className = "re-effort-canvas";
    const flare = document.createElement("span");
    flare.className = "re-effort-flare";
    const knob = document.createElement("span");
    knob.className = "re-effort-knob";
    fx.appendChild(canvasEl);
    fx.appendChild(flare);
    sliderEl.appendChild(trackBg);
    sliderEl.appendChild(fx);
    sliderEl.appendChild(knob);
    track.appendChild(sliderEl);
    p.appendChild(track);

    // 先入 DOM 量尺寸;定位在 panel 赋值之后做(见函数末尾 placePanel)
    document.body.appendChild(p);
    initCanvasFx();

    // 拖拽/点击:指针横向位置 → 连续 raw(浮点),松手吸附最近档位提交
    const rawFromEvent = (e) => {
      const r = (sliderEl || track).getBoundingClientRect();
      if (r.width <= 0 || state.levels.length < 2) return state.preview;
      const n = state.levels.length;
      return clamp((e.clientX - r.left) / r.width * (n - 1), 0, n - 1);
    };
    const committedIdx = () => {
      const i = state.levels.indexOf(state.cur);
      return i >= 0 ? i : 0;
    };
    const commitTo = (idx) => {
      const value = state.levels[idx];
      if (!value || value === state.cur || state.busy) return;
      state.busy = true;
      if (track) track.classList.add("is-busy");
      Promise.resolve(commitLevel(value, idx))
        .catch(() => false)
        .then((ok) => {
          state.busy = false;
          if (track && track.isConnected) track.classList.remove("is-busy");
          if (!ok) {
            // 提交失败( fiber 与菜单路径均未命中 ):回弹到原档位
            preview(committedIdx());
            if (labelEl) labelEl.textContent = state.cur;
            console.warn("[zslider] 档位提交失败(fiber 与菜单路径均未命中)");
          }
          // 成功路径:探针 data-thought 更新 → MutationObserver 触发 sync → refreshPanel 回填
        });
    };
    track.addEventListener("pointerdown", (e) => {
      e.preventDefault();
      if (state.busy) return;
      state.drag = true;
      track.classList.add("is-dragging");   // 跟手:旋钮/光斑过渡归零 + canvas 增辉加速
      radiation.dragging = true;
      try { track.setPointerCapture(e.pointerId); } catch (err) { /* ignore */ }
      preview(rawFromEvent(e));
    });
    track.addEventListener("pointermove", (e) => {
      if (!state.drag) return;
      preview(rawFromEvent(e));
    });
    const finish = (e) => {
      if (!state.drag) return;
      state.drag = false;
      radiation.dragging = false;
      track.classList.remove("is-dragging");
      try { track.releasePointerCapture(e.pointerId); } catch (err) { /* ignore */ }
      const idx = Math.round(rawFromEvent(e));
      preview(idx);   // 吸附:旋钮/光斑带着 190ms 缓动滑到最近档位
      commitTo(idx);
      // 不立刻 sync():提交期间保留乐观位置,探针回流后由 refreshPanel 确认/回弹
    };
    track.addEventListener("pointerup", finish);
    track.addEventListener("pointercancel", () => {
      // 取消:回滚到已提交档位(上游 rollback 同款)
      state.drag = false;
      radiation.dragging = false;
      track.classList.remove("is-dragging");
      preview(committedIdx());
      if (labelEl) labelEl.textContent = state.cur;
    });

    // ←/→ 微调(面板聚焦时):按一下直接提交一档(上游 onKeyDown 同款)
    p.addEventListener("keydown", (e) => {
      const n = state.levels.length;
      if (!n) return;
      const current = clamp(Math.round(state.preview), 0, n - 1);
      let target;
      if (e.key === "ArrowLeft" || e.key === "ArrowDown" || e.key === "PageDown") target = Math.max(0, current - 1);
      else if (e.key === "ArrowRight" || e.key === "ArrowUp" || e.key === "PageUp") target = Math.min(n - 1, current + 1);
      else if (e.key === "Home") target = 0;
      else if (e.key === "End") target = n - 1;
      else return;
      e.preventDefault();
      preview(target);
      commitTo(target);
    });

    panel = p;
    placePanel();
    window.addEventListener("resize", onViewportChange);
    window.addEventListener("scroll", onViewportChange, true);
    refreshPanel();
    setThinking(detectThinking());   // 思考态仅记录/暴露,不改变拖动条样式
    // 定位就绪后启动弹出动效
    p.style.animation = "zsliderIn 260ms cubic-bezier(0.2,0.9,0.25,1.15)";
    document.addEventListener("pointerdown", onDocDown, true);
    document.addEventListener("keydown", onDocKey, true);
    try { p.focus(); } catch (err) { /* ignore */ }
  }

  // 面板定位:入口上方左对齐,越界收敛到视口内,上方放不下落到底部。
  // 用 offsetWidth/Height 量尺寸——它们不受 transform(弹出动画的 scale)影响。
  function placePanel() {
    if (!panel || !entry) return;
    const er = entry.getBoundingClientRect();
    const w = panel.offsetWidth, h = panel.offsetHeight;
    let left = er.left;
    let top = er.top - h - 8;
    if (left + w > window.innerWidth - 8) left = window.innerWidth - w - 8;
    if (top < 8) top = er.bottom + 8;                    // 上方放不下 → 入口下方
    if (top + h > window.innerHeight - 8) top = Math.max(8, window.innerHeight - h - 8);
    panel.style.transformOrigin = (top > er.bottom + 8) ? "top left" : "bottom left";
    panel.style.left = Math.max(8, left) + "px";
    panel.style.top = top + "px";
  }

  // 视口变化(窗口缩放 / 工具栏换行 / 页面滚动)时重新贴合入口;
  // 入口被 React 重建(面板已失效)时直接收起,避免面板飘在旧坐标上。
  // rAF 节流:scroll 触发极密,定位要读布局,不能每次同步跑
  let vpRaf = 0;
  function onViewportChange() {
    if (!panel || vpRaf) return;
    vpRaf = requestAnimationFrame(() => {
      vpRaf = 0;
      if (!panel) return;
      if (!entry || !entry.isConnected) { closePanel(false); return; }
      placePanel();
    });
  }

  function refreshPanel() {
    if (!panel) return;
    const n = state.levels.length;
    if (!n) { closePanel(false); return; }
    const idx = state.levels.indexOf(state.cur);
    if (!state.drag) {
      preview(idx >= 0 ? idx : 0);   // idx < 0(未知档位):进度归零 + is-error 描边
      if (labelEl) labelEl.textContent = state.cur;
    }
    if (sliderEl) {
      if (idx >= 0 && idx === n - 1) sliderEl.setAttribute("data-top", "true");
      else sliderEl.removeAttribute("data-top");
    }
    if (track && track.isConnected) track.classList.toggle("is-error", idx < 0);
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
        // 换档确认:档名脉冲(面板 chrome)
        if (panel && labelEl) {
          labelEl.style.animation = "none";
          void labelEl.offsetHeight;   // 强制回流重触发动画
          labelEl.style.animation = "zsliderPulse .35s cubic-bezier(.34,1.56,.64,1)";
        }
      }
      setThinking(detectThinking());   // 每轮同步刷新"是否正在思考"
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

  // ---------- 对外控制台接口（排障 / 调参；DevTools 里直接敲） ----------
  //   __zsliderCtl.config                        参数对象，直接改字段即可
  //                                              （thinking / autoDetectThinking / segments(已废弃)）
  //   __zsliderCtl.setThinking(true|false|null)  手动锁定思考态；传 null 恢复自动检测
  //   __zsliderCtl.setSegments(n)                已废弃:连续轨道无段数概念,仅为兼容保留
  //   __zsliderCtl.refresh()                     立即重跑一轮同步
  //   __zsliderCtl.diag() / __zsliderCtl.state() 诊断字符串 / 状态快照
  window.__zsliderCtl = {
    version: "dsh-look-1",
    config: CONFIG,
    setThinking: (v) => {
      CONFIG.thinking = (v === null || v === undefined) ? null : !!v;
      setThinking(detectThinking());
      return CONFIG.thinking;
    },
    setSegments: (n) => {
      // 已废弃:新版拖动条为上游同款连续轨道,段数不再参与渲染,接口保留防报错
      CONFIG.segments = Math.max(0, Number(n) | 0);
      return CONFIG.segments;
    },
    refresh: () => { sync(); return true; },
    diag: () => collectDiag(),
    state: () => ({
      levels: state.levels.slice(), cur: state.cur, thinking: state.thinking,
      drag: state.drag, preview: state.preview, busy: state.busy,
      segments: CONFIG.segments | 0, panel: !!panel,
      config: Object.assign({}, CONFIG),
    }),
  };

  // 自检:加载 5s 后探针状态打到 console(排障用;无档位模型静默属预期)
  window.__zsliderDiag = collectDiag;
  setTimeout(() => {
    window.__zsliderDiag = collectDiag();
    if (probe()) console.info("[zslider] 已就绪:", probe().levels.join("/"), "当前", probe().cur || "(未设)");
    else console.info("[zslider] 探针未命中:", window.__zsliderDiag, "(输入框下方应显示诊断条)");
  }, 5000);
})();
