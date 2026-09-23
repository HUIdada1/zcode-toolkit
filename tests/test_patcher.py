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
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
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


@contextlib.contextmanager
def patcher_stubbed(sync, states=None, verdicts=None):
    """把 sync 里三处会真正碰客户端的地方换成记录桩，并返回调用记录列表。

    **单元测试绝不能真的跑 zcode_patcher.py** —— 那会按当前开关改写本机的 app.asar，
    跑一次测试就顺手把用户的客户端改了。所以：
      check_state   → 查表返回（默认全 "on"，即「客户端已一致」，最安全的基线）
      run_patcher   → 只记录，不执行（默认返回 "ok"；verdicts 可让某组参数返回 "refused"/"fail"）
      start_watchdog→ 只记录，不起进程
    调用记录形如 ("run", ("--usage-chart",), False) / ("watchdog", {"tps_footer": True})。
    """
    table = states or {}
    vtable = verdicts or {}
    calls = []
    orig = (sync.check_state, sync.run_patcher, sync.start_watchdog)
    sync.check_state = lambda args: table.get(tuple(args), "on")
    sync.run_patcher = lambda args, revert: (
        calls.append(("run", tuple(args), revert)),
        vtable.get(tuple(args), "ok"))[1]
    sync.start_watchdog = lambda wanted: calls.append(("watchdog", dict(wanted)))
    try:
        yield calls
    finally:
        sync.check_state, sync.run_patcher, sync.start_watchdog = orig


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


class TestReplaceWithRetry(TempCase):
    """Windows 上 os.replace 会遇到杀软/索引器的瞬时占用（WinError 5 / 32）。

    实测：ZCode 进程持有 app.asar 读句柄时，裸 os.replace 直接 WinError 5 失败；
    这个占用可能是另一个进程正在扫描/释放句柄的瞬时状态，短退避即可跨过去。
    """

    def setUp(self):
        super().setUp()
        self.dst = self.tmp / "app.asar"
        self.dst.write_bytes(b"old")
        self.tmpfile = self.tmp / "app.asar.123.tmp"
        self.tmpfile.write_bytes(b"new")

    def test_happy_path_replaces(self):
        zp._replace_with_retry(self.tmpfile, self.dst)
        self.assertEqual(self.dst.read_bytes(), b"new")
        self.assertFalse(self.tmpfile.exists())

    def test_retries_through_transient_lock(self):
        """模拟「先占用、0.6 秒后释放」——裸 os.replace 会失败，带重试必须成功。"""
        calls = {"n": 0}
        real = os.replace

        def flaky(src, dst):
            calls["n"] += 1
            if calls["n"] < 3:
                err = OSError(13, "拒绝访问")
                err.winerror = 5
                raise err
            return real(src, dst)

        with unittest.mock.patch.object(zp.os, "replace", flaky):
            with unittest.mock.patch.object(zp.time, "sleep", lambda _s: None):
                zp._replace_with_retry(self.tmpfile, self.dst)
        self.assertEqual(calls["n"], 3, "应在第 3 次尝试时成功")
        self.assertEqual(self.dst.read_bytes(), b"new")

    def test_non_lock_errors_are_not_retried(self):
        """不是占用类的错误（如文件不存在）必须原样抛出，不能白等 5 轮。"""
        calls = {"n": 0}

        def bad(src, dst):
            calls["n"] += 1
            raise FileNotFoundError(2, "No such file")

        with unittest.mock.patch.object(zp.os, "replace", bad):
            with self.assertRaises(FileNotFoundError):
                zp._replace_with_retry(self.tmpfile, self.dst)
        self.assertEqual(calls["n"], 1, "非占用类错误只应尝试一次")

    def test_gives_up_after_max_attempts(self):
        calls = {"n": 0}

        def always_locked(src, dst):
            calls["n"] += 1
            err = OSError(13, "拒绝访问")
            err.winerror = 5
            raise err

        with unittest.mock.patch.object(zp.os, "replace", always_locked):
            with unittest.mock.patch.object(zp.time, "sleep", lambda _s: None):
                with self.assertRaises(OSError):
                    zp._replace_with_retry(self.tmpfile, self.dst, attempts=3)
        self.assertEqual(calls["n"], 3)

    def test_repack_uses_the_retrying_replace(self):
        """重打包路径必须走 _replace_with_retry，而不是裸 os.replace。"""
        src = (Path(zp.__file__)).read_text(encoding="utf-8")
        self.assertIn("_replace_with_retry(tmp, asar)", src)
        self.assertNotIn("os.replace(tmp, asar)", src)


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

    def test_enhance_resolution_never_ships_models_from_disabled_providers(self):
        """★ 回归：跨机「HTTP 400 Model is unavailable」的根因。

        旧逻辑在 ref 档失败后会直接进兜底档，把请求打给「第一个带 baseURL 的
        自定义供应商」—— 与界面所选模型无关，且不检查该供应商是否可用
        （缺 key / 被 systemDisabledReason 禁用），于是 400。
        新逻辑必须先过 usable()（baseURL + apiKey + 未禁用），
        拿不出可用候选时返回可读原因，而不是构造一个注定失败的请求。
        """
        code = zp._enhance_main_block("j").decode()
        self.assertIn("function usable(", code, "必须存在可用性判定")
        self.assertIn("systemDisabledReason", code, "必须排除被系统禁用的供应商")
        # 四档解析都必须走 cand()/usable()，不能有任何一条绕过判定直接 pick
        self.assertIn('cand(pid,mid,"ref")', code)
        self.assertIn('cand(pid,mid,"ref-label")', code)
        self.assertIn('cand(pid,mid,"label")', code)
        self.assertIn('cand(c.pid,mid,"fallback")', code)
        self.assertNotIn('pick={pid:pid,mid:mid,p:pp};how="fallback"', code,
                         "兜底档不得绕过可用性判定")
        # 失败时必须给出原因与轨迹，前端才能做友好提示
        self.assertIn('code:"no-model"', code)
        self.assertIn("tried", code)

    def test_enhance_reads_authoritative_provider_config(self):
        """★ 回归：客户端真正发请求用的是 provider_config.json，不是 config.json。

        config.json 是遗留副本，可能滞后（供应商缺失、apiKey 过期、模型列表旧）。
        若只读 config.json，界面选中的 provider/model 可能根本查不到 →
        掉进兜底档 → 打到别的供应商 → HTTP 400 Model is unavailable。
        必须 provider_config.json 优先、config.json 兜底。
        """
        code = zp._enhance_main_block("j").decode()
        # 权威源必须被读取，且顺序在 config.json 之前
        self.assertIn('"provider_config.json"', code, "必须读取 provider_config.json")
        i_auth = code.index('"provider_config.json"')
        i_legacy = code.index('"config.json"')
        self.assertLess(i_auth, i_legacy,
                        "provider_config.json 必须在 config.json 之前读取（后者仅作兜底）")
        # 两个配置文件都要解析出候选
        for key in ("providerConfigRules", "providerRules", "personalModelIds", "modelConfigRules"):
            self.assertIn(key, code, f"provider_config.json 解析缺少 {key}")
        # 降级兜底必须把内置/账号级供应商排到最后（优先用户自定义供应商）
        self.assertIn("builtin:", code)
        self.assertIn("account:", code)
        self.assertRegex(code, r"\.sort\(function\(a,b\)\{",
                         "兜底候选需要排序，用户自定义供应商优先")

    def test_enhance_handler_classifies_errors_and_retries_only_retryable(self):
        """错误必须分类：model/auth/quota 不重试，超时/限流/5xx 才退避重试。"""
        code = zp._enhance_main_block("j").decode()
        self.assertIn("function classify(", code)
        for kind in ('"model"', '"quota"', '"auth"', '"path"', '"rate"', '"server"'):
            self.assertIn(f"kind:{kind}", code, f"缺少错误分类 {kind}")
        self.assertIn("if(!cls.retry)break;", code, "不可重试的错误必须立刻停")
        # 分类命中「模型不可用」的关键词（上游原样透传的英文/中文都要覆盖）
        self.assertIn("model is unavailable", code)
        self.assertIn("model_not_found", code)
        # 返回体要带 tip，前端据此给「可执行的下一步」
        self.assertIn("tip:fc.tip", code)
        self.assertRegex(code, r"for\(let cur of cs\)[\s\S]*setTimeout",
                         "重试前应有退避等待")

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


class TestCheckStateWording(unittest.TestCase):
    """check_state() 必须认得每一套 `--check` 输出措辞。

    锁死的是一个真实故障：`--reasoning-config --check` 打的是
    「[ ] …（新建规则）」/「[=] 档位配置已是最新，无需写入」，一个
    `已打 / 未打 / 不适用` 都没有 → 判定落回 unknown → sync 报
    「未处理: reasoning_config(状态未知)」，这个开关**既不会被写入也不会被还原**。
    注意不能用「已是最新」判 on：有待写入项时它也会打印（说的是另外 N 个已配好的模型）。
    """

    def _state(self, text: str) -> str:
        import sync

        class _R:
            returncode = 0
            stdout = text
            stderr = ""

        class _FakeSub:
            run = staticmethod(lambda *a, **kw: _R())

        orig = sync.subprocess
        sync.subprocess = _FakeSub          # 只换 sync 里的名字，不动全局 subprocess
        try:
            return sync.check_state(["--reasoning-config"])
        finally:
            sync.subprocess = orig

    def test_reasoning_config_pending_is_off(self):
        out = ("=== 3.14+ 原生档位配置（provider_config.json），模式：检查 ===\n"
               "    [ ] openai/tierflow  →  4 档 off/low/high/max  （新建规则，来源 config.optionSpecs）\n"
               "    已是最新 12 个：a/b, c/d\n")
        self.assertEqual(self._state(out), "off")

    def test_reasoning_config_uptodate_is_on(self):
        out = ("=== 3.14+ 原生档位配置（provider_config.json），模式：检查 ===\n"
               "    已是最新 12 个：a/b, c/d\n"
               "[=] F:\\ZcodeData\\.zcode\\v2\\provider_config.json\n"
               "    档位配置已是最新，无需写入\n")
        self.assertEqual(self._state(out), "on")

    def test_kernel_not_applicable_wins_over_reasoning_branch(self):
        """内核补丁的「不适用」必须优先判成 na（它的提示里也带「原生档位机制」）。"""
        out = "    [i] 该内核使用 3.14+ 原生档位机制（optionSpecs），本补丁不适用："
        self.assertEqual(self._state(out), "na")

    def test_byte_level_wording_still_works(self):
        self.assertEqual(self._state("    [=] a.js | 已打（每日趋势图）"), "on")
        self.assertEqual(self._state("    [ ] a.js | 未打（每日趋势图）"), "off")

    def test_unrecognized_output_is_unknown(self):
        self.assertEqual(self._state("完全看不懂的输出"), "unknown")


