# NCM-Patch

把网易云音乐下载的 `.ncm` 文件批量转换为通用 `.flac` / `.mp3` 的开源工具，自动写入歌曲信息与封面。

![Python](https://img.shields.io/badge/Python-3.8%2B-3776AB)
![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey)
![Dependencies](https://img.shields.io/badge/dependencies-zero%20third--party-brightgreen)
![Engine](https://img.shields.io/badge/engine-pure%20stdlib-4B8BBE)

网易云音乐下载的无损/高码率歌曲以 `.ncm` 私有格式加密存储，无法直接在其他播放器或设备上使用。本项目提供一套**零第三方依赖**的纯 Python 解密引擎（`ncm_core.py`），并附带网页界面与命令行两种使用方式，把 `.ncm` 还原为携带完整元数据（标题、歌手、专辑、封面）的标准音频文件。

## 功能特性

- **零第三方依赖**：核心解密引擎完全用 Python 标准库实现（自实现 AES-128-ECB 解密 + NCM 变体 RC4），无 `pycryptodome`、无 `ffmpeg`、无外部可执行文件
- **跨平台**：Windows / macOS / Linux 均可源码运行；另有各平台单文件打包产物
- **批量并发**：默认 4 线程并发转换，支持大量文件快速处理
- **封面与元数据回写**：自动写入标题、歌手、专辑与官方封面图（flac 写 PICTURE 块，mp3 写 ID3v2 APIC）
- **安全可重复**：以输出目录为准做跳过判断，已有同名 `.flac` / `.mp3` 的歌曲自动跳过，重复运行零风险
- **网页界面**：本地 Web 操作，支持拖拽选目录、扫描预览、实时进度与日志；任务在服务端后台运行，关掉网页不中断
- **可选删除源文件**：转换成功后删除 `.ncm`（默认关闭）
- **内置自检**：`--self-test` 使用 NIST 官方 AES 向量验证解密引擎正确性

## 快速开始

### 方式一：Windows 双击即用（推荐）

从 [Releases](https://github.com/Lizhenghe-Chen/NCM-Patch/releases) 下载 `ncm-patch-windows-x64.exe`（单文件，约 12 MB），双击启动：

1. 自动打开浏览器界面 `http://127.0.0.1:8765/`
2. 点「源目录」选择 `.ncm` 所在文件夹（或直接把文件夹 / `.ncm` 文件拖进去）
3. 「输出目录」留空表示与源目录相同
4. 点「扫描预览」→「开始转换」，等待完成

> 黑色窗口保持打开是正常的：它是服务进程。用完按 `Ctrl+C` 或直接关闭窗口即可退出。

### 方式二：源码运行（Windows / macOS / Linux）

只需本机有 **Python 3.8+**，无需安装任何第三方包：

```bash
# 网页界面（自动打开浏览器）
python ncm_web.py

# 命令行批量转换
python ncm_flac_processor.py                    # 转换当前目录全部 .ncm
python ncm_flac_processor.py /path/to/folder -o /path/to/output
```

## 命令行参考

### `ncm_flac_processor.py` — 批量转换器

| 参数 | 说明 | 默认 |
| --- | --- | --- |
| `target_dir` | 要扫描的目录 | 当前目录 `.` |
| `-o, --output` | 输出目录 | 与 `.ncm` 同目录 |
| `-j, --jobs` | 并发转换数，`1` 为顺序执行 | `4` |
| `-v, --verbose` | 打印每个被跳过文件的明细 | 关 |
| `-m, --remove` | 转换成功后删除原始 `.ncm` | 关 |

```bash
# 示例：4 线程转换，输出到 /music/flac，并删除源文件
python ncm_flac_processor.py /downloads/ncm -o /music/flac -j 4 -m
```

### `ncm_core.py` — 底层解密引擎

| 参数 | 说明 |
| --- | --- |
| `target` | `.ncm` 文件或目录 |
| `-o, --output` | 输出目录（默认与源相同） |
| `--no-cover` | 不回写封面 |
| `--self-test` | 运行 NIST AES 向量自测后退出 |

```bash
# 自检引擎正确性
python ncm_core.py --self-test
```

### `ncm_web.py` — 网页界面服务

| 参数 | 说明 | 默认 |
| --- | --- | --- |
| `--host` | 监听地址 | `127.0.0.1`（仅本机） |
| `--port` | 端口 | `8765` |
| `--no-browser` | 不自动打开浏览器 | 自动打开 |
| `--picker` | 目录选择方式：`auto` / `native` / `web` | `auto` |

## 工作原理

`.ncm` 文件结构固定（格式长期稳定，被多个开源项目公开分析并交叉验证）：

```
┌──────────────┬──────────────┬──────────────┬──────────────────┐
│  Magic       │  Key 区       │  Meta 区      │  音频主体          │
│  "CTENFDAM"  │  AES 加密的   │  AES 加密的   │  NCM 变体 RC4     │
│              │  歌曲 key     │  元数据 JSON  │  加密的音频流       │
└──────────────┴──────────────┴──────────────┴──────────────────┘
```

1. **头部校验**：读取 8 字节魔数 `CTENFDAM`，确认是合法 `.ncm`
2. **密钥解密**：用内置 `CORE_KEY` 对 Key 区做 **AES-128-ECB** 解密，再异或还原出真正的音频密钥（RC4 key）
3. **元数据解密**：用内置 `META_KEY` 对 Meta 区做 AES-128-ECB 解密，得到 JSON（标题、歌手、专辑、封面）
4. **音频解密**：用还原出的 key 对音频主体做 NCM 变体 RC4 流解密，得到原始 FLAC / MP3 流
5. **封面回写**：把官方封面嵌入输出文件——flac 写为标准 `PICTURE` 元数据块（与官方工具字节级一致），mp3 写为 `ID3v2` `APIC` 帧

> 关键点：AES 只用于解密体积很小的 Key 区与 Meta 区，因此自实现一个 AES-128-ECB 解密即可完全避开第三方加密库依赖；音频主体的 RC4 是流密码，用标准库几行即可实现。

## 项目结构

```
NCM-Patch/
├── ncm_core.py               # 纯标准库解密引擎（可独立使用）
├── ncm_flac_processor.py     # 命令行批量转换器
├── ncm_web.py                # 本地网页界面服务
├── NCM转换台.spec            # PyInstaller 构建配置（重新打包用）
├── web/
│   ├── index.html            # 前端页面
│   ├── app.js                # 前端逻辑（选目录 / 拖拽 / 进度轮询）
│   └── style.css             # 样式
├── .github/workflows/
│   └── release.yml           # 打 tag 自动构建三平台产物并发布 Release
├── start_web.cmd             # Windows 双击启动脚本（源码方式）
├── vip_sound_pairing.py      # 辅助脚本：.ncm 与已有 .flac/.lrc 配对整理（可选，需 send2trash）
└── README.md
```

> 各平台打包产物（`ncm-patch-windows-x64.exe` / `ncm-patch-macos-x64` / `ncm-patch-linux-x64`）由 GitHub Actions 在打 tag 时自动构建，见 [Releases](https://github.com/Lizhenghe-Chen/NCM-Patch/releases)，不随源码分发。

## 兼容性

| 项目 | 说明 |
| --- | --- |
| Python | ≥ 3.8（仅标准库） |
| 系统 | Windows / macOS / Linux（源码）；各平台（打包产物） |
| 输入 | 网易云音乐下载的 `.ncm` 文件 |
| 输出 | 原始音频流对应的 `.flac`（无损）或 `.mp3`（有损），自动匹配 |

## 验证与质量

- **AES 正确性**：通过 NIST 官方 AES-128 测试向量自检
- **样本覆盖**：全库 1172 个真实 `.ncm` 文件，1171 个头部解析成功
- **字节级对齐**：flac 输出与主流参考实现 `ncmdump` **逐字节一致**（含封面元数据块布局）；mp3 输出音频区逐字节一致、封面干净替换为官方封面
- **端到端**：网页界面 扫描 → 转换 → 状态轮询 全链路验证通过；打包后的 exe 独立运行验证通过

## 常见问题

**问：为什么有些歌曲被跳过？**
答：跳过判断以输出目录为准——输出目录里已有同名 `.flac` / `.mp3` 即视为已转换。重复运行、或源目录混有已转换文件时这是预期行为。

**问：勾选「删除源文件」安全吗？**
答：转换成功后才会删除对应 `.ncm`。建议首次使用时先不勾选，确认输出无误后再批量删除，并为重要文件保留备份。

**问：exe 的黑色窗口能关吗？**
答：可以，那就是服务进程本身。关闭窗口即退出服务，不影响已完成转换的文件。

**问：端口 8765 被占用怎么办？**
答：改用其他端口，例如 `python ncm_web.py --port 8800`，浏览器访问 `http://127.0.0.1:8800/`。

**问：macOS / Linux 能用吗？**
答：可以，用源码方式运行（见「快速开始 · 方式二」），仅需系统自带的 Python 3.8+；也可下载对应平台的打包产物。

**问：转换后的文件能导入 iPhone / Android / 车载播放器吗？**
答：`.flac` / `.mp3` 都是标准格式，绝大多数播放器可直接识别，歌曲信息与封面已内置。

## 开发与贡献

本地开发：

```bash
git clone <repo-url>
cd NCM-Patch
python ncm_core.py --self-test   # 先确认引擎自检通过
python ncm_flac_processor.py <测试目录>
```

重新打包 Windows 单文件 exe（需要 Python 3.8+ 与 PyInstaller）：

```bash
pip install pyinstaller
python -m PyInstaller NCM转换台.spec
# 产物输出到 dist/NCM转换台.exe
```

推送 `v*` 格式的 tag 时，GitHub Actions 会自动在 Windows / macOS / Linux 三个平台分别构建并发布到 Releases。

贡献指南：

1. Fork 本仓库并创建特性分支
2. 改动请附带对应的验证方式（AES 自检、真实 `.ncm` 转换对比）
3. 提交 PR 前先运行 `python ncm_core.py --self-test` 与一次真实转换冒烟测试
4. 重大变更请先开 Issue 讨论

## 致谢

- [unlock-music](https://gitee.com/convergers/unlock-music) — `.ncm` 格式逆向与参考实现
- [ncmdump-py](https://pypi.org/project/ncmdump-py/) — PyPI 上的纯 Python 参考实现
- 主流 `ncmdump` 参考实现 — 用于本项目的输出字节级对比验证

## License

本仓库暂未指定开源许可证。如需对外发布，请先补充 `LICENSE` 文件并更新本段说明。
