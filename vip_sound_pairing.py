import shutil
from pathlib import Path

import re
from send2trash import send2trash


DUPLICATE_SUFFIX_PATTERN = re.compile(r"\s*\(\d+\)$")


def move_matching_files(folder_a, folder_b):
    """
    检查A文件夹中的.ncm文件，如果在B文件夹中有同名的.flac或.lrc文件，
    则将这些文件移动到A文件夹中，目的是为了在文件夹A在转换.ncm文件前检查并处理文夹B中已经存在的转换结果文件，避免重复转换。
    """
    folder_a = Path(folder_a)
    folder_b = Path(folder_b)

    # 确保文件夹存在
    if not folder_a.exists():
        print(f"错误：A文件夹不存在: {folder_a}")
        return
    if not folder_b.exists():
        print(f"错误：B文件夹不存在: {folder_b}")
        return

    # 获取A文件夹中所有的.ncm文件（不包括子文件夹）
    ncm_files = list(folder_a.glob("*.ncm"))

    if not ncm_files:
        print(f"在 {folder_a} 中没有找到.ncm文件")
        return

    print(f"找到 {len(ncm_files)} 个.ncm文件")

    moved_count = 0

    for ncm_file in ncm_files:
        # 获取文件名（不含扩展名）
        stem = ncm_file.stem

        # 检查B文件夹中是否有同名的.flac或.lrc文件
        for ext in [".flac", ".lrc"]:
            source_file = folder_b / f"{stem}{ext}"

            if source_file.exists():
                target_file = folder_a / f"{stem}{ext}"

                # 如果目标位置已存在同名文件，则跳过
                if target_file.exists():
                    print(f"跳过：{target_file.name} 已存在于A文件夹中")
                    continue

                try:
                    shutil.move(str(source_file), str(target_file))
                    print(f"已移动: {source_file.name} -> {folder_a}")
                    moved_count += 1
                except Exception as e:
                    print(f"移动失败 {source_file.name}: {e}")

    print(f"\n完成！共移动了 {moved_count} 个文件")


def normalize_duplicate_stem(stem):
    return DUPLICATE_SUFFIX_PATTERN.sub("", stem).strip()


def is_numbered_copy_stem(stem):
    return DUPLICATE_SUFFIX_PATTERN.search(stem) is not None


def format_file_size_mb(file_path):
    return f"{file_path.stat().st_size / (1024):.2f} KB"


def find_dublicate_files(folder, extensions, delete_dublicate=False):
    """
    请谨慎使用 delete_dublicate=True 参数！请谨慎使用 delete_dublicate=True 参数！请谨慎使用 delete_dublicate=True 参数！
    在指定文件夹中查找指定扩展名列表中的重复文件。
    返回一个字典，键是文件名（不带扩展名），值是包含该文件的路径列表。
    当 delete_dublicate=True 时，会把名称末尾为(数字)的重复文件移动到回收站。
    注意：这个函数只会识别文件名末尾带有(数字)的文件为重复文件，并且会把它们与没有(数字)后缀的原文件进行比较。
    函数不会递归检查子文件夹中的文件，只会检查指定文件夹中的文件。
    注意：U盘没有回收站，所以文件删除后不可恢复，请谨慎使用 delete_dublicate=True 参数！请谨慎使用 delete_dublicate=True 参数！请谨慎使用 delete_dublicate=True 参数！
    """
    folder = Path(folder)
    if not folder.exists():
        print(f"❌ 错误：文件夹不存在: {folder}")
        return {}

    files = [file for ext in extensions for file in folder.glob(f"*{ext}")]

    # 只把“非(数字)后缀”的文件视为原文件，按“文件名+扩展名”建立索引。
    originals = {
        (file.stem, file.suffix.lower()): file
        for file in files
        if not is_numbered_copy_stem(file.stem)
    }

    duplicates = {}
    for file in files:
        if not is_numbered_copy_stem(file.stem):
            continue

        normalized_stem = normalize_duplicate_stem(file.stem)
        original_key = (normalized_stem, file.suffix.lower())
        original_file = originals.get(original_key)
        if not original_file:
            continue

        group_key = f"{normalized_stem}{file.suffix.lower()}"
        if group_key not in duplicates:
            duplicates[group_key] = [original_file]
        duplicates[group_key].append(file)

        print(
            f"⚠️ 发现重复文件: {file} ({format_file_size_mb(file)}) 与 "
            f"{original_file} ({format_file_size_mb(original_file)})"
        )

        if delete_dublicate:
            try:
                send2trash(str(file))
                print(f"🗑️ 已移到回收站: {file}")
            except Exception as e:
                print(f"❌ 移到回收站失败: {file} - {e}")

    return duplicates


# 使用示例
if __name__ == "__main__":
    # 修改为你的实际路径
    vip_Folder = r"Z:\音乐\VipSongsDownload"  # A文件夹路径
    target_folder = r"Z:\音乐"  # B文件夹路径
    find_dublicate_files(
        target_folder, [".flac", ".mp3", ".lrc"], delete_dublicate=True
    )
    # move_matching_files(vip_Folder, target_folder)