class TestProcessProbeDecodesSafely(unittest.TestCase):
    """进程探测器必须能同时接住 subprocess 返回的 str 和 bytes。

    为什么值得钉死：`_from_running_processes` 用 `errors="replace"` 调 subprocess ——
    这个参数会让 subprocess **直接把输出解码成 str**。而原实现写的是
    `out = subprocess.run(...).stdout or b""` 再 `out.decode(...)`，对 str 调 `.decode()`
    必然抛 `AttributeError`；偏偏外层是 `except Exception: return`，异常被静默吞掉，
    于是探测器**永远命中 0 个候选目录**。

    这个 bug 极难发现：注册表/常见目录两条探测器还在工作，功能「看起来是好的」，
    只是丢掉了最准的那条线索（用户实际在跑哪个安装）。本机因为注册表恰好命中，
    完全看不出来。所以这里直接用假数据喂进去，把「str 也要能处理」钉下来。
    """

    def _run_probe(self, stdout_value):
        import zcode_patcher as zp

        class _FakeProc:
            def __init__(self, out):
                self.stdout = out

        fake = _FakeProc(stdout_value)
        orig_run, orig_nt = zp.subprocess.run, zp.os.name
        zp.subprocess.run = lambda *a, **k: fake
        zp.os.name = "nt"
        try:
            found: list = []
            zp._from_running_processes(found)
            return found
        finally:
            zp.subprocess.run, zp.os.name = orig_run, orig_nt

    def test_handles_str_stdout(self):
        """errors="replace" 时 subprocess 给的就是 str —— 必须能解析出安装目录。"""
        lines = "C:\\WINDOWS\\system32\\ApplicationFrameHost.exe\r\nD:\\ZCode\\ZCode.exe\r\n"
        found = self._run_probe(lines)
        self.assertEqual([p.name for p in found], ["ZCode"],
                         f"str 输出没被解析出来（很可能又是 .decode() 抛异常被吞了）：{found}")

    def test_handles_bytes_stdout(self):
        """没传 errors 时仍是 bytes —— 老路径也不能回归。"""
        lines = b"D:\\ZCode\\ZCode.exe\r\nC:\\Other\\notepad.exe\r\n"
        found = self._run_probe(lines)
        self.assertEqual([p.name for p in found], ["ZCode"])

    def test_non_zcode_paths_are_ignored(self):
        found = self._run_probe("C:\\WINDOWS\\explorer.exe\n/usr/bin/python\n")
        self.assertEqual(found, [])


class TestNoConsoleWindowFlags(unittest.TestCase):
    """每个会起 console 子进程的调用都必须带「别弹控制台窗口」的标志。

    为什么值得钉死：钩子用 `--detach` 把 sync.py 拉成 `DETACHED_PROCESS|CREATE_NO_WINDOW`
    的后台 worker —— 也就是**没有控制台**。Windows 的语义是「无控制台的父进程创建 console
    子进程时，系统会新建一个控制台并**显示**出来」，于是 worker 里每调一次
    `check_state()` 就闪一个 cmd 窗口；而 `run_sync()` 会为每个开关调一次 —— 一个会话能弹 8 个以上。

    实测（本机探针，EnumWindows 统计可见控制台窗口数）：无控制台父进程
      * 不传 creationflags → 期间出现 **2 个**可见控制台窗口
      * 传 `CREATE_NO_WINDOW` → **0 个**
    注意 `capture_output=True` 挡不住它 —— 它管的是管道，不是控制台分配。

    `apply_after_exit.py` 早就知道这个坑（注释里写着「pythonw 无控制台…会每次新弹 cmd 窗口」），
    但 sync.py / zcode_patcher.py / doctor.py 漏了。这条测试就是防它再漏。

    允许两种写法：`**no_window_kwargs()`（推荐）或 `creationflags=...`。
    确实不需要的调用，在调用处同一行写 `# no-window-ok: <理由>` 显式豁免。
    """

    @staticmethod
    def _mask(src: str) -> str:
        """把注释与字符串**内容**替换成空格（保持长度与行号不变）。

        必须屏蔽：`_console.py` 的文档里就写着 `subprocess.run(capture_output=True)` 作为例子，
        不屏蔽会把它当成真实调用误报。标记 `# no-window-ok:` 是注释，所以判定时要用原文。
        """
        import io as _io
        import tokenize
        starts = [0]
        for ln in src.splitlines(keepends=True):
            starts.append(starts[-1] + len(ln))

        def off(pos):
            row, col = pos
            return starts[row - 1] + col if row - 1 < len(starts) else len(src)

        buf = list(src)
        try:
            for tok in tokenize.generate_tokens(_io.StringIO(src).readline):
                if tok.type not in (tokenize.COMMENT, tokenize.STRING):
                    continue
                for i in range(off(tok.start), min(off(tok.end), len(buf))):
                    if buf[i] != "\n":
                        buf[i] = " "
        except Exception:
            return src
        return "".join(buf)

    def _calls(self, masked: str, original: str):
        """找出所有 subprocess.run/Popen 调用：返回 (行号, 调用原文, 调用后同行尾巴)。"""
        found = []
        for m in re.finditer(r"subprocess\.(?:run|Popen)\(", masked):
            i, depth = m.end(), 1
            while i < len(masked) and depth:
                if masked[i] == "(":
                    depth += 1
                elif masked[i] == ")":
                    depth -= 1
                i += 1
            line_end = masked.find("\n", i)
            line_end = len(masked) if line_end == -1 else line_end
            found.append((masked[:m.start()].count("\n") + 1,
                          masked[m.start():i],            # 已屏蔽，用于判定
                          original[i:line_end]))          # 原文，用于读豁免标记
        return found

    def test_every_subprocess_call_suppresses_the_console_window(self):
        d = _HERE.parent / "skills" / "zcode-tokenspeed" / "scripts"
        if not d.is_dir():
            self.skipTest("非插件形态布局")
        offenders = []
        for p in sorted(d.glob("*.py")):
            src = p.read_text(encoding="utf-8")
            masked = self._mask(src)
            for line, text, tail in self._calls(masked, src):
                if "no_window_kwargs" in text or "creationflags" in text:
                    continue
                if "no-window-ok" in tail:
                    continue
                offenders.append("%s:%d  %s" % (p.name, line, text.splitlines()[0][:72]))
        self.assertEqual(
            offenders, [],
            "这些子进程调用会弹出可见控制台窗口，请加 **no_window_kwargs()"
            "（或在不必要时写 # no-window-ok: 理由）：\n  " + "\n  ".join(offenders))

    def test_no_window_helper_is_a_noop_off_windows(self):
        """helper 在非 Windows 上必须返回空 dict，否则会污染别的平台。"""
        import _console
        self.assertEqual(set(_console.no_window_kwargs()),
                         {"creationflags"} if os.name == "nt" else set())


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


