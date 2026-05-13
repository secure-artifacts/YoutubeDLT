# -*- coding: utf-8 -*-
"""
YouTube 下载工具（基于 PyQt6 + yt-dlp）
增强版：崩溃后可正常重启 + 全局异常捕获
"""
import sys
import os
import json
import datetime
import requests
import subprocess
import platform
import webbrowser
import traceback

from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QTextEdit, QPushButton, QMessageBox,
    QComboBox, QFileDialog, QCheckBox
)
from PyQt6.QtCore import QThread, pyqtSignal, QTimer, QEvent, Qt
from PyQt6.QtGui import QIcon, QCursor

try:
    from yt_dlp import YoutubeDL
    from yt_dlp.version import __version__ as yt_dlp_version
except ImportError:
    print("请先安装 yt-dlp：pip install yt-dlp")
    sys.exit(1)

# ================== 全局异常捕获 ==================
def global_exception_handler(exctype, value, tb):
    error_msg = "".join(traceback.format_exception(exctype, value, tb))
    print("=== 程序发生致命错误 ===", file=sys.stderr)
    print(error_msg, file=sys.stderr)
    
    try:
        log_path = os.path.join(os.path.expanduser("~"), "YoutubeDLT_crash.log")
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"\n=== {datetime.datetime.now()} ===\n")
            f.write(error_msg)
            f.write("\n" + "="*80 + "\n")
        print(f"崩溃日志已保存: {log_path}")
    except:
        pass

sys.excepthook = global_exception_handler

# ================== 配置路径 ==================
CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".youtube_downloader")
os.makedirs(CONFIG_DIR, exist_ok=True)
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")
GITHUB_API_URL = "https://api.github.com/repos/yt-dlp/yt-dlp/releases/latest"
GITHUB_RELEASES_URL = "https://github.com/yt-dlp/yt-dlp/releases/latest"
APP_GITHUB_REPO = "https://github.com/secure-artifacts/YoutubeDLT"
APP_RELEASES_URL = f"{APP_GITHUB_REPO}/releases/latest"
APP_GITHUB_API_URL = f"https://api.github.com/repos/secure-artifacts/YoutubeDLT/releases/latest"
APP_CURRENT_VERSION = "2.0.13"

# ================== 增强版单实例保护 ==================
LOCK_FILE = os.path.join(CONFIG_DIR, "app.lock")
PID_FILE = os.path.join(CONFIG_DIR, "app.pid")

def is_already_running():
    """增强版：程序崩溃后下次可正常启动"""
    if not os.path.exists(LOCK_FILE):
        return False

    try:
        with open(PID_FILE, 'r', encoding='utf-8') as f:
            old_pid = int(f.read().strip())
    except:
        remove_lock_file()
        return False

    # 检查进程是否真实存在
    try:
        if platform.system() == "Windows":
            import ctypes
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(1, False, old_pid)
            if handle:
                kernel32.CloseHandle(handle)
                return True
            else:
                remove_lock_file()
                return False
        else:
            # Linux / macOS
            if os.path.exists(f"/proc/{old_pid}"):
                try:
                    with open(f"/proc/{old_pid}/comm", 'r') as f:
                        comm = f.read().strip().lower()
                    if "python" in comm or "youtubedlt" in comm:
                        return True
                except:
                    pass
            remove_lock_file()
            return False
    except:
        remove_lock_file()
        return False


def create_lock_file():
    """创建锁文件并记录 PID"""
    try:
        pid = os.getpid()
        with open(PID_FILE, 'w', encoding='utf-8') as f:
            f.write(str(pid))
        with open(LOCK_FILE, 'w', encoding='utf-8') as f:
            f.write(str(pid))
    except:
        pass


def remove_lock_file():
    """清理锁文件"""
    for fpath in (LOCK_FILE, PID_FILE):
        try:
            if os.path.exists(fpath):
                os.remove(fpath)
        except:
            pass


# ================== 单实例检查 ==================
if is_already_running():
    print("程序已经在运行中！")
    sys.exit(0)

create_lock_file()

