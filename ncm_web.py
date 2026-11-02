"""本地 Web 界面：扫描并批量把 .ncm 转成 .flac/.mp3（只用标准库，无额外依赖）。

用法:
    python ncm_web.py                       # 自动打开 http://127.0.0.1:8765/
    python ncm_web.py --port 8800 --no-browser
    python ncm_web.py --picker web           # 只用网页内目录浏览，不弹系统对话框

目录不用手输：点路径行会弹系统目录对话框（Tk 不可用时回退到网页内浏览），
也可以把文件夹或 .ncm 文件直接拖进来，由后端按名字定位真实路径。

最近一次使用的参数保存在脚本同目录的 ncm_web_settings.json。
"""

import argparse
import json
import os
import queue
import sys
import threading
import time
import webbrowser
from concurrent.futures import ThreadPoolExecutor, as_completed
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote

from ncm_flac_processor import convert_one, scan_ncm_files

if getattr(sys, "frozen", False):
    # PyInstaller 打包模式：静态资源在解压临时目录，设置文件放 exe 旁边持久化
    BASE_DIR = Path(sys.executable).resolve().parent
    WEB_DIR = Path(sys._MEIPASS) / "web"
else:
    BASE_DIR = Path(__file__).resolve().parent
    WEB_DIR = BASE_DIR / "web"
SETTINGS_FILE = BASE_DIR / "ncm_web_settings.json"
MAX_LOG_LINES = 800
MAX_DROP_DIRS = 60
PICKER_MODE = "auto"


def default_dir():
    """跨平台默认目录：优先用户音乐目录，回退到主目录。"""
    home = Path.home()
    for candidate in (home / "Music", home / "音乐", home):
        try:
            if candidate.is_dir():
                return str(candidate)
        except OSError:
            continue
    return str(home)


DEFAULT_SETTINGS = {
    "source": default_dir(),
    "output": default_dir(),
    "jobs": 4,
    "remove_source": False,
}


def load_settings():
    try:
        data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return dict(DEFAULT_SETTINGS)
    settings = dict(DEFAULT_SETTINGS)
    settings.update({k: v for k, v in data.items() if k in DEFAULT_SETTINGS})
    return settings