class TestEnhancePromptScript(unittest.TestCase):
    """润色按钮的挂载逻辑：按钮绝不能渲染到会话消息区。

    历史 bug：`findInput()` 在**整个 document** 上按 `textarea` /
    `[contenteditable='true']` 这类通用选择器找「交互输入框」。但客户端的会话消息区
    （`[data-v4-timeline-scroll]` 内）也会出现这类节点，一旦命中就会：
      ① 把消息区元素误认成输入框 → 读写正文全错；
      ② 用它推导挂载点 → 按钮被插进消息流 → 表现为「按钮跑出输入框」。
    而且输入框 dock（`[data-v4-composer-dock]`）与消息层是**同级兄弟**，
    都位于滚动容器内，dock 仅靠 `sticky bottom-0` 贴底，所以插错位置后
    会随消息增长被推到列表底部。

    修法：所有查找先锚定 dock；兼容模式禁用会误伤消息区的通用选择器。
    """

    @classmethod
    def setUpClass(cls):
        cls.script = None
        for cand in (_HERE.parent / "scripts",
                     _HERE.parent / "skills" / "zcode-tokenspeed" / "scripts"):
            p = cand / "zcode-enhance-prompt.js"
            if p.is_file():
                cls.script = p
                break
        if cls.script is None:
            raise unittest.SkipTest("未找到润色脚本")

    def setUp(self):
        self.src = self.script.read_text(encoding="utf-8")

    def test_scopes_input_lookup_to_composer_dock(self):
        """必须先解析 dock，再在 dock 内找输入框。"""
        self.assertIn("function findDock(", self.src, "必须存在 dock 解析函数")
        self.assertIn("data-v4-composer-dock", self.src,
                      "dock 锚点应使用客户端的 data-v4-composer-dock")
        # findInput 内必须先拿到 dock，并用 dock.querySelectorAll 而不是 document
        body = self.src[self.src.index("function findInput("):]
        body = body[:body.index("\n  function readText")]
        self.assertIn("findDock()", body, "findInput 必须先锚定 dock")
        self.assertIn("dock.querySelectorAll", body, "输入框只在 dock 内查找")
        self.assertLess(body.index("dock.querySelectorAll"),
                        body.index("document.querySelectorAll"),
                        "dock 内查找必须排在全局查找之前")

    def test_fallback_never_uses_generic_selectors(self):
        """兼容模式的全局查找必须排除会误伤消息区的通用选择器。"""
        body = self.src[self.src.index("function findInput("):]
        body = body[:body.index("\n  function readText")]
        # 兼容分支里必须把 textarea / contenteditable 挡掉
        self.assertIn('sel === "textarea"', body)
        self.assertIn('[contenteditable=', body)
        self.assertIn("continue", body)
        # COMPOSER_INPUT_SELECTORS 里仍保留这些选择器（供 dock 内查找使用），
        # 但整个 document 直接用它就是 bug
        self.assertIn("COMPOSER_INPUT_SELECTORS", body)

    def test_host_is_validated_inside_dock(self):
        """挂载点最终必须在 dock 内，否则宁可不挂。"""
        body = self.src[self.src.index("function ensureButton("):]
        self.assertIn("dock.contains(host)", body,
                      "挂载点必须校验在 dock 内（否则会渲染进消息区）")
        self.assertIn("if (host && dock && !dock.contains(host)) host = dock;", body,
                      "越界的挂载点应回退到 dock 本身")

    def test_button_self_heals_when_detached_from_dock(self):
        """客户端重渲染会把按钮搬走 —— 必须能自动搬回来（且记账到 diag）。"""
        body = self.src[self.src.index("function ensureButton("):]
        self.assertIn("stillInDock", body, "需要判断按钮是否已脱离 dock")
        self.assertIn("reattaches", body, "自愈次数要记入诊断，便于线上确认")
        # 自愈分支：先记账，再 insertBefore 搬回，最后 return（不重复创建按钮）
        m = re.search(
            r"if \(!okPlace \|\| !stillInDock\) \{([\s\S]*?)\}", body)
        self.assertIsNotNone(m, "找不到「位置不对就搬回」的分支")
        branch = m.group(1)
        self.assertIn("reattaches", branch, "自愈分支要记账")
        self.assertIn("host.insertBefore(btn, host.firstChild)", branch,
                      "自愈分支要把按钮搬回挂载点")
        # 已连接的按钮分支不得重新 createElement（否则会重复插入）
        self.assertNotIn("createElement", branch, "自愈分支不应重建按钮")

    def test_no_unscoped_generic_query_remains(self):
        """全局 document 查询里不得再出现裸 textarea / contenteditable。"""
        for bad in ('document.querySelectorAll("textarea")',
                    "document.querySelectorAll('[contenteditable='",
                    'document.querySelectorAll("form textarea")'):
            self.assertNotIn(bad, self.src,
                             f"存在会误伤消息区的全局查询：{bad}")

    def test_mount_poll_is_tight_enough_for_streaming(self):
        """流式输出时消息持续增长，轮询周期必须够短，否则按钮会肉眼可见地错位。"""
        m = re.search(r"setInterval\(\(\) => \{ if \(!document\.hidden\) ensureButton\(\); \}, (\d+)\)",
                      self.src)
        self.assertIsNotNone(m, "找不到挂载轮询")
        self.assertLessEqual(int(m.group(1)), 1000,
                             "轮询周期过长：流式对话中按钮会长时间停在错误位置")


class TestDoctor(unittest.TestCase):
    """doctor.py 是「插件装了没生效」时的第一入口。它靠一批常量去定位安装目录、
    配置键和开关表——这些常量一旦和真实实现漂移，自检报告会指向错误的目录，
    比没有自检更误导。所以这里把它们和 plugin.json / sync.py 对齐钉死。"""

    def test_plugin_name_matches_manifest(self):
        import doctor
        manifest = json.loads((_HERE.parent / ".zcode-plugin" / "plugin.json")
                              .read_text(encoding="utf-8"))
        self.assertEqual(doctor.PLUGIN_NAME, manifest["name"],
                         "doctor 按 PLUGIN_NAME 前缀找安装目录与配置键，必须等于清单里的 name")

    def test_patch_key_table_covers_every_switch(self):
        import doctor
        import sync
        self.assertEqual({k for k, _label in doctor.PATCH_KEYS} | {"core_patch"},
                         {k for k, _args, _repack in sync.PATCHES},
                         "doctor 的开关表漏项 → 自检报告会漏掉某个功能的状态")

    def test_repack_set_matches_sync(self):
        """doctor 用这个集合区分「重打包级」补丁，必须与 sync.PATCHES 的第三列一致。"""
        import doctor
        import sync
        self.assertEqual(doctor.REPACK_KEYS, {k for k, _a, r in sync.PATCHES if r})

    def test_prefix_entries_only_matches_this_plugin(self):
        import doctor
        cfg = {"plugins": {
            "options": {"zcode-tokenspeed@some-market": {"tps_footer": True},
                        "other-plugin@m": {"x": 1}},
            "enabledPlugins": {"zcode-tokenspeed@some-market": True},
        }}
        self.assertEqual(list(doctor._prefix_entries(cfg, "options")),
                         ["zcode-tokenspeed@some-market"])
        self.assertEqual(list(doctor._prefix_entries(cfg, "enabledPlugins")),
                         ["zcode-tokenspeed@some-market"])
        # 缺失 / 类型异常都不能抛异常（config.json 是用户可手改的文件）
        self.assertEqual(doctor._prefix_entries({}, "options"), {})
        self.assertEqual(doctor._prefix_entries({"plugins": {"options": []}}, "options"), {})
        self.assertEqual(doctor._prefix_entries(None, "options"), {})

    def test_manifest_version_reads_both_layouts(self):
        import doctor
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / ".claude-plugin").mkdir()
            (root / ".claude-plugin" / "plugin.json").write_text(
                json.dumps({"name": "x", "version": "1.2.3"}), encoding="utf-8")
            self.assertEqual(doctor._manifest_version(root), "1.2.3")
            self.assertEqual(doctor._manifest_version(root / "nope"), "?")

    def test_json_mode_emits_parseable_report(self):
        """--json 是让用户「贴给别人看」的输出，必须是合法 JSON 且包含关键字段。"""
        import doctor
        r = subprocess.run([sys.executable, str(doctor.__file__), "--json"],
                           capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr[-400:])
        data = json.loads(r.stdout)
        for key in ("python", "plugin_dirs", "enabled", "options_saved", "hook_fired"):
            self.assertIn(key, data)


class TestSyncHeartbeat(unittest.TestCase):
    """心跳文件 _sync.last 是「钩子到底跑没跑」的唯一证据：
    没拨过开关时 sync 什么都不做、日志也是空的，「钩子没触发」与「触发了但无事可做」
    在日志里长得一模一样。所以必须保证它在任何一条提前返回的路径上都被写出来。"""

    def setUp(self):
        import sync
        self.sync = sync
        self._orig = (sync.CONFIG, sync.STAMP, sync.LOG, sync.MARKER)
        self._tmp = tempfile.TemporaryDirectory(prefix="zpatch-hb-", ignore_cleanup_errors=True)
        d = Path(self._tmp.name)
        sync.CONFIG = d / "config.json"       # 故意不存在
        sync.STAMP = d / "_sync.last"
        sync.LOG = d / "_sync.log"
        sync.MARKER = d / "_autoinject.done"  # 别把标记写进真实安装目录

    def tearDown(self):
        (self.sync.CONFIG, self.sync.STAMP, self.sync.LOG,
         self.sync.MARKER) = self._orig
        self._tmp.cleanup()

    def test_beat_writes_timestamp_and_message(self):
        self.sync.beat("单元测试")
        text = self.sync.STAMP.read_text(encoding="utf-8")
        self.assertIn("单元测试", text)
        self.assertRegex(text, r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}")

    def test_main_beats_when_config_is_missing(self):
        """从未点过「保存配置」时，心跳必须写明这次是按**插件清单默认值**自动注入的。

        这正是「安装成功但不生效」的根因：ZCode 只在用户点过保存之后才写
        `plugins.options`，旧逻辑把「没表态」当成「不要做」，于是装完重启什么都没发生，
        而用户在界面上看不出任何原因。现在退回清单默认值，装完即自动注入。
        """
        with patcher_stubbed(self.sync) as calls:
            quiet(self.sync.main)
        text = self.sync.STAMP.read_text(encoding="utf-8")
        self.assertTrue(text.strip(), "心跳文件不能为空")
        self.assertIn("从未保存过开关", text)
        self.assertEqual(calls, [], "开关都已是目标状态时不应产生任何写入")

    def test_main_beats_when_nothing_to_do(self):
        """配置存在但所有开关都已一致时，也要留下心跳并写明「无需改动」。"""
        self.sync.CONFIG.write_text(json.dumps(
            {"plugins": {"options": {"zcode-tokenspeed@m": {}}}}), encoding="utf-8")
        with patcher_stubbed(self.sync):
            quiet(self.sync.main)
        text = self.sync.STAMP.read_text(encoding="utf-8")
        self.assertTrue(text.strip(), "心跳文件不能为空")


