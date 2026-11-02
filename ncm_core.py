"""纯标准库实现的 .ncm 解密模块（零第三方依赖）。

格式（公开算法，多实现交叉确认）：
    magic "CTENFDAM"(8) | gap(2) | key_len(4LE) | key_data
    | meta_len(4LE) | meta_data | crc32(4) | gap(5) | img_len(4) | img | 音频(RC4)

    key_data : XOR 0x64 → AES-128-ECB(CORE_KEY) → unpad → [17:] 得到 RC4 密钥
    meta_data: XOR 0x63 → [22:] → base64 → AES-128-ECB(META_KEY) → unpad → [6:] → JSON
    音频     : NCM 变体 RC4（KSA 后按块位置查表异或，不更新状态盒）

用法:
    python ncm_core.py --self-test                # AES-128-ECB 向量自测
    python ncm_core.py 文件.ncm [-o 输出目录]       # 解密单个文件
    python ncm_core.py 目录 [-o 输出目录]          # 解密目录内全部 .ncm
"""

import argparse
import base64
import json
import struct
import sys
from pathlib import Path

MAGIC = b"CTENFDAM"
CORE_KEY = bytes.fromhex("687A4852416D736F356B496E62617857")  # hzHRAmso5kInbaxW
META_KEY = bytes.fromhex("2331346C6A6B5F215C5D2630553C2728")  # #14ljk_!\]&0U<'("
CHUNK_SIZE = 0x8000

# ---------------------------------------------------------------------------
# AES-128（仅解密所需的最小实现，S-box 来自 FIPS-197）
# ---------------------------------------------------------------------------

_SBOX = bytes([
    0x63, 0x7c, 0x77, 0x7b, 0xf2, 0x6b, 0x6f, 0xc5, 0x30, 0x01, 0x67, 0x2b, 0xfe, 0xd7, 0xab, 0x76,
    0xca, 0x82, 0xc9, 0x7d, 0xfa, 0x59, 0x47, 0xf0, 0xad, 0xd4, 0xa2, 0xaf, 0x9c, 0xa4, 0x72, 0xc0,
    0xb7, 0xfd, 0x93, 0x26, 0x36, 0x3f, 0xf7, 0xcc, 0x34, 0xa5, 0xe5, 0xf1, 0x71, 0xd8, 0x31, 0x15,
    0x04, 0xc7, 0x23, 0xc3, 0x18, 0x96, 0x05, 0x9a, 0x07, 0x12, 0x80, 0xe2, 0xeb, 0x27, 0xb2, 0x75,
    0x09, 0x83, 0x2c, 0x1a, 0x1b, 0x6e, 0x5a, 0xa0, 0x52, 0x3b, 0xd6, 0xb3, 0x29, 0xe3, 0x2f, 0x84,
    0x53, 0xd1, 0x00, 0xed, 0x20, 0xfc, 0xb1, 0x5b, 0x6a, 0xcb, 0xbe, 0x39, 0x4a, 0x4c, 0x58, 0xcf,
    0xd0, 0xef, 0xaa, 0xfb, 0x43, 0x4d, 0x33, 0x85, 0x45, 0xf9, 0x02, 0x7f, 0x50, 0x3c, 0x9f, 0xa8,
    0x51, 0xa3, 0x40, 0x8f, 0x92, 0x9d, 0x38, 0xf5, 0xbc, 0xb6, 0xda, 0x21, 0x10, 0xff, 0xf3, 0xd2,
    0xcd, 0x0c, 0x13, 0xec, 0x5f, 0x97, 0x44, 0x17, 0xc4, 0xa7, 0x7e, 0x3d, 0x64, 0x5d, 0x19, 0x73,
    0x60, 0x81, 0x4f, 0xdc, 0x22, 0x2a, 0x90, 0x88, 0x46, 0xee, 0xb8, 0x14, 0xde, 0x5e, 0x0b, 0xdb,
    0xe0, 0x32, 0x3a, 0x0a, 0x49, 0x06, 0x24, 0x5c, 0xc2, 0xd3, 0xac, 0x62, 0x91, 0x95, 0xe4, 0x79,
    0xe7, 0xc8, 0x37, 0x6d, 0x8d, 0xd5, 0x4e, 0xa9, 0x6c, 0x56, 0xf4, 0xea, 0x65, 0x7a, 0xae, 0x08,
    0xba, 0x78, 0x25, 0x2e, 0x1c, 0xa6, 0xb4, 0xc6, 0xe8, 0xdd, 0x74, 0x1f, 0x4b, 0xbd, 0x8b, 0x8a,
    0x70, 0x3e, 0xb5, 0x66, 0x48, 0x03, 0xf6, 0x0e, 0x61, 0x35, 0x57, 0xb9, 0x86, 0xc1, 0x1d, 0x9e,
    0xe1, 0xf8, 0x98, 0x11, 0x69, 0xd9, 0x8e, 0x94, 0x9b, 0x1e, 0x87, 0xe9, 0xce, 0x55, 0x28, 0xdf,
    0x8c, 0xa1, 0x89, 0x0d, 0xbf, 0xe6, 0x42, 0x68, 0x41, 0x99, 0x2d, 0x0f, 0xb0, 0x54, 0xbb, 0x16,
])

