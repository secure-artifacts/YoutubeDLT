# -*- coding: utf-8 -*-
"""
YouTube 下载工具（基于 PyQt6 + yt-dlp）
- 支持单视频 / 播放列表 / 频道
- 支持一次粘贴多个链接（多行，每行一个）
- 频道链接 + 勾选播放列表时会有警告
- 逐个串行下载
"""
import sys
import os
import json
import datetime
import requests
import subprocess
import platform
import webbrowser
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

# ================== 配置路径 ==================
CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".youtube_downloader")
os.makedirs(CONFIG_DIR, exist_ok=True)
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")
GITHUB_API_URL = "https://api.github.com/repos/yt-dlp/yt-dlp/releases/latest"
GITHUB_RELEASES_URL = "https://github.com/yt-dlp/yt-dlp/releases/latest"

# 单实例锁
LOCK_FILE = os.path.join(CONFIG_DIR, "app.lock")

def is_already_running():
    if os.path.exists(LOCK_FILE):
        try:
            fd = os.open(LOCK_FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
            os.close(fd)
            return False
        except FileExistsError:
            return True
    return False

def create_lock_file():
    try:
        open(LOCK_FILE, 'w').close()
    except:
        pass

def remove_lock_file():
    try:
        if os.path.exists(LOCK_FILE):
            os.remove(LOCK_FILE)
    except:
        pass

if is_already_running():
    print("程序已经在运行中！")
    sys.exit(0)
create_lock_file()

# 默认下载路径
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
                        data.get('last_quality', "最高质量（推荐）")
                    )
            except:
                pass
        return get_default_downloads(), "最高质量（推荐）"

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

class VersionChecker:
    @staticmethod
    def get_latest_version():
        try:
            r = requests.get(GITHUB_API_URL, timeout=8)
            if r.status_code == 200:
                return r.json()['tag_name'].lstrip('v').strip()
        except:
            pass
        return None

class DownloadWorker(QThread):
    log = pyqtSignal(str)
    status = pyqtSignal(str)
    current_file = pyqtSignal(str)
    finished = pyqtSignal(bool, str)

    def __init__(self, url, save_dir, quality_mode, download_playlist=False):
        super().__init__()
        self.url = url
        self.save_dir = save_dir
        self.quality_mode = quality_mode
        self.download_playlist = download_playlist
        self._stop_requested = False

    def request_stop(self):
        self._stop_requested = True

    def run(self):
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
                self.log.emit("开始合并/转换（此阶段无精确进度，请耐心等待）...")
                self.status.emit("合并中...")
            if 'filename' in d:
                self.current_file.emit(os.path.basename(d['filename']))

        format_str = "bestvideo*+bestaudio/best"
        merge_format = "mp4"
        postprocessors = []
        q = self.quality_mode
        if q == "1080p（或更低）":
            format_str = "bestvideo[height<=1080]+bestaudio/best[height<=1080]/best"
        elif q == "720p（或更低）":
            format_str = "bestvideo[height<=?720]+bestaudio/best[height<=?720]/best"
        elif q == "480p（或更低）":
            format_str = "bestvideo[height<=?480]+bestaudio/best[height<=?480]/best"
        elif q == "最小体积（适合流量少）":
            format_str = "bestvideo[height<=?360][vcodec^=avc1]+bestaudio[ext=m4a]/best[ext=mp4]"
        elif q == "仅音频（MP3 192kbps）":
            format_str = "bestaudio/best"
            merge_format = None
            postprocessors = [{'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3', 'preferredquality': '192'}]

        opts = {
            'outtmpl': os.path.join(self.save_dir, '%(title)s.%(ext)s'),
            'format': format_str,
            'merge_output_format': merge_format,
            'postprocessors': postprocessors,
            'progress_hooks': [hook],
            'continuedl': True,
            'quiet': True,
            'no_warnings': True,
            'retries': 10,
            'fragment_retries': 10,
            'noplaylist': not self.download_playlist,
        }

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

class MainWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("YouTube 下载工具")
        self.setFixedSize(780, 480)

        icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app_icon.ico")
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))

        self.download_path, last_quality = ConfigManager.load()
        self.worker = None
        self.pending_urls = []
        self.total_count = 0
        self.finished_count = 0
        self.current_url = None
        self.current_quality = None
        self.current_playlist = False
        self.is_paused = False
        self.current_version = yt_dlp_version
        self.latest_version = "检查中..."

        self.init_ui()
        index = self.combo_quality.findText(last_quality)
        if index >= 0:
            self.combo_quality.setCurrentIndex(index)
        self.combo_quality.currentTextChanged.connect(ConfigManager.save_quality)

        self.check_latest_version()
        self.version_timer = QTimer(self)
        self.version_timer.setInterval(86400000)
        self.version_timer.timeout.connect(self.check_latest_version)
        self.version_timer.start()

    def init_ui(self):
        layout = QVBoxLayout()
        layout.setSpacing(12)

        # 保存路径
        path_layout = QHBoxLayout()
        path_layout.addWidget(QLabel("保存到："))
        self.lbl_path = QLabel(self.download_path)
        self.lbl_path.setStyleSheet("font-weight:bold; color:#0066cc;")
        path_layout.addWidget(self.lbl_path, 1)
        btn_choose = QPushButton("更改文件夹")
        btn_choose.clicked.connect(self.choose_path)
        path_layout.addWidget(btn_choose)
        btn_open = QPushButton("打开文件夹")
        btn_open.setStyleSheet("background-color: #4CAF50; color: white;")
        btn_open.clicked.connect(self.open_folder)
        path_layout.addWidget(btn_open)
        layout.addLayout(path_layout)

        # 版本信息
        version_layout = QHBoxLayout()
        version_layout.addWidget(QLabel("当前 yt-dlp 版本："))
        lbl_current = QLabel(self.current_version)
        lbl_current.setStyleSheet("font-weight:bold;")
        version_layout.addWidget(lbl_current)
        version_layout.addStretch(1)
        version_layout.addWidget(QLabel("最新稳定版："))
        self.lbl_latest = QLabel(self.latest_version)
        self.lbl_latest.setStyleSheet("font-weight:bold; color:#0066cc;")
        self.lbl_latest.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.lbl_latest.mousePressEvent = self.open_latest_release
        version_layout.addWidget(self.lbl_latest)
        layout.addLayout(version_layout)

        # 输入区域
        input_layout = QHBoxLayout()
        input_layout.addWidget(QLabel("链接："))
        self.edit_url = QTextEdit()
        self.edit_url.setPlaceholderText(
            "支持一次粘贴多个链接（每行一个）\n"
            "示例：\n"
            "https://www.youtube.com/watch?v=abc123\n"
            "https://youtu.be/xyz789\n"
            "https://www.youtube.com/@频道名  ← 频道链接 + 勾选播放列表 = 下载该频道所有视频（慎用）"
        )
        self.edit_url.setAcceptRichText(False)
        self.edit_url.setTabChangesFocus(True)
        self.edit_url.setMinimumHeight(100)
        self.edit_url.setMaximumHeight(160)
        input_layout.addWidget(self.edit_url, 1)

        input_layout.addWidget(QLabel("质量："))
        self.combo_quality = QComboBox()
        self.combo_quality.addItems([
            "最高质量（推荐）",
            "1080p（或更低）",
            "720p（或更低）",
            "480p（或更低）",
            "最小体积（适合流量少）",
            "仅音频（MP3 192kbps）"
        ])
        input_layout.addWidget(self.combo_quality)
        layout.addLayout(input_layout)

        # 播放列表选项
        playlist_layout = QHBoxLayout()
        self.cb_playlist = QCheckBox("下载整个播放列表 / 频道全部视频（如果链接包含）")
        self.cb_playlist.setChecked(False)
        playlist_layout.addWidget(self.cb_playlist)
        playlist_layout.addStretch(1)
        layout.addLayout(playlist_layout)

        # 按钮
        btn_layout = QHBoxLayout()
        self.btn_main = QPushButton("开始下载")
        self.btn_main.setFixedHeight(50)
        self.btn_main.clicked.connect(self.toggle_main)
        btn_layout.addWidget(self.btn_main)

        self.btn_cancel = QPushButton("取消全部")
        self.btn_cancel.setEnabled(False)
        self.btn_cancel.clicked.connect(self.cancel_all)
        btn_layout.addWidget(self.btn_cancel)
        layout.addLayout(btn_layout)

        self.lbl_status = QLabel("就绪")
        self.lbl_status.setStyleSheet("font-size:14px; font-weight:bold;")
        layout.addWidget(self.lbl_status)

        self.lbl_progress = QLabel("进度： - / - ")
        layout.addWidget(self.lbl_progress)

        self.lbl_file = QLabel("当前文件： - ")
        layout.addWidget(self.lbl_file)

        layout.addWidget(QLabel("日志："))
        self.log_area = QTextEdit()
        self.log_area.setReadOnly(True)
        layout.addWidget(self.log_area)

        self.setLayout(layout)

    def open_latest_release(self, event):
        webbrowser.open(GITHUB_RELEASES_URL)

    def check_latest_version(self):
        def fetch():
            latest = VersionChecker.get_latest_version()
            if latest:
                QApplication.instance().postEvent(self, UpdateVersionEvent(latest))
        from threading import Thread
        Thread(target=fetch, daemon=True).start()

    def customEvent(self, event):
        if isinstance(event, UpdateVersionEvent):
            self.update_version_label(event.latest)

    def update_version_label(self, latest):
        self.latest_version = latest
        text = latest
        style = "font-weight:bold; color:#27ae60;"
        if self.current_version != latest:
            text += " （点击查看更新）"
            style = "font-weight:bold; color:#e67e22;"
        self.lbl_latest.setStyleSheet(style)
        self.lbl_latest.setText(text)

    def _connect_worker(self):
        if not self.worker:
            return
        self.worker.log.connect(self.append_log)
        self.worker.status.connect(self.lbl_status.setText)
        self.worker.current_file.connect(lambda f: self.lbl_file.setText(f"当前文件：{f}"))
        self.worker.finished.connect(self.on_one_finished)

    def choose_path(self):
        folder = QFileDialog.getExistingDirectory(
            self, "选择保存文件夹", self.download_path,
            QFileDialog.Option.ShowDirsOnly | QFileDialog.Option.DontResolveSymlinks
        )
        if folder:
            self.download_path = folder
            self.lbl_path.setText(folder)
            ConfigManager.save_download_path(folder)
            self.append_log(f"保存路径已更新：{folder}")

    def open_folder(self):
        path = self.download_path
        if not os.path.isdir(path):
            QMessageBox.warning(self, "提示", f"文件夹不存在：\n{path}")
            return
        try:
            if platform.system() == 'Windows':
                os.startfile(path)
            elif platform.system() == 'Darwin':
                subprocess.call(['open', path])
            else:
                subprocess.call(['xdg-open', path])
            self.append_log(f"已打开文件夹：{path}")
        except Exception as e:
            QMessageBox.warning(self, "错误", f"无法打开文件夹：\n{str(e)}")

    def toggle_main(self):
        raw_text = self.edit_url.toPlainText().strip()
        if not raw_text:
            QMessageBox.warning(self, "提示", "请至少输入一个链接")
            return

        urls = [line.strip() for line in raw_text.splitlines() if line.strip() and line.strip().startswith(('http://', 'https://'))]

        if not urls:
            QMessageBox.warning(self, "提示", "没有找到有效的 YouTube 链接")
            return

        # 频道 + 批量警告（只针对单个频道链接）
        is_channel = any(x in urls[0].lower() for x in ['/channel/', '/@', '/c/', '/user/'])
        if is_channel and self.cb_playlist.isChecked() and len(urls) == 1:
            reply = QMessageBox.question(
                self, "频道批量下载警告",
                "检测到频道链接 + 已勾选“下载整个播放列表”，\n"
                "这将下载该频道**所有**上传视频（可能几百上千个），确定继续吗？",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No
            )
            if reply == QMessageBox.No:
                return

        quality = self.combo_quality.currentText()
        playlist = self.cb_playlist.isChecked()

        if self.worker and self.worker.isRunning():
            self.worker.request_stop()
            self.btn_main.setText("继续下载")
            self.append_log("已请求暂停当前任务...")
            self.is_paused = True
            self.btn_cancel.setEnabled(True)
            return

        # 新任务开始
        if self.pending_urls or self.total_count == 0:
            self.pending_urls = urls[1:] if len(urls) > 1 else []
            self.total_count = len(urls)
            self.finished_count = 0
            # 可选：self.log_area.clear()

        self.start_next_download(urls[0], quality, playlist)

    def start_next_download(self, url, quality, playlist):
        self.current_url = url
        self.current_quality = quality
        self.current_playlist = playlist

        if self.worker and self.worker.isRunning():
            self.worker.quit()
            self.worker.wait(3000)

        self.worker = DownloadWorker(url, self.download_path, quality, playlist)
        self._connect_worker()

        self.append_log(f"───── 开始 ({self.finished_count + 1}/{self.total_count}) ─────")
        self.append_log(f"链接：{url}")
        self.lbl_progress.setText(f"进度：{self.finished_count + 1} / {self.total_count}")

        self.worker.start()

        self.btn_main.setText("暂停当前")
        self.btn_cancel.setEnabled(True)
        self.lbl_status.setText(f"正在下载 ({self.finished_count + 1}/{self.total_count})")

    def on_one_finished(self, success, msg):
        self.append_log(f"───── {msg} ─────")

        self.finished_count += 1
        self.lbl_progress.setText(f"进度：{self.finished_count} / {self.total_count}")

        if self.finished_count >= self.total_count:
            self.append_log("所有任务处理完毕")
            self.lbl_status.setText("全部完成")
            if success:
                QMessageBox.information(self, "完成", f"已处理 {self.total_count} 个任务")
            self.reset_ui()
            self.pending_urls = []
        else:
            if self.pending_urls:
                next_url = self.pending_urls.pop(0)
                self.start_next_download(next_url, self.current_quality, self.current_playlist)
            else:
                self.reset_ui()

    def cancel_all(self):
        if self.worker and self.worker.isRunning():
            self.worker.request_stop()
            self.worker.wait(5000)
        self.pending_urls = []
        self.reset_ui()
        self.append_log("全部任务已取消")

    def reset_ui(self):
        self.btn_main.setText("开始下载")
        self.btn_cancel.setEnabled(False)
        self.lbl_status.setText("就绪")
        self.lbl_progress.setText("进度： - / - ")
        self.lbl_file.setText("当前文件： - ")
        self.is_paused = False

    def append_log(self, text):
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        self.log_area.append(f"[{ts}] {text}")
        if any(kw in text for kw in ["[download]", "完成", "错误", "暂停", "─────"]):
            self.log_area.verticalScrollBar().setValue(self.log_area.verticalScrollBar().maximum())

class UpdateVersionEvent(QEvent):
    def __init__(self, latest):
        super().__init__(QEvent.Type.User)
        self.latest = latest

if __name__ == "__main__":
    import ctypes
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('com.yt.downloader.v3')
    except:
        pass
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    window = MainWindow()
    window.show()
    exit_code = app.exec()
    remove_lock_file()
    sys.exit(exit_code)