class TestSyncModes(unittest.TestCase):
    """钩子必须是「登记心跳 + 后台化」就立刻返回。

    为什么：hook 是**内联**执行的（`async` 字段当前无运行时效果），而同步一次要跑多次
    `--check`（每次约 2 秒）。同步做完再返回会把会话启动硬生生拖住，还可能撞上钩子的
    超时上限被砍掉——表现就是「什么都没发生」。所以 --detach 必须只做两件事：写心跳、
    拉起后台进程，然后立刻退出。
    """

    def setUp(self):
        import sync
        self.sync = sync
        self._orig = (sync.CONFIG, sync.STAMP, sync.LOG, sync.MARKER, list(sys.argv))
        self._tmp = tempfile.TemporaryDirectory(prefix="zpatch-mode-", ignore_cleanup_errors=True)
        d = Path(self._tmp.name)
        sync.CONFIG = d / "config.json"       # 故意不存在
        sync.STAMP = d / "_sync.last"
        sync.LOG = d / "_sync.log"
        sync.MARKER = d / "_autoinject.done"  # 别把标记写进真实安装目录

    def tearDown(self):
        (self.sync.CONFIG, self.sync.STAMP, self.sync.LOG,
         self.sync.MARKER) = self._orig[:4]
        sys.argv[:] = self._orig[4]
        self._tmp.cleanup()

    def _run(self, *args):
        sys.argv[:] = ["sync.py", *args]
        quiet(self.sync.main)

    def test_detach_spawns_worker_and_returns(self):
        spawned = []
        orig = self.sync.spawn_detached
        self.sync.spawn_detached = lambda extra: (spawned.append(extra), True)[1]
        try:
            with patcher_stubbed(self.sync):
                self._run("--detach")
        finally:
            self.sync.spawn_detached = orig
        self.assertEqual(spawned, [["--worker", "--from-hook"]])
        self.assertIn("[钩子]", self.sync.STAMP.read_text(encoding="utf-8"))

    def test_detach_falls_back_to_foreground_when_spawn_fails(self):
        """后台起不来（比如被安全软件拦）时必须自己把活干完，而不是静默失败。"""
        orig = self.sync.spawn_detached
        self.sync.spawn_detached = lambda extra: False
        try:
            with patcher_stubbed(self.sync):
                self._run("--detach")
        finally:
            self.sync.spawn_detached = orig
        text = self.sync.STAMP.read_text(encoding="utf-8")
        self.assertTrue(text.strip(), "兜底路径也必须留下心跳")
        self.assertIn("从未保存过开关", text)

    def test_worker_records_the_full_chain(self):
        """心跳要能证明「钩子 → 后台」这条链，否则看不出是钩子拉起来的。"""
        with patcher_stubbed(self.sync):
            self._run("--worker", "--from-hook")
        self.assertIn("[钩子→后台]", self.sync.STAMP.read_text(encoding="utf-8"))

    def test_hook_mode_never_writes_to_stdout(self):
        """钩子的 stdout 会被按严格 JSON schema 校验：输出非 JSON 会被判为「运行失败」。
        脚本副作用虽已生效，但日志里会留下假故障，所以后台路径必须一声不吭。"""
        buf = io.StringIO()
        with patcher_stubbed(self.sync):
            with contextlib.redirect_stdout(buf):
                self.sync.run_sync(echo=False)
        self.assertEqual(buf.getvalue(), "")


class TestAutoInject(unittest.TestCase):
    """零配置自动注入 —— 「从插件市场装完就能用」这条要求就靠它落地。

    ZCode 的插件清单里**没有安装时钩子**（`plugin-json-spec.md` 只允许声明
    `skills` / `commands` / `hooks` / `mcpServers`），所以最早能自动触发的时机是
    「下一次会话启动」的 `SessionStart`。在这条硬约束下，让「装完即生效」成立只能靠
    一件事：**没保存过开关时按插件清单里声明的默认值注入**。
    下面把这条链路的每一环都钉住，避免哪天有人把默认值改回 false 又变回「装了没生效」。
    """

    def setUp(self):
        import sync
        self.sync = sync
        self._tmp = tempfile.TemporaryDirectory(prefix="zpatch-auto-", ignore_cleanup_errors=True)
        self._d = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _resolve(self, opts, source, defaults):
        """在受控输入下跑 resolve_wanted()。"""
        orig = (self.sync.read_options, self.sync.declared_defaults)
        self.sync.read_options = lambda: (opts, source)
        self.sync.declared_defaults = lambda: defaults
        try:
            return self.sync.resolve_wanted()
        finally:
            self.sync.read_options, self.sync.declared_defaults = orig

    def _switch_keys(self):
        return {key for key, _args, _repack in self.sync.PATCHES}

    # ---------------------------------------------------------------- 清单契约

    def test_manifest_declares_every_switch_on_by_default(self):
        """**这是「装完即用」的根契约**：清单里每个开关的 default 都必须是 true。

        插件市场安装后，ZCode 的配置里根本没有 `plugins.options` 这一项（用户没点过
        「保存配置」）。若默认值是 false，自动注入就变成「按默认值什么都不做」，
        用户看到的还是「安装成功但没生效」——正是这条要求要消灭的情形。
        """
        manifest = json.loads((_HERE.parent / ".zcode-plugin" / "plugin.json")
                              .read_text(encoding="utf-8"))
        uc = manifest.get("userConfig") or {}
        self.assertEqual(set(uc), self._switch_keys(),
                         "plugin.json 的 userConfig 必须与 sync.PATCHES 一一对应")
        off = sorted(k for k, v in uc.items() if v.get("default") is not True)
        self.assertEqual(off, [], f"这些开关的 default 不是 true，装完不会自动生效: {off}")

    def test_declared_defaults_reads_the_real_manifest(self):
        got = self.sync.declared_defaults()
        self.assertEqual(set(got), self._switch_keys())
        self.assertTrue(all(got.values()), f"默认值应全为 true，实际 {got}")

    def test_declared_defaults_skips_non_boolean_entries(self):
        """userConfig 里可能混着非开关项（字符串/枚举），不能当成开关塞进结果。"""
        root = self._d / "plug"
        (root / ".zcode-plugin").mkdir(parents=True)
        (root / ".zcode-plugin" / "plugin.json").write_text(json.dumps({
            "name": "x",
            "userConfig": {"a": {"default": True}, "b": {"default": "high"},
                           "c": {"default": False}, "d": {"no_default": 1}},
        }), encoding="utf-8")
        scripts = root / "skills" / "zcode-tokenspeed" / "scripts"
        scripts.mkdir(parents=True)
        orig = self.sync.HERE
        self.sync.HERE = scripts
        try:
            self.assertEqual(self.sync.declared_defaults(), {"a": True, "c": False})
        finally:
            self.sync.HERE = orig

    def test_declared_defaults_without_manifest_is_empty(self):
        scripts = self._d / "lonely" / "skills" / "zcode-tokenspeed" / "scripts"
        scripts.mkdir(parents=True)
        orig = self.sync.HERE
        self.sync.HERE = scripts
        try:
            self.assertEqual(self.sync.declared_defaults(), {})
        finally:
            self.sync.HERE = orig

    # ------------------------------------------------------------ 合并优先级

    def test_never_saved_config_falls_back_to_defaults(self):
        """从未保存过开关 → 全部按默认值注入。这就是零配置自动注入本身。"""
        wanted, origin = self._resolve({}, None, {"a": True, "b": True})
        self.assertEqual(wanted, {"a": True, "b": True})
        self.assertIn("从未保存过开关", origin)

    def test_saved_value_wins_over_default(self):
        """保存过的值优先——用户明确关掉的开关不能被默认值悄悄打开。"""
        wanted, _ = self._resolve({"a": False}, "config", {"a": True, "b": True})
        self.assertEqual(wanted, {"a": False, "b": True})

    def test_missing_key_is_filled_from_defaults(self):
        """插件升级新增开关时，老配置里没有这个键 → 补默认值，不会漏注入。"""
        wanted, _ = self._resolve({"a": False}, "config", {"a": True, "new": True})
        self.assertEqual(wanted, {"a": False, "new": True})

    def test_explicit_false_survives_as_revert(self):
        """显式关掉必须是 false（触发还原），不能被当成「没表态」。"""
        wanted, origin = self._resolve({"a": False}, "config", {"a": True})
        self.assertIs(wanted["a"], False)
        self.assertEqual(origin, "config")

    def test_nothing_available_means_no_operation(self):
        """配置读不到 + 清单也读不到 → 只能什么都不做（并如实说明）。"""
        self.assertEqual(self._resolve({}, None, {}), ({}, ""))
        self.assertEqual(self._resolve({"a": "high"}, "config", {}), ({}, ""))

    # ------------------------------------------------------ 第三态 na（不适用）

    def test_check_state_reports_not_applicable(self):
        """≤3.11 专用的内核补丁在 3.14+ 会打印「不适用」，必须单独成一态。

        否则它会被当成「未打」→ 去执行 → 脚本空转一圈什么都没做，
        最后却被报成「已生效」——默认值全开之后这个误报每次装完都会出现。
        """
        cases = {"本补丁不适用（ZCode 3.14+ 已原生支持）": "na",
                 "[!] 未打": "off", "已打": "on", "看不懂的输出": "unknown"}
        for text, want in cases.items():
            with self.subTest(text=text):
                orig = self.sync.subprocess.run
                self.sync.subprocess.run = lambda *a, **k: subprocess.CompletedProcess(
                    [], 0, stdout=text, stderr="")
                try:
                    self.assertEqual(self.sync.check_state([]), want)
                finally:
                    self.sync.subprocess.run = orig

    def test_run_sync_skips_switches_that_do_not_apply(self):
        """na 的开关既不执行也不报错，只在结论里注明「本版本不适用」。"""
        orig = (self.sync.read_options, self.sync.declared_defaults)
        self.sync.read_options = lambda: ({"core_patch": True}, "config")
        self.sync.declared_defaults = lambda: {}
        try:
            with patcher_stubbed(self.sync, {(): "na"}) as calls:
                summary = self.sync.run_sync(echo=False)
        finally:
            self.sync.read_options, self.sync.declared_defaults = orig
        self.assertEqual(calls, [], "不适用的补丁不应被执行")
        self.assertIn("本版本不适用", summary)
        self.assertNotIn("已写入", summary)

    def test_na_is_skipped_even_when_user_asked_for_it(self):
        """用户显式打开也不该去跑不适用的补丁——照样只标注、不执行。"""
        orig = (self.sync.read_options, self.sync.declared_defaults)
        self.sync.read_options = lambda: ({"core_patch": False}, "config")
        self.sync.declared_defaults = lambda: {}
        try:
            with patcher_stubbed(self.sync, {(): "na"}) as calls:
                summary = self.sync.run_sync(echo=False)
        finally:
            self.sync.read_options, self.sync.declared_defaults = orig
        self.assertEqual(calls, [])
        self.assertIn("本版本不适用", summary)

    # ------------------------------------------- 被客户端拒绝 → 转交退出后看护

    def _resolve_only(self, opts):
        """把 read_options / declared_defaults 固定住，只观察 run_sync 的动作。"""
        orig = (self.sync.read_options, self.sync.declared_defaults)
        self.sync.read_options = lambda: (opts, "config")
        self.sync.declared_defaults = lambda: {}
        return orig

    def test_refused_write_is_deferred_not_reported_as_failure(self):
        """客户端在运行 → zcode_patcher.py 直接拒绝写入（exit 2）。

        这不是失败，而是「现在不能写，等退出后写」：必须转交 apply_after_exit.py。
        否则用户看到的就是「未处理: xxx(执行失败)」而**永远不生效** ——
        SessionStart 钩子必然在 ZCode 运行中触发，所以这条路是常态而非例外。
        """
        orig = self._resolve_only({"reasoning_config": True})
        try:
            with patcher_stubbed(self.sync,
                                 {("--reasoning-config",): "off"},
                                 {("--reasoning-config",): "refused"}) as calls:
                summary = self.sync.run_sync(echo=False)
        finally:
            self.sync.read_options, self.sync.declared_defaults = orig
        self.assertEqual(calls, [("run", ("--reasoning-config",), False),
                                 ("watchdog", {"reasoning_config": True})])
        self.assertNotIn("未处理", summary)
        self.assertIn("ZCode 退出时写入", summary)

    def test_hard_failure_is_still_reported(self):
        """真正的失败（锚点不匹配等）不能被「被拒」这条新分支吞掉。"""
        orig = self._resolve_only({"reasoning_config": True})
        try:
            with patcher_stubbed(self.sync,
                                 {("--reasoning-config",): "off"},
                                 {("--reasoning-config",): "fail"}) as calls:
                summary = self.sync.run_sync(echo=False)
        finally:
            self.sync.read_options, self.sync.declared_defaults = orig
        self.assertIn("未处理", summary)
        self.assertEqual([c[0] for c in calls], ["run"], "失败不该起看护")

    def test_env_option_keys_are_lowercased(self):
        """Windows 上 os.environ 的键名是**大写**，读配置时必须归一。

        不归一的话 `ZCODE_PLUGIN_CONFIG_reasoning_config` 会变成 `REASONING_CONFIG`，
        与开关名对不上 → `{**defaults, **explicit}` 里默认值（全 true）胜出，
        **用户保存的开关会被整体忽略**（静默失效，最难查）。
        """
        from unittest import mock
        env = {"ZCODE_PLUGIN_CONFIG_REASONING_CONFIG": "false",
               "ZCODE_PLUGIN_CONFIG_USAGE_CHART": "true"}
        with mock.patch.dict("os.environ", env, clear=False):
            opts, source = self.sync.read_options()
        self.assertEqual(source, "env")
        self.assertEqual(opts, {"reasoning_config": False, "usage_chart": True})

    def test_watchdog_knows_every_switch(self):
        """apply_after_exit 的参数表必须覆盖 sync.PATCHES 的每一个键。

        漏一个键的后果：`--want=<键>=on` 被 parse_wants 当「未知开关」忽略，
        那个开关**开不起来也关不干净**（enhance_prompt 就漏过这一条）。
        """
        import apply_after_exit as aae
        self.assertEqual({k for k, _a, _r in self.sync.PATCHES} - set(aae.PATCH_ARGS),
                         set(), "apply_after_exit.PATCH_ARGS 漏了开关，退出后看护会静默忽略它")

    # ------------------------------------------------------ 会话提示（可见性）

    def test_notice_is_a_schema_valid_hook_output(self):
        """提示必须是一个「以 { 开头的合法 JSON 对象」，且只带 additionalContext。

        内核只在 stdout 以 `{` 开头时才解析（`wQs()` 里 `startsWith("{")`），
        并按 zod schema 严格校验；`hookSpecificOutput` 还要核对 `hookEventName`
        与本次事件一致，写错会把这次钩子标成失败。所以只用被**无条件**消费的
        顶层 `additionalContext`（`Lio()` 里 `t.additionalContext && …push(…)`）。
        """
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.sync.emit_notice()
        raw = buf.getvalue()
        self.assertTrue(raw.startswith("{"), f"必须以 {{ 开头才会被解析: {raw[:40]!r}")
        data = json.loads(raw)
        self.assertEqual(list(data), ["additionalContext"], "只允许这一个字段")
        self.assertIsInstance(data["additionalContext"], str)
        self.assertIn(self.sync.NOTICE_HEAD, data["additionalContext"])

    def test_notice_explains_the_exit_requirement(self):
        """提示要讲清「八项都要完全退出 ZCode 才写入」——否则用户会以为没生效。"""
        notice = self.sync.build_notice()
        self.assertIn("完全退出 ZCode", notice)
        self.assertIn("自动把 ZCode 重新拉起", notice)
        self.assertIn("doctor.py", notice)

    def test_first_auto_inject_fires_exactly_once(self):
        """提示只出一次，之后每次开会话都弹就成了噪声。"""
        orig = self.sync.MARKER
        self.sync.MARKER = self._d / "_autoinject.done"
        try:
            self.assertTrue(self.sync.first_auto_inject())
            self.assertFalse(self.sync.first_auto_inject())
            self.assertTrue(self.sync.MARKER.exists())
        finally:
            self.sync.MARKER = orig

    def test_first_auto_inject_survives_unwritable_marker(self):
        """标记写不进去（只读安装目录）时不能抛异常，也不能每次都当首次而反复提示。"""
        orig = self.sync.MARKER
        self.sync.MARKER = self._d / "nope" / "x" / "_autoinject.done"
        try:
            self.assertFalse(self.sync.first_auto_inject())
        finally:
            self.sync.MARKER = orig

    def test_detach_emits_notice_only_on_the_first_session(self):
        """整条钩子链路：第一次开会话出提示，第二次安静。"""
        import sync
        orig = (sync.CONFIG, sync.STAMP, sync.LOG, sync.MARKER, sync.spawn_detached,
                list(sys.argv))
        d = self._d / "hook"
        d.mkdir()
        sync.CONFIG = d / "config.json"
        sync.STAMP = d / "_sync.last"
        sync.LOG = d / "_sync.log"
        sync.MARKER = d / "_autoinject.done"
        sync.spawn_detached = lambda extra: True
        try:
            outs = []
            with patcher_stubbed(sync):
                for _ in range(2):
                    sys.argv[:] = ["sync.py", "--detach"]
                    buf = io.StringIO()
                    with contextlib.redirect_stdout(buf):
                        sync.main()
                    outs.append(buf.getvalue())
            self.assertTrue(outs[0].startswith("{"), "首次必须给出提示")
            self.assertEqual(outs[1], "", "第二次不该再提示")
        finally:
            (sync.CONFIG, sync.STAMP, sync.LOG, sync.MARKER,
             sync.spawn_detached) = orig[:5]
            sys.argv[:] = orig[5]


