import argparse
import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

import ncm_core


@dataclass
class ScanResult:
    """一次扫描的结果：全部 .ncm、输出目录、待转换、已跳过。"""

    ncm_files: list
    dest_path: Path
    todo: list
    skipped: list


def list_dir_names(path):
    """一次性枚举目录，返回小写的文件名集合，避免逐文件 stat（网络盘上每次 stat 都是一次往返）。"""
    with os.scandir(path) as entries:
        return {entry.name.lower() for entry in entries if entry.is_file()}


def scan_ncm_files(target_dir, output_dir=None):
    """扫描待转换的 .ncm：以输出目录中已有的 .flac/.mp3 判断是否已转换。"""
    target_path = Path(target_dir)
    dest_path = Path(output_dir).resolve() if output_dir else target_path.resolve()

    ncm_files = sorted(target_path.glob("*.ncm"), key=lambda p: p.name.lower())
    existing_names = list_dir_names(dest_path)

    todo, skipped = [], []
    for ncm_file in ncm_files:
        stem = ncm_file.stem.lower()
        if f"{stem}.flac" in existing_names or f"{stem}.mp3" in existing_names:
            skipped.append(ncm_file)
        else:
            todo.append(ncm_file)

    return ScanResult(ncm_files, dest_path, todo, skipped)


def locked_logger(lock):
    """把 print 包一层锁，避免多线程输出互相打断。"""

    def log(message):
        with lock:
            print(message)

    return log


def flac_bitrate_warning(path):
    """ncmdump 偶尔会写出截断的 flac（源 .ncm 没下载完整）仍返回 0，用平均码率兜一下。"""
    try:
        with open(path, "rb") as fh:
            if fh.read(4) != b"fLaC":
                return ""
            header = fh.read(4)
            if len(header) < 4:
                return ""
            block = fh.read(min(int.from_bytes(header[1:4], "big"), 34))
    except OSError:
        return ""

    if len(block) < 18:
        return ""
    bits = int.from_bytes(block[10:18], "big")
    sample_rate = (bits >> 44) & 0xFFFFF
    total_samples = bits & 0xFFFFFFFFF
    if sample_rate <= 0 or total_samples <= 0:
        return ""

    duration = total_samples / sample_rate
    if duration < 30:
        return ""
    kbps = path.stat().st_size * 8 / duration / 1000
    if kbps < 200:
        return f"输出可能不完整（{duration:.0f}s 只有 {kbps:.0f} kbps），已保留源文件"
    return ""


def convert_one(ncm_file, dest_dir, remove_source, log=None):
    """转换单个 .ncm 文件到 dest_dir，返回 (是否成功, 错误/警告信息, 是否已删除源文件)。"""
    log = log or print
    log(f"🔄 转换中: {ncm_file.name}")

    try:
        out_path, _meta = ncm_core.decrypt_file(ncm_file, dest_dir)
    except Exception as exc:
        return False, str(exc), False

    produced = Path(out_path)
    if not produced.is_file() or produced.stat().st_size <= 0:
        return False, "未生成输出文件", False

    warning = flac_bitrate_warning(produced)

    if remove_source:
        if warning:
            return True, warning, False
        try:
            ncm_file.unlink()
        except OSError as exc:
            return True, f"删除原文件失败: {exc}", False
        return True, "", True

    return True, warning, False


def convert_ncm_files(
    target_dir,
    jobs=4,
    verbose=False,
    output_dir=None,
    remove_source=False,
):
    """
    检测指定目录下的.ncm文件，如果没有对应的.flac/.mp3文件，则调用内置解密引擎转换，避免重复转换。
    基于纯 Python 标准库实现，无需 ncmdump.exe、无需第三方依赖。

    参数:
        target_dir: 要扫描的目录路径
        jobs: 并发转换的文件数（1 表示顺序执行）
        verbose: 是否打印每个被跳过文件的明细
        output_dir: 输出目录（默认与 .ncm 同目录）；跳过判断以该目录中已有的文件为准
        remove_source: 转换成功（并确认输出文件已生成）后删除原始 .ncm 文件
    """
    target_path = Path(target_dir)

    # 检查目录是否存在
    if not target_path.is_dir():
        print(f"❌ 错误：目标目录不存在: {target_path}")
        return

    # 输出目录：未指定时与 .ncm 同目录
    if output_dir and not Path(output_dir).is_dir():
        print(f"❌ 错误：输出目录不存在: {output_dir}")
        return

    scan = scan_ncm_files(target_path, output_dir)

    if not scan.ncm_files:
        print(f"ℹ️ 在 {target_path} 中没有找到.ncm文件")
        return

    print(f"🔍 找到 {len(scan.ncm_files)} 个.ncm文件\n")
    if scan.dest_path != target_path.resolve():
        print(f"📁 输出目录: {scan.dest_path}")

    if verbose:
        for ncm_file in scan.skipped:
            print(f"⏭️  跳过: {ncm_file.name} (已存在转换文件)")
    elif scan.skipped:
        print(f"⏭️  跳过 {len(scan.skipped)} 个已存在转换文件的.ncm（加 -v 查看明细）")

    converted = 0
    failed = 0
    removed = 0
    log = locked_logger(threading.Lock())

    with ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
        futures = {
            pool.submit(
                convert_one, ncm_file, scan.dest_path, remove_source, log
            ): ncm_file
            for ncm_file in scan.todo
        }
        for future in as_completed(futures):
            ncm_file = futures[future]
            ok, err, was_removed = future.result()
            if ok:
                print(f"✅ 成功: {ncm_file.name}")
                converted += 1
                if was_removed:
                    removed += 1
                elif err:
                    print(f"   ⚠️ {err}")
            else:
                print(f"❌ 失败: {ncm_file.name}")
                if err:
                    print(f"   错误信息: {err}")
                failed += 1

    # 统计报告
    print(f"\n{'='*50}")
    print(f"📊 转换完成统计:")
    print(f"   ✅ 成功: {converted}")
    print(f"   ⏭️  跳过: {len(scan.skipped)}")
    print(f"   ❌ 失败: {failed}")
    print(f"   📁 总计: {len(scan.ncm_files)}")
    if remove_source:
        print(f"   🗑️  已删除原始.ncm: {removed}")


# 使用示例
if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="批量将 .ncm 转换为 .flac/.mp3（纯标准库，无第三方依赖），自动跳过已转换的文件"
    )
    parser.add_argument(
        "target_dir",
        nargs="?",
        default=".",
        help="要扫描的目录（默认当前目录）",
    )
    parser.add_argument(
        "-j",
        "--jobs",
        type=int,
        default=4,
        help="并发转换数，1 为顺序执行（默认 4）",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="打印每个被跳过文件的明细",
    )
    parser.add_argument(
        "-o",
        "--output",
        default=None,
        help="输出目录（默认与 .ncm 同目录）",
    )
    parser.add_argument(
        "-m",
        "--remove",
        action="store_true",
        help="转换成功后删除原始 .ncm 文件",
    )
    args = parser.parse_args()

    convert_ncm_files(
        args.target_dir,
        jobs=args.jobs,
        verbose=args.verbose,
        output_dir=args.output,
        remove_source=args.remove,
    )