_INV_SBOX = bytes(_SBOX.index(i) for i in range(256))

_RCON = (0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80, 0x1b, 0x36)


def _xtime(a):
    a <<= 1
    if a & 0x100:
        a ^= 0x11B
    return a & 0xFF


def _gmul(a, b):
    result = 0
    for _ in range(8):
        if b & 1:
            result ^= a
        a = _xtime(a)
        b >>= 1
    return result


def _key_expansion(key):
    """AES-128 密钥扩展 → 44 个 4 字节字（列序）。"""
    w = [list(key[i * 4:(i + 1) * 4]) for i in range(4)]
    for i in range(4, 44):
        temp = w[i - 1][:]
        if i % 4 == 0:
            temp = temp[1:] + temp[:1]                      # RotWord
            temp = [_SBOX[b] for b in temp]                 # SubWord
            temp[0] ^= _RCON[i // 4 - 1]
        w.append([w[i - 4][j] ^ temp[j] for j in range(4)])
    return w


def _inv_shift_rows(state):
    """state[col][row]；行 r 循环右移 r。"""
    return [[state[(c - r) % 4][r] for r in range(4)] for c in range(4)]


def _inv_sub_bytes(state):
    return [[_INV_SBOX[b] for b in col] for col in state]


def _inv_mix_columns(state):
    out = []
    for s0, s1, s2, s3 in state:
        out.append([
            _gmul(0x0E, s0) ^ _gmul(0x0B, s1) ^ _gmul(0x0D, s2) ^ _gmul(0x09, s3),
            _gmul(0x09, s0) ^ _gmul(0x0E, s1) ^ _gmul(0x0B, s2) ^ _gmul(0x0D, s3),
            _gmul(0x0D, s0) ^ _gmul(0x09, s1) ^ _gmul(0x0E, s2) ^ _gmul(0x0B, s3),
            _gmul(0x0B, s0) ^ _gmul(0x0D, s1) ^ _gmul(0x09, s2) ^ _gmul(0x0E, s3),
        ])
    return out


def _aes_decrypt_block(block, key):
    """解密一个 16 字节块。"""
    state = [[block[4 * c + r] for r in range(4)] for c in range(4)]
    w = _key_expansion(key)

    for c in range(4):
        for r in range(4):
            state[c][r] ^= w[40 + c][r]  # 第 10 轮密钥

    for rnd in range(9, 0, -1):
        state = _inv_shift_rows(state)
        state = _inv_sub_bytes(state)
        for c in range(4):
            for r in range(4):
                state[c][r] ^= w[rnd * 4 + c][r]
        state = _inv_mix_columns(state)

    state = _inv_shift_rows(state)
    state = _inv_sub_bytes(state)
    for c in range(4):
        for r in range(4):
            state[c][r] ^= w[c][r]

    return bytes(state[c][r] for c in range(4) for r in range(4))


def aes_ecb_decrypt(data, key):
    """AES-128-ECB 解密，长度须为 16 的倍数。"""
    if len(data) % 16:
        raise ValueError("AES 数据长度必须是 16 的倍数")
    out = bytearray()
    for offset in range(0, len(data), 16):
        out += _aes_decrypt_block(data[offset:offset + 16], key)
    return bytes(out)


def _self_test():
    """NIST FIPS-197 C.2 与 SP 800-38A 的 AES-128 ECB 解密向量。"""
    vectors = [
        # (key, ciphertext, plaintext)
        (
            bytes.fromhex("000102030405060708090a0b0c0d0e0f"),
            bytes.fromhex("69c4e0d86a7b0430d8cdb78070b4c55a"),
            bytes.fromhex("00112233445566778899aabbccddeeff"),
        ),
        (
            bytes.fromhex("2b7e151628aed2a6abf7158809cf4f3c"),
            bytes.fromhex("3ad77bb40d7a3660a89ecaf32466ef97"),
            bytes.fromhex("6bc1bee22e409f96e93d7e117393172a"),
        ),
    ]
    for index, (key, cipher, plain) in enumerate(vectors, 1):
        got = aes_ecb_decrypt(cipher, key)
        if got != plain:
            print(f"❌ 向量 {index} 失败")
            print(f"  期望 {plain.hex()}")
            print(f"  得到 {got.hex()}")
            return False
        print(f"✅ AES-128 向量 {index} 通过")
    return True


# ---------------------------------------------------------------------------
# NCM 变体 RC4
# ---------------------------------------------------------------------------

def _rc4_ksa(key_data):
    """标准 RC4 KSA，生成 256 字节状态盒。"""
    key_length = len(key_data)
    key_box = bytearray(range(256))
    c = 0
    last_byte = 0
    key_offset = 0
    for i in range(256):
        swap = key_box[i]
        c = (swap + last_byte + key_data[key_offset]) & 0xFF
        key_offset += 1
        if key_offset >= key_length:
            key_offset = 0
        key_box[i] = key_box[c]
        key_box[c] = swap
        last_byte = c
    return key_box


def _rc4_mask(key_box):
    """NCM 变体 PRGA 的位置掩码：chunk[k] ^= mask[(k+1) & 0xff]。

    该变体不更新状态盒、偏移只依赖块内位置，因此 256 字节掩码可预计算，
    配合大整数异或实现接近 C 的解密速度。
    """
    mask = bytearray(256)
    for j in range(256):
        mask[j] = key_box[(key_box[j] + key_box[(key_box[j] + j) & 0xFF]) & 0xFF]
    return bytes(mask)


def _rc4_decrypt_chunk(chunk, mask):
    """解密一块：整块与偏移后的 256 循环掩码做一次大整数异或。"""
    if not chunk:
        return chunk
    base = mask[1:] + mask[:1]                       # 偏移 1：chunk[0] 用 mask[1]
    pattern = (base * ((len(chunk) + 255) // 256))[:len(chunk)]
    x = int.from_bytes(chunk, "little") ^ int.from_bytes(pattern, "little")
    return x.to_bytes(len(chunk), "little")


# ---------------------------------------------------------------------------
# .ncm 解析与解密
# ---------------------------------------------------------------------------

def _unpad(data):
    if not data:
        return data
    pad = data[-1]
    if 1 <= pad <= 16:
        return data[:-pad]
    return data


def _xor(data, value):
    return bytes(b ^ value for b in data)


def _detect_ext(first_chunk, fallback_format):
    if first_chunk[:4] == b"fLaC":
        return ".flac"
    if first_chunk[:3] == b"ID3":
        return ".mp3"
    if len(first_chunk) > 2 and first_chunk[0] == 0xFF and (first_chunk[1] & 0xE0) == 0xE0:
        return ".mp3"
    fmt = (fallback_format or "").lower()
    if fmt in ("mp3", "flac"):
        return "." + fmt
    return ".mp3"


# ---------------------------------------------------------------------------
# 封面回写（纯标准库）：flac 用 METADATA_BLOCK_PICTURE，mp3 用 ID3v2.3 APIC
# ---------------------------------------------------------------------------

def _detect_image_mime(image_data):
    if image_data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if image_data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    return "image/jpeg"


def _picture_payload(image_data, mime):
    mime_bytes = mime.encode("ascii")
    payload = struct.pack(">I", 3)                     # 3 = front cover
    payload += struct.pack(">I", len(mime_bytes)) + mime_bytes
    payload += struct.pack(">I", 0)                    # description 为空
    payload += struct.pack(">IIII", 0, 0, 0, 0)        # width/height/depth/colors
    payload += struct.pack(">I", len(image_data)) + image_data
    return payload


def _embed_flac_cover(flac_path, image_data, mime):
    """把封面写进 flac 元数据；已有 PICTURE 块则原位替换为 .ncm 封面。"""
    data = bytearray(flac_path.read_bytes())
    if len(data) < 4 or data[:4] != b"fLaC":
        return False
    payload = _picture_payload(image_data, mime)
    if len(payload) > 0xFFFFFF:
        return False

    offset = 4
    metas = []  # (block_type, block_bytes)
    while offset + 4 <= len(data):
        header = data[offset:offset + 4]
        block_type = header[0] & 0x7F
        block_len = int.from_bytes(header[1:4], "big")
        end = offset + 4 + block_len
        if end > len(data):
            return False
        metas.append((block_type, bytes(data[offset:end])))
        offset = end
        if header[0] & 0x80:
            break
    if not metas or metas[0][0] != 0:
        return False
    audio_start = offset

    picture_block = bytes([0x06]) + len(payload).to_bytes(3, "big") + payload
    pic_idx = next((i for i, (bt, _b) in enumerate(metas) if bt == 6), None)
    if pic_idx is not None:
        metas[pic_idx] = (6, picture_block)          # 替换已有封面
    elif len(metas) == 1:
        metas.append((6, picture_block))             # 只有 STREAMINFO
    else:
        metas.insert(len(metas) - 1, (6, picture_block))  # 插到最后一个块之前
    # 与 ncmdump 一致：最后一个 PADDING 块统一重写为固定 4096 字节
    padding_block = bytes([0x01]) + (4096).to_bytes(3, "big") + b"\x00" * 4096
    if metas[-1][0] == 1:
        metas[-1] = (1, padding_block)
    else:
        metas.append((1, padding_block))

    out = bytearray(data[:4])
    for i, (btype, block) in enumerate(metas):
        is_last = i == len(metas) - 1
        first = block[0] | 0x80 if is_last else block[0] & 0x7F
        out += bytes([first]) + block[1:]
    out += data[audio_start:]
    flac_path.write_bytes(out)
    return True


def _syncsafe(n):
    return bytes([(n >> 21) & 0x7F, (n >> 14) & 0x7F, (n >> 7) & 0x7F, n & 0x7F])


def _unsyncsafe(data):
    return (data[0] << 21) | (data[1] << 14) | (data[2] << 7) | data[3]


def _apic_frame(image_data, mime):
    payload = b"\x00" + mime.encode("ascii") + b"\x00" + b"\x03" + b"\x00" + image_data
    return b"APIC" + len(payload).to_bytes(4, "big") + b"\x00\x00" + payload


def _embed_mp3_cover(mp3_path, image_data, mime):
    """把 .ncm 封面写进 mp3 的 ID3v2 标签（支持 v2.3 / v2.4，替换已有 APIC）。"""
    data = bytearray(mp3_path.read_bytes())
    frame = _apic_frame(image_data, mime)

    if data[:3] != b"ID3":
        header = b"ID3" + b"\x03\x00" + b"\x00" + _syncsafe(len(frame))
        mp3_path.write_bytes(header + frame + data)
        return True
    if len(data) < 10:
        return False
    ver = data[3]
    if ver not in (3, 4):
        return False
    if ver == 4:
        tag_size = _unsyncsafe(data[6:10])
    else:
        tag_size = int.from_bytes(data[6:10], "big")
    frames_end = 10 + tag_size
    if frames_end > len(data):
        return False

    pos = 10
    kept = []
    while pos + 10 <= frames_end:
        frame_id = data[pos:pos + 4]
        if frame_id == b"\x00\x00\x00\x00":
            break                                # 到达 padding
        if ver == 4:
            fsize = _unsyncsafe(data[pos + 4:pos + 8])
        else:
            fsize = int.from_bytes(data[pos + 4:pos + 8], "big")
        if fsize <= 0 or pos + 10 + fsize > frames_end:
            break
        if frame_id != b"APIC":                  # 跳过旧封面帧
            kept.append(bytes(data[pos:pos + 10 + fsize]))
        pos += 10 + fsize

    new_frames = b"".join(kept) + frame          # 新封面追加到尾部
    if ver == 4:
        header = bytes(data[:6]) + _syncsafe(len(new_frames))
    else:
        header = bytes(data[:6]) + len(new_frames).to_bytes(4, "big")
    mp3_path.write_bytes(header + new_frames + data[frames_end:])
    return True


def parse_header(handle):
    """读取并解析 .ncm 头部，返回 (rc4_key, metadata, image_data)。

    调用后文件指针停在音频数据起点。头部非法时抛 ValueError。
    """
    header = handle.read(8)
    if header != MAGIC:
        raise ValueError("不是有效的 .ncm 文件（magic 不符）")

    handle.read(2)  # 固定 gap

    key_length = struct.unpack("<I", handle.read(4))[0]
    if not 0 < key_length <= 1024:
        raise ValueError(f"密钥长度异常：{key_length}")
    key_data = _xor(handle.read(key_length), 0x64)
    key_data = _unpad(aes_ecb_decrypt(key_data, CORE_KEY))[17:]
    if not key_data:
        raise ValueError("RC4 密钥为空，文件可能损坏")

    meta_length = struct.unpack("<I", handle.read(4))[0]
    if not 0 < meta_length <= 1 << 20:
        raise ValueError(f"元数据长度异常：{meta_length}")
    meta_raw = _xor(handle.read(meta_length), 0x63)
    if len(meta_raw) <= 22:
        raise ValueError("元数据格式异常")
    meta_plain = _unpad(aes_ecb_decrypt(base64.b64decode(meta_raw[22:]), META_KEY))
    metadata = json.loads(meta_plain.decode("utf-8")[6:])

    handle.read(4)  # crc32
    handle.read(5)  # gap
    image_size = struct.unpack("<I", handle.read(4))[0]
    if not 0 <= image_size <= 1 << 24:
        raise ValueError(f"封面尺寸异常：{image_size}")
    image_data = handle.read(image_size)

    return key_data, metadata, image_data


def decrypt_file(ncm_path, out_dir=None, embed_cover=True):
    """解密单个 .ncm 文件，输出同目录或 out_dir。

    返回 (输出文件路径, 元数据 dict)。embed_cover=True 时把封面写回
    flac（PICTURE 块）或 mp3（ID3v2 APIC），与 ncmdump 行为一致。
    """
    ncm_path = Path(ncm_path)
    if not ncm_path.is_file():
        raise FileNotFoundError(f"文件不存在：{ncm_path}")

    out_dir = Path(out_dir) if out_dir else ncm_path.parent
    out_dir.mkdir(parents=True, exist_ok=True)

    with open(ncm_path, "rb") as handle:
        key_data, metadata, image_data = parse_header(handle)
        key_box = _rc4_ksa(key_data)
        mask = _rc4_mask(key_box)

        first_chunk = handle.read(CHUNK_SIZE)
        if not first_chunk:
            raise ValueError("音频数据为空")
        ext = _detect_ext(_rc4_decrypt_chunk(first_chunk, mask), metadata.get("format"))
        out_path = out_dir / (ncm_path.stem + ext)

        with open(out_path, "wb") as out:
            out.write(_rc4_decrypt_chunk(first_chunk, mask))
            while True:
                chunk = handle.read(CHUNK_SIZE)
                if not chunk:
                    break
                out.write(_rc4_decrypt_chunk(chunk, mask))

    if embed_cover and image_data:
        mime = _detect_image_mime(image_data)
        if ext == ".flac":
            _embed_flac_cover(out_path, image_data, mime)
        elif ext == ".mp3":
            _embed_mp3_cover(out_path, image_data, mime)

    return out_path, metadata


def decrypt_many(ncm_files, out_dir=None, jobs=1, embed_cover=True):
    """批量解密，逐文件返回 (文件, 输出路径, 错误)。"""
    for ncm_file in ncm_files:
        try:
            out_path, _meta = decrypt_file(ncm_file, out_dir, embed_cover=embed_cover)
            yield ncm_file, out_path, None
        except Exception as exc:
            yield ncm_file, None, str(exc)


def main(argv=None):
    parser = argparse.ArgumentParser(description="纯标准库 .ncm 解密工具")
    parser.add_argument("target", nargs="?", help=".ncm 文件或目录")
    parser.add_argument("-o", "--output", default=None, help="输出目录（默认与源相同）")
    parser.add_argument("--no-cover", action="store_true", help="不回写封面")
    parser.add_argument("--self-test", action="store_true", help="运行 AES 向量自测后退出")
    args = parser.parse_args(argv)

    if args.self_test:
        sys.exit(0 if _self_test() else 1)

    if not args.target:
        parser.print_help()
        return 1

    target = Path(args.target)
    if target.is_dir():
        files = sorted(target.glob("*.ncm"), key=lambda p: p.name.lower())
    elif target.is_file():
        files = [target]
    else:
        print(f"❌ 路径不存在：{target}")
        return 1

    if not files:
        print(f"ℹ️ 没有找到 .ncm 文件")
        return 0

    ok = failed = 0
    for ncm_file, out_path, error in decrypt_many(files, args.output, embed_cover=not args.no_cover):
        if error:
            print(f"❌ {ncm_file.name}: {error}")
            failed += 1
        else:
            print(f"✅ {ncm_file.name} → {out_path.name}")
            ok += 1

    print(f"\n成功 {ok}，失败 {failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
