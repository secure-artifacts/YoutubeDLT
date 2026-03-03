"""
同步引擎 - 处理增量同步逻辑
"""
import os
from datetime import datetime
from typing import List, Tuple, Optional, Callable
from core.gdrive_client import GDriveClient, FileInfo
from core.downloader import Downloader
from database.models import Database, SyncTask
from dateutil import parser as date_parser
from utils.path_helpers import get_safe_path


class SyncEngine:
    """同步引擎"""
    
    def __init__(self, gdrive_client: GDriveClient, db: Database):
        self.client = gdrive_client
        self.db = db
        self.task_model = SyncTask(db)
    
    def compare_files(self, remote_file: FileInfo, local_path: str) -> str:
        """
        比较远程和本地文件
        
        Args:
            remote_file: 远程文件信息
            local_path: 本地文件路径
        
        Returns:
            'download' - 需要下载
            'skip' - 跳过（相同）
        """
        # 本地文件不存在
        if not os.path.exists(local_path):
            return 'download'
        
        # 检查文件大小
        local_size = os.path.getsize(local_path)
        if local_size != remote_file.size:
            return 'download'
        
        # 检查修改时间
        try:
            local_mtime = datetime.fromtimestamp(os.path.getmtime(local_path))
            remote_mtime = date_parser.parse(remote_file.modified_time)
            
            # 移除时区信息进行比较
            if remote_mtime.tzinfo:
                remote_mtime = remote_mtime.replace(tzinfo=None)
            
            # 如果远程文件更新，需要下载
            if remote_mtime > local_mtime:
                return 'download'
        except Exception as e:
            print(f"时间比较失败: {e}")
            # 如果无法比较时间，默认下载
            return 'download'
        
        # 文件相同，跳过
        return 'skip'
    
    def scan_and_compare(self, task_id: int, 
                        progress_callback: Optional[Callable[[str], None]] = None) -> Tuple[List[FileInfo], List[FileInfo]]:
        """
        扫描云端文件并与本地对比
        
        Args:
            task_id: 任务ID
            progress_callback: 进度回调
        
        Returns:
            (需要下载的文件列表, 跳过的文件列表)
        """
        # 获取任务配置
        task = self.task_model.get(task_id)
        if not task:
            raise ValueError(f"任务 {task_id} 不存在")
        
        gdrive_folder_id = task['gdrive_folder_id']
        local_folder = task['local_folder']
        filters = task.get('filters') or {}
        
        # 获取云端文件列表
        if progress_callback:
            progress_callback("正在扫描云端文件...")
        
        remote_files = self.client.list_files_recursive(
            folder_id=gdrive_folder_id,
            progress_callback=progress_callback
        )
        
        # 应用过滤器
        filtered_files = self.apply_filters(remote_files, filters)
        
        # 对比文件
        to_download = []
        to_skip = []
        
        for file_info in filtered_files:
            local_path = get_safe_path(os.path.join(local_folder, file_info.path))
            
            decision = self.compare_files(file_info, local_path)
            
            if decision == 'download':
                to_download.append(file_info)
            else:
                to_skip.append(file_info)
        
        return to_download, to_skip
    
    def apply_filters(self, files: List[FileInfo], filters: dict) -> List[FileInfo]:
        """
        应用过滤规则
        
        Args:
            files: 文件列表
            filters: 过滤规则
                {
                    'include_extensions': ['.jpg', '.png'],  # 包含的扩展名
                    'exclude_extensions': ['.tmp'],          # 排除的扩展名
                    'min_size': 1024,                        # 最小大小（字节）
                    'max_size': 104857600,                   # 最大大小（字节）
                    'name_contains': 'photo',                # 文件名包含
                    'name_excludes': 'backup'                # 文件名排除
                }
        
        Returns:
            过滤后的文件列表
        """
        filtered = []
        
        for file_info in files:
            # 跳过文件夹
            if file_info.is_folder():
                continue
            
            # 扩展名过滤
            if 'include_extensions' in filters:
                ext = os.path.splitext(file_info.name)[1].lower()
                if ext not in filters['include_extensions']:
                    continue
            
            if 'exclude_extensions' in filters:
                ext = os.path.splitext(file_info.name)[1].lower()
                if ext in filters['exclude_extensions']:
                    continue
            
            # 大小过滤
            if 'min_size' in filters:
                if file_info.size < filters['min_size']:
                    continue
            
            if 'max_size' in filters:
                if file_info.size > filters['max_size']:
                    continue
            
            # 文件名过滤
            if 'name_contains' in filters:
                if filters['name_contains'].lower() not in file_info.name.lower():
                    continue
            
            if 'name_excludes' in filters:
                if filters['name_excludes'].lower() in file_info.name.lower():
                    continue
            
            filtered.append(file_info)
        
        return filtered
    
    def start_sync(self, task_id: int,
                   scan_callback: Optional[Callable] = None,
                   progress_callback: Optional[Callable] = None,
                   file_callback: Optional[Callable] = None,
                   stop_flag: Optional[Callable[[], bool]] = None) -> dict:
        """
        开始同步
        
        Args:
            task_id: 任务ID
            scan_callback: 扫描进度回调
            progress_callback: 下载进度回调
            file_callback: 文件完成回调
            stop_flag: 停止标志函数，返回True表示停止
        
        Returns:
            同步统计信息
        """
        # 扫描并对比
        to_download, to_skip = self.scan_and_compare(task_id, scan_callback)
        
        # 检查停止标志
        if stop_flag and stop_flag():
            return {'scanned': 0, 'success': 0, 'failed': 0, 'skipped': 0}
        
        # 获取任务配置
        task = self.task_model.get(task_id)
        
        # 创建下载器
        downloader = Downloader(
            gdrive_client=self.client,
            db=self.db,
            thread_count=task.get('thread_count', 3),
            bandwidth_limit=task.get('bandwidth_limit', 0)
        )
        
        # 开始下载（传递 stop_flag）
        stats = downloader.download_batch(
            task_id=task_id,
            files=to_download,
            base_local_path=task['local_folder'],
            progress_callback=progress_callback,
            file_callback=file_callback,
            stop_flag=stop_flag
        )
        
        stats['scanned'] = len(to_download) + len(to_skip)
        stats['skipped'] = len(to_skip)
        
        return stats
