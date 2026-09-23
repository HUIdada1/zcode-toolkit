#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""增强提示词（润色按钮）链路诊断 —— 只读，不发任何真实补全请求。

定位「本机能润色、别人报 HTTP 400 Model is unavailable」这类跨机差异。

    python enhance_doctor.py                 # 完整体检
    python enhance_doctor.py --json          # 机器可读
    python enhance_doctor.py --probe         # 额外做真实连通性探测（会消耗极少量额度）
    python enhance_doctor.py --probe --only <providerId>

它逐段复刻 app.asar 里 `zcode:enhance-prompt` handler 的解析逻辑，因此
**本脚本判定用哪个供应商/模型，就等于润色按钮实际会用哪个**。

检查顺序（与 handler 一一对应）：
  1. 数据根定位（setting.json 的 dataBaseDir 优先，退回 ~/.zcode/v2）
  2. config.json 能否解析、provider 段结构
  3. 模型解析三档（① 界面 ref ② 显示名反查 ③ 兜底首个自定义供应商）
     ③ 兜底是「模型不可用」的高发区 —— 它会把请求打到不相干的供应商上
  4. 命中供应商的关键字段：baseURL / apiKey / kind / enabled / systemDisabledReason
  5. 请求构造复核：URL 拼接、鉴权头、max_tokens（会被供应商上限卡 400）
  6. --probe 时的真实 HTTP 探测 + 错误码归因

退出码：0 = 没发现阻断项；1 = 发现会导致润色失败的问题。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
try:
    from _console import safe_stdio
except ImportError:
    sys.path.insert(0, str(HERE))
    from _console import safe_stdio

# —— 只能用在 cp936 下编得出来的符号（见 _console.py 说明）——
OK = "[OK]"
BAD = "[!!]"
WARN = "[!]"
INFO = "[i]"

MAX_TOKENS = 2048          # 与 handler 一致
TIMEOUT = 60000


# ------------------------------------------------------------------ 步骤 1

def resolve_root() -> tuple[Path, str]:
    """复刻 handler：先读 <home>/.zcode/v2/setting.json 的 dataBaseDir，否则用默认。"""
    home = Path(os.path.expanduser("~"))
    base = home
    how = "默认 (~/.zcode/v2)"
    sfile = home / ".zcode" / "v2" / "setting.json"
    try:
        s = json.loads(sfile.read_text(encoding="utf-8"))
        if isinstance(s, dict):
            dbd = s.get("dataBaseDir")
            if isinstance(dbd, str) and dbd.strip():
                base = Path(dbd.strip())
                how = f"setting.json dataBaseDir = {base}"
    except FileNotFoundError:
        how += "（没有 setting.json，正常）"
    except Exception as e:
        how += f"（setting.json 读取失败 {e!r}，已退回默认）"
    return base / ".zcode" / "v2", how


# ------------------------------------------------------------------ 步骤 3

def resolve_model(cfg: dict, mv: str, ml: str) -> tuple[dict | None, str]:
    """逐字复刻 handler 的 ①②③ 三档解析。返回 (pick, how)。"""
    prov = cfg.get("provider") or {}
    ml = str(ml or "").strip().lower()

    if mv:
        k = mv.find("/")
        if k > 0:
            pid, mid = mv[:k], mv[k + 1:]
            pp = prov.get(pid)
            if isinstance(pp, dict) and (pp.get("models") or {}).get(mid):
                return {"pid": pid, "mid": mid, "p": pp}, "ref"

    if ml:
        for pid, pp in prov.items():
            if not isinstance(pp, dict) or str(pid).startswith("builtin:"):
                continue
            for mid in (pp.get("models") or {}):
                mm = (pp.get("models") or {}).get(mid) or {}
                for cand in (mid, mm.get("name"), pid + "/" + mid):
                    if cand and str(cand).strip().lower() == ml:
                        return {"pid": pid, "mid": mid, "p": pp}, "label"

    for pid, pp in prov.items():
        if not isinstance(pp, dict) or str(pid).startswith("builtin:"):
            continue
        models = pp.get("models") or {}
        mid = next(iter(models), None)
        if mid and (pp.get("options") or {}).get("baseURL"):
            return {"pid": pid, "mid": mid, "p": pp}, "fallback"
    return None, "none"


