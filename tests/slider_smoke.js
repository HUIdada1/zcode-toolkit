// 滑条注入脚本冒烟测试：用最小 DOM 桩把 zcode-thought-slider.js 真跑一遍。
//
// 为什么需要它：脚本的对外接口（window.__zsliderCtl）与内部状态一度只存在于注释里——
// 肉眼 review 看不出来，`node --check` 也照样通过（语法合法）。这里验证的是「能加载」+「接口真实存在」。
//
// 用法：node tests/slider_smoke.js skills/zcode-tokenspeed/scripts/zcode-thought-slider.js
// 退出码 0 = 通过；非 0 时逐条打印失败原因。
const fs = require("fs");
const vm = require("vm");

const src = fs.readFileSync(process.argv[2], "utf8");

function makeStyle() {
  return { cssText: "", setProperty() {}, removeProperty() {} };
}

function makeEl(tag) {
  return {
    tagName: String(tag || "div").toUpperCase(),
    style: makeStyle(),
    dataset: {},
    children: [],
    attrs: {},
    classList: { add() {}, remove() {}, contains() { return false; } },
    isConnected: false,
    offsetWidth: 100,
    offsetHeight: 30,
    offsetLeft: 0,
    parentElement: null,
    previousElementSibling: null,
    firstChild: null,
    textContent: "",
    title: "",
    tabIndex: -1,
    setAttribute(k, v) { this.attrs[k] = v; },
    getAttribute(k) { return Object.prototype.hasOwnProperty.call(this.attrs, k) ? this.attrs[k] : null; },
    removeAttribute(k) { delete this.attrs[k]; },
    appendChild(c) { this.children.push(c); c.parentElement = this; return c; },
    insertBefore(c) { this.children.push(c); c.parentElement = this; return c; },
    insertAdjacentElement() { return null; },
    remove() { this.isConnected = false; },
    addEventListener() {},
    removeEventListener() {},
    getBoundingClientRect() { return { left: 0, top: 0, right: 100, bottom: 30, width: 100, height: 30 }; },
    getClientRects() { return []; },
    querySelector() { return null; },
    querySelectorAll() { return []; },
    contains() { return false; },
    focus() {},
    setPointerCapture() {},
    releasePointerCapture() {},
    dispatchEvent() { return true; },
  };
}

const document = {
  head: makeEl("head"),
  body: makeEl("body"),
  getElementById() { return null; },
  createElement: (t) => makeEl(t),
  createElementNS: (ns, t) => makeEl(t),
  querySelector() { return null; },
  querySelectorAll() { return []; },
  addEventListener() {},
  removeEventListener() {},
};

const sandbox = {
  document,
  console,
  performance,
  matchMedia: () => ({ matches: false }),
  requestAnimationFrame: () => 0,
  cancelAnimationFrame: () => {},
  setTimeout: () => 0,
  clearTimeout: () => {},
  setInterval: () => 0,
  clearInterval: () => {},
  MutationObserver: class { observe() {} disconnect() {} },
  PointerEvent: class {},
  MouseEvent: class {},
  Event: class {},
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;

const bad = [];
try {
  vm.runInNewContext(src, sandbox, { filename: "zcode-thought-slider.js" });
} catch (err) {
  bad.push("script threw on load: " + err.message);
}

const ctl = sandbox.__zsliderCtl;
if (!ctl) {
  bad.push("window.__zsliderCtl is undefined (documented in header but never defined?)");
} else {
  for (const m of ["config", "setThinking", "setSegments", "refresh", "diag", "state"]) {
    if (!(m in ctl)) bad.push("__zsliderCtl missing member: " + m);
  }
  if (typeof ctl.state === "function") {
    const st = ctl.state();
    for (const k of ["levels", "cur", "thinking", "segments", "config"]) {
      if (!(k in st)) bad.push("state() missing field: " + k);
    }
  }
}
if (typeof sandbox.__zsliderDiag !== "function") {
  bad.push("window.__zsliderDiag is not a function");
}
if (sandbox.__zslider !== true) {
  bad.push("window.__zslider idempotency guard not set");
}

if (bad.length) {
  console.error("smoke FAIL");
  for (const b of bad) console.error("  - " + b);
  process.exit(1);
}
console.log("smoke OK members=" + Object.keys(ctl).join(","));