class TestDoctorDiscovery(unittest.TestCase):
    """doctor 必须能认出「插件根 = 市场根」这种安装（marketplace.json 里 `source: "./"`）。

    最初的实现只找 `<市场>/<插件名>/` 子目录——本地 directory 来源的市场恰好长这样，
    所以在本机"看起来是对的"；但 GitHub 来源的市场是把仓库整个 clone 下来，插件根就是
    市场根，根本没有同名子目录。这会在用户机器上误报「插件没装成功」，把人带偏。
    """

    def _with_storage(self, storage: Path):
        import doctor
        orig = doctor._storage_roots
        doctor._storage_roots = lambda: [storage]
        try:
            return doctor._installed_plugin_dirs()
        finally:
            doctor._storage_roots = orig

    def _make_plugin(self, root: Path, version: str = "9.9.9") -> None:
        (root / ".zcode-plugin").mkdir(parents=True, exist_ok=True)
        (root / ".zcode-plugin" / "plugin.json").write_text(
            json.dumps({"name": "zcode-tokenspeed", "version": version}), encoding="utf-8")

    def test_finds_plugin_whose_root_is_the_marketplace_root(self):
        with tempfile.TemporaryDirectory() as d:
            storage = Path(d)
            root = storage / "cli" / "plugins" / "marketplaces" / "zcode-toolkit-abc123"
            self._make_plugin(root)
            self.assertEqual(self._with_storage(storage), [root])

    def test_finds_plugin_in_a_subdirectory(self):
        with tempfile.TemporaryDirectory() as d:
            storage = Path(d)
            root = storage / "cli" / "plugins" / "marketplaces" / "some-market" / "plugins" / "x"
            self._make_plugin(root)
            self.assertEqual(self._with_storage(storage), [root])

    def test_ignores_plugins_with_other_names(self):
        with tempfile.TemporaryDirectory() as d:
            storage = Path(d)
            other = storage / "cli" / "plugins" / "marketplaces" / "m" / "other-plugin"
            (other / ".zcode-plugin").mkdir(parents=True)
            (other / ".zcode-plugin" / "plugin.json").write_text(
                json.dumps({"name": "other-plugin"}), encoding="utf-8")
            self.assertEqual(self._with_storage(storage), [])

    def test_manifest_version_reads_from_found_root(self):
        with tempfile.TemporaryDirectory() as d:
            storage = Path(d)
            root = storage / "cli" / "plugins" / "marketplaces" / "m"
            self._make_plugin(root, "1.2.3")
            import doctor
            self.assertEqual(doctor._manifest_version(root), "1.2.3")

    def test_finds_plugin_in_cache_version_dir(self):
        """用户机器上的真实落点：GitHub 来源的市场被缓存成
        `cache/<市场名>/<插件名>/<版本>/` —— 插件根在**版本目录**里。

        实测本机 `cache/zcode-plugins-official/computer-use/0.5.13/.zcode-plugin/plugin.json`。
        用户从 `cache/zcode-toolkit` 敲 `python skills/.../doctor.py` 报 No such file，
        就是因为那一层是**市场目录**，根本没有 skills/。
        """
        with tempfile.TemporaryDirectory() as d:
            storage = Path(d)
            root = (storage / "cli" / "plugins" / "cache"
                    / "zcode-toolkit" / "zcode-tokenspeed" / "0.5.2")
            self._make_plugin(root, "0.5.2")
            self.assertEqual(self._with_storage(storage), [root])

    def test_cache_dir_itself_is_not_a_plugin_root(self):
        """市场目录那一层（`cache/zcode-toolkit`）不该被认成插件根。"""
        with tempfile.TemporaryDirectory() as d:
            storage = Path(d)
            market = storage / "cli" / "plugins" / "cache" / "zcode-toolkit"
            (market / "zcode-tokenspeed" / "0.5.2").mkdir(parents=True)
            self.assertEqual(self._with_storage(storage), [])

    def test_multiple_cached_versions_all_found(self):
        """缓存里会堆积多个版本（本机实测 4 个 zcode-patcher），要全都报出来。"""
        import doctor
        with tempfile.TemporaryDirectory() as d:
            storage = Path(d)
            base = storage / "cli" / "plugins" / "cache" / "m" / "zcode-tokenspeed"
            for v in ("0.5.1", "0.5.2"):
                self._make_plugin(base / v, v)
            found = self._with_storage(storage)
            self.assertEqual(len(found), 2)
            self.assertEqual(sorted(doctor._manifest_version(p) for p in found),
                             ["0.5.1", "0.5.2"])

    def _capture_where(self, storage: Path, verbose: bool = False) -> str:
        """跑一次 --where 并把它打印的内容抓回来。"""
        import doctor
        orig = doctor._storage_roots
        doctor._storage_roots = lambda: [storage]
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                rc = doctor.print_where(verbose=verbose)
        finally:
            doctor._storage_roots = orig
        self.assertEqual(rc, 0)
        return buf.getvalue()

    def test_where_mode_lists_hits_and_prints_usable_command(self):
        """`--where` 存在的意义：用户照着 README 敲相对路径失败时，一条命令
        告诉他脚本到底在哪、以及该用哪个绝对路径。

        默认**只列命中项**：本机实测候选目录有 120 个（claude-plugins-official 一家
        就几十个插件），全列出来会把答案淹没。
        """
        with tempfile.TemporaryDirectory() as d:
            storage = Path(d)
            root = (storage / "cli" / "plugins" / "cache"
                    / "zcode-toolkit" / "zcode-tokenspeed" / "0.5.2")
            self._make_plugin(root, "0.5.2")
            # 一堆噪声：别的插件不该出现在默认输出里
            for n in ("android-emulator", "browser-use", "computer-use"):
                other = storage / "cli" / "plugins" / "cache" / "zcode-plugins-official" / n / "0.1.0"
                (other / ".zcode-plugin").mkdir(parents=True)
                (other / ".zcode-plugin" / "plugin.json").write_text(
                    json.dumps({"name": n, "version": "0.1.0"}), encoding="utf-8")
            out = self._capture_where(storage)
            self.assertIn(str(root), out)
            self.assertIn("0.5.2", out)
            self.assertIn("doctor.py", out)          # 给出了可直接复制的命令
            self.assertIn(str(Path("skills") / "zcode-tokenspeed" / "scripts"), out)
            self.assertNotIn("browser-use", out)     # 默认不列别人的插件
            self.assertIn("--where-all", out)        # 想看全量时告诉用户怎么开

    def test_where_all_lists_every_candidate(self):
        with tempfile.TemporaryDirectory() as d:
            storage = Path(d)
            other = storage / "cli" / "plugins" / "cache" / "zcode-plugins-official" / "browser-use" / "0.1.0"
            (other / ".zcode-plugin").mkdir(parents=True)
            (other / ".zcode-plugin" / "plugin.json").write_text(
                json.dumps({"name": "browser-use", "version": "0.1.0"}), encoding="utf-8")
            out = self._capture_where(storage, verbose=True)
            self.assertIn("browser-use", out)

    def test_where_mode_says_not_installed_when_no_hit(self):
        with tempfile.TemporaryDirectory() as d:
            storage = Path(d)
            (storage / "cli" / "plugins" / "cache" / "m" / "other" / "1.0.0").mkdir(parents=True)
            out = self._capture_where(storage)
            self.assertIn("没装成功", out)

    def test_where_flag_is_wired_into_cli(self):
        """--where 必须真能从命令行跑通（check_plugin 的提示里已经写了它）。"""
        import doctor
        r = subprocess.run([sys.executable, str(doctor.__file__), "--where"],
                           capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr[-400:])
        self.assertIn("插件位置扫描", r.stdout)


