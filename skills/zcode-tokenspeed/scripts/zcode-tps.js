/**
 * ZCode 状态栏：TPS 统计胶囊 —— 事件流实现（唯一方案）
 * ==================================================
 * 输入框工具栏常驻胶囊：● 时间 · 首 token · tok/s · out（当前会话最近一轮）
 *
 * 数据源（ZCode 主进程经 window.postMessage 转交的 ServicePort 事件流）：
 *   - version:1 事件流：usage.delta（真实 outputTokens/inputTokens）、stream.chunk
 *   - conversation 行事件：turnHeader / userInput / reasoning / assistantText / row.delta
 * 由此得到精确的 tok/s（4s 滑动窗口）、首 token 延迟、out（本轮累计输出）。
 *
 * ⚠️ 关键约束：**绝不调用 port.start()**
 *   MessagePort 队列一旦启用，消息只派发给"启用瞬间已注册的监听器"。本脚本是普通
 *   script（早于 type=module 的应用主包执行），若先 start() 就会消费掉服务端的
 *   Initialize 启动握手 → 应用的协议客户端永远收不到握手 → ZCode 3.12.2 卡在启动界面。
 *   因此只 addEventListener，把 start() 留给应用；届时双方都能收到消息。
 *
 *   真实 Chrome 实测（MessageChannel，双方持有同一 port）：
 *     我们先 start()          → 我们 ["Initialize"]，应用 []            ✗ 卡启动
 *     我们不 start、应用 start → 我们 ["Initialize"]，应用 ["Initialize"] ✓ 正常
 *   注意：不能用 Node 验证此语义——Node 的 MessagePort 在 addEventListener 时隐式
 *   start，会让"不 start"也失败，从而得出错误结论。
 *
 * 已知特性：tok/s 在开始生成后约 1~4 秒才出现（需要滑动窗口内 ≥2 个采样点，且首块到达
 *   前的思考阶段无文本增量可算），期间只显示 ● 与时间，属预期。
 *
 * 原始版本来自社区分享（原作者授权可直接借鉴定制），
 * 本实现仅删除 port.start() 一行并补充诊断，计算逻辑与原版一致。
 *
 * 安全：只读事件流、只写自己的 host 元素；异常静默；找不到工具栏行时自清理。
 * 回滚：脚本/补丁异常时用 scripts/restore_clean.py --latest 秒级还原，无需重装。
 */