# ================== 其他函数 ==================
def get_default_downloads():
    home = os.path.expanduser("~")
    if os.name == 'nt':
        try:
            import winreg
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                 r"Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders")
            path = winreg.QueryValueEx(key, "{374DE290-123F-4565-9164-39C4925E467B}")[0]
            winreg.CloseKey(key)
            return path
        except:
            return os.path.join(home, "Downloads")
    else:
        return os.path.join(home, "Downloads")


class ConfigManager:
    @staticmethod
    def load():
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    return (
                        data.get('download_path', get_default_downloads()),
                        data.get('last_quality', "最高质量（推荐）"),
                        data.get('cookie_file', '')
                    )
            except:
                pass
        return get_default_downloads(), "最高质量（推荐）", ""

    @staticmethod
    def save(key, value):
        data = {}
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                    data = json.load(f)
            except:
                pass
        data[key] = value
        try:
            with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except:
            pass

    @staticmethod
    def save_download_path(path):
        ConfigManager.save('download_path', path)

    @staticmethod
    def save_quality(quality):
        ConfigManager.save('last_quality', quality)

    @staticmethod
    def save_cookie_file(path):
        ConfigManager.save('cookie_file', path)


# ================== 其余代码保持不变（VersionChecker、DownloadWorker、MainWindow）==================
class VersionChecker:
    @staticmethod
    def get_latest_version(api_url):
        try:
            r = requests.get(api_url, timeout=8)
            if r.status_code == 200:
                return r.json()['tag_name'].lstrip('v').strip()
        except:
            pass
        return None


class DownloadWorker(QThread):
    # ... 你原来的 DownloadWorker 代码保持不变 ...
    log = pyqtSignal(str)
    status = pyqtSignal(str)
    current_file = pyqtSignal(str)
    finished = pyqtSignal(bool, str)

    def __init__(self, url, save_dir, quality_mode, download_playlist=False, cookie_file=None):
        super().__init__()
        self.url = url
        self.save_dir = save_dir
        self.quality_mode = quality_mode
        self.download_playlist = download_playlist
        self.cookie_file = cookie_file
        self._stop_requested = False

    def request_stop(self):
        self._stop_requested = True

    def run(self):
        # 你原来的 run() 方法完整保留
        def hook(d):
            if self._stop_requested:
                raise Exception("用户手动暂停")
            if d['status'] == 'downloading':
                try:
                    percent = d.get('_percent_str', '0%')
                    speed = d.get('_speed_str', '??MiB/s')
                    eta = d.get('_eta_str', '??:??')
                    line = f"[download] {percent} of {d.get('downloaded_bytes', '?')} at {speed} ETA {eta}"
                    self.log.emit(line)
                except:
                    pass
            elif d['status'] == 'finished':
                self.log.emit("[download] 100% 下载完成")
                self.status.emit("处理中...")
            if 'filename' in d:
                self.current_file.emit(os.path.basename(d['filename']))

        # 格式设置、ffmpeg 查找、opts 等保持你原来的代码
        format_str = "bestvideo*+bestaudio/best"
        q = self.quality_mode
        if q == "1080p（或更低）":
            format_str = "bestvideo[height<=1080]+bestaudio/best[height<=1080]/best"
        elif q == "720p（或更低）":
            format_str = "bestvideo[height<=720]+bestaudio/best[height<=720]/best"
        elif q == "480p（或更低）":
            format_str = "bestvideo[height<=480]+bestaudio/best[height<=480]/best"
        elif q == "最小体积（适合流量少）":
            format_str = "bestvideo[height<=360][vcodec^=avc1]+bestaudio[ext=m4a]/best[ext=mp4]"
        elif q == "仅音频（MP3 192kbps）":
            format_str = "bestaudio/best"

        import shutil
        ffmpeg_path = shutil.which('ffmpeg')
        if not ffmpeg_path and getattr(sys, 'frozen', False):
            base_path = sys._MEIPASS
            if sys.platform == "win32":
                candidate = os.path.join(base_path, "ffmpeg.exe")
            else:
                candidate = os.path.join(base_path, "ffmpeg")
            if os.path.exists(candidate):
                ffmpeg_path = candidate
                os.environ["PATH"] = os.path.dirname(ffmpeg_path) + os.pathsep + os.environ.get("PATH", "")

        has_ffmpeg = bool(ffmpeg_path)

        opts = {
            'outtmpl': os.path.join(self.save_dir, '%(title)s.%(ext)s'),
            'format': format_str,
            'progress_hooks': [hook],
            'continuedl': True,
            'quiet': True,
            'no_warnings': True,
            'retries': 10,
            'fragment_retries': 10,
            'noplaylist': not self.download_playlist,
        }

        if not has_ffmpeg:
            self.log.emit("[警告] 未检测到 ffmpeg，已自动切换为不合并模式")
            opts['merge_output_format'] = None
            if q == "仅音频（MP3 192kbps）":
                opts['postprocessors'] = [{'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3', 'preferredquality': '192'}]
        else:
            if q != "仅音频（MP3 192kbps）":
                opts['merge_output_format'] = 'mp4'
            else:
                opts['postprocessors'] = [{'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3', 'preferredquality': '192'}]

        if self.cookie_file and os.path.exists(self.cookie_file):
            opts['cookiefile'] = self.cookie_file
            self.log.emit(f"[Cookie] 使用 Cookie 文件：{os.path.basename(self.cookie_file)}")

        try:
            os.makedirs(self.save_dir, exist_ok=True)
            self.status.emit("下载中...")
            self.log.emit("[开始] 正在提取信息并准备下载...")
           
            with YoutubeDL(opts) as ydl:
                ydl.download([self.url])
            if not self._stop_requested:
                self.finished.emit(True, "下载 & 处理完成")
            else:
                self.finished.emit(False, "已暂停（支持断点续传）")
        except Exception as e:
            msg = str(e)
            if "用户手动暂停" in msg:
                self.finished.emit(False, "已暂停（支持断点续传）")
            else:
                self.finished.emit(False, f"发生错误：{msg}")


