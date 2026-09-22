#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
zcode_patcher 回归测试（纯标准库 unittest，无第三方依赖）
==========================================================
运行：
  python -m unittest discover -s tests -v
  python tests/test_patcher.py            # 等价

为什么需要这些测试：本工具做的是**二进制级改写**（asar 头解析、offset 重排、integrity 重算、
字节级原地覆盖、备份指纹校验），靠人工点一遍根本覆盖不到。历史上踩过的坑都能在这里复现：
  * 「offset 重排后用旧位置切片」→ 抽查 50 个文件错 11 个（test_repack_*）
  * 「全局替换 integrity 哈希串」误伤同内容条目（test_sync_integrity_*）
  * 「文本模式读写把 CRLF 归一为 LF」（test_kernel_patch_preserves_bytes）
  * 「升级后还原把旧版内核盖回新客户端」（test_kernel_backup_fingerprint_*）
"""

import contextlib
import io
import json
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

# 兼容两种仓库布局：扁平（scripts/）与插件（skills/zcode-tokenspeed/scripts/）
_HERE = Path(__file__).resolve().parent
for _cand in (_HERE.parent / "scripts",
              _HERE.parent / "skills" / "zcode-tokenspeed" / "scripts"):
    if (_cand / "zcode_patcher.py").is_file():
        sys.path.insert(0, str(_cand))
        break
import zcode_patcher as zp          # noqa: E402


# ------------------------------------------------------------------ 测试夹具

def build_asar(path: Path, files: dict, unpacked: tuple = ()) -> None:
    """构造一个最小合法 asar（头 16 字节 + JSON + pad + 数据区），与 Electron/asar 同构。"""
    data = bytearray()
    placed = {}
    for p in sorted(files):
        if p in unpacked:
            continue
        placed[p] = (len(files[p]), len(data))
        data += files[p]

    tree = {"files": {}}

    def node_for(p: str) -> dict:
        node = tree
        parts = p.split("/")
        for part in parts[:-1]:
            node = node["files"].setdefault(part, {"files": {}})
        return node["files"]

    for p, (size, off) in placed.items():
        node_for(p)[p.split("/")[-1]] = {
            "size": size, "offset": str(off), "integrity": zp._asar_integrity(files[p]),
        }
    for p in unpacked:
        node_for(p)[p.split("/")[-1]] = {
            "size": len(files.get(p, b"")), "offset": "0", "unpacked": True,
        }

    js = json.dumps(tree, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    pad = (4 - len(js) % 4) % 4
    header = (struct.pack("<4I", 4, 8 + len(js) + pad, 4 + len(js) + pad, len(js))
              + js + b"\x00" * pad)
    path.write_bytes(header + bytes(data))


def read_entry(asar: Path, entry_path: str) -> bytes:
    raw, header, data_start = zp._asar_header_raw(asar)
    return zp._asar_entry_bytes(raw, data_start, zp._asar_find_entry(header, entry_path))


def quiet(fn, *a, **kw):
    """吞掉被测函数的打印输出（测试关注返回值与副作用，不看日志）。"""
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*a, **kw)


def make_cjs(anchor: str, prefix: str = "/*pre*/", suffix: str = "/*post*/",
             newline: str = "\n") -> bytes:
    """用真实锚点拼一个假的 zcode.cjs（含换行，用于验证字节级改写）。"""
    body = f"{prefix}{newline}{anchor}{newline}{suffix}{newline}"
    return body.encode("utf-8")


class TempCase(unittest.TestCase):
    def setUp(self):
        # Windows 上临时目录偶尔会被索引/杀软短暂占用，cleanup 失败不该让测试变红
        self._tmp = tempfile.TemporaryDirectory(prefix="zpatch-test-", ignore_cleanup_errors=True)
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()


# ------------------------------------------------------------------ asar 读写 / 重打包

class TestAsarRepack(TempCase):
    FILES = {
        "out/renderer/index.html": b"<html><body></body></html>",
        "out/renderer/assets/a.js": b"console.log('a');",
        "out/renderer/assets/deep/b.js": b"console.log('b');",
        "out/main/index.js": b"require('electron');",
        "package.json": b'{"name":"demo","version":"1.2.3"}',
        "node_modules/native/dup.txt": b"same-content",          # 与下一条内容完全相同
        "node_modules/native/dup2.txt": b"same-content",
    }

    def setUp(self):
        super().setUp()
        self.asar = self.tmp / "app.asar"
        build_asar(self.asar, self.FILES, unpacked=("node_modules/native/big.node",))
        self.asar_unpacked = self.tmp / "app.asar.unpacked"
        self.asar_unpacked.mkdir()

    def test_header_parse_matches_fixture(self):
        """头解析出的偏移必须能取回原始字节（验证 16 字节头 / data_start 公式）。"""
        for p, want in self.FILES.items():
            self.assertEqual(read_entry(self.asar, p), want, f"{p} 字节不一致")

    def test_package_version_readable(self):
        self.assertEqual(zp.asar_version(self.asar), "1.2.3")

    def test_repack_without_changes_keeps_every_entry(self):
        """空重打包：每个条目字节必须与原来完全一致（offset 重排不能错位）。"""
        before = {p: read_entry(self.asar, p) for p in self.FILES}
        zp._repack_asar(self.asar, {}, set())
        for p, want in before.items():
            self.assertEqual(read_entry(self.asar, p), want, f"{p} 重打包后错位")

    def test_repack_overwrite_and_add(self):
        """覆盖 + 新增：改动的条目字节与 integrity 都要对，未改动的原样保留。"""
        new_index = b"<html><body><script src='./x.js'></script></body></html>"
        new_script = b"/* injected */"
        zp._repack_asar(self.asar, {
            "out/renderer/index.html": new_index,
            "out/renderer/x.js": new_script,
        }, set())

        self.assertEqual(read_entry(self.asar, "out/renderer/index.html"), new_index)
        self.assertEqual(read_entry(self.asar, "out/renderer/x.js"), new_script)
        for p in ("out/main/index.js", "package.json", "out/renderer/assets/deep/b.js"):
            self.assertEqual(read_entry(self.asar, p), self.FILES[p], f"{p} 被误改")

        raw, header, data_start = zp._asar_header_raw(self.asar)
        for p in ("out/renderer/index.html", "out/renderer/x.js"):
            ent = zp._asar_find_entry(header, p)
            self.assertEqual(ent["integrity"], zp._asar_integrity(read_entry(self.asar, p)),
                             f"{p} integrity 未重算")

    def test_repack_relayouts_offsets_contiguously(self):
        """重排后 offset 必须紧邻且等于真实位置，不能出现空洞或重叠。"""
        zp._repack_asar(self.asar, {"out/renderer/index.html": b"x" * 100}, set())
        raw, header, data_start = zp._asar_header_raw(self.asar)
        pos = []
        for p, ent in zp._asar_walk_entries(header):
            pos.append((int(ent["offset"]), ent["size"], p))
        pos.sort()
        cursor = 0
        for off, size, p in pos:
            self.assertEqual(off, cursor, f"{p} offset 不连续")
            cursor += size
        self.assertEqual(data_start + cursor, len(raw), "数据区末尾与文件长度不符")

    def test_repack_removes_entry(self):
        zp._repack_asar(self.asar, {}, {"out/renderer/assets/a.js"})
        raw, header, _ = zp._asar_header_raw(self.asar)
        self.assertIsNone(zp._asar_find_entry(header, "out/renderer/assets/a.js"))
        self.assertEqual(read_entry(self.asar, "out/renderer/assets/deep/b.js"),
                         self.FILES["out/renderer/assets/deep/b.js"])

    def test_repack_skips_unpacked_entry(self):
        """unpacked 条目要留在树里（Electron 从 app.asar.unpacked 读它），但不能进数据区。"""
        before = read_entry(self.asar, "out/renderer/assets/a.js")
        zp._repack_asar(self.asar, {"out/renderer/index.html": b"y" * 50}, set())
        raw, header, data_start = zp._asar_header_raw(self.asar)
        ent = zp._asar_find_entry(header, "node_modules/native/big.node")
        self.assertIsNotNone(ent, "unpacked 条目被从树里删掉了")
        self.assertTrue(ent.get("unpacked"))
        self.assertEqual(ent["offset"], "0", "unpacked 条目不应被分配数据区偏移")
        # 数据区长度 == 所有非 unpacked 条目之和（unpacked 没被塞进去）
        total = sum(int(e["size"]) for _, e in zp._asar_walk_entries(header))
        self.assertEqual(len(raw) - data_start, total)
        self.assertEqual(read_entry(self.asar, "out/renderer/assets/a.js"), before)


class TestIntegritySync(TempCase):
    """integrity 同步必须只动目标条目——同内容条目不能被误伤。"""

    def setUp(self):
        super().setUp()
        self.asar = self.tmp / "app.asar"
        build_asar(self.asar, {
            "dup1.txt": b"same-content",
            "dup2.txt": b"same-content",
            "target.js": b"old-target",
        })

    def test_sync_only_touches_target_entry(self):
        new = b"new-target"
        raw, header, data_start = zp._asar_header_raw(self.asar)
        ent = zp._asar_find_entry(header, "target.js")
        # 原地覆盖 + 同步 integrity（模拟字节级补丁）
        with open(self.asar, "r+b") as f:
            f.seek(data_start + int(ent["offset"]))
            f.write(new)
        quiet(zp._asar_sync_integrity, self.asar, "target.js", new)

        _, header2, _ = zp._asar_header_raw(self.asar)
        self.assertEqual(zp._asar_find_entry(header2, "target.js")["integrity"],
                         zp._asar_integrity(new))
        for p in ("dup1.txt", "dup2.txt"):
            self.assertEqual(zp._asar_find_entry(header2, p)["integrity"],
                             zp._asar_integrity(b"same-content"),
                             f"{p} 的 integrity 被误改")

    def test_sync_is_idempotent(self):
        new = b"new-target"
        zp._asar_sync_integrity(self.asar, "target.js", new)
        before = self.asar.read_bytes()
        quiet(zp._asar_sync_integrity, self.asar, "target.js", new)
        self.assertEqual(self.asar.read_bytes(), before, "重复同步不应改文件")

    def test_sync_dry_run_does_not_write(self):
        before = self.asar.read_bytes()
        quiet(zp._asar_sync_integrity, self.asar, "target.js", b"zzz", dry_run=True)
        self.assertEqual(self.asar.read_bytes(), before)


class TestRepackMemory(TempCase):
    """重打包不该把整包读进内存——真实 app.asar 三百多 MB，整包读入会让峰值接近 2× 包体。"""

    def setUp(self):
        super().setUp()
        self.big = b"x" * (3 * 1024 * 1024)          # 单条 3MB，跨多个 1MB 搬运块
        self.files = {"big.bin": self.big, "out/renderer/index.html": b"<html></html>"}
        for i in range(12):
            self.files[f"pad/{i}.bin"] = bytes([65 + i]) * (3 * 1024 * 1024)
        self.asar = self.tmp / "app.asar"
        build_asar(self.asar, self.files)

    def test_peak_memory_far_below_package_size(self):
        import tracemalloc
        size = self.asar.stat().st_size
        self.assertGreater(size, 30 * 1024 * 1024, "夹具太小，测不出内存行为")

        tracemalloc.start()
        zp._repack_asar(self.asar, {"out/renderer/index.html": b"z" * 1000}, set())
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        self.assertLess(peak, size // 3,
                        f"峰值 {peak / 1048576:.1f}MB 相对包体 {size / 1048576:.1f}MB 过大")

    def test_large_entries_survive_chunked_copy(self):
        zp._repack_asar(self.asar, {"out/renderer/index.html": b"z" * 1000}, set())
        self.assertEqual(read_entry(self.asar, "big.bin"), self.big)
        for i in (0, 5, 11):
            self.assertEqual(read_entry(self.asar, f"pad/{i}.bin"), self.files[f"pad/{i}.bin"])
        self.assertEqual(read_entry(self.asar, "out/renderer/index.html"), b"z" * 1000)


class TestPrune(TempCase):
    """--prune 只清理本工具自己的产物：默认不碰当前备份与 sidecar，也绝不碰邻居文件。"""

    def setUp(self):
        super().setUp()
        self.asar = self.tmp / "app.asar"
        build_asar(self.asar, {"a.js": b"aaaa"})
        for name in ("app.asar.tps.bak", "app.asar.tps.bak.meta.json",
                     "app.asar.chart-patch.json",
                     "app.asar.puller.bak.stale-20260101-000000",
                     "app.asar.123.tmp", "app.asar.tps-tmp",
                     "unrelated.txt", "app.asar.old", "app.asarfoo"):
            (self.tmp / name).write_bytes(b"x" * 10)

    def names(self):
        return {p.name for p in self.tmp.iterdir()}

    def test_default_prune_only_archives_and_temps(self):
        quiet(zp.prune_artifacts, [self.asar], False)
        left = self.names()
        for keep in ("app.asar.tps.bak", "app.asar.chart-patch.json",
                     "unrelated.txt", "app.asar.old", "app.asarfoo", "app.asar"):
            self.assertIn(keep, left, f"{keep} 不该被删")
        for gone in ("app.asar.puller.bak.stale-20260101-000000",
                     "app.asar.123.tmp", "app.asar.tps-tmp"):
            self.assertNotIn(gone, left, f"{gone} 应被清理")

    def test_deep_prune_also_removes_backups_and_sidecars(self):
        quiet(zp.prune_artifacts, [self.asar], True)
        left = self.names()
        for gone in ("app.asar.tps.bak", "app.asar.tps.bak.meta.json",
                     "app.asar.chart-patch.json"):
            self.assertNotIn(gone, left, f"{gone} 应被 --deep 清理")
        self.assertIn("app.asar", left, "包本体不能删")
        self.assertIn("unrelated.txt", left)

    def test_dry_run_keeps_everything(self):
        quiet(zp.prune_artifacts, [self.asar], True, True)
        self.assertTrue((self.tmp / "app.asar.tps.bak").exists())
        self.assertTrue((self.tmp / "app.asar.123.tmp").exists())


# ------------------------------------------------------------------ 内核补丁（≤3.11）

class TestKernelPatch(TempCase):
    ANCHOR = zp.ANCHORS["3.11.2"]

    def setUp(self):
        super().setUp()
        self.cjs = self.tmp / "zcode.cjs"
        self.original = make_cjs(self.ANCHOR)
        self.cjs.write_bytes(self.original)

    def patch(self, **kw):
        return quiet(zp.process, self.cjs, False, False, **kw)

    def revert(self, **kw):
        return quiet(zp.process, self.cjs, False, True, **kw)

    def test_patch_then_check_then_revert(self):
        self.assertTrue(self.patch())
        self.assertIn(zp.MARKER.encode(), self.cjs.read_bytes())
        self.assertNotIn(self.ANCHOR.encode(), self.cjs.read_bytes())
        self.assertTrue(quiet(zp.process, self.cjs, True, False))   # --check
        self.assertTrue(self.revert())
        self.assertEqual(self.cjs.read_bytes(), self.original)

    def test_patch_preserves_bytes_and_crlf(self):
        """关键回归：改补丁不能顺手把整个文件的 CRLF 归一为 LF。"""
        self.cjs.write_bytes(make_cjs(self.ANCHOR, newline="\r\n"))
        before = self.cjs.read_bytes()
        crlf_before = before.count(b"\r\n")
        self.assertTrue(self.patch())
        after = self.cjs.read_bytes()
        self.assertGreater(crlf_before, 0)
        self.assertEqual(after.count(b"\r\n"), crlf_before, "CRLF 数量被改变（文本模式读写回归）")
        # 只在锚点处新增字节：前后缀必须原样保留
        self.assertTrue(after.startswith(b"/*pre*/"))
        self.assertTrue(after.endswith(b"/*post*/\r\n"))

    def test_backup_fingerprint_blocks_cross_version_revert(self):
        """升级后（内核被替换）还原必须被拒绝，且不得改动文件。"""
        self.assertTrue(self.patch())
        upgraded = make_cjs(self.ANCHOR.replace("RD", "QQ"), prefix="/*new-kernel*/")
        self.cjs.write_bytes(upgraded)
        self.assertFalse(self.revert(), "跨版本还原竟然成功了")
        self.assertEqual(self.cjs.read_bytes(), upgraded, "拒绝还原时不应改动文件")
        self.assertTrue(self.revert(force=True), "--force 应可强制还原")

    def test_patch_after_upgrade_archives_stale_backup(self):
        """升级到另一个已支持版本后重打：旧备份要归档（改名 .stale-*），并按新内核重建。"""
        self.assertTrue(self.patch())
        upgraded = make_cjs(zp.ANCHORS["3.9.2"], prefix="/*upgraded-kernel*/")
        self.cjs.write_bytes(upgraded)
        self.assertTrue(self.patch())
        stale = list(self.tmp.glob("zcode.cjs.bak.stale-*"))
        self.assertTrue(stale, "旧备份未归档")
        meta = json.loads((self.tmp / "zcode.cjs.bak.meta.json").read_text(encoding="utf-8"))
        self.assertEqual(meta["sha256"], zp._sha256(upgraded), "备份不是升级后内核的原始副本")
        self.assertEqual(meta["patched_sha256"], zp._sha256(self.cjs.read_bytes()))
        self.assertEqual(stale[0].read_bytes(), make_cjs(self.ANCHOR), "归档内容应是旧内核原始副本")

    def test_dry_run_does_not_write(self):
        before = self.cjs.read_bytes()
        self.assertTrue(self.patch(dry_run=True))
        self.assertEqual(self.cjs.read_bytes(), before, "dry-run 竟然写了盘")
        self.assertFalse((self.tmp / "zcode.cjs.bak").exists(), "dry-run 不应创建备份")

    def test_unknown_structure_is_refused(self):
        self.cjs.write_bytes(b"/* totally different kernel */")
        self.assertFalse(self.patch(), "结构未知时必须拒绝盲改")

    def test_native_mechanism_is_reported_as_not_applicable(self):
        """3.14+ 内核（有 optionSpecs、无 defaultVariant）应判为原生机制并跳过。"""
        self.cjs.write_bytes(b"/* kernel with optionSpecs and no legacy symbols */")
        self.assertEqual(zp.detect_reasoning_mechanism(self.cjs), "native")
        self.assertTrue(self.patch(), "原生机制应视为「无需改动」而非失败")

    def test_replacement_contains_helper_and_marker(self):
        patched = zp.replacement_for(self.ANCHOR)
        self.assertIn(zp.MARKER, patched)
        self.assertIn("providerOptionsByLevel", patched)


# ------------------------------------------------------------------ 3.14+ 档位配置

CONFIG_FIXTURE = {
    "provider": {
        "custom-a": {
            "name": "A 网关", "kind": "openai-compatible", "source": "custom",
            "options": {"baseURL": "https://a.example/v1", "apiKey": "sk-secret"},
            "models": {
                "m-variants": {"limit": {"context": 200000, "output": 32000},
                               "reasoning": {"enabled": True, "variants": ["low", "high", "max"],
                                             "defaultVariant": "high"}},
                "m-specs": {"limit": {"context": 100000},
                            "optionSpecs": {"reasoningLevel": {"values": ["off", "max"], "map": "{}"}}},
                "m-plain": {"limit": {"context": 100000}},
            },
        },
        "builtin:zai": {          # 内置模板供应商：不应被处理
            "name": "内置", "kind": "anthropic", "source": "custom",
            "models": {"GLM": {"reasoning": {"enabled": True, "variants": ["low", "max"]}}},
        },
    }
}

PROVIDER_CONFIG_FIXTURE = {
    "schemaVersion": 1,
    "config": {
        "providerConfigRules": {"providerRules": [
            {"providerId": "custom-a", "providerName": "A 网关",
             "config": {"personalModelIds": ["m-variants", "m-specs", "m-plain"]}},
        ]},
        "modelConfigRules": {
            "providerModelRules": [
                {"modelId": "m-variants", "providerId": "custom-a",
                 "config": {"properties": {"contextWindow": 200000}}},
                {"modelId": "m-specs", "providerId": "custom-a",
                 "config": {"properties": {"contextWindow": 100000},
                            "optionSpecs": {"reasoningLevel": {"values": ["off", "max"], "map": "{}"}}}},
            ],
            "manualProviderModelRules": [
                {"modelId": "m-manual", "providerId": "custom-a",
                 "config": {"optionSpecs": {"reasoningLevel": {"values": ["low", "high"], "map": "{}"}}}},
            ],
        },
    },
}


class TestReasoningConfig(TempCase):
    def setUp(self):
        super().setUp()
        self.root = self.tmp / "v2"
        self.root.mkdir()
        (self.root / "config.json").write_text(
            json.dumps(CONFIG_FIXTURE, ensure_ascii=False), encoding="utf-8")
        self.pc_path = self.root / "provider_config.json"
        self.pc_path.write_text(
            json.dumps(PROVIDER_CONFIG_FIXTURE, ensure_ascii=False), encoding="utf-8")
        self.orig_pc = self.pc_path.read_text(encoding="utf-8")

    def run_cfg(self, check=False, revert=False, dry_run=False):
        return quiet(zp.process_reasoning_config, self.root, check, revert, dry_run=dry_run)

    def rules(self):
        pc = json.loads(self.pc_path.read_text(encoding="utf-8"))
        return pc["config"]["modelConfigRules"]["providerModelRules"]

    def rule(self, model_id):
        return next(r for r in self.rules() if r["modelId"] == model_id)

    def test_write_creates_option_specs(self):
        self.assertTrue(self.run_cfg())
        rl = self.rule("m-variants")["config"]["optionSpecs"]["reasoningLevel"]
        # defaultVariant 要排到末位（3.14 约定：values 末位即默认档）
        self.assertEqual(rl["values"], ["low", "max", "high"])
        self.assertIn("reasoning_effort", rl["map"])
        self.assertIn("contextWindow", self.rule("m-variants")["config"]["properties"])

    def test_new_rule_gets_context_window(self):
        """config.json 里存在但 providerModelRules 缺失的模型要新建规则（含 contextWindow）。"""
        pc = json.loads(self.pc_path.read_text(encoding="utf-8"))
        pc["config"]["modelConfigRules"]["providerModelRules"] = [
            r for r in pc["config"]["modelConfigRules"]["providerModelRules"]
            if r["modelId"] != "m-specs"]
        self.pc_path.write_text(json.dumps(pc, ensure_ascii=False), encoding="utf-8")
        self.assertTrue(self.run_cfg())
        self.assertEqual(self.rule("m-specs")["config"]["properties"]["contextWindow"], 100000)

    def test_manual_conflicts_are_skipped(self):
        self.assertTrue(self.run_cfg())
        keys = {(r["providerId"], r["modelId"]) for r in self.rules()}
        manual = json.loads(self.pc_path.read_text(encoding="utf-8"))[
            "config"]["modelConfigRules"]["manualProviderModelRules"]
        manual_keys = {(r["providerId"], r["modelId"]) for r in manual}
        self.assertFalse(keys & manual_keys, "同一模型同时出现在两个规则列表（内核会整表降级）")

    def test_preexisting_duplicate_is_reported_not_silently_ignored(self):
        """已存在的重复声明（同一模型在两个列表）要报警——它是「模型全没了」的成因。"""
        pc = json.loads(self.pc_path.read_text(encoding="utf-8"))
        pc["config"]["modelConfigRules"]["providerModelRules"].append(
            {"modelId": "m-manual", "providerId": "custom-a",
             "config": {"properties": {"contextWindow": 1}}})
        self.pc_path.write_text(json.dumps(pc, ensure_ascii=False), encoding="utf-8")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            zp.process_reasoning_config(self.root, True, False)
        self.assertIn("降级为空", buf.getvalue())

    def test_builtin_provider_untouched(self):
        self.assertTrue(self.run_cfg())
        self.assertFalse([r for r in self.rules() if r["providerId"] == "builtin:zai"])

    def test_models_without_levels_are_skipped(self):
        self.assertTrue(self.run_cfg())
        self.assertFalse([r for r in self.rules() if r["modelId"] == "m-plain"
                          and "optionSpecs" in (r.get("config") or {})])

    def test_idempotent_and_dry_run_and_revert(self):
        self.assertTrue(self.run_cfg(dry_run=True))
        self.assertEqual(self.pc_path.read_text(encoding="utf-8"), self.orig_pc, "dry-run 写了盘")

        self.assertTrue(self.run_cfg())
        snapshot = self.pc_path.read_bytes()
        self.assertTrue(self.run_cfg())                      # 二次运行
        self.assertEqual(self.pc_path.read_bytes(), snapshot, "非幂等：二次运行改动了文件")
        self.assertTrue((self.root / "provider_config.json.reasoning-bak").is_file())

        self.assertTrue(self.run_cfg(revert=True))
        self.assertEqual(self.pc_path.read_text(encoding="utf-8"), self.orig_pc, "还原内容不一致")
        self.assertFalse((self.root / "provider_config.json.reasoning-bak").exists())

    def test_json_written_is_valid_and_atomic(self):
        self.assertTrue(self.run_cfg())
        json.loads(self.pc_path.read_text(encoding="utf-8"))   # 必须是合法 JSON
        self.assertFalse(list(self.root.glob("*.tmp")), "残留了临时文件")

    def test_missing_config_is_reported(self):
        (self.root / "config.json").unlink()
        self.assertFalse(self.run_cfg())


# ------------------------------------------------------------------ sidecar 指纹

class TestSidecarFingerprint(TempCase):
    def setUp(self):
        super().setUp()
        self.asar = self.tmp / "app.asar"
        build_asar(self.asar, {"a.js": b"aaaa", "b.js": b"bbbb"})

    def test_mismatched_size_invalidates_records(self):
        side = self.asar.with_name(self.asar.name + ".chart-patch.json")
        side.write_text(json.dumps({"patches": [
            {"path": "a.js", "offset": 0, "size": 4, "asar_size": 12345, "original_b64": ""},
        ]}), encoding="utf-8")
        self.assertEqual(zp._load_sidecar(side, self.asar.stat().st_size), [])
        self.assertEqual(len(zp._load_sidecar(side)), 1, "不带指纹时不应过滤")

    def test_refresh_updates_byte_level_offsets(self):
        side = self.asar.with_name(self.asar.name + ".chart-patch.json")
        side.write_text(json.dumps({"patches": [
            {"path": "b.js", "offset": 0, "size": 4,
             "asar_size": self.asar.stat().st_size, "original_b64": ""},
        ]}), encoding="utf-8")
        zp._repack_asar(self.asar, {"a.js": b"aaaaaa"}, set())
        quiet(zp._refresh_chart_sidecar, self.asar)
        rec = json.loads(side.read_text(encoding="utf-8"))["patches"][0]
        _, header, data_start = zp._asar_header_raw(self.asar)
        ent = zp._asar_find_entry(header, "b.js")
        self.assertEqual(rec["offset"], data_start + int(ent["offset"]))
        self.assertEqual(rec["asar_size"], self.asar.stat().st_size)

    def test_refresh_updates_repack_level_sidecar_size(self):
        """TPS/滑条/拉取按钮的 sidecar 只记 asar_size，也要跟着重打包更新。"""
        side = self.asar.with_name(self.asar.name + ".tps-patch.json")
        side.write_text(json.dumps({"asar_size": 1, "script_entry": "x.js"}), encoding="utf-8")
        quiet(zp._refresh_chart_sidecar, self.asar)
        self.assertEqual(json.loads(side.read_text(encoding="utf-8"))["asar_size"],
                         self.asar.stat().st_size)


# ------------------------------------------------------------------ 注入段比对

class TestPullerInjectionState(unittest.TestCase):
    def test_main_segment_comparison_ignores_leading_newline(self):
        """回归：注入段以换行开头，比对/剥离必须把它算进去，否则永远误报「版本旧」。"""
        blob = b"var x=1;\n" + zp._models_main_block("j") + \
               b"j.handle(E.SaveMcpToUserDirectory,()=>{});"
        injected, synced, clean, alias = zp._models_main_state(blob)
        self.assertTrue(injected)
        self.assertTrue(synced, "main 注入段被误判为旧版")
        self.assertEqual(alias, "j")
        self.assertNotIn(b"zcode:read-model-config", clean)

    def test_preload_segment_comparison(self):
        blob = b"_.contextBridge.exposeInMainWorld(\"zcode\",{" + \
               zp._models_preload_block("_") + b"other:1});"
        injected, synced, _clean, alias = zp._models_preload_state(blob)
        self.assertTrue(injected and synced)
        self.assertEqual(alias, "_")

    def test_marker_absent_means_not_injected(self):
        blob = b'_.contextBridge.exposeInMainWorld("zcode",{other:1});'
        self.assertEqual(zp._models_preload_state(blob)[:2], (False, False))


class TestGeneratedJs(unittest.TestCase):
    """注入到 main/preload 的 JS 是拼接出来的——必须保证拼出来仍是合法 JS。"""

    @classmethod
    def setUpClass(cls):
        cls.node = shutil.which("node") or shutil.which("node.exe")
        if not cls.node:
            raise unittest.SkipTest("本机没有 node，跳过注入代码语法校验")

    def _check(self, code: str) -> None:
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "snippet.js"
            f.write_text(code, encoding="utf-8")
            r = subprocess.run([self.node, "--check", str(f)], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, f"生成的 JS 语法错误：{r.stderr[:300]}")

    def test_main_injection_is_valid_js(self):
        code = zp._models_main_block("j").decode()
        self._check(code)
        self.assertIn("renameSync", code, "配置写入应为原子替换")
        self.assertIn("oldOrder", code, "modelOrder 应保序")

    def test_preload_injection_is_valid_js(self):
        self._check("const o={" + zp._models_preload_block("_").decode()
                    + zp._enhance_preload_block("_").decode() + "x:1};")

    def test_enhance_main_injection_is_valid_js(self):
        code = zp._enhance_main_block("j").decode()
        self._check(code)
        self.assertIn("zcode:enhance-prompt", code)
        self.assertIn("chat/completions", code)

    def test_main_segment_matches_regenerated(self):
        """注入 → 状态判定 → 再生成，三段必须逐字节一致（否则每次都会白重写）。"""
        blob = b"var x=1;\n" + zp._models_main_block("j") + b"j.handle(E.SaveMcpToUserDirectory,1);"
        injected, synced, clean, alias = zp._models_main_state(blob)
        self.assertTrue(injected and synced)
        rebuilt = clean[:0] + b"var x=1;\n" + zp._models_main_block(alias) + \
            b"j.handle(E.SaveMcpToUserDirectory,1);"
        self.assertEqual(rebuilt, blob)


# ------------------------------------------------------------------ 真实安装（只读，可选）

def _real_asar() -> Path | None:
    try:
        for cjs in zp.discover():
            asar = cjs.parent.parent / "app.asar"
            if asar.is_file():
                return asar
    except Exception:
        pass
    return None


class TestRealInstall(unittest.TestCase):
    """对本机真实 app.asar 做只读自洽性校验（没装 ZCode 就跳过）。

    这一组能验证「头公式与 Electron 实际产物一致」——纯合成夹具做不到这点。"""

    @classmethod
    def setUpClass(cls):
        cls.asar = _real_asar()
        if cls.asar is None:
            raise unittest.SkipTest("本机未探测到 ZCode 安装")
        cls.raw, cls.header, cls.data_start = zp._asar_header_raw(cls.asar)

    def test_every_entry_integrity_matches_content(self):
        checked = 0
        for p, ent in zp._asar_walk_entries(self.header):
            data = zp._asar_entry_bytes(self.raw, self.data_start, ent)
            self.assertEqual(len(data), int(ent["size"]), f"{p} 长度不符")
            itg = ent.get("integrity")
            if itg:
                self.assertEqual(itg["hash"], zp._sha256(data), f"{p} integrity 与实际内容不符")
            checked += 1
        self.assertGreater(checked, 1000, "真实 asar 条目数异常偏少")

    def test_data_area_is_contiguous(self):
        ends = [self.data_start + int(e["offset"]) + int(e["size"])
                for _, e in zp._asar_walk_entries(self.header)]
        self.assertLessEqual(max(ends), len(self.raw), "条目越过文件末尾")

    def test_version_readable(self):
        self.assertRegex(zp.asar_version(self.asar) or "", r"^\d+\.\d+")


# ------------------------------------------------------------------ 模块符号与注入块

class TestModuleSurface(unittest.TestCase):
    """防止重构时误删符号——本轮就真发生过：整段替换把 _read_asar_header 与
    REASONING_MAP_* 一起删掉，直到跑测试才暴露。"""

    REQUIRED = [
        # asar 基础
        "_read_asar_header", "_asar_header_raw", "_asar_entry_bytes", "_asar_integrity",
        "_asar_walk_entries", "_repack_asar", "_asar_sync_integrity", "_load_sidecar",
        "_refresh_chart_sidecar", "_cleanup_stale_tmp", "_relocate_sidecar",
        # 备份指纹
        "_ensure_backup", "_load_backup_meta", "_mark_backup_patched", "_archive_backup",
        # 内核补丁
        "process", "replacement_for", "extract_anchor", "detect_reasoning_mechanism",
        "ANCHORS", "HELPER", "MARKER",
        # 补丁入口
        "process_usage_chart", "process_model_width", "process_tps_footer",
        "process_thought_slider", "process_model_puller", "process_enhance_prompt",
        "process_reasoning_config", "prune_artifacts", "_process_ipc_patch",
        # 注入块（标记定界）
        "MODELS_BLOCK", "ENHANCE_BLOCK", "_block_mark", "_wrap_block", "_block_span",
        "_bridge_block_state",
        "_models_preload_block", "_models_main_block", "_models_preload_state", "_models_main_state",
        "_enhance_preload_block", "_enhance_main_block", "_enhance_preload_state", "_enhance_main_state",
        "PULLER_SPEC", "ENHANCE_SPEC",
        # 档位映射 / 路径常量
        "REASONING_MAP_OPENAI", "REASONING_MAP_ANTHROPIC", "_desired_levels", "_v2_root",
        "TPS_SCRIPT_PATH", "SLIDER_SCRIPT_PATH", "PULLER_SCRIPT_PATH", "ENHANCE_SCRIPT_PATH",
        "TPS_TAG", "SLIDER_TAG", "PULLER_TAG", "ENHANCE_TAG",
        # --all 一键操作
        "ALL_PATCH_FLAGS", "_expand_all",
    ]

    def test_required_symbols_exist(self):
        missing = [n for n in self.REQUIRED if not hasattr(zp, n)]
        self.assertEqual(missing, [], f"模块缺少符号：{missing}")


class TestAllFlag(unittest.TestCase):
    """--all 是「一条命令体检/全装/全还原」的入口，必须真的覆盖到每一个补丁——
    漏一个就是用户装完发现某个功能没生效，却从汇总表上看不出来。"""

    def test_expand_all_sets_every_patch_flag(self):
        import argparse
        ns = argparse.Namespace(**{name: False for name in zp.ALL_PATCH_FLAGS})
        zp._expand_all(ns)
        off = [n for n in zp.ALL_PATCH_FLAGS if not getattr(ns, n)]
        self.assertEqual(off, [], f"--all 没有打开这些补丁：{off}")

    def test_every_flag_is_a_real_cli_option(self):
        """ALL_PATCH_FLAGS 里写了名字但没加 add_argument → --all 直接 AttributeError。"""
        r = subprocess.run([sys.executable, str(zp.__file__), "--help"],
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        missing = [n for n in zp.ALL_PATCH_FLAGS
                   if "--" + n.replace("_", "-") not in r.stdout]
        self.assertEqual(missing, [], f"这些补丁名不是真实命令行参数：{missing}")
        self.assertIn("--all", r.stdout)

    def test_all_covers_every_plugin_switch(self):
        """插件开关表（sync.py 的 PATCHES）与 --all 必须一一对应，否则
        「在插件里能开关、但命令行 --all 覆盖不到」这类不一致会悄悄存在。
        例外只有 core_patch：它是无参数的内核补丁（≤3.11 专用）。"""
        import sync
        switch_keys = {key for key, _args, _repack in sync.PATCHES}
        self.assertEqual(switch_keys - {"core_patch"}, set(zp.ALL_PATCH_FLAGS))

    def test_kernel_patch_is_included_by_all(self):
        """内核补丁没有命令行参数，--all 走的是 main() 里的分支条件，
        这里守住「--all 时该分支一定会跑」这个前提。"""
        src = Path(zp.__file__).read_text(encoding="utf-8")
        self.assertIn("if args.all or (not asar_flags and not args.reasoning_config):", src)


class TestMarketplaceManifest(unittest.TestCase):
    """marketplace.json 与 plugin.json 的一致性。ZCode 对这两份清单有两条硬性要求，
    违反了都不会在本地测试里露出来，只会表现成「装不上」或「永远不提示更新」：

    * 市场条目的 name 必须等于插件清单的 name（否则安装直接报
      `Plugin manifest name does not match marketplace entry`）
    * 两处 version 必须同步（「检查更新」拿 marketplace.json 当最新版本、plugin.json 当已安装版本）
    """

    @classmethod
    def setUpClass(cls):
        cls.root = _HERE.parent
        cls.market_path = cls.root / "marketplace.json"
        cls.manifest_path = cls.root / ".zcode-plugin" / "plugin.json"
        if not cls.market_path.is_file() or not cls.manifest_path.is_file():
            raise unittest.SkipTest("非插件形态布局（缺 marketplace.json / .zcode-plugin/plugin.json）")
        cls.market = json.loads(cls.market_path.read_text(encoding="utf-8"))
        cls.manifest = json.loads(cls.manifest_path.read_text(encoding="utf-8"))

    def _entry(self) -> dict:
        for p in self.market.get("plugins", []):
            if p.get("name") == self.manifest["name"]:
                return p
        self.fail(f"marketplace.json 里没有名为 {self.manifest['name']} 的条目")

    def test_entry_name_matches_plugin_manifest(self):
        self.assertEqual(self._entry()["name"], self.manifest["name"])

    def test_versions_are_in_sync(self):
        self.assertEqual(self._entry().get("version"), self.manifest.get("version"),
                         "marketplace.json 与 plugin.json 的 version 必须同步，否则「检查更新」永远不提示")

    def test_source_resolves_to_the_plugin_root(self):
        """source 相对市场根目录解析，必须落在含插件清单的目录上。
        「插件与市场同仓库」时写作 "./"（去掉前缀后为空 = 市场根）。"""
        src = str(self._entry()["source"])
        target = (self.market_path.parent / src[2:] if src.startswith("./") else
                  self.market_path.parent / src).resolve()
        self.assertTrue((target / ".zcode-plugin" / "plugin.json").is_file(),
                        f"source {src!r} 解析到 {target}，那里没有 .zcode-plugin/plugin.json")


class TestInjectionBlocks(unittest.TestCase):
    """标记定界注入块：模型拉取与增强提示词共用 preload/main 锚点，必须互不干扰。"""

    def test_block_roundtrip(self):
        body = b"readConfigFile:()=>x,"
        blob = b"prefix" + zp._wrap_block("demo", body) + b"suffix"
        span = zp._block_span(blob, "demo")
        self.assertIsNotNone(span)
        i, j = span
        self.assertEqual(blob[i:j], zp._wrap_block("demo", body))
        self.assertEqual(blob[:i] + blob[j:], b"prefixsuffix")
        self.assertIsNone(zp._block_span(blob, "not-there"))

    def test_two_blocks_coexist_and_strip_independently(self):
        models = zp._models_preload_block("_")
        enhance = zp._enhance_preload_block("_")
        blob = (b'_.contextBridge.exposeInMainWorld("zcode",{' + models + enhance + b"other:1});")
        for block_id, want in ((zp.MODELS_BLOCK, models), (zp.ENHANCE_BLOCK, enhance)):
            span = zp._block_span(blob, block_id)
            self.assertIsNotNone(span, block_id)
            self.assertEqual(blob[span[0]:span[1]], want, block_id)
        # 摘掉 models 块后，enhance 块内容必须原样保留
        s = zp._block_span(blob, zp.MODELS_BLOCK)
        stripped = blob[:s[0]] + blob[s[1]:]
        self.assertIsNone(zp._block_span(stripped, zp.MODELS_BLOCK))
        e = zp._block_span(stripped, zp.ENHANCE_BLOCK)
        self.assertIsNotNone(e)
        self.assertEqual(stripped[e[0]:e[1]], enhance)

    def test_main_blocks_coexist(self):
        models = zp._models_main_block("j")
        enhance = zp._enhance_main_block("j")
        blob = models + enhance + b"j.handle(E.SaveMcpToUserDirectory,1);"
        inj, synced, clean, alias = zp._models_main_state(blob)
        self.assertTrue(inj and synced, "共用锚点时 models 块仍应判定为已同步")
        self.assertNotIn(b"read-model-config", clean)
        self.assertIn(zp._enhance_main_block(alias), clean)

    def test_legacy_preload_format_is_migrated(self):
        """老版本（无标记定界）注入段应判为「存在但需更新」，且能被安全剥离。"""
        legacy = (b'_.contextBridge.exposeInMainWorld("zcode",{'
                  b'readConfigFile:()=>_.ipcRenderer.invoke("zcode:read-model-config"),'
                  b'writeConfigFile:t=>_.ipcRenderer.invoke("zcode:write-model-config",t),'
                  b'fetchModelsFromUrl:(t,n)=>_.ipcRenderer.invoke("zcode:fetch-models-from-url",'
                  b'{baseUrl:t,apiKey:n}),other:1});')
        inj, synced, clean, _alias = zp._models_preload_state(legacy)
        self.assertTrue(inj)
        self.assertFalse(synced, "老格式应被判为需要更新（迁移到标记定界）")
        self.assertNotIn(b"read-model-config", clean)
        self.assertIn(b"other:1", clean)

    def test_enhance_block_absent_when_not_injected(self):
        blob = b'_.contextBridge.exposeInMainWorld("zcode",{other:1});'
        self.assertEqual(zp._enhance_preload_state(blob)[:2], (False, False))


class TestSliderScript(unittest.TestCase):
    """滑条注入脚本：能加载 + 注释里承诺的调试接口真实存在。

    这类「注释写了、代码里没有」的漂移肉眼 review 看不出来，`node --check` 也照样通过
    （语法完全合法）——历史上 window.__zsliderCtl 就只存在于注释里。所以这里用最小 DOM 桩
    把脚本真跑一遍，再核对接口成员。
    """

    @classmethod
    def setUpClass(cls):
        cls.node = shutil.which("node") or shutil.which("node.exe")
        if not cls.node:
            raise unittest.SkipTest("本机没有 node，跳过滑条脚本检查")
        cls.smoke = _HERE / "slider_smoke.js"
        cls.script = None
        for cand in (_HERE.parent / "scripts",
                     _HERE.parent / "skills" / "zcode-tokenspeed" / "scripts"):
            p = cand / "zcode-thought-slider.js"
            if p.is_file():
                cls.script = p
                break
        if cls.script is None or not cls.smoke.is_file():
            raise unittest.SkipTest("未找到滑条脚本或冒烟脚本")

    def _node(self, *args):
        return subprocess.run([self.node, *args], capture_output=True,
                              text=True, encoding="utf-8", errors="replace")

    def test_slider_script_is_valid_js(self):
        r = self._node("--check", str(self.script))
        self.assertEqual(r.returncode, 0, f"滑条脚本语法错误：{r.stderr[:300]}")

    def test_slider_loads_and_exposes_control_api(self):
        r = self._node(str(self.smoke), str(self.script))
        self.assertEqual(r.returncode, 0, f"冒烟测试失败：{(r.stdout + r.stderr)[:400]}")
        self.assertIn("smoke OK", r.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