def save_settings(values):
    try:
        SETTINGS_FILE.write_text(
            json.dumps(values, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except OSError:
        pass


def resolve_picker_mode(requested):
    if requested != "auto":
        return requested
    try:
        import tkinter  # noqa: F401

        return "native"
    except Exception:
        return "web"


def list_drives():
    drives = []
    if os.name == "nt":
        import ctypes

        bitmask = ctypes.windll.kernel32.GetLogicalDrives()
        for index in range(26):
            if bitmask & (1 << index):
                drives.append(f"{chr(65 + index)}:\\")
    else:
        drives.append("/")
    return [drive for drive in drives if Path(drive).is_dir()]


_DIALOG_QUEUE = queue.Queue()
_DIALOG_THREAD = None
_DIALOG_LOCK = threading.Lock()


def _dialog_worker():
    """Tk 只能在自己创建的线程里创建和销毁，所以所有对话框都交给这条常驻线程。"""
    while True:
        item = _DIALOG_QUEUE.get()
        try:
            if item is None:
                return
            initial_dir, result = item

            root = None
            try:
                import tkinter
                from tkinter import filedialog

                root = tkinter.Tk()
                root.withdraw()
                root.attributes("-topmost", True)
                root.update_idletasks()
                options = {"parent": root, "title": "选择目录", "mustexist": True}
                if initial_dir and Path(initial_dir).is_dir():
                    options["initialdir"] = initial_dir
                result["path"] = filedialog.askdirectory(**options)
            except Exception as exc:
                result["error"] = str(exc)
            finally:
                if root is not None:
                    try:
                        root.destroy()
                    except Exception:
                        pass
            result["event"].set()
        except Exception:
            continue


def pick_directory(initial_dir=""):
    """弹系统目录选择框，返回 (路径, 状态)，状态为 ok / cancelled / unavailable。"""
    global _DIALOG_THREAD

    try:
        import tkinter  # noqa: F401
    except Exception as exc:
        return None, f"unavailable: {exc}"

    with _DIALOG_LOCK:
        if _DIALOG_THREAD is None or not _DIALOG_THREAD.is_alive():
            _DIALOG_THREAD = threading.Thread(target=_dialog_worker, daemon=True)
            _DIALOG_THREAD.start()

    result = {"path": None, "error": None, "event": threading.Event()}
    _DIALOG_QUEUE.put((initial_dir, result))
    result["event"].wait()

    if result["error"]:
        return None, f"error: {result['error']}"
    if not result["path"]:
        return None, "cancelled"
    return str(Path(result["path"])), "ok"


def _children(path):
    """返回 (子目录路径列表, 小写文件名集合)；读不到时返回空。"""
    dirs, files = [], set()
    try:
        with os.scandir(path) as entries:
            for entry in entries:
                try:
                    if entry.is_dir():
                        dirs.append(entry.path)
                    else:
                        files.add(entry.name.lower())
                except OSError:
                    continue
    except OSError:
        return [], set()
    return dirs, files


def candidate_roots(extra_roots):
    """拖拽解析的搜索起点：先看当前源/输出目录的父目录，再看目录自身，最后才退到盘符根。"""
    nearby = []
    for raw in extra_roots:
        if not raw:
            continue
        path = Path(raw)
        for candidate in (path.parent, path):
            try:
                if candidate.is_dir() and str(candidate) not in nearby:
                    nearby.append(str(candidate))
            except OSError:
                continue
    drives = [drive for drive in list_drives() if drive not in nearby]
    return nearby, drives


def _search_roots(roots, name_lower):
    """按顺序搜索，某一层命中就停；同一层的多个同名目录仍交给上层消歧。"""
    for root in roots:
        dirs, files = _children(root)
        found = []
        if name_lower in files:
            found.append(root)
        for sub in dirs[:MAX_DROP_DIRS]:
            if os.path.basename(sub).lower() == name_lower:
                found.append(sub)
            elif name_lower.endswith(".ncm") and name_lower in _children(sub)[1]:
                found.append(sub)
        if found:
            return found
    return []


def resolve_drop(name, items, extra_roots):
    """浏览器拿不到绝对路径，只能凭拖入的文件夹名/文件名在候选根里定位。"""
    if not name:
        return {"error": "没有拿到拖入项的名称"}

    name_lower = name.lower()
    wanted = [item.lower() for item in items if item][:50]
    nearby, drives = candidate_roots(extra_roots)
    matches = _search_roots(nearby, name_lower) or _search_roots(drives, name_lower)

    seen, unique = set(), []
    for path in matches:
        key = path.lower()
        if key not in seen:
            seen.add(key)
            unique.append(path)

    if not unique:
        return {"error": f"没能在附近定位「{name}」，请点「选择…」指定"}
    if len(unique) == 1:
        return {"path": unique[0]}

    # 同名目录不止一个时，用拖入的子项名做消歧
    if wanted:
        scored = sorted(
            ((sum(1 for item in wanted if item in _children(p)[1]), p) for p in unique),
            key=lambda pair: pair[0],
            reverse=True,
        )
        if scored[0][0] > 0 and scored[0][0] > scored[1][0]:
            return {"path": scored[0][1]}

    return {"candidates": unique[:10]}


class Job:
    """一次转换任务的状态：工作线程写，HTTP 线程读。"""

    def __init__(self, scan, output, jobs, remove_source):
        self.scan = scan
        self.output = output
        self.jobs = max(1, jobs)
        self.remove_source = remove_source
        self.state = "running"
        self.total = len(scan.todo)
        self.done = 0
        self.converted = 0
        self.failed = 0
        self.removed = 0
        self.failures = []
        self.log_lines = []
        self.log_count = 0
        self.started_at = time.time()
        self.finished_at = None
        self.cancel = threading.Event()
        self.lock = threading.Lock()

    def log(self, message):
        line = f"{time.strftime('%H:%M:%S')}  {message}"
        with self.lock:
            self.log_lines.append(line)
            self.log_count += 1
            if len(self.log_lines) > MAX_LOG_LINES:
                del self.log_lines[: len(self.log_lines) - MAX_LOG_LINES]

    def snapshot(self, since=0):
        with self.lock:
            first_index = self.log_count - len(self.log_lines)
            offset = max(0, since - first_index)
            end = self.finished_at or time.time()
            return {
                "state": self.state,
                "total": self.total,
                "done": self.done,
                "converted": self.converted,
                "failed": self.failed,
                "removed": self.removed,
                "skipped": len(self.scan.skipped),
                "dest": str(self.scan.dest_path),
                "elapsed": round(end - self.started_at, 1),
                "failures": self.failures[-50:],
                "log": self.log_lines[offset:],
                "log_total": self.log_count,
            }

    def run(self):
        try:
            with ThreadPoolExecutor(max_workers=self.jobs) as pool:
                futures = {
                    pool.submit(
                        convert_one,
                        ncm_file,
                        self.scan.dest_path,
                        self.remove_source,
                        self.log,
                    ): ncm_file
                    for ncm_file in self.scan.todo
                }
                for future in as_completed(futures):
                    if self.cancel.is_set():
                        for pending in futures:
                            pending.cancel()
                        break

                    ncm_file = futures[future]
                    ok, err, was_removed = future.result()
                    with self.lock:
                        self.done += 1
                        if ok:
                            self.converted += 1
                            self.removed += 1 if was_removed else 0
                        else:
                            self.failed += 1
                            self.failures.append({"name": ncm_file.name, "error": err})

                    if ok:
                        self.log(f"✅ 成功: {ncm_file.name}")
                        if err:
                            self.log(f"⚠️  {err}")
                    else:
                        self.log(f"❌ 失败: {ncm_file.name}")
                        if err:
                            self.log(f"    {err}")
        except Exception as exc:  # 线程里不能把异常抛给 HTTP 线程
            self.log(f"❌ 任务异常终止: {exc}")
        finally:
            with self.lock:
                self.state = "cancelled" if self.cancel.is_set() else "done"
                self.finished_at = time.time()


class State:
    def __init__(self):
        self.settings = load_settings()
        self.lock = threading.Lock()
        self.job = None

    def scan(self, source, output):
        """校验路径并扫描，返回 ScanResult；参数不合法时抛 ValueError。"""
        if not source:
            raise ValueError("请填写源目录")
        source_path = Path(source)
        if not source_path.is_dir():
            raise ValueError(f"源目录不存在：{source_path}")
        if output:
            output_path = Path(output)
            if not output_path.is_dir():
                raise ValueError(f"输出目录不存在：{output_path}")
        return scan_ncm_files(source_path, output or None)


STATE = State()


class Handler(BaseHTTPRequestHandler):
    server_version = "ncm_web"
    sys_version = ""
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            self._send_static("index.html")
        elif path in ("/style.css", "/app.js"):
            self._send_static(path.lstrip("/"))
        elif path == "/api/settings":
            self._json(self._settings_payload())
        elif path == "/api/status":
            self._json(self._status_payload())
        elif path == "/api/browse":
            self._json(self._browse_payload(self._query_param("path")))
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        # 必须在分发前把请求体读完，否则 keep-alive 连接里残留的 body 会被当成下一个请求行
        data = self._read_json()
        if path == "/api/scan":
            self._handle_scan(data)
        elif path == "/api/start":
            self._handle_start(data)
        elif path == "/api/cancel":
            self._handle_cancel()
        elif path == "/api/pick-dir":
            self._handle_pick_dir(data)
        elif path == "/api/resolve-drop":
            self._handle_resolve_drop(data)
        else:
            self._json({"error": "not found"}, 404)

    # ---- payloads ----

    def _settings_payload(self):
        return {
            "settings": STATE.settings,
            "engine": {
                "name": "内置纯标准库解密引擎",
                "requirements": "仅需 Python 3.8+，无第三方依赖",
            },
            "picker": PICKER_MODE,
        }

    def _browse_payload(self, raw_path):
        drives = list_drives()
        if not raw_path:
            return {"path": "", "parent": None, "dirs": [], "drives": drives}

        path = Path(raw_path)
        if not path.is_dir():
            return {
                "path": "",
                "parent": None,
                "dirs": [],
                "drives": drives,
                "error": f"目录不存在：{raw_path}",
            }

        try:
            dirs = [
                {"name": entry.name, "path": entry.path}
                for entry in sorted(
                    (e for e in os.scandir(path) if e.is_dir()),
                    key=lambda e: e.name.lower(),
                )
            ]
        except OSError as exc:
            return {
                "path": str(path),
                "parent": None,
                "dirs": [],
                "drives": drives,
                "error": f"读取失败：{exc}",
            }

        parent = str(path.parent) if str(path.parent) != str(path) else None
        return {"path": str(path), "parent": parent, "dirs": dirs, "drives": drives}

    def _status_payload(self):
        since = self._query_param("since")
        job = STATE.job
        if job is None:
            return {"state": "idle", "log": [], "log_total": 0}
        return job.snapshot(int(since) if since.isdigit() else 0)

    # ---- actions ----

    def _handle_pick_dir(self, data):
        if PICKER_MODE == "web":
            self._json({"fallback": "web"})
            return

        start = str(data.get("start") or "").strip()
        picked, status = pick_directory(start)
        if status == "ok":
            self._json({"path": picked})
        elif status == "cancelled":
            self._json({"cancelled": True})
        else:
            self._json({"fallback": "web", "error": status})

    def _handle_resolve_drop(self, data):
        result = resolve_drop(
            str(data.get("name") or ""),
            [str(item) for item in (data.get("items") or [])],
            [str(root) for root in (data.get("roots") or [])],
        )
        self._json(result)

    def _handle_scan(self, data):
        try:
            scan = STATE.scan(str(data.get("source", "")).strip(), str(data.get("output", "")).strip())
        except ValueError as exc:
            self._json({"error": str(exc)}, 400)
            return
        except OSError as exc:
            self._json({"error": f"读取目录失败：{exc}"}, 400)
            return

        self._json(
            {
                "total": len(scan.ncm_files),
                "pending": len(scan.todo),
                "skipped": len(scan.skipped),
                "dest": str(scan.dest_path),
                "preview": [f.name for f in scan.todo[:200]],
            }
        )

    def _handle_start(self, data):
        source = str(data.get("source", "")).strip()
        output = str(data.get("output", "")).strip()

        try:
            jobs = int(data.get("jobs") or 4)
        except (TypeError, ValueError):
            jobs = 4
        remove_source = bool(data.get("remove_source"))

        with STATE.lock:
            if STATE.job is not None and STATE.job.state == "running":
                self._json({"error": "已有任务在运行，先等它结束或点「停止」"}, 409)
                return

            try:
                scan = STATE.scan(source, output)
            except ValueError as exc:
                self._json({"error": str(exc)}, 400)
                return
            except OSError as exc:
                self._json({"error": f"读取目录失败：{exc}"}, 400)
                return

            if not scan.todo:
                self._json(
                    {
                        "error": f"没有待转换的文件（已跳过 {len(scan.skipped)} 个）",
                        "skipped": len(scan.skipped),
                    },
                    400,
                )
                return

            job = Job(scan, output, jobs, remove_source)
            STATE.job = job
            threading.Thread(target=job.run, daemon=True).start()

        STATE.settings = {
            "source": source,
            "output": output,
            "jobs": max(1, jobs),
            "remove_source": remove_source,
        }
        save_settings(STATE.settings)

        self._json({"ok": True, "total": job.total, "skipped": len(scan.skipped)})

    def _handle_cancel(self):
        job = STATE.job
        if job is None or job.state != "running":
            self._json({"error": "当前没有运行中的任务"}, 400)
            return
        job.cancel.set()
        job.log("⏹️  已请求停止，正在进行的文件会跑完")
        self._json({"ok": True})

    # ---- helpers ----

    def _query_param(self, name):
        if "?" not in self.path:
            return ""
        for part in self.path.split("?", 1)[1].split("&"):
            key, _, value = part.partition("=")
            if key == name:
                return unquote(value)
        return ""

    def _read_json(self):
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return {}
        if length <= 0:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return {}

    def _send_static(self, name):
        path = (WEB_DIR / name).resolve()
        if not path.is_relative_to(WEB_DIR) or not path.is_file():
            self._json({"error": "not found"}, 404)
            return
        content_type = "text/html; charset=utf-8"
        if name.endswith(".css"):
            content_type = "text/css; charset=utf-8"
        elif name.endswith(".js"):
            content_type = "text/javascript; charset=utf-8"

        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, payload, status=200):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


def main():
    global PICKER_MODE

    parser = argparse.ArgumentParser(description="本地 Web 界面：批量转换 .ncm")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址（默认只允许本机）")
    parser.add_argument("--port", type=int, default=8765, help="端口（默认 8765）")
    parser.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    parser.add_argument(
        "--picker",
        choices=["auto", "native", "web"],
        default="auto",
        help="选目录方式：auto=能用系统对话框就用（默认），native=只用系统对话框，web=只用网页内浏览",
    )
    args = parser.parse_args()
    PICKER_MODE = resolve_picker_mode(args.picker)

    # GBK 控制台下遇到无法编码的字符时降级为替代符，而不是直接抛异常退出
    try:
        sys.stdout.reconfigure(errors="replace")
    except (AttributeError, ValueError):
        pass

    try:
        server = ThreadingHTTPServer((args.host, args.port), Handler)
    except OSError as exc:
        print(f"无法监听 {args.host}:{args.port}（{exc}），换个端口试试：--port 8800")
        return

    url = f"http://{args.host}:{args.port}/"
    print(f"服务已启动：{url}   按 Ctrl+C 退出")
    if not args.no_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已退出")
        sys.stdout.flush()
        # 跳过解释器收尾：Tk 的对话框线程在退出阶段会打印 Tcl_AsyncDelete 噪音
        os._exit(0)


if __name__ == "__main__":
    main()