# MainWindow 类保持你原来的代码（这里省略以节省篇幅，你直接复制粘贴你原来的 MainWindow 即可）
# ... [你的 MainWindow 完整代码] ...

class MainWindow(QWidget):
    # ================== 你原来的 MainWindow 代码（保持不变）==================
    def __init__(self):
        super().__init__()
        self.setWindowTitle("YouTube 下载工具")
        self.setFixedSize(780, 540)
        icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app_icon.ico")
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))
        self.download_path, last_quality, self.cookie_file = ConfigManager.load()
        self.worker = None
        self.pending_urls = []
        self.total_count = 0
        self.finished_count = 0
        self.current_version = APP_CURRENT_VERSION
        self.init_ui()
        index = self.combo_quality.findText(last_quality)
        if index >= 0:
            self.combo_quality.setCurrentIndex(index)
        if self.cookie_file and os.path.exists(self.cookie_file):
            self.cb_cookie.setChecked(True)
            self.lbl_cookie_file.setText(f"已加载：{os.path.basename(self.cookie_file)}")
        self.combo_quality.currentTextChanged.connect(ConfigManager.save_quality)
        QTimer.singleShot(1500, self.check_app_update)
        self.check_latest_version()
        self.version_timer = QTimer(self)
        self.version_timer.setInterval(86400000)
        self.version_timer.timeout.connect(self.check_latest_version)
        self.version_timer.start()

    # 下面 init_ui、其他方法全部使用你原来的代码...
    # （为节省长度，这里不再重复贴，保持你提供的原代码即可）

# ================== 程序入口 ==================
if __name__ == "__main__":
    try:
        if platform.system() == 'Windows':
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('com.yt.downloader.v4')
        
        app = QApplication(sys.argv)
        app.setStyle("Fusion")
        window = MainWindow()
        window.show()
        exit_code = app.exec()
        remove_lock_file()
        sys.exit(exit_code)
    except Exception as e:
        print("启动失败:", e)
        remove_lock_file()
        sys.exit(1)
