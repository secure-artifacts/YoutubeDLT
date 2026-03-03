"""
文件下载器 - 支持断点续传和多线程
"""
import os
import hashlib
import time
from typing import Optional, Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from core.gdrive_client import GDriveClient, FileInfo
from database.models import Database, DownloadProgress
from utils.path_helpers import get_safe_path


class Downloader:
    """文件下载器"""
    
    def __init__(self, gdrive_client: GDriveClient, db: Database, 
                 thread_count: int = 3, bandwidth_limit: int = 0):
        self.client = gdrive_client
        self.db = db
        self.progress_model = DownloadProgress(db)
        self.thread_count = thread_count
        self.bandwidth_limit = bandwidth_limit  # KB/s, 0 表示不限制
        self.is_paused = False
        self.is_stopped = False
    
    def calculate_md5(self, file_path: str, chunk_size: int = 8192) -> str:
        """计算文件MD5"""
        md5 = hashlib.md5()
        
        try:
            with open(file_path, 'rb') as f:
                while True:
                    chunk = f.read(chunk_size)
                    if not chunk:
                        break
                    md5.update(chunk)
            return md5.hexdigest()
        except Exception as e:
            print(f"MD5计算失败: {e}")
            return ""
    
    def verify_file(self, file_path: str, expected_md5: str) -> bool:
        """验证文件完整性"""
        if not expected_md5:
            return True  # 如果没有MD5，跳过验证
        
        actual_md5 = self.calculate_md5(file_path)
        return actual_md5.lower() == expected_md5.lower()
    
    def download_single_file(self, task_id: int, file_info: FileInfo, 
                            local_path: str,
                            progress_callback: Optional[Callable[[int, int, str], None]] = None) -> bool:
        """
        下载单个文件
        
        Args:
            task_id: 任务ID
            file_info: 文件信息
            local_path: 本地保存路径
            progress_callback: 进度回调 (downloaded, total, filename)
        
        Returns:
            下载是否成功
        """
        print(f"\n=== 开始下载文件 ===")
        print(f"文件名: {file_info.name}")
        print(f"文件ID: {file_info.id}")
        print(f"文件大小: {file_info.size} bytes ({file_info.size / 1024 / 1024:.2f} MB)")
        print(f"本地路径: {local_path}")
        print(f"MD5: {file_info.md5_checksum}")
        
        # 检查数据库中是否有进度记录
        record = self.progress_model.get_by_file_id(task_id, file_info.id)
        
        if not record:
            print(f"创建新的下载记录...")
            # 创建新记录
            record_id = self.progress_model.create(
                task_id=task_id,
                file_id=file_info.id,
                file_path=file_info.path,
                local_path=local_path,
                total_size=file_info.size,
                md5_checksum=file_info.md5_checksum or ""
            )
            resume_from = 0
            print(f"创建记录ID: {record_id}")
        else:
            print(f"找到已有下载记录，ID: {record['id']}, 状态: {record['status']}")
            record_id = record['id']
            
            # 检查是否已完成
            if record['status'] == 'completed':
                if os.path.exists(local_path):
                    print(f"文件已完成，验证中...")
                    # 验证文件
                    if self.verify_file(local_path, file_info.md5_checksum):
                        print(f"文件验证通过，跳过下载")
                        return True  # 已完成且验证通过
                
                print(f"文件损坏或不存在，重新下载")
                # 文件损坏或不存在，重新下载
                resume_from = 0
            else:
                # 从断点继续
                resume_from = record.get('downloaded_size', 0)
                print(f"从断点继续: {resume_from} bytes")
                
                # 检查本地文件是否存在且大小匹配
                if os.path.exists(local_path):
                    actual_size = os.path.getsize(local_path)
                    print(f"本地文件大小: {actual_size} bytes")
                    if actual_size != resume_from:
                        resume_from = actual_size
                        print(f"调整断点位置为: {resume_from} bytes")
        
        # 下载文件
        try:
            print(f"开始下载，从 {resume_from} bytes 开始...")
            
            def download_progress(downloaded, total):
                # 更新数据库
                self.progress_model.update_progress(
                    record_id, 
                    downloaded, 
                    'downloading'
                )
                
                # 回调
                if progress_callback:
                    progress_callback(downloaded, total, file_info.name)
                
                # 带宽限制
                if self.bandwidth_limit > 0:
                    # 简单的带宽控制（可以改进）
                    time.sleep(0.1)
            
            success = self.client.download_file(
                file_id=file_info.id,
                local_path=local_path,
                resume_from=resume_from,
                progress_callback=download_progress
            )
            
            if success:
                print(f"下载完成，开始验证...")
                # 验证文件
                if self.verify_file(local_path, file_info.md5_checksum):
                    print(f"✓ 文件验证通过")
                    self.progress_model.mark_completed(record_id)
                    return True
                else:
                    print(f"✗ MD5校验失败！")
                    # 验证失败
                    self.progress_model.mark_failed(record_id, "MD5校验失败")
                    # 删除损坏的文件
                    if os.path.exists(local_path):
                        os.remove(local_path)
                        print(f"已删除损坏的文件")
                    return False
            else:
                print(f"✗ 下载失败")
                self.progress_model.mark_failed(record_id, "下载失败")
                return False
                
        except Exception as e:
            error_msg = str(e)
            print(f"✗ 下载异常: {error_msg}")
            import traceback
            traceback.print_exc()
            self.progress_model.mark_failed(record_id, error_msg)
            return False
    
    def download_batch(self, task_id: int, files: list, base_local_path: str,
                      progress_callback: Optional[Callable] = None,
                      file_callback: Optional[Callable[[str, str], None]] = None,
                      stop_flag: Optional[Callable[[], bool]] = None) -> dict:
        """
        批量下载文件（多线程）
        
        Args:
            task_id: 任务ID
            files: 文件列表 (FileInfo)
            base_local_path: 本地基路径
            progress_callback: 整体进度回调
            file_callback: 单文件完成回调 (filename, status)
            stop_flag: 停止标志函数
        
        Returns:
            统计信息 {success: int, failed: int, skipped: int}
        """
        stats = {'success': 0, 'failed': 0, 'skipped': 0}
        
        def download_worker(file_info: FileInfo):
            """下载工作线程"""
            # 检查停止标志
            if stop_flag and stop_flag():
                return 'stopped'
            
            if self.is_stopped:
                return 'stopped'
            
            while self.is_paused:
                time.sleep(0.5)
            
            # 构建本地路径
            local_path = get_safe_path(os.path.join(base_local_path, file_info.path))
            
            # 检查是否已存在
            if os.path.exists(local_path):
                # 检查大小和修改时间
                local_size = os.path.getsize(local_path)
                if local_size == file_info.size:
                    # 文件已存在且大小相同，跳过
                    if file_callback:
                        file_callback(file_info.name, 'skipped')
                    return 'skipped'
            
            # 下载
            success = self.download_single_file(
                task_id, file_info, local_path, progress_callback
            )
            
            if success:
                if file_callback:
                    file_callback(file_info.name, 'success')
                return 'success'
            else:
                if file_callback:
                    file_callback(file_info.name, 'failed')
                return 'failed'
        
        # 使用线程池下载
        with ThreadPoolExecutor(max_workers=self.thread_count) as executor:
            futures = {executor.submit(download_worker, f): f for f in files}
            
            for future in as_completed(futures):
                # 检查停止标志
                if stop_flag and stop_flag():
                    # 取消所有待执行的任务
                    for f in futures:
                        f.cancel()
                    break
                
                result = future.result()
                if result == 'success':
                    stats['success'] += 1
                elif result == 'failed':
                    stats['failed'] += 1
                elif result == 'skipped':
                    stats['skipped'] += 1
        
        return stats
    
    def pause(self):
        """暂停下载"""
        self.is_paused = True
    
    def resume(self):
        """恢复下载"""
        self.is_paused = False
    
    def stop(self):
        """停止下载"""
        self.is_stopped = True