def build_request(pick: dict) -> tuple[list[str], dict, str]:
    """复刻 handler 的 URL / 请求体构造。返回 (候选URL列表, headers, 错误说明)。"""
    p = pick["p"] or {}
    opts = p.get("options") or {}
    u = str(opts.get("baseURL") or "").rstrip("/")
    k = str(opts.get("apiKey") or "")
    kind = str(p.get("kind") or "openai-compatible")
    if not u:
        return [], {}, f'供应商「{pick["pid"]}」没有填 Base URL'
    if not k:
        return [], {}, f'供应商「{pick["pid"]}」没有填 API Key'

    headers = {"Content-Type": "application/json", "Accept": "application/json",
               "Authorization": "Bearer " + k, "x-api-key": k}
    if kind == "anthropic":
        urls = [(u if u.endswith("/v1") else u + "/v1") + "/messages"]
    else:
        urls = [(u if u.endswith("/v1") else u + "/v1") + "/chat/completions",
                u + "/chat/completions"]
    return urls, headers, ""


def probe(pick: dict, urls: list[str], headers: dict) -> str:
    """真实打一发最小请求。返回人类可读结论。"""
    p = pick["p"] or {}
    kind = str(p.get("kind") or "openai-compatible")
    mid = pick["mid"]
    if kind == "anthropic":
        body = {"model": mid, "max_tokens": 32, "system": "ping",
                "messages": [{"role": "user", "content": "ping"}]}
    else:
        body = {"model": mid, "stream": False, "max_tokens": 32,
                "messages": [{"role": "user", "content": "ping"}]}
    payload = json.dumps(body).encode()
    last = "未能连通"
    for url in urls:
        hdrs = dict(headers)
        hdrs["Content-Length"] = str(len(payload))
        req = urllib.request.Request(url, data=payload, method="POST", headers=hdrs)
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT // 1000) as r:
                return f"{OK} {url} -> HTTP {r.status}，端到端可用"
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", "replace")[:300]
            msg = raw
            try:
                j = json.loads(raw)
                msg = (j.get("error") or {}).get("message") or j.get("message") or raw
            except Exception:
                pass
            verdict = diagnose_http(e.code, str(msg))
            line = f"{BAD} {url} -> HTTP {e.code}：{msg}\n       归因：{verdict}"
            if e.code == 400:
                return line                     # 400 换地址也没用，直接给结论
            last = line
            continue
        except Exception as e:
            last = f"{BAD} {url} -> {type(e).__name__}: {e}"
            continue
    return last


def diagnose_http(code: int, msg: str) -> str:
    m = msg.lower()
    if code == 400:
        if "model is unavailable" in m or "model_not_found" in m or "not available" in m:
            return ("★ 模型不被该供应商接受：模型名写了，但这个 key/套餐里没有它。"
                    "检查 config.json 里模型 id 的拼写与大小写（glm-5.2 != GLM-5.2）")
        if "invalid_request" in m or "invalid" in m:
            return "请求体字段不被接受（max_tokens 超上限 / 参数名不符）——多为 non-anthropic 端点套用了 anthropic 路径"
        return "上游判定请求非法：核对 model id、max_tokens、endpoint 路径前缀"
    if code == 401:
        return "★ 鉴权失败：API Key 过期/错填/不属于该 endpoint"
    if code == 402:
        return "★ 余额或套餐不足：需要充值或换供应商"
    if code == 403:
        return "★ 权限/地区被拒：key 无权访问该模型，或出口 IP 被风控"
    if code == 404:
        return "★ 路径不存在：baseURL 少了或多写了 /v1"
    if code == 429:
        return "限流或额度耗尽（可重试；若持续则升级套餐）"
    if code >= 500:
        return "供应商侧故障（可重试）"
    return "未知错误码"