(() => {
  if (window.__ztps) return;
  window.__ztps = true;
  const dec = new TextDecoder();
  const MARK = "data-ztps";

  const turns = new Map();          // turnId(msg_xxx) -> 轮统计
  const firstChunkByScid = {};
  const rowTurn = new Map();        // rowId -> turnId（row.delta 增量归属）
  const respTurn = new Map();       // assistantResponseId -> turnId（stream.chunk 的 assistantMessageId 归属）
  const scidTurn = new Map();       // sourceCommandId -> turnId（usage.delta 关联兜底）

  const get = (id) => {
    if (!turns.has(id)) turns.set(id, {
      msgId: id, sourceCommandId: null, sessionId: null,
      startedAt: null, endedAt: null, activeMs: null,
      firstChunkAt: null, rowFirstAt: null, lastUsageAt: null,
      outputTokens: 0, inputTokens: 0, cacheReadTokens: 0, totalTokens: 0,
      textTok: 0, win: [], modelId: null, streaming: false,
    });
    return turns.get(id);
  };

  // 轮复用（编辑重发/重试复用同一 turnId）时清空上一轮统计，避免旧值残留到新轮
  function resetTurnStats(t) {
    t.endedAt = null; t.activeMs = null;
    t.firstChunkAt = null; t.rowFirstAt = null; t.lastUsageAt = null;
    t.outputTokens = 0; t.inputTokens = 0; t.cacheReadTokens = 0; t.totalTokens = 0;
    t.textTok = 0; t.win = []; t.lastTps = null; t.streaming = true;
  }

  // token 粗估：CJK 字符 1 字 ≈ 1 token，其余 4 字符 ≈ 1 token
  const estTok = (s) => {
    let n = 0;
    for (let i = 0; i < s.length; i++) n += s.charCodeAt(i) > 0x2e7f ? 1 : 0.25;
    return Math.round(n);
  };

  const pushWin = (t, ts, tok) => {
    t.win.push([ts, tok]);
    const floor = ts - 6000;
    while (t.win.length > 2 && t.win[0][0] < floor) t.win.shift();
  };

  function onRow(row) {
    if (!row) return;
    if (row.op === "row.delta") { onRowDelta(row); return; }
    if (!row.turnId) return;
    const t = get(row.turnId);
    if (row.rowId != null) rowTurn.set(row.rowId, row.turnId);
    if (row.kind === "turnHeader") {
      if ((t.endedAt != null || t.lastUsageAt != null) && row.startedAt && row.startedAt !== t.startedAt) resetTurnStats(t);
      t.startedAt = row.startedAt ?? t.startedAt;
      t.endedAt = row.endedAt ?? t.endedAt;
      t.activeMs = row.activeMs ?? t.activeMs;
      t.streaming = row.state != null && !/^(completedSuccess|completed|failed|stopped|cancelled)/.test(row.state);
      if (row.sourceCommandId) t.sourceCommandId = row.sourceCommandId;
      scan();
    } else if (row.kind === "userInput") {
      if ((t.endedAt != null || t.lastUsageAt != null) && row.createdAt && row.createdAt !== t.startedAt) resetTurnStats(t);
      t.startedAt = row.createdAt ?? t.startedAt;
      t.streaming = true;
      if (row.sourceCommandId) t.sourceCommandId = row.sourceCommandId;
      scan();
    } else if (row.kind === "reasoning" || row.kind === "assistantText") {
      if (row.assistantResponseId) respTurn.set(row.assistantResponseId, row.turnId);
      if (row.text && row.text.length) t.textTok = estTok(row.text);   // 全量覆盖，防增量累计漂移
      if (t.firstChunkAt == null && t.rowFirstAt == null && row.createdAt) {
        t.rowFirstAt = row.createdAt;   // 兜底首块（优先 stream.chunk）
      }
    }
  }

  function onRowDelta(row) {
    if (row.path !== "text" || !row.append) return;
    const turnId = rowTurn.get(row.rowId);
    if (!turnId) return;
    const t = turns.get(turnId);
    if (!t) return;
    t.textTok += estTok(row.append);
    pushWin(t, Date.now(), t.textTok);
  }

  function onEvent(ev) {
    if (!ev || ev.version !== 1) return;
    const scid = ev.sourceCommandId;
    if (ev.kind === "usage.delta") {
      if (!scid) return;
      const t = findByScid(scid) || turns.get(scidTurn.get(scid));
      if (!t) return;
      t.outputTokens += ev.outputTokens || 0;
      t.inputTokens += ev.inputTokens || 0;
      t.cacheReadTokens += ev.cacheReadTokens || 0;
      t.totalTokens += ev.totalTokens || 0;
      t.modelId = ev.modelId || t.modelId;
      t.sessionId = ev.sessionId || t.sessionId;
      t.lastUsageAt = ev.occurredAt ?? t.lastUsageAt;
      if (t.firstChunkAt == null && firstChunkByScid[scid] != null) {
        t.firstChunkAt = firstChunkByScid[scid];
      }
      scan();
    } else if (ev.kind === "stream.chunk") {
      if (firstChunkByScid[scid] == null) firstChunkByScid[scid] = ev.occurredAt;
      let t = findByScid(scid);
      if (!t && ev.assistantMessageId) {
        const tid = respTurn.get(ev.assistantMessageId);
        if (tid) { t = turns.get(tid); if (t && scid) scidTurn.set(scid, tid); }
      }
      if (!t && scid && scidTurn.has(scid)) t = turns.get(scidTurn.get(scid));
      if (!t) return;
      if (t.firstChunkAt == null) t.firstChunkAt = firstChunkByScid[scid];
      t.sessionId = ev.sessionId || t.sessionId;
      t.streaming = true;   // turnHeader 未到时也标记生成中
      pushWin(t, ev.occurredAt || Date.now(), t.textTok);   // 心跳采样，保证窗口时间轴连续
    }
  }

  const findByScid = (scid) => {
    for (const t of turns.values()) if (t.sourceCommandId === scid) return t;
    return null;
  };

  function handleFrame(data) {
    try {
      let d = data;
      if (d == null) return;
      if (d instanceof ArrayBuffer) d = new Uint8Array(d);
      if (!ArrayBuffer.isView(d)) return;
      const txt = dec.decode(d);
      const i = txt.indexOf("{");
      if (i < 0) return;
      const j = JSON.parse(txt.slice(i));
      if (j.version === 1) { onEvent(j); return; }
      const payload = j.frame && j.frame.payload;
      if (!payload) return;
      const evs = payload.events || payload.deltas || [];
      for (const e of evs) {
        if (e.row) onRow(e.row);
        else if (e.op === "row.appended" || e.op === "row.upserted" || e.op === "row.delta") onRow(e.row || e);
      }
    } catch (err) { /* 静默 */ }
  }

  // ---------- 格式化 ----------
  const fmtLat = (ms) => {
    const s = Math.max(0, (ms || 0) / 1000);
    return (s < 10 ? String(+s.toFixed(1)) : String(Math.round(s))) + "s";
  };
  const fmtTps = (v) => (v >= 10 ? String(Math.round(v)) : String(+Number(v || 0).toFixed(1)));
  const fmtTok = (v) => {
    if (v < 1e3) return String(v);
    const trim = (n) => String(+n.toFixed(n < 10 ? 1 : 0));
    if (v < 1e6) return trim(v / 1e3) + "k";
    if (v < 1e9) return trim(v / 1e6) + "m";
    return trim(v / 1e9) + "b";
  };
  const fmtStamp = (ms) => {
    if (ms == null) return null;
    const d = new Date(ms), now = new Date();
    const hm = `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
    return d.toDateString() === now.toDateString() ? hm
      : d.getFullYear() === now.getFullYear() ? `${d.getMonth() + 1}月${d.getDate()}日 ${hm}`
      : `${d.getFullYear()}年${d.getMonth() + 1}月${d.getDate()}日 ${hm}`;
  };

  function statsOf(t) {
    const firstAt = t.firstChunkAt ?? t.rowFirstAt ?? null;
    const ttft = firstAt != null && t.startedAt != null ? firstAt - t.startedAt : null;
    let out = t.outputTokens;
    if (out === 0 && t.textTok > 0) out = t.textTok;   // 中止/失败未报 usage：以内容估算兜底
    let tps = null;
    if (t.streaming) {
      out = Math.max(out, t.textTok);   // 流式中 usage 未到，用内容估算补齐（精确值到达后自然切换）
      const now = Date.now();
      const win = t.win.filter((w) => now - w[0] <= 4000);
      if (win.length >= 2) {
        const dt = (win[win.length - 1][0] - win[0][0]) / 1000;
        const dtok = win[win.length - 1][1] - win[0][1];
        if (dt >= 1 && dtok > 0) tps = dtok / dt;
      }
      if (tps == null && t.lastTps != null) tps = t.lastTps;   // 工具执行等静默期：保持最近速度
    } else {
      const decodeFrom = firstAt ?? t.startedAt;
      const decodeMs = decodeFrom != null && t.lastUsageAt != null ? t.lastUsageAt - decodeFrom : null;
      tps = t.outputTokens > 0 && decodeMs > 500 ? t.outputTokens / (decodeMs / 1000) : null;
      if (tps == null && t.lastTps != null) tps = t.lastTps;
    }
    if (tps != null) t.lastTps = tps;
    return {
      stamp: fmtStamp(t.endedAt || t.startedAt), ttft, tps, out,
      streaming: t.streaming,
    };
  }

  const hasActivity = (t) => t.streaming || t.textTok > 0 || t.outputTokens > 0;

  // ---------- 渲染 ①: 输入框工具栏中央（当前会话最近一轮） ----------
  // 工具栏行的定位策略（按稳定性排序，逐级回退；不依赖任何随版本/语言变化的文案）：
  //   a) composer 容器内带 items-end 的 flex 行，且含交互控件（button/select/combobox）
  //   b) composer 容器内最后一个 items-end 的 flex 行
  // 历史：旧实现用 textContent.includes("完全访问") 判定，3.12.2 起该文案改为按 i18n key
  // 动态渲染，DOM 文本不再稳定命中 → 改为结构特征判定。
  // 诊断：window.__ztpsDiag 暴露每一环的探测结果（选择器命中数/候选行数/最终选择），
  // 版本升级后若工具栏消失，先在 console 看该对象定位失败环节。
  const COMPOSER_INPUT_SELECTORS = [
    "[data-testid='v4-composer-input']",
    "[data-testid*='composer-input']",
    "textarea[data-testid]",
    "form textarea",
    "textarea",
  ];

  function findComposerInput() {
    const tried = [];
    for (const sel of COMPOSER_INPUT_SELECTORS) {
      let hit = null;
      try {
        const els = Array.from(document.querySelectorAll(sel));
        if (els.length) {
          // 多个匹配时取可见且最靠近视口底部的（composer 在底部）
          const vis = els.filter((e) => e.offsetParent != null);
          hit = (vis.length ? vis : els).sort(
            (a, b) => b.getBoundingClientRect().top - a.getBoundingClientRect().top
          )[0];
        }
      } catch (err) { /* 选择器不合法，跳过 */ }
      tried.push({ selector: sel, count: hit ? 1 : 0 });
      if (hit) return { el: hit, tried };
    }
    return { el: null, tried };
  }

  function findToolbarRow() {
    const { el: ta, tried } = findComposerInput();
    if (diag) {
      diag.inputTried = tried;
      diag.inputFound = !!ta;
    }
    if (!ta) return null;

    // 从某元素向上找「工具栏行」：class 同时含 flex 与 items-end 的 div，且不得越过 card
    const rowFrom = (el, card) => {
      let cur = el;
      while (cur && cur !== card.parentElement) {
        if (cur.tagName === "DIV") {
          const c = typeof cur.className === "string" ? cur.className : "";
          if (/(^|\s)flex(\s|$)/.test(c) && /items-end/.test(c)) return cur;
        }
        cur = cur.parentElement;
      }
      return null;
    };

    // ① 首选：发送按钮向上找工具栏行。
    //    发送按钮（v4-composer-send）恒在工具栏内，且不会与输入框的 testid 混淆——
    //    注意不能用 ta.closest("[data-testid*='composer']")：输入框自身 testid 为
    //    v4-composer-input 也含 "composer"，closest 从自身起匹配会返回输入框本身，
    //    导致在输入框内部找工具栏行而落空（3.12.2 实测踩到）。
    let card = null;
    try { card = ta.closest("[data-testid='v4-composer']"); } catch (err) { /* ignore */ }
    if (!card) card = ta.closest("form") ? ta.closest("form").parentElement : null;
    if (!card) card = ta.parentElement;
    if (!card) return null;

    let picked = null;
    try {
      const sendBtn = document.querySelector("[data-testid='v4-composer-send']");
      if (sendBtn && card.contains(sendBtn)) picked = rowFrom(sendBtn, card);
    } catch (err) { /* ignore */ }

    // ② 回退：card 内所有 items-end 行，取含交互控件的那一行（或最后一个）
    if (!picked) {
      const rows = Array.from(card.querySelectorAll("div")).filter((el) => {
        const c = typeof el.className === "string" ? el.className : "";
        return /(^|\s)flex(\s|$)/.test(c) && /items-end/.test(c);
      });
      if (diag) diag.rowCandidates = rows.length;
      if (!rows.length) return null;
      picked = rows.find((el) => el.querySelector("button,select,[role='combobox'],input")) ||
               rows[rows.length - 1];
    } else if (diag) {
      diag.rowCandidates = 1;
      diag.rowVia = "send-button";
    }

    if (diag) {
      diag.pickedRowText = (picked.textContent || "").slice(0, 60);
      diag.pickedRowClass = String(picked.className || "").slice(0, 120);
      diag.cardTag = card.tagName;
    }
    return picked;
  }

  // 诊断对象（console 里直接看 window.__ztpsDiag）
  let diag = null;
  try {
    diag = window.__ztpsDiag = window.__ztpsDiag || {};
    diag.scriptVersion = "3.12-fix";
    diag.loadedAt = new Date().toISOString();
  } catch (err) { /* ignore */ }

  function renderBar() {
    try {
      const row = findToolbarRow();
      if (!row) {
        // 找不到工具栏行（新建任务空态/设置页等）时清掉已注入的 host，杜绝旧内容残留
        document.querySelectorAll("[data-ztps-bar]").forEach((el) => el.remove());
        return;
      }
      // 只认当前会话 DOM 里可见的轮次——切走后旧统计不再展示。
      // 当前会话 id 从 DOM 的 data-session-id 读取（激活 tab 即变，多会话并行互不干扰，reload 后立即可用）
      let domSess = null;
      document.querySelectorAll("[data-session-id]").forEach((el) => {
        if (!domSess && el.offsetParent != null) domSess = el.getAttribute("data-session-id");
      });
      if (!domSess) {
        const el = document.querySelector("[data-session-id]");
        if (el) domSess = el.getAttribute("data-session-id");
      }
      const visible = new Set();
      document.querySelectorAll("section[data-turn-id]").forEach((el) => visible.add(el.getAttribute("data-turn-id")));
      let latest = null;
      for (const t of turns.values()) {
        if (!visible.has(t.msgId)) continue;
        // 轮次归属会话与当前显示会话不符时排除（会话切换的 DOM 中间态残留兜底）
        if (t.sessionId && domSess && t.sessionId !== domSess) continue;
        if (!latest || (t.startedAt ?? 0) > (latest.startedAt ?? 0)) latest = t;
      }
      let host = row.querySelector(":scope > [data-ztps-bar]");
      if (diag) {
        diag.domSessionId = domSess;
        diag.visibleTurns = visible.size;
        diag.turnStats = turns.size;
        diag.latestFound = !!latest;
        if (latest) {
          diag.latest = {
            msgId: latest.msgId, sessionId: latest.sessionId,
            startedAt: latest.startedAt, endedAt: latest.endedAt,
            streaming: latest.streaming, out: latest.outputTokens, textTok: latest.textTok,
          };
        }
        diag.hostAttached = !!host;
      }
      // 无可见轮次 → 不展示；轮次无时间戳但仍在生成/有内容 → 显示（省略时间段）；完全无数据防假时钟。
      // 仅在 host 已存在时移除，绝不走「先创建再删除」，否则 MutationObserver 会自激
      if (!latest || (!hasActivity(latest) && latest.endedAt == null && latest.startedAt == null)) {
        if (diag) diag.hiddenReason = !latest ? "无可见轮次" : "轮次无时间戳且无活动";
        if (host) host.remove();
        return;
      }
      if (diag) diag.hiddenReason = null;
      if (!host) {
        host = document.createElement("div");
        host.setAttribute("data-ztps-bar", "1");
        Object.assign(host.style, {
          display: "inline-flex", alignItems: "center", gap: "6px",
          flex: "0 1 auto",
          minWidth: "0",
          maxWidth: "50%",
          marginLeft: "auto", marginRight: "auto",   // 两侧 auto = 工具栏行内水平居中
          alignSelf: "center",
          fontSize: "11px", height: "22px", userSelect: "none",
          fontVariantNumeric: "tabular-nums",
          whiteSpace: "nowrap", overflow: "hidden",
          borderRadius: "999px",
          background: "rgba(127,127,127,0.12)",
          padding: "0 10px",
        });
        row.insertBefore(host, row.children[1] || null);
      }
      host.style.alignSelf = "center";
      row.style.alignItems = "center";
      const s = statsOf(latest);
      // 内容签名：未变化时只做溢出复查（零 DOM 写），避免 MutationObserver 自激
      const key = [s.stamp, s.ttft, s.tps, s.out, s.streaming].join("|");
      let segs = host._zsegs;
      if (host._zkey !== key) {
        host._zkey = key;
        host.style.background = "rgba(127,127,127,0.12)";
        host.style.padding = "0 10px";
        host.style.color = "var(--color-foreground-subtle, #7a7a7a)";
        host.innerHTML = "";
        const ACCENT = "var(--color-warning, #e0983a)";
        const VALUE = "var(--color-foreground, #e8e8e8)";
        const span = (txt, cls) => {
          const sp = document.createElement("span");
          sp.textContent = txt;
          if (cls === "ACCENT") sp.style.color = ACCENT;
          else if (cls === "VALUE") sp.style.color = VALUE;
          return sp;
        };
        // 绿点常驻：生成中发亮，空闲静态
        const dot = span("●");
        dot.style.color = "#4ade80";
        if (s.streaming) dot.style.textShadow = "0 0 6px rgba(74,222,128,.8)";
        host.appendChild(dot);
        // 定位：原生右下角已展示上下文水位，此处只放本轮性能指标
        segs = [];
        if (s.stamp != null) segs.push({ p: 0, nodes: [span(s.stamp)] });
        if (s.ttft != null && s.ttft >= 0) segs.push({ p: 1, nodes: [span("首 token "), span(fmtLat(s.ttft), "VALUE")] });
        if (s.tps != null) segs.push({ p: 2, nodes: [span(fmtTps(s.tps) + " tok/s", "ACCENT")] });
        if (s.out > 0) segs.push({ p: 3, nodes: [span("out "), span(fmtTok(s.out), "VALUE")] });
        segs.forEach((g, i) => {
          if (i > 0) {
            const sep = span("·");
            sep.style.opacity = "0.55";
            g.nodes.unshift(sep);   // 分隔符跟段一起，降级时同生共死
          }
        });
        segs.forEach((g) => g.nodes.forEach((n) => host.appendChild(n)));
        host._zsegs = segs;
      }
      // 渐进降级：溢出时按优先级丢段（out → tok/s）；窗口尺寸变化时也复查
      try {
        for (const drop of [3, 2]) {
          if (host.scrollWidth <= host.clientWidth + 1) break;
          const g = segs.find((x) => x.p === drop);
          if (!g) continue;
          g.nodes.forEach((n) => n.remove());
        }
      } catch (err) { /* 静默 */ }
    } catch (err) { /* 静默 */ }
  }

  // ---------- 渲染 ②: 清理历史遗留的逐轮统计行（统计只在工具栏展示） ----------
  function removeLegacyFooters() {
    try {
      document.querySelectorAll(`[${MARK}]:not([data-ztps-bar])`).forEach((el) => el.remove());
    } catch (err) { /* 静默 */ }
  }

  function scan() {
    renderBar();
    removeLegacyFooters();
  }

  function start() {
    setInterval(scan, 1000);   // 流式估算 1s 刷新节奏
    try {
      let lastSync = 0;
      const mo = new MutationObserver(() => {
        // DOM 一变立即同步刷新，切换会话零残留；16ms 节流防回放风暴
        const now = performance.now();
        if (now - lastSync > 16) { lastSync = now; renderBar(); }
        clearTimeout(mo._t);
        mo._t = setTimeout(scan, 60);
      });
      mo.observe(document.body, { childList: true, subtree: true });
    } catch (err) { /* 静默 */ }
    scan();
  }
  if (document.body) start();
  else document.addEventListener("DOMContentLoaded", start);

  // ---------- 端口获取 ----------
  function hookPort(port) {
    if (!port || port.__ztps) return;
    port.__ztps = true;
    // 关键：只注册监听，**绝不调用 port.start()**
    // start() 会让消息只派发给"启用瞬间"的监听器，从而吃掉应用的 Initialize 握手。
    // 交给应用 start；届时所有已注册监听器（含我们）同时收到消息。
    port.addEventListener("message", (ev) => handleFrame(ev.data));
    window.__ztpsPort = port;   // 调试用
    if (diag) {
      diag.portHooked = true;
      diag.portStarted = false;   // 恒为 false —— 本版本刻意不 start
      diag.portHookedAt = new Date().toISOString();
    }
  }
  // 端口转交的消息格式跨版本不同，两种都要认：
  //   旧版（≤3.11.x）：window.postMessage("zcode:service-port", "*", [port])   → data 为字符串
  //   新版（3.12.2+）：window.postMessage({type:"zcode:service-port",...}, "*", [port]) → data 为对象
  // 只比较字符串会让新版端口永远不被接管（表现为 turnStats 恒为 0、胶囊不显示）。
  function takePortIfHandoff(d) {
    if (!d) return false;
    if (d === "zcode:service-port") return true;
    if (typeof d === "object" && d.type) {
      return d.type === "zcode:service-port" || d.type === "zcode:scoped-service-port";
    }
    return false;
  }
  window.addEventListener("message", (e) => {
    if (e.source !== window) return;
    if (diag) {
      diag.lastMessage = typeof e.data === "string" ? e.data
        : (e.data && e.data.type) || Object.prototype.toString.call(e.data);
      diag.lastMessagePorts = (e.ports || []).length;
    }
    if (takePortIfHandoff(e.data)) {
      if (e.ports && e.ports[0]) hookPort(e.ports[0]);
    }
  }, true);

  window.__ztpsTurns = turns;
  window.__ztpsHook = hookPort;   // 调试用：热注入时可对存量端口手动补挂
  // 自检：加载 5s 后若仍未接管端口，把诊断信息打到 console（便于版本升级后排障）
  setTimeout(() => {
    if (diag) {
      diag.portInjectedAt = diag.portInjectedAt || null;
      diag.diagReady = true;
      if (!diag.portHooked) {
        console.warn("[zcode-tps] 未接管到服务端口，统计将不可用。window.__ztpsDiag =", diag);
      } else {
        console.info("[zcode-tps] 已接管端口，window.__ztpsDiag =", diag);
      }
    }
  }, 5000);
})();