class TestConsoleEncoding(unittest.TestCase):
    """中文 Windows 的控制台代码页是 cp936（GBK）。输出**走管道**时（`> log.txt`、
    `subprocess.run(capture_output=True)`）Python 不再走 WriteConsoleW，而是按 cp936 编码 ——
    这时 print 一个 GBK 里没有的字符会抛 UnicodeEncodeError，**把整段输出打断**。

    真实故障：doctor.py 捕获 zcode_patcher.py 的输出，汇总表在
    `✓ 用量页去截断补丁` 那一行崩掉，用户只看到半张表 + traceback，
    还以为是补丁本身失败。交互式控制台不受影响，所以这个坑**只在管道里露头**，
    平时手动跑脚本永远测不出来。
    """

    def test_marks_are_printable_in_the_current_stdout_encoding(self):
        import _console
        enc = getattr(sys.stdout, "encoding", None) or "ascii"
        for mark in (_console.ok_mark(), _console.bad_mark(), _console.warn_mark()):
            mark.encode(enc)      # 编不出来就会抛 UnicodeEncodeError

    def test_glyph_falls_back_when_encoding_cannot_represent_it(self):
        import _console
        buf = io.BytesIO()
        orig = sys.stdout
        sys.stdout = io.TextIOWrapper(buf, encoding="cp936", errors="strict")
        try:
            self.assertEqual(_console.glyph("✓", "v"), "v")
            self.assertEqual(_console.glyph("✗", "x"), "x")
            self.assertEqual(_console.glyph("⚠", "!"), "!")
            self.assertEqual(_console.glyph("✓"), "v")          # 走内置备选表
            self.assertEqual(_console.glyph("→", "->"), "→")     # cp936 里有 →，不该降级
        finally:
            sys.stdout = orig

    def test_safe_stdio_replaces_instead_of_crashing(self):
        """errors=replace 是最后一道防线：任何编不出的字符都降级，绝不抛异常。"""
        import _console
        buf = io.BytesIO()
        orig = sys.stdout
        sys.stdout = io.TextIOWrapper(buf, encoding="cp936", errors="strict")
        try:
            _console.safe_stdio()
            self.assertEqual(sys.stdout.errors, "replace")
            print("✓✗⚠ 混在中文里也不该崩")
            sys.stdout.flush()
            raw = buf.getvalue()      # 必须在换回 stdout 前读，否则 wrapper 被 GC 时连 buf 一起关掉
        finally:
            sys.stdout = orig
        self.assertIn("不该崩".encode("cp936"), raw)

    def test_safe_stdio_is_idempotent(self):
        import _console
        _console.safe_stdio()
        _console.safe_stdio()     # 重复调用不该抛（reconfigure 有状态）

    def test_patcher_summary_survives_a_cp936_pipe(self):
        """端到端回归：把子进程 stdout 强制成 cp936，汇总表必须完整打出来。

        这正是用户报的那次故障 —— 没有 _console 的话，这一行会抛
        `UnicodeEncodeError: 'gbk' codec can't encode character '\\u2713'`。
        """
        try:
            if not zp.resolve_target(None):
                self.skipTest("本机没有 ZCode，跳过端到端编码回归")
        except SystemExit:
            self.skipTest("本机没有 ZCode，跳过端到端编码回归")
        env = dict(os.environ, PYTHONIOENCODING="cp936")
        r = subprocess.run([sys.executable, str(zp.__file__), "--all", "--check"],
                           capture_output=True, encoding="cp936", errors="replace",
                           cwd=str(Path(zp.__file__).resolve().parent), timeout=300, env=env)
        out = (r.stdout or "") + (r.stderr or "")
        self.assertNotIn("UnicodeEncodeError", out, out[-600:])
        self.assertNotIn("Traceback", out, out[-600:])
        self.assertIn("执行汇总", out)
        self.assertIn("合计 8 项", out)


