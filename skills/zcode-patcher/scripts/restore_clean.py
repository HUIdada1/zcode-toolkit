#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ZCode 紧急还原脚本（从干净备份整包恢复）
=========================================
用途：补丁导致客户端无法启动时，一条命令恢复，**无需重装 ZCode**。

备份目录（默认 D:/ZCode-clean-backup）里存的是未打补丁的干净文件：
  app.asar.<版本>.clean
  zcode.cjs.<版本>.clean

用法：
  python restore_clean.py                 # 列出可用备份
  python restore_clean.py --latest        # 用最新备份还原（需先完全退出 ZCode）
  python restore_clean.py --version 3.12.2
  python restore_clean.py --backup        # 把当前安装的文件另存为新备份

注意：还原会覆盖 resources 下的 app.asar / glm/zcode.cjs，执行前务必完全退出 ZCode。
"""

import argparse
import shutil
import sys
import time
from pathlib import Path

DEFAULT_BACKUP_DIR = Path("D:/ZCode-clean-backup")
# 探测常见安装位置（与 zcode_patcher 的 discover 保持一致的思路）
CANDIDATE_ROOTS = [
    Path(r"C:/Program Files/ZCode"),
    Path(r"D:/ZCode"),
    Path.home() / "AppData/Local/Programs/ZCode",
    Path("/Applications/ZCode.app/Contents"),
    Path("/opt/ZCode"),
    Path("/usr/share/ZCode"),
    Path("/Applications/ZCode.app"),
]


def find_resources() -> Path | None:
    for root in CANDIDATE_ROOTS:
        res = root / "resources"
        if (res / "app.asar").is_file() and (res / "glm" / "zcode.cjs").is_file():
            return res
    return None


def list_backups(bdir: Path):
    asars = sorted(bdir.glob("app.asar.*.clean"), key=lambda p: p.stat().st_mtime, reverse=True)
    out = []
    for a in asars:
        ver = a.name[len("app.asar."):-len(".clean")]
        cjs = bdir / f"zcode.cjs.{ver}.clean"
        out.append((ver, a, cjs if cjs.is_file() else None, a.stat().st_mtime))
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="ZCode 紧急还原（从干净备份整包恢复）")
    ap.add_argument("--backup-dir", default=str(DEFAULT_BACKUP_DIR), help="备份目录")
    ap.add_argument("--latest", action="store_true", help="用最新备份还原")
    ap.add_argument("--version", default=None, help="指定版本备份（如 3.12.2）")
    ap.add_argument("--backup", action="store_true", help="把当前安装另存为备份")
    args = ap.parse_args()

    bdir = Path(args.backup_dir)
    if not bdir.is_dir():
        bdir.mkdir(parents=True, exist_ok=True)
    backups = list_backups(bdir)

    res = find_resources()
    if res is None:
        raise SystemExit("[!] 未找到 ZCode 安装（请用 --backup-dir 或手动复制）")
    tgt_asar, tgt_cjs = res / "app.asar", res / "glm" / "zcode.cjs"

    if args.backup:
        ver = "manual-" + time.strftime("%Y%m%d-%H%M%S")
        shutil.copy2(tgt_asar, bdir / f"app.asar.{ver}.clean")
        shutil.copy2(tgt_cjs, bdir / f"zcode.cjs.{ver}.clean")
        print(f"[+] 已备份当前安装 → {bdir}（版本标记 {ver}）")
        print(f"    app.asar: {tgt_asar.stat().st_size:,} 字节")
        return

    if not backups:
        raise SystemExit(f"[!] {bdir} 下没有可用备份；先运行 --backup 保存干净副本")
    if not (args.latest or args.version):
        print(f"可用备份（{bdir}）：")
        for ver, a, c, mt in backups:
            print(f"  {ver:24} app.asar {a.stat().st_size:>13,}B  "
                  f"zcode.cjs {'有' if c else '无'}  {time.strftime('%Y-%m-%d %H:%M', time.localtime(mt))}")
        print("\n用 --latest 或 --version <版本> 执行还原")
        return

    pick = None
    if args.version:
        pick = next((b for b in backups if b[0] == args.version), None)
        if pick is None:
            raise SystemExit(f"[!] 没有版本 {args.version} 的备份")
    else:
        pick = backups[0]
    ver, src_asar, src_cjs, _ = pick

    print(f"准备从备份 {ver} 还原：")
    print(f"  源: {src_asar}")
    print(f"  目标: {tgt_asar}")
    try:
        shutil.copy2(src_asar, tgt_asar)
        print(f"  ✓ app.asar 已还原（{src_asar.stat().st_size:,} 字节）")
        if src_cjs:
            shutil.copy2(src_cjs, tgt_cjs)
            print(f"  ✓ zcode.cjs 已还原（{src_cjs.stat().st_size:,} 字节）")
        else:
            print(f"  ⚠ 该版本无 zcode.cjs 备份，内核未改回")
    except PermissionError as e:
        raise SystemExit(f"[!] 写入被拒绝（ZCode 未完全退出 或 需要管理员权限）：{e}")

    # 清理本机补丁产物，避免还原后 sidecar 与实际不符
    for pat in ("*.bak", "*-patch.json", "*.tps-tmp"):
        for f in res.rglob(pat):
            try:
                f.unlink()
                print(f"  · 已清理 {f.name}")
            except Exception:
                pass
    print("\n✓ 还原完成，请启动 ZCode 验证")


if __name__ == "__main__":
    main()
