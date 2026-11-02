# NCM-Patch

**网易云音乐 .ncm 解密转换工具**：一键把 `.ncm` 批量转成通用 `.flac` / `.mp3`，自动回写歌曲信息与封面，零第三方依赖，Windows / macOS / Linux 全平台可用。

![Python](https://img.shields.io/badge/Python-3.8%2B-3776AB)
![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey)
![Dependencies](https://img.shields.io/badge/dependencies-zero%20third--party-brightgreen)

`ncm` 是网易云音乐下载的加密私有格式，普通播放器无法直接打开。本项目提供**纯标准库实现的解密引擎**（内置 AES-128-ECB + NCM 变体 RC4，无 `pycryptodome`、无 `ffmpeg`、无外部程序），还原无损 flac / 高码率 mp3，并写入标题、歌手、专辑和官方封面，是 `ncmdump` 的零依赖替代方案。

## 📥 下载（各平台直接可用）

前往 [Releases](https://github.com/Lizhenghe-Chen/NCM-Patch/releases) 下载对应平台的单文件产物，无需安装 Python：

| 平台 | 文件 | 说明 |
| --- | --- | --- |
| Windows | `ncm-patch-windows-x64.exe` | 双击即用，约 12 MB |
| macOS | `ncm-patch-macos-x64` | 终端 `chmod +x` 后运行 |
| Linux | `ncm-patch-linux-x64` | 终端 `chmod +x` 后运行 |

> 也可直接点此：**[下载 v1.0.0](https://github.com/Lizhenghe-Chen/NCM-Patch/releases/latest)**

## 🚀 使用教程

### Windows：双击即用

1. 下载 `ncm-patch-windows-x64.exe`，双击启动
2. 自动打开浏览器界面 `http://127.0.0.1:8765/`
3. 点「源目录」选择 `.ncm` 所在文件夹（或直接拖进去）
4. 点「扫描预览」→「开始转换」，等待完成

> 黑色窗口是服务进程，保持打开即可；用完关掉窗口就退出。

### macOS / Linux：终端运行

```bash
chmod +x ncm-patch-macos-x64   # 或 ncm-patch-linux-x64
./ncm-patch-macos-x64
# 然后浏览器访问 http://127.0.0.1:8765/
```

### 源码运行（任意平台，仅需 Python 3.8+）

```bash
git clone https://github.com/Lizhenghe-Chen/NCM-Patch.git
cd NCM-Patch

python ncm_web.py                                # 网页界面，自动打开浏览器
python ncm_flac_processor.py                     # 命令行：转换当前目录全部 .ncm
python ncm_flac_processor.py 源目录 -o 输出目录   # 指定输入输出
python ncm_core.py --self-test                   # 自检解密引擎
```

## 常见问题

- **为什么有的歌被跳过？** 输出目录已有同名 flac/mp3 即视为已转换，重复运行会跳过。
- **转出来的文件能导进手机/播放器吗？** flac、mp3 都是标准格式，绝大多数播放器直接识别，信息与封面已内置。
- **端口 8765 被占用？** `python ncm_web.py --port 8800`，访问对应端口。
- **「删除源文件」安全吗？** 转换成功后才删除对应 `.ncm`，建议首次不勾选、留备份。

## 工作原理（3 步）

1. 用内置密钥对 `.ncm` 头部 Key/Meta 区做 **AES-128-ECB** 解密，得到音频密钥与歌曲元数据（JSON）；
2. 用还原出的密钥对音频主体做 **NCM 变体 RC4** 流解密，得到原始 flac / mp3 流；
3. 把官方封面写回：flac 用 `PICTURE` 元数据块，mp3 用 `ID3v2 APIC` 帧。

> 格式细节参考 [unlock-music](https://gitee.com/convergers/unlock-music)、[ncmdump-py](https://pypi.org/project/ncmdump-py/)。本实现输出与主流 `ncmdump` 字节级一致。

## License

暂未指定，发布前将补充。