class TestZcodeLogScan(unittest.TestCase):
    """doctor 会读 ZCode 自己的 jsonl 日志 —— 那里有比心跳文件更靠前的一层证据：
    `bootstrap.app.startup.plugins.completed` 的 `hookCount`（这次启动注册了几个钩子）。

    用户实测的那份报告里，心跳为空、补丁全未打，结论只能说「钩子从未运行过」，
    然后甩一张四选一清单。但日志里 hookCount=0 已经说明：
    那次启动 ZCode 根本没把钩子挂上，「钩子没跑」是必然结果，与钩子写法无关。
    """

    @staticmethod
    def _write_log(d: Path, startups, phases: int = 1) -> list[Path]:
        lines = []
        for ts, hooks, enabled, diag in startups:
            lines.append(json.dumps({
                "timestamp": ts, "level": "info",
                "event": "bootstrap.app.startup.plugins.completed",
                "message": "ZCode plugins resolved",
                "context": {"startupKind": "zcode_app", "pluginCount": 14,
                            "enabledPluginCount": enabled, "hookCount": hooks,
                            "diagnosticCount": diag, "skillRootCount": 9},
            }, ensure_ascii=False))
        for _ in range(phases):
            lines.append(json.dumps({
                "timestamp": "2026-09-22T01:00:00.000Z", "level": "info",
                "event": "turn.phase.completed", "message": "Turn phase completed",
                "context": {"queryId": "q", "turnNumber": 0, "phase": "session_start_hooks"},
            }, ensure_ascii=False))
        p = d / "zcode-2026-09-22.jsonl"
        p.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return [p]

    def test_scan_reads_hook_count_and_phase_count(self):
        import doctor
        with tempfile.TemporaryDirectory() as d:
            files = self._write_log(Path(d), [("2026-09-22T01:00:00.000Z", 0, 11, 0),
                                              ("2026-09-22T02:00:00.000Z", 1, 12, 0)])
            data = doctor._scan_zcode_log(files)
        self.assertEqual(len(data["startups"]), 2)
        self.assertEqual(data["startups"][-1]["hooks"], 1)
        self.assertEqual(data["startups"][-1]["enabled"], 12)
        self.assertEqual(data["startups"][-1]["kind"], "zcode_app")
        self.assertEqual(data["phase_count"], 1)

    def test_scan_survives_garbage_lines(self):
        """日志是逐行追加的，写到一半被截断很正常，不能因此让自检崩掉。"""
        import doctor
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "zcode-2026-09-22.jsonl"
            p.write_text('{"hookCount" 这不是 json\n随机一行\n', encoding="utf-8")
            data = doctor._scan_zcode_log([p])
        self.assertEqual(data["startups"], [])
        self.assertEqual(data["phase_count"], 0)

    def test_report_flags_zero_hooks_as_the_cause(self):
        """hookCount=0 时要直接点明「必然结果」，而不是甩一张四选一清单。"""
        import doctor
        with tempfile.TemporaryDirectory() as d:
            files = self._write_log(Path(d), [("2026-09-22T02:00:00.000Z", 0, 11, 0)])
            orig = doctor._latest_zcode_logs
            doctor._latest_zcode_logs = lambda *a, **kw: files
            buf = io.StringIO()
            try:
                with contextlib.redirect_stdout(buf):
                    data = doctor.report_zcode_log()
            finally:
                doctor._latest_zcode_logs = orig
        out = buf.getvalue()
        self.assertIn("0 个钩子", out)
        self.assertIn("不是钩子本身", out)
        self.assertEqual(data["startups"][-1]["hooks"], 0)

    def test_report_says_plugin_side_is_ready_when_hooks_registered(self):
        import doctor
        with tempfile.TemporaryDirectory() as d:
            files = self._write_log(Path(d), [("2026-09-22T02:00:00.000Z", 2, 12, 0)])
            orig = doctor._latest_zcode_logs
            doctor._latest_zcode_logs = lambda *a, **kw: files
            buf = io.StringIO()
            try:
                with contextlib.redirect_stdout(buf):
                    doctor.report_zcode_log()
            finally:
                doctor._latest_zcode_logs = orig
        out = buf.getvalue()
        self.assertIn("已经就绪", out)
        self.assertIn("CLAUDE_PLUGIN_ROOT", out)     # 注册了但没执行 → 才轮到查这个

    def test_verdict_uses_log_evidence_when_hook_never_ran(self):
        import doctor
        log_info = {"startups": [{"ts": "2026-09-22 02:00:00", "kind": "zcode_app",
                                  "plugins": 14, "enabled": 11, "hooks": 0,
                                  "diagnostics": 0}],
                    "phase_count": 3, "last_phase": ""}
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            doctor.verdict(True, True, True, True, True, True, False, log_info)
        out = buf.getvalue()
        self.assertIn("注册的钩子数是 0", out)
        self.assertIn("完全退出", out)

    def test_verdict_falls_back_to_checklist_without_log(self):
        """读不到日志（旧版 ZCode / 日志被清过）时仍要给四选一清单。"""
        import doctor
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            doctor.verdict(True, True, True, True, True, True, False, None)
        out = buf.getvalue()
        self.assertIn("依次确认", out)


class TestDoctorAutoInjectWording(unittest.TestCase):
    """自检报告里关于「配置没保存过」的措辞必须与零配置自动注入一致。

    旧措辞把它写成**卡点**并让人去点「保存配置」—— 那是旧行为（没保存过就什么都不做）。
    现在没保存过 = 按插件清单默认值自动注入，恰恰是「装完即用」的正常状态；
    如果自检还把它报成故障，用户就会被指去做一件根本不必要的事，
    而且会误以为「我没保存配置，所以插件没生效」——正是要消灭的那种误导。
    """

    def _options(self, saved):
        import doctor
        orig = doctor._saved_options
        doctor._saved_options = lambda: saved
        return orig

    def test_check_options_does_not_call_missing_config_a_fault(self):
        import doctor
        orig = self._options({})
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                ok = doctor.check_options()
        finally:
            doctor._saved_options = orig
        out = buf.getvalue()
        self.assertFalse(ok, "返回值仍表示「没有保存过的开关」")
        self.assertIn("这不是故障", out)
        self.assertIn("默认值", out)
        self.assertNotIn(doctor.BAD, out, "不该再用 ✗ 把它标成故障")

    def test_verdict_does_not_block_when_nothing_was_ever_saved(self):
        """saved=False + 钩子跑过 → 链路是完整的，不能停在这里。"""
        import doctor
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            doctor.verdict(True, True, True, True, False, True, True, None)
        out = buf.getvalue()
        self.assertNotIn("★ 卡点", out)
        self.assertIn("链路完整", out)
        self.assertIn("默认值", out)

    def test_verdict_still_blocks_when_hook_never_ran(self):
        """去掉 saved 这道闸之后，「钩子没跑」仍然必须被报成卡点。"""
        import doctor
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            doctor.verdict(True, True, True, True, False, True, False, None)
        out = buf.getvalue()
        self.assertIn("★ 卡点", out)
        self.assertIn("从未运行过", out)


class TestPluginHookSpec(unittest.TestCase):
    """`hooks/hooks.json` 必须落在内核那两个 zod schema 的字段表里。

    内核 `a7s()`（反编译自 `resources/glm/zcode.cjs`）对每个 matcher 跑
    `qz.safeParse(u)`；**解析失败就 `continue` 直接丢掉这个钩子**，只留一条
    `plugin_hook_invalid` / severity=error 的诊断。后果是「钩子静默消失、`hookCount` 变 0」，
    而日志里那条 error 很容易被忽略。所以这里把 schema 钉死，避免以后手滑加字段。

    内核原文（已核对）：
        _rs = G.object({type:G.literal("process"), command:G.string().min(1),
                        enabled:G.boolean().optional(), args:G.array(G.string()).optional(),
                        timeoutMs:G.number().int().positive().optional(),
                        statusMessage:G.string().optional()})
        yrs = G.object({type:G.literal("command"), command:G.string().min(1),
                        enabled:G.boolean().optional(), async:G.boolean().optional(),
                        shell:G.union([G.literal(!0), G.string().min(1)]).optional(),
                        timeout:G.number().positive().optional(),          // 秒
                        timeoutMs:G.number().int().positive().optional(),  // 毫秒
                        statusMessage:G.string().optional()})
        qz  = G.object({matcher:G.string().optional(), hooks:G.array(vrs).min(1)})
    """

    EVENTS = {"SessionStart", "UserPromptSubmit", "PreToolUse", "PermissionRequest",
              "PostToolUse", "PostToolUseFailure", "Stop"}
    COMMAND_FIELDS = {"type", "command", "enabled", "async", "shell",
                      "timeout", "timeoutMs", "statusMessage"}
    PROCESS_FIELDS = {"type", "command", "enabled", "args", "timeoutMs", "statusMessage"}

    def _spec(self) -> dict:
        return json.loads((_HERE.parent / "hooks" / "hooks.json").read_text(encoding="utf-8"))

    def _hooks(self):
        for event, matchers in self._spec()["hooks"].items():
            for i, m in enumerate(matchers):
                for j, h in enumerate(m["hooks"]):
                    yield f"{event}[{i}].hooks[{j}]", h

    def test_declares_at_least_one_supported_event(self):
        events = set(self._spec()["hooks"])
        self.assertTrue(events, "hooks.json 至少要声明一个事件")
        self.assertLessEqual(events, self.EVENTS,
                             "内核只认这 7 个事件，多写会被报 plugin_hook_unsupported_event 并跳过")

    def test_matcher_objects_only_use_matcher_and_hooks(self):
        for event, matchers in self._spec()["hooks"].items():
            self.assertIsInstance(matchers, list, event)
            self.assertTrue(matchers, f"{event} 的 matcher 列表不能为空")
            for m in matchers:
                self.assertLessEqual(set(m), {"matcher", "hooks"}, f"{event}: {sorted(m)}")
                self.assertIsInstance(m["hooks"], list)
                self.assertTrue(m["hooks"], "hooks 数组至少要有一条（内核 min(1)）")

    def test_session_start_omits_matcher_to_match_every_session(self):
        """刻意**不写** matcher —— 省略即匹配全部。

        内核的 matcher 取值是 `startup` / `resume` / `clear` / `compact`。若写成
        `startup|clear|compact`，会静默漏掉 `resume`（用户从历史会话恢复时钩子不跑）；
        写死 `startup` 则「恢复会话」这条路径永远不触发。省略最稳。
        """
        for m in self._spec()["hooks"]["SessionStart"]:
            self.assertNotIn("matcher", m)

    def test_every_hook_uses_declared_fields_only(self):
        seen = []
        for path, h in self._hooks():
            seen.append(path)
            self.assertIn(h.get("type"), ("command", "process"), path)
            allowed = self.PROCESS_FIELDS if h["type"] == "process" else self.COMMAND_FIELDS
            extra = set(h) - allowed
            self.assertEqual(extra, set(), f"{path} 用了内核 schema 未声明的字段: {sorted(extra)}")
            self.assertIsInstance(h.get("command"), str, path)
            self.assertTrue(h["command"].strip(), f"{path} 的 command 不能为空（内核 min(1)）")
        self.assertTrue(seen, "一个钩子都没有？")

    def test_timeout_units_follow_the_schema(self):
        """`timeout` 是**秒**、`timeoutMs` 是**毫秒** —— 混用会让超时变得荒谬。

        内核里两者都存在（`c7s()` 把它们分别搬进 details），解析顺序是
        `timeoutMs` → `timeout×1000` → 配置的 `timeoutMs` → 默认 60000ms。
        所以写成 `"timeout": 120000` 会被当成 12 万秒（33 小时），
        而 `"timeoutMs": 120` 只有 0.12 秒，钩子还没拉起后台进程就被砍掉。
        """
        for path, h in self._hooks():
            if "timeout" in h:
                self.assertIsInstance(h["timeout"], (int, float), path)
                self.assertGreater(h["timeout"], 0, path)
                self.assertLess(h["timeout"], 600, f"{path}: timeout 的单位是秒，{h['timeout']} 太大了")
            if "timeoutMs" in h:
                self.assertIsInstance(h["timeoutMs"], int, path)
                self.assertGreater(h["timeoutMs"], 0, path)
                self.assertGreater(h["timeoutMs"], 1000, f"{path}: timeoutMs 的单位是毫秒")

    def test_hook_command_has_a_python3_fallback(self):
        """Windows 上是 `python`，macOS / Linux 上常常只有 `python3`。

        钩子命令是插件唯一的自动入口，写死 `python` 会让一半用户在 macOS/Linux 上
        静默什么都不发生（钩子被调用但命令不存在），所以必须带 `|| python3 ...` 兜底。
        """
        for path, h in self._hooks():
            self.assertIn("python", h["command"], path)
            self.assertIn("python3", h["command"], f"{path} 缺少 python3 兜底")
            self.assertIn("CLAUDE_PLUGIN_ROOT", h["command"],
                          f"{path} 应该用 ${{CLAUDE_PLUGIN_ROOT}} 定位脚本，不要写死路径")