# ------------------------------------------------------------------ main

def main() -> int:
    safe_stdio()
    ap = argparse.ArgumentParser(description="增强提示词（润色）链路诊断（只读）")
    ap.add_argument("--json", action="store_true", help="以 JSON 输出")
    ap.add_argument("--probe", action="store_true",
                    help="额外做真实连通性探测（会消耗极少量额度）")
    ap.add_argument("--only", default="", help="配合 --probe：只探测指定 providerId")
    ap.add_argument("--model-value", default="",
                    help="模拟界面给的 data-model-current-value（providerId/modelId）")
    ap.add_argument("--model-label", default="",
                    help="模拟界面显示的模型名（ref 解析失败时的第二档）")
    args = ap.parse_args()

    report: dict = {}
    problems: list[str] = []

    root, how = resolve_root()
    report["config_root"] = str(root)
    report["config_root_how"] = how
    if not args.json:
        print("增强提示词（润色按钮）链路诊断 —— 只读")
        print("=" * 68)
        print(f"1. 数据根  {root}")
        print(f"   {how}")

    cfg_path = root / "config.json"
    if not cfg_path.is_file():
        problems.append(f"找不到 {cfg_path}")
        if not args.json:
            print(f"{BAD} 2. 找不到 {cfg_path} —— 客户端从未配置过供应商")
        if args.json:
            print(json.dumps({"report": report, "problems": problems},
                             ensure_ascii=False, indent=2))
        return 1
    try:
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    except Exception as e:
        problems.append(f"config.json 解析失败：{e!r}")
        if args.json:
            print(json.dumps({"report": report, "problems": problems},
                             ensure_ascii=False, indent=2))
        else:
            print(f"{BAD} 2. config.json 解析失败：{e!r}")
        return 1

    prov = cfg.get("provider") or {}
    custom = {k: v for k, v in prov.items()
              if isinstance(v, dict) and not str(k).startswith("builtin:")}
    report["providers_total"] = len(prov)
    report["providers_custom"] = list(custom)
    if not args.json:
        print(f"{OK} 2. config.json 可解析：{len(prov)} 个供应商"
              f"（其中自定义 {len(custom)} 个 —— 只有自定义的会被三档解析选中）")

    # ---- 3. 模型解析 ----
    mv = args.model_value.strip()
    ml = args.model_label.strip()
    pick, how_used = resolve_model(cfg, mv, ml)
    report["resolve"] = {"modelValue": mv, "modelLabel": ml, "how": how_used,
                         "providerId": pick and pick["pid"],
                         "modelId": pick and pick["mid"]}
    if not args.json:
        print("3. 模型解析（复刻 handler 三档）")
        if mv:
            print(f"   界面 ref = {mv!r} / 显示名 = {ml!r}")
        else:
            print(f"   （未提供 --model-value，演示最坏情况：界面 ref 读不到）"
                  f" 显示名 = {ml or '(空)'!r}")
        if not pick:
            problems.append("没有解析出任何模型 -> 润色直接报「没有可用的模型」")
            print(f"{BAD}   解析结果：无 —— 界面没给 ref，且显示名反查不到")
            print("         → 修复：确认供应商配置里 models 键的拼写与界面显示名一致")
        else:
            tag = {"ref": OK, "label": WARN, "fallback": BAD}.get(how_used, WARN)
            print(f"{tag}   命中 {pick['pid']} / {pick['mid']}   (how={how_used})")
            if how_used == "label":
                print("         按显示名反查：ref 通道没取到值，说明界面 DOM 里"
                      "没有 data-model-current-value 或取值失败")
            if how_used == "fallback":
                print("         兜底档：把请求打给了「第一个带 Base URL 的自定义供应商」")
                print("         ★ 这就是跨机差异的主因之一 —— 与你界面上选的模型无关")

    # ---- 4. 关键字段 ----
    if pick:
        p = pick["p"]
        opts = p.get("options") or {}
        info = {
            "name": p.get("name"),
            "kind": p.get("kind"),
            "baseURL": opts.get("baseURL"),
            "apiKey_present": bool(opts.get("apiKey")),
            "apiKey_len": len(str(opts.get("apiKey") or "")),
            "enabled": p.get("enabled"),
            "systemDisabledReason": p.get("systemDisabledReason"),
            "model_in_config": pick["mid"] in (p.get("models") or {}),
        }
        report["provider"] = info
        if not args.json:
            print("4. 命中供应商的关键字段")
            for k, v in info.items():
                print(f"   {k:24} = {v}")
        if info["kind"] == "anthropic" and str(pick["pid"]).startswith("builtin:"):
            print(f"{BAD}   ★ kind=anthropic 且 providerId 以 builtin: 开头 —— "
                  f"这是**客户端内置供应商**，key 通常由宿主注入，"
                  f"插件手工读取的 apiKey 可能是旧值/空值")
        if info["systemDisabledReason"]:
            problems.append(f"命中供应商被系统禁用：{info['systemDisabledReason']}")
            print(f"{BAD}   ★ systemDisabledReason={info['systemDisabledReason']} "
                  f"—— 套餐未生效/未授权，模型服务端会直接判不可用")
        if info["enabled"] is False and not info["systemDisabledReason"]:
            print(f"{WARN}   enabled=false 但无禁用原因（内置 provider 常如此）")

    # ---- 5. 请求构造 ----
    urls, headers, err = build_request(pick) if pick else ([], {}, "")
    if pick:
        report["request"] = {"urls": urls, "max_tokens": MAX_TOKENS}
        if not args.json:
            print("5. 请求构造复核")
        if err:
            problems.append(err)
            print(f"{BAD}   {err}")
        else:
            for i, u in enumerate(urls):
                print(f"   {'  首次 ' if i == 0 else '  备选 '}POST {u}")
            print(f"   headers: Content-Type / Accept / Authorization / x-api-key"
                  f"（key 长度 {len(headers['Authorization']) - 7}）")
            print(f"   max_tokens={MAX_TOKENS}（部分供应商 output 上限 <2048 会返回 400）")
            if pick["p"].get("kind") == "anthropic" and "chat/completions" in urls[0]:
                problems.append("anthropic 供应商却拼出了 chat/completions 路径")
                print(f"{BAD}   kind 与路径不匹配")

    # ---- 6. 探测 ----
    if args.probe and pick and not err:
        if args.only and args.only != pick["pid"]:
            print(f"   （--only {args.only} 与命中供应商不符，跳过探测）")
        else:
            print("6. 真实连通性探测")
            res = probe(pick, urls, headers)
            report["probe"] = res
            for ln in res.splitlines():
                print("   " + ln)
            if BAD in res:
                problems.append("探测失败：" + res.splitlines()[0])

    # ---- 结论 ----
    report["problems"] = problems
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 1 if problems else 0

    print("=" * 68)
    if not problems:
        print(f"{OK} 结论：配置侧没发现阻断项。")
        print("   若别人机器上仍报 400 Model is unavailable，请在那台机器上：")
        print("   a) 运行本脚本（同一份文件，直接拷过去即可）对比输出；")
        print("   b) 在本脚本 --probe 之外，用界面里**真正选中的那个模型**再试一次润色；")
        print("   c) 抓包看请求体里的 model 字段究竟发了什么。")
        return 0
    print(f"{BAD} 结论：发现 {len(problems)} 个会导致润色失败的问题：")
    for i, s in enumerate(problems, 1):
        print(f"   {i}) {s}")
    print()
    print("修复优先级：")
    print("   ① 让「界面选中的模型」与「插件解析到的模型」一致（消除兜底档）")
    print("      → 插件侧已加固：先按 ref、再按显示名，且不接收被禁用的内置供应商")
    print("   ② 补齐/更新该供应商的 API Key，确认套餐与模型权限")
    print("   ③ 核对 baseURL 是否带 /v1，kind 是否与端点协议匹配")
    return 1


if __name__ == "__main__":
    sys.exit(main())
