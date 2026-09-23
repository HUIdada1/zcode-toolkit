/**
 * ZCode 思考强度滑条 —— 分段轨道 + 吸附拖拽(替代原生「思考级别」下拉)
 * ====================================================================
 * 工具栏交互:
 *   [ 思考 · high ▁▃▆ ]  ←常驻入口:当前档名 + 迷你电量条
 *        └─点击→ 弹出面板(260ms 弹出动效):
 *          ┌────────────────────────────┐
 *          │ 思考强度               high │
 *          │ [▬▬▬][▬▬▬][▬▬▬][    ]      │ ← 分段轨道:每档一段,点/拖/←→ 切换
 *          └────────────────────────────┘
 *   点外部 / Esc 收起;原生下拉经 CSS 隐藏(单档位固定徽章除外),状态仍双向同步。
 *
 * 轨道形态:
 *   轨道 = N 段等宽胶囊(16px 加粗,填充层带顶部高光+底部投影的立体光泽),
 *   每段内含填充层 .zslider-fill,width 0↔100% 即「分段点亮」,--zs-stagger 让各段错开。
 *   段数默认 = 可用档位数(可用 __zsliderCtl.setSegments 强制)。
 *   四态(写在 track 的 data-zs):loading 扫入+扫光 / dragging 跟手增辉 /
 *   settling 涟漪 / idle 静止(末段外发光呼吸)。
 *   与 data-zs 正交的 data-thinking(1/0):思考中激活段流动(zsFlow),空闲定格。
 *
 * 数据源(全部只读 DOM,零协议逆向):
 *   - 状态探针:V4ComposerToolbar 渲染的隐藏 span(className:"hidden"),
 *     data-thought=当前档位,data-thought-levels=可用档位(逗号分隔);
 *     React 随会话实时更新,原生操作(含 t 键循环切档)自动回流到入口与面板。
 *   - 入口锚点:原生档位触发器 [data-composer-thought-control](隐藏后仅作定位基准)。
 *   - 生成中判定:停止按钮(aria-label = i18n chat.stop「停止生成 / Stop generating」,
 *     与「发送」按钮互斥渲染,最贴近语义)→ 运行中 live tail → 推理流,见 THINK_SELECTORS。
 *
 * 写路径(按优先级):
 *   1) React fiber:沿 __reactFiber$ return 链找到 onValueChange(option.type==='select')
 *      直调——等价于用户点选菜单项,原生继续走 session/setThoughtLevel 会话 RPC。
 *   2) 降级:模拟点击触发器打开 Radix 菜单,按 options 顺序点第 index 个
 *      [role="option"](档位显示名是 i18n 文案,按序号而非文本定位)。
 *
 * 调试接口:window.__zsliderCtl(config / setThinking / setSegments / refresh / diag / state),
 *   渲染层诊断:window.__zsliderDiag(5s 后由函数变成字符串,两种都要认)。
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
  const ACCENT = "var(--color-warning, var(--zs-accent, #e0983a))";

  // ---------- 可调参数（对外暴露为 window.__zsliderCtl.config，运行时可改） ----------
  const CONFIG = {
    segments: 0,             // 0 = 段数自动等于可用档位数；>0 = 强制段数（档位按比例映射到段）
    thinking: null,          // null = 自动检测；true / false = 手动锁定（setThinking(null) 恢复自动）
    autoDetectThinking: true,
    flowMs: 1400,            // 思考中激活段的流动周期（ms）
  };
  // 段数：自动时等于档位数；强制时用 CONFIG.segments（最少 2 段，最多 8 段）
  const segCount = (n) => (CONFIG.segments > 0
    ? Math.max(2, Math.min(8, CONFIG.segments | 0)) : Math.max(1, n));

  // ---------- 样式:隐藏原生下拉(单档位固定徽章除外)+ 面板动效 ----------
  // 主题判定与 TPS 同构(应用主题类 → 系统偏好 → 兜底):原实现只认 prefers-color-scheme,
  // 「应用深色 + 系统浅色」时轨道会用浅色渐变压在深色面板上,几乎看不见。
  const STYLE_ID = "zslider-style";
  function ensureStyle() {
    if (document.getElementById(STYLE_ID)) return;
    const st = document.createElement("style");
    st.id = STYLE_ID;
    // 主题变量块：暗色值只列颜色类，尺寸/时长类仍在 :root 上（继承下来即可）。
    // 应用主题类（.dark / html.dark / body.dark / data-theme）优先，系统偏好兜底。
    const VARS_LIGHT = "{"
      + "--zs-bg:#ffffff;--zs-fg:#1b1f24;--zs-dim:#5b6470;--zs-accent:#b45309;"
      + "--zs-rail-a:#eaf0fb;--zs-rail-b:#dce6fa;"
      + "--zs-rail-inset:inset 0 0 0 1px rgba(77,111,255,.14),inset 0 1px 0 rgba(255,255,255,.9);"
      + "--zs-rail-shadow:0 1px 2px rgba(31,58,147,.10);"
      + "--zs-dot:rgba(92,104,128,.32);--zs-shimmer:rgba(120,150,255,.35);"
      + "--zs-flare:radial-gradient(ellipse at 100% 50%,rgba(255,255,255,.9) 0 5%,rgba(141,166,255,.6) 16%,rgba(91,120,255,.34) 36%,rgba(91,63,255,.12) 55%,transparent 74%);"
      + "--zs-flare-blend:normal;"
      + "--zs-glass:rgba(255,255,255,.75);--zs-border:rgba(15,23,42,.10);--zs-chip:rgba(15,23,42,.06);"
      + "--zs-shadow:0 12px 30px rgba(15,23,42,.16),0 2px 8px rgba(15,23,42,.10);"
      + "--zs-r-panel:14px;--zs-r-pill:999px;--zs-h:16px;"
      + "--zs-gap:5px;--zs-seg:rgba(92,104,128,.22);"
      + "--zs-dur:260ms;--zs-ease:cubic-bezier(.2,.8,.2,1)}";
    const VARS_DARK = "{"
      + "--zs-bg:#191c22;--zs-fg:#e9ecf1;--zs-dim:#9aa3b2;--zs-accent:#e0983a;"
      + "--zs-rail-a:#06070e;--zs-rail-b:#2b1d58;"
      + "--zs-rail-inset:inset 0 1px 0 rgba(189,199,255,.14),inset 0 -1px 0 rgba(0,0,0,.55);"
      + "--zs-rail-shadow:0 2px 8px rgba(0,0,0,.38);"
      + "--zs-dot:rgba(165,175,196,.45);--zs-shimmer:rgba(255,255,255,.55);"
      + "--zs-flare:radial-gradient(ellipse at 100% 50%,rgba(255,255,255,.95) 0 4%,rgba(188,189,255,.8) 11%,rgba(106,87,255,.5) 28%,rgba(105,31,255,.2) 49%,transparent 74%);"
      + "--zs-flare-blend:screen;"
      + "--zs-glass:rgba(24,27,34,.66);--zs-border:rgba(255,255,255,.16);--zs-chip:rgba(255,255,255,.08);"
      + "--zs-seg:rgba(178,188,208,.34);"
      + "--zs-shadow:0 14px 42px rgba(0,0,0,.45),0 3px 10px rgba(0,0,0,.22)}";
    st.textContent = [
      '[data-composer-thought-control]:not([data-thought-level-fixed="true"]){display:none!important}',
      ":root" + VARS_LIGHT,
      ".dark,html.dark,body.dark,[data-theme='dark']" + VARS_DARK,
      "@media (prefers-color-scheme: dark){:root:not(.light):not([data-theme='light'])" + VARS_DARK + "}",
      "/* 面板：圆角/描边/阴影统一走变量，叠一层自上而下的玻璃渐变 */",
      ".zslider-panel{border:1px solid var(--zs-border)!important;border-radius:var(--zs-r-panel)!important;"
        + "box-shadow:var(--zs-shadow)!important;"
        + "background-image:linear-gradient(180deg,var(--zs-glass),transparent 62%);"
        + "backdrop-filter:blur(10px) saturate(1.1)}",
      "/* 分段轨道：rail 是段容器（段间隙透出轨道渐变），每段内含一个 .zslider-fill 做填充层。",
      "   类名/变量/属性全部沿用旧版，外部样式与文档不受影响 */",
      ".zslider-track{display:flex!important;align-items:center;gap:8px}",
      // 轨道本身不要 box-shadow：分段后视觉厚度由段提供，加阴影反而糊住段间隙
      ".zslider-rail{position:relative;flex:1 1 auto;min-width:0;display:flex;align-items:center;"
        + "gap:var(--zs-gap);overflow:hidden;"
        + "background:linear-gradient(100deg,var(--zs-rail-a) 0%,var(--zs-rail-b) 100%)!important;"
        + "border-radius:var(--zs-r-pill);box-shadow:none;transition:filter .2s ease}",
      ".zslider-track:hover .zslider-rail{filter:brightness(1.06) saturate(1.05)}",
      "/* 段：弱化底（未激活时看到的就是它）；内部 fill 宽度 0↔100% 即分段填充过渡 */",
      ".zslider-seg{position:relative;flex:1 1 0;min-width:0;height:100%;"
        + "border-radius:var(--zs-r-pill);background:var(--zs-seg);overflow:hidden;"
        + "box-shadow:inset 0 1px 2px rgba(0,0,0,.16),inset 0 -1px 0 rgba(255,255,255,.10)}",
      "/* 填充层沿用旧类名：width 过渡 + stagger 延迟做出逐段点亮的层次；"
        + "内高光 + 外投影让加粗后的胶囊立起来 */",
      ".zslider-fill{border-radius:var(--zs-r-pill)!important;"
        + "box-shadow:inset 0 1px 1px rgba(255,255,255,.6),inset 0 -1.5px 2px rgba(0,0,0,.28),"
        + "0 2px 5px rgba(0,0,0,.22);"
        + "transition:width var(--zs-dur) var(--zs-ease) var(--zs-stagger,0ms)}",
      "/* 光泽层：顶部亮 → 中部透 → 底部压深；对比度刻意拉大，避免加粗后成为整块平涂 */",
      ".zslider-fill::after{content:'';position:absolute;inset:0;border-radius:inherit;"
        + "pointer-events:none;"
        + "background:linear-gradient(180deg,rgba(255,255,255,.72) 0%,rgba(255,255,255,.30) 32%,"
        + "rgba(255,255,255,.04) 52%,rgba(0,0,0,.10) 74%,rgba(0,0,0,.30) 100%)}",
      ".zslider-track.is-dragging .zslider-fill{filter:saturate(1.35) brightness(1.22) "
        + "drop-shadow(0 0 5px rgba(125,211,252,.45));"
        + "transition-delay:0ms!important;animation:none!important}",
      "/* 思考中：激活段依次流动（延迟复用 --zs-stagger，与点亮同一套错开节奏） */",
      "@keyframes zsFlow{0%,100%{opacity:.6}50%{opacity:1}}",
      ".zslider-track[data-thinking='1'] .zslider-seg[data-on='1'] .zslider-fill"
        + "{animation:zsFlow var(--zs-flow,1400ms) ease-in-out infinite var(--zs-stagger,0ms)}",
      "/* 段弹入：从左向右逐段展开（旧刻度点的级联弹入改挂到段上） */",
      "@keyframes zsSegIn{from{transform:scaleX(.15);opacity:0}to{transform:scaleX(1);opacity:1}}",
      ".zslider-seg{transform-origin:left center}",
      ".zslider-flare{position:absolute;top:50%;left:var(--zp,0%);width:56px;height:26px;border-radius:50%;"
        + "transform:translate(-100%,-50%);background:var(--zs-flare);"
        + "mix-blend-mode:var(--zs-flare-blend);filter:blur(2px) saturate(1.2);"
        + "transition:left var(--zs-dur) var(--zs-ease),filter .14s ease;pointer-events:none}",
      ".zslider-track.is-dragging .zslider-flare{filter:blur(1.5px) saturate(1.55) brightness(1.35)}",
      "/* 加载中：轨道上掠过一道扫光，与填充条扫入同步 */",
      "@keyframes zsShimmer{from{transform:translateX(-120%)}to{transform:translateX(420%)}}",
      ".zslider-shimmer{position:absolute;top:0;bottom:0;left:0;width:34%;border-radius:var(--zs-r-pill);"
        + "background:linear-gradient(90deg,transparent,var(--zs-shimmer),transparent);"
        + "opacity:0;pointer-events:none}",
      ".zslider-track[data-zs='loading'] .zslider-shimmer{opacity:.9;animation:zsShimmer 1.05s linear infinite}",
      "/* 静止/暂停：max 档外发光呼吸。挂在最后一段的填充层上——段盒子的 animation 被入场",
      "   zsSegIn 的 inline 样式占着（inline 优先级高于样式表），挂段上会被整个顶掉 */",
      "@keyframes zsBreathe{0%,100%{box-shadow:0 0 0 0 rgba(125,211,252,0)}"
        + "50%{box-shadow:0 0 10px 1px rgba(125,211,252,.55)}}",
      ".zslider-track[data-top][data-zs='idle']:not([data-thinking='1']) .zslider-seg:last-child .zslider-fill"
        + "{animation:zsBreathe 2.6s ease-in-out infinite}",
      "@keyframes zsliderIn{from{opacity:0;transform:translateY(10px) scale(.85);filter:blur(6px)}60%{filter:blur(0)}to{opacity:1;transform:translateY(0) scale(1);filter:blur(0)}}",
      "@keyframes zsliderOut{from{opacity:1;transform:translateY(0) scale(1);filter:blur(0)}to{opacity:0;transform:translateY(5px) scale(.96);filter:blur(4px)}}",
      "@keyframes zsliderPulse{0%{transform:scale(1)}40%{transform:scale(1.28)}100%{transform:scale(1)}}",
      "@keyframes zsliderRipple{from{opacity:.75;transform:translate(-50%,-50%) scale(.4)}to{opacity:0;transform:translate(-50%,-50%) scale(2.4)}}",
      "/* 响应式：窄屏收紧圆角；触控放大热区到 40px */",
      "@media (max-width:420px){:root{--zs-r-panel:12px}}",
      "@media (pointer:coarse){.zslider-track{height:40px!important}}",
      "/* 减弱动效：时长归零、动画全关 */",
      "@media (prefers-reduced-motion: reduce){:root{--zs-dur:0ms}"
        + ".zslider-panel,.zslider-track,.zslider-track *{animation:none!important}}",
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

  // ---------- 弹出面板(滑条) ----------
  let panel = null;
  let track = null;
  let rail = null;
  let flareEl = null;
  let segs = [];              // 分段：每段 { box(.zslider-seg), fill(.zslider-fill) }
  let labelEl = null;
  let state = { levels: [], cur: "", key: "", drag: false, thinking: false, lit: -1, effortColor: "#4d9dff" };
  let closeTimer = null;
  let phaseTimer = null;
  // 进度条相位(写进 track 的 data-zs,样式表按态下发):
  //   loading  填充扫入中 —— 轨道掠过扫光
  //   dragging 拖拽中     —— 跟手、逐段点亮不延迟
  //   settling 已落位     —— 涟漪
  //   idle     静止/暂停   —— max 档最后一段外发光呼吸
  // 与 data-zs 正交的还有 data-thinking(1/0)：思考中激活段流动，空闲定格。
  function setPhase(v) {
    if (track && track.isConnected) track.dataset.zs = v;
  }

  // ---------- 思考中/空闲：检测与切换 ----------
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
    // 面板关闭时 track 可能已脱离文档：此时只比对 state，避免每秒白写 DOM
    const cur = (track && track.isConnected) ? track.dataset.thinking : null;
    if (state.thinking === b && cur === (b ? "1" : "0")) return;
    state.thinking = b;
    // 新面板的 track 还没有 data-thinking（cur === undefined），这里必须补写：
    // 否则"状态没变就早退"会让新面板的流动动画永远不生效
    if (track && track.isConnected) track.dataset.thinking = b ? "1" : "0";
  }

  // 辐射特效:换档确认时从「当前点亮的最后一段」中心发射一圈涟漪
  // 挂 track(不裁剪)而非 rail(rail 有 overflow:hidden 会把涟漪压成 8px 带子)
  function rippleAt() {
    if (!rail || !rail.parentElement) return;
    const S = segs.length;
    if (!S) return;
    const lit = state.lit >= 0 ? state.lit : 0;
    const cx = rail.offsetLeft + ((lit + 0.5) / S) * rail.offsetWidth;
    const r = document.createElement("div");
    Object.assign(r.style, {
      position: "absolute", left: cx + "px", top: "50%",
      width: "30px", height: "30px", borderRadius: "50%",
      border: "1.5px solid " + (state.effortColor || FILL), pointerEvents: "none",
      animation: "zsliderRipple .5s ease-out forwards",
    });
    rail.parentElement.appendChild(r);
    setTimeout(() => r.remove(), 520);
  }

  // 档位着色(dsh 规格):低/中蓝 → 高紫罗兰 → max 亮蓝并泛光脉冲
  function effortColor(idx, n) {
    const ratio = (idx >= 0 && n > 1) ? idx / (n - 1) : 0;
    if (ratio >= 0.999) return { color: "#7dd3fc", glow: "0 0 10px 2px rgba(125,211,252,.65)", pulse: true };
    if (ratio >= 0.6) return { color: "#a78bfa", glow: "0 0 8px rgba(167,139,250,.5)", pulse: false };
    return { color: "#4d9dff", glow: "0 0 6px rgba(77,157,255,.45)", pulse: false };
  }

  // 按档位数构建分段：段数 = segCount(n)，每段 = 弱化底(.zslider-seg) + 填充层(.zslider-fill)
  function buildSegs(n) {
    const S = segCount(n);
    const out = [];
    for (let i = 0; i < S; i++) {
      const box = document.createElement("div");
      box.className = "zslider-seg";
      box.dataset.on = "0";
      box.style.animation = `zsSegIn .34s cubic-bezier(.34,1.56,.64,1) ${40 + i * 35}ms backwards`;
      const f = document.createElement("div");
      f.className = "zslider-fill";
      Object.assign(f.style, {
        position: "absolute", left: "0", top: "0", height: "100%",
        borderRadius: "var(--zs-r-pill)", background: FILL, width: "0",
      });
      box.appendChild(f);
      // 插在扫光/光斑之前：重建段（切模型 / setSegments）时覆盖层必须仍在最上层。
      // 首次构建时 rail 里还没有覆盖层，insertBefore(node, null) 等价于 append。
      rail.insertBefore(box, rail.querySelector(".zslider-shimmer, .zslider-flare"));
      out.push({ box, fill: f });
    }
    return out;
  }

  // 分段填充：档位 idx(共 n 档) → 点亮前 lit+1 段(共 S 段)
  function setFill(idx, n) {
    const S = segCount(n);
    const lit = (n > 1 && S > 1) ? Math.round((idx / (n - 1)) * (S - 1)) : 0;
    state.lit = idx >= 0 ? lit : -1;
    const onCount = idx >= 0 ? lit + 1 : 0;
    const ec = effortColor(idx, n);
    state.effortColor = ec.color;
    segs.forEach((s, i) => {
      const on = i < onCount;
      s.box.dataset.on = on ? "1" : "0";
      // 0 → 100% 的 width 过渡即"分段填充平滑过渡"；--zs-stagger 让它逐段点亮
      s.fill.style.width = on ? "100%" : "0%";
      s.fill.style.background = ec.color;
      s.fill.style.setProperty("--zs-stagger", (i * 45) + "ms");
    });
    // --zp 旧语义保留：光斑锚点，停在已点亮段的右端
    if (track) track.style.setProperty("--zp", (S > 0 ? ((lit + 1) / S) * 100 : 100) + "%");
    if (track) {
      if (idx >= 0 && n > 1 && idx === n - 1) track.setAttribute("data-top", "1");
      else track.removeAttribute("data-top");
    }
    // max 档泛光：由 CSS 落在最后一段填充层(zsBreathe)
  }

  function closePanel(animate) {
    if (!panel) return;
    const p = panel;
    panel = null;
    clearTimeout(phaseTimer);
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
    p.className = "zslider-panel";
    p.tabIndex = -1;
    Object.assign(p.style, {
      position: "fixed", zIndex: "2147483000",
      // 响应式:窄窗口收缩到视口内(原来 236px 硬编码,窗口 <252px 必然溢出)
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

    // 滑条行 = 分段轨道（无前缀图标，轨道占满整行）
    track = document.createElement("div");
    track.className = "zslider-track";
    Object.assign(track.style, {
      position: "relative", height: "38px",
      cursor: "pointer", touchAction: "none",
    });

    // 分段轨道:rail 是段容器(段间隙透出轨道渐变),段数由 segCount() 决定
    rail = document.createElement("div");
    rail.className = "zslider-rail";
    Object.assign(rail.style, {
      position: "relative", flex: "1 1 auto", minWidth: "0",
      height: "var(--zs-h)", borderRadius: "var(--zs-r-pill)",
    });
    segs = buildSegs(state.levels.length);
    // 扫光(加载中)与拖尾光斑(--zp 定位到已点亮段右端):都在 rail 内,被圆角裁住
    const shimEl = document.createElement("div");
    shimEl.className = "zslider-shimmer";
    rail.appendChild(shimEl);
    flareEl = document.createElement("div");
    flareEl.className = "zslider-flare";
    rail.appendChild(flareEl);
    track.appendChild(rail);
    p.appendChild(track);
    const tr = track;   // 本面板的轨道快照(事件回调里不依赖会被重写的 track)

    // 先入 DOM 量尺寸;定位在 panel 赋值之后做(见函数末尾 placePanel)
    document.body.appendChild(p);

    // 拖拽/点击:指针横向位置 → 最近档位,松手提交
    const idxFromEvent = (e) => {
      // 用 rail 的矩形:rail 是轨道本体,横向坐标换算最直接
      const r = (rail || track).getBoundingClientRect();
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
      track.classList.add("is-dragging");   // 拖拽增辉:光斑/填充提亮
      // 拖拽跟手:时长归零(填充与光斑读同一个变量,不会再各跑各的)
      tr.style.setProperty("--zs-dur", "0ms");
      setPhase("dragging");
      clearTimeout(phaseTimer);
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
      tr.style.removeProperty("--zs-dur");   // 松手恢复弹性:逐段点亮重新带上 stagger
      setPhase("settling");                  // 完成:涟漪(由 sync 触发)
      clearTimeout(phaseTimer);
      phaseTimer = setTimeout(() => setPhase("idle"), 520);
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
      tr.style.removeProperty("--zs-dur");
      setPhase("settling");
      clearTimeout(phaseTimer);
      phaseTimer = setTimeout(() => setPhase("idle"), 520);
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
      setPhase("settling");
      clearTimeout(phaseTimer);
      phaseTimer = setTimeout(() => setPhase("idle"), 520);
      if (value && value !== state.cur) commitLevel(value, idx);
    });

    panel = p;
    placePanel();
    window.addEventListener("resize", onViewportChange);
    window.addEventListener("scroll", onViewportChange, true);
    refreshPanel();
    setThinking(detectThinking());   // 思考中 → 激活段流动;空闲 → 定格
    // 定位就绪后启动弹出动效;填充条同时从 0 扫到当前档位(扫光标出"加载中")
    p.style.animation = "zsliderIn 260ms cubic-bezier(0.2,0.9,0.25,1.15)";
    setPhase("loading");
    clearTimeout(phaseTimer);
    phaseTimer = setTimeout(() => setPhase("idle"), 420);
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
    // 档位数变了(切模型)→ 段数跟着重建
    if (segs.length !== segCount(n)) {
      segs.forEach((s) => s.box.remove());
      segs = buildSegs(n);
    }
    if (track) track.style.setProperty("--zs-flow", CONFIG.flowMs + "ms");
    if (!state.drag) {
      setFill(idx, n);   // idx < 0(未知档位)时 onCount = 0，全部段回到弱化
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
        // 换档确认:当前点亮段处发射涟漪 + 档名脉冲
        if (panel) rippleAt();
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
  //                                              （segments / thinking / autoDetectThinking / flowMs）
  //   __zsliderCtl.setThinking(true|false|null)  手动锁定思考态；传 null 恢复自动检测
  //   __zsliderCtl.setSegments(n)                强制段数（0 = 自动等于档位数）
  //   __zsliderCtl.refresh()                     立即重跑一轮同步
  //   __zsliderCtl.diag() / __zsliderCtl.state() 诊断字符串 / 状态快照
  window.__zsliderCtl = {
    version: "segmented-1",
    config: CONFIG,
    setThinking: (v) => {
      CONFIG.thinking = (v === null || v === undefined) ? null : !!v;
      setThinking(detectThinking());
      return CONFIG.thinking;
    },
    setSegments: (n) => {
      CONFIG.segments = Math.max(0, Number(n) | 0);
      if (panel && rail) {                 // 面板开着就立刻重建
        segs.forEach((s) => s.box.remove());
        segs = buildSegs(state.levels.length);
        refreshPanel();
      }
      return CONFIG.segments;
    },
    refresh: () => { sync(); return true; },
    diag: () => collectDiag(),
    state: () => ({
      levels: state.levels.slice(), cur: state.cur, thinking: state.thinking,
      drag: state.drag, lit: state.lit, segments: segs.length, panel: !!panel,
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