# ------------------------------------------------------- bootstrap.py（引导脚本）

class TestBootstrapScript(unittest.TestCase):
    """`bootstrap.py` 声称「Windows / macOS / Linux 都能直接运行、不含任何本机绝对路径」。

    这类声明最容易静默失效：开发者在自己的机器上跑一次看到绿字，就把
    `D:\\ZCode` 或 `C:\\Users\\xxx` 留在了源码里；换台机器表现是「莫名其妙找不到文件」。
    所以这里**不靠人眼 review**，而是把声明逐条钉成断言。
    """

    _repo = None

    @classmethod
    def setUpClass(cls):
        # 仓库根：tests/ 的上一级
        cls._repo = Path(__file__).resolve().parent.parent
        cls._src_path = cls._repo / "bootstrap.py"
        if not cls._src_path.is_file():
            raise unittest.SkipTest("未找到 bootstrap.py")
        cls._src = cls._src_path.read_text(encoding="utf-8")

    # ---------- 1. 无硬编码本机路径 ----------

    def test_source_has_no_machine_specific_absolute_paths(self):
        """源码里不得出现任何本机绝对路径字面量。

        这是脚本最核心的承诺：换台电脑、换个用户名、装在别的盘也照样能跑。
        """
        # 注意：这里刻意列「本机真实路径」而不是泛化的正则，命中即说明真的写死了。
        banned = [
            "D:\\ZCode", "D:/ZCode",
            "C:\\Users\\80361", "C:/Users/80361",
            "F:\\ZcodeData", "F:/ZcodeData",
            "F:\\WorkBuddyAI", "F:/WorkBuddyAI",
            ".workbuddy-ai/binaries",
            "ZcodeData",
        ]
        hits = [b for b in banned if b in self._src]
        self.assertEqual(hits, [], f"bootstrap.py 写死了本机路径：{hits}")

    def test_repo_root_is_derived_from_dunder_file(self):
        """仓库根必须由 `__file__` 推导 —— 这样脚本放哪、从哪调用都对。"""
        self.assertIn("Path(__file__).resolve().parent", self._src,
                      "仓库根应该用 Path(__file__).resolve().parent 推导")

    # ---------- 2. 可执行文件靠自动检测 ----------

    def test_locates_executables_via_which_not_hardcoded(self):
        """必须实现 which/where 等价的查找，而不是拼一个猜测的安装路径。"""
        self.assertIn("shutil.which", self._src, "应该用 shutil.which 做 PATH 查找")
        self.assertIn("def which(", self._src, "应该提供 which() 包装（含 PATH 未命中的兜底）")

    def test_which_finds_python_and_returns_none_for_garbage(self):
        """`which()` 的真实行为：能查到 python，查不到的返回 None（不是抛异常）。"""
        import importlib.util
        spec = importlib.util.spec_from_file_location("_bs_probe", self._src_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        self.assertTrue(mod.which("python", "python3", "py"),
                        "which() 应该能在 PATH 中找到 python")
        self.assertIsNone(mod.which("definitely-not-a-real-binary-xyz-42"),
                          "找不到时应返回 None，而不是抛异常")

    def test_which_fallback_scans_convention_dirs_without_crashing(self):
        """PATH 查找失败时必须能安全降级到「约定目录」扫描。

        这里复现的是实现过程中真实踩到的坑：早先的兜底逻辑写成
        `Path("/").glob(绝对模式)`，在 Windows 上会抛
        `UnsupportedOperation: cannot instantiate 'PosixPath' on your system`。
        它平时「看不出来」——因为 shutil.which 总能先命中，兜底分支根本没被走到。
        这条测试主动把 shutil.which 打桩成 None，逼出兜底分支。
        """
        import importlib.util
        spec = importlib.util.spec_from_file_location("_bs_probe2", self._src_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        orig_which, orig_name = mod.shutil.which, mod.os.name
        try:
            mod.shutil.which = lambda n: None      # 模拟 PATH 里啥都没有
            mod.os.name = "posix"                  # 顺便走一遍 POSIX 目录表
            try:
                result = mod.which("sh", "bash", "ls", "env")
            except Exception as exc:               # noqa: BLE001
                self.fail(f"兜底扫描抛异常了（应当干净返回 None）：{exc!r}")
            self.assertTrue(result is None or Path(result).is_file(),
                            f"返回了不存在的路径：{result!r}")
        finally:
            mod.shutil.which, mod.os.name = orig_which, orig_name

    def test_fallback_dirs_are_portable_not_baked_in(self):
        """兜底目录表必须是跨平台惯例写法，且不能固化当前机器的家目录。

        早先的实现用 `os.path.expanduser(...)` 在 **import 时**求值，
        等于把「本机家目录」写进了模块属性；而模块属性是在别的机器上也会被加载的。
        现在要求表里写 `~`，运行时才展开。
        """
        import importlib.util
        spec = importlib.util.spec_from_file_location("_bs_probe3", self._src_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        for key, dirs in mod._FALLBACK_DIRS.items():
            for d in dirs:
                self.assertNotIn("80361", d, f"{key} 表里固化了本机用户名：{d}")
                self.assertNotIn("ZCodeData", d, f"{key} 表里固化了本机路径：{d}")
        # 家目录相关的项必须写成 ~ 形式（运行时展开）
        home_like = [d for d in mod._FALLBACK_DIRS["posix"] if ".local" in d or "pyenv" in d]
        self.assertTrue(all(d.startswith("~/") for d in home_like),
                        f"家目录相关兜底项应写成 ~ 形式：{home_like}")
        self.assertIn("expanduser", self._src,
                      "应该在运行时用 expanduser 展开 ~，而不是 import 时求值")

    def test_optional_dependency_absence_is_tolerated(self):
        """Node 是可选依赖：缺失只能降级跳过，不能让整个引导失败。"""
        self.assertIn("def find_node(", self._src)
        # 找不到 Node 的分支必须是「跳过」，不是 die()
        self.assertIn("Node（可选）", self._src)
        self.assertIn("跳过已有脚本", self._src.replace("跳过注入脚本", "跳过已有脚本")
                      .replace("跳过滑条冒烟", "跳过已有脚本"))

    # ---------- 3. 跨平台执行细节 ----------

    def test_subprocess_calls_never_use_shell(self):
        """一律用列表传参、不经 shell —— 路径含空格/中文才安全，也避开 shell 语法差异。"""
        import ast
        tree = ast.parse(self._src)
        offenders = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "run":
                for kw in node.keywords:
                    if kw.arg == "shell" and getattr(kw.value, "value", False):
                        offenders.append(node.lineno)
        self.assertEqual(offenders, [], f"第 {offenders} 行使用了 shell=True")

    def test_windows_children_suppress_console_window(self):
        """Windows 下起子进程必须带 CREATE_NO_WINDOW（否则每步闪一个 cmd 窗口）。"""
        self.assertIn("CREATE_NO_WINDOW", self._src)
        self.assertIn("0x08000000", self._src)

    def test_subprocess_output_is_decoded_leniently(self):
        """子进程输出按 UTF-8 + errors=replace 解码。

        直接 apply cp936 管道下的 bytes.decode() 会抛 UnicodeDecodeError 打断整段输出，
        而钩子/CI 环境恰恰会把 stdout 重定向到管道。
        """
        self.assertIn('errors="replace"', self._src)

    def test_no_high_unicode_glyphs_in_output(self):
        """输出只用 ASCII 标记（[+] / [x] / [!]），不依赖 ✓✗⚠ 这类字符。

        理由：cp936 管道里打印这些字符会抛 UnicodeEncodeError（见 _console.py 的说明）。
        """
        bad = [g for g in "✓✗⚠✅↻✔✘" if g in self._src]
        self.assertEqual(bad, [], f"bootstrap.py 使用了高位 Unicode 符号：{bad}")

    # ---------- 4. 步骤编排 ----------

    def test_step_names_are_consistent(self):
        """STEP_ORDER 里每个名字都要有标题和处理器，别留下半截。"""
        import importlib.util
        spec = importlib.util.spec_from_file_location("_bs_steps", self._src_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        for name in mod.STEP_ORDER:
            self.assertIn(name, mod.STEP_TITLES, f"步骤 {name} 缺标题")
            self.assertTrue(hasattr(mod, f"step_{name}"), f"步骤 {name} 缺实现函数")

    def test_dry_run_never_executes_writes(self):
        """`--dry-run` 必须真的不执行命令（只打印），否则「预演」就失去意义。"""
        self.assertIn("def run(", self._src)
        # run() 在 dry_run 时应当提前返回，不落到 subprocess.run
        body = self._src.split("def run(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("if dry_run:", body)
        dry_pos = body.index("if dry_run:")
        exec_pos = body.index("subprocess.run")
        self.assertLess(dry_pos, exec_pos,
                        "dry_run 判断必须在 subprocess.run 之前")


if __name__ == "__main__":
    unittest.main(verbosity=2)
