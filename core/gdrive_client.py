"""
Google Drive API 客户端
处理 OAuth2 认证和文件操作
"""
import os
import pickle
from typing import List, Dict, Optional, Callable
from google.auth.transport.requests import Request, AuthorizedSession
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
import io
import requests
from utils.path_helpers import get_safe_path


# Google Drive API 权限范围
SCOPES = ['https://www.googleapis.com/auth/drive.readonly']


class FileInfo:
    """文件信息类"""
    
    def __init__(self, file_dict: Dict):
        self.id = file_dict.get('id')
        self.name = file_dict.get('name')
        self.mime_type = file_dict.get('mimeType')
        self.size = int(file_dict.get('size', 0))
        self.modified_time = file_dict.get('modifiedTime')
        self.md5_checksum = file_dict.get('md5Checksum')
        self.parents = file_dict.get('parents', [])
        self.path = ""  # 完整路径，需要后续构建
    
    def is_folder(self) -> bool:
        """判断是否为文件夹"""
        return self.mime_type == 'application/vnd.google-apps.folder'
    
    def __repr__(self):
        return f"FileInfo(name={self.name}, size={self.size}, path={self.path})"


class GDriveClient:
    """Google Drive API 客户端"""
    
    def __init__(self, credentials_path: str = 'config/credentials.json', 
                 token_path: str = 'config/token.pickle'):
        self.credentials_path = credentials_path
        self.token_path = token_path
        self.service = None
        self.creds = None
    
    def authenticate(self) -> bool:
        """
        OAuth2 认证
        返回: 认证是否成功
        """
        # 检查是否已有令牌
        if os.path.exists(self.token_path):
            with open(self.token_path, 'rb') as token:
                self.creds = pickle.load(token)
        
        # 如果令牌无效或不存在，重新认证
        if not self.creds or not self.creds.valid:
            if self.creds and self.creds.expired and self.creds.refresh_token:
                # 刷新过期的令牌
                self.creds.refresh(Request())
            else:
                # 检查 credentials.json 是否存在
                if not os.path.exists(self.credentials_path):
                    raise FileNotFoundError(
                        f"未找到 {self.credentials_path}\n"
                        "请从 Google Cloud Console 下载 OAuth 2.0 客户端密钥"
                    )
                
                # 执行 OAuth 流程
                flow = InstalledAppFlow.from_client_secrets_file(
                    self.credentials_path, SCOPES
                )
                self.creds = flow.run_local_server(port=0)
            
            # 保存令牌供下次使用
            os.makedirs(os.path.dirname(self.token_path), exist_ok=True)
            with open(self.token_path, 'wb') as token:
                pickle.dump(self.creds, token)
        
        # 构建服务 - 使用 credentials 参数会自动处理 HTTP 层
        # 这比手动配置 httplib2 更稳定
        self.service = build('drive', 'v3', credentials=self.creds, static_discovery=False)
        return True
    
    def get_user_info(self) -> Dict:
        """获取当前授权用户信息"""
        if not self.service:
            raise Exception("未认证，请先调用 authenticate()")
        
        about = self.service.about().get(fields="user").execute()
        return about.get('user', {})
    
    def list_folder_contents(self, folder_id: str = 'root') -> List[FileInfo]:
        """
        列出文件夹内容（仅一层）
        
        Args:
            folder_id: 文件夹ID，默认为根目录
        
        Returns:
            文件信息列表
        """
        if not self.service:
            raise Exception("未认证，请先调用 authenticate()")
        
        query = f"'{folder_id}' in parents and trashed=false"
        fields = "files(id, name, mimeType, size, modifiedTime, md5Checksum, parents)"
        
        results = self.service.files().list(
            q=query,
            fields=fields,
            pageSize=1000
        ).execute()
        
        files = results.get('files', [])
        return [FileInfo(f) for f in files]
    
    def list_files_recursive(self, folder_id: str = 'root', 
                            current_path: str = "",
                            progress_callback: Optional[Callable[[str], None]] = None) -> List[FileInfo]:
        """
        递归获取文件夹内所有文件
        
        Args:
            folder_id: 起始文件夹ID
            current_path: 当前路径（用于构建完整路径）
            progress_callback: 进度回调函数
        
        Returns:
            所有文件信息列表
        """
        all_files = []
        items = self.list_folder_contents(folder_id)
        
        for item in items:
            item_path = os.path.join(current_path, item.name) if current_path else item.name
            item.path = item_path
            
            if progress_callback:
                progress_callback(f"扫描: {item_path}")
            
            if item.is_folder():
                # 递归获取子文件夹内容
                sub_files = self.list_files_recursive(
                    item.id, 
                    item_path, 
                    progress_callback
                )
                all_files.extend(sub_files)
            else:
                # 只添加文件，不添加文件夹
                all_files.append(item)
        
        return all_files
    
    def download_file(self, file_id: str, local_path: str, 
                     chunk_size: int = 10 * 1024 * 1024,  # 10MB chunks
                     resume_from: int = 0,
                     progress_callback: Optional[Callable[[int, int], None]] = None) -> bool:
        """
        下载文件（支持断点续传）
        
        Args:
            file_id: 文件ID
            local_path: 本地保存路径
            chunk_size: 分块大小（字节）
            resume_from: 从哪个字节开始下载（断点续传）
            progress_callback: 进度回调 callback(downloaded, total)
        
        Returns:
            下载是否成功
        """
        if not self.service:
            raise Exception("未认证，请先调用 authenticate()")
        
        try:
            local_path = get_safe_path(local_path)
            
            # 确保目录存在
            os.makedirs(os.path.dirname(local_path), exist_ok=True)
            
            print(f"[GDrive] 获取文件元数据: {file_id}")
            # 获取文件元数据
            file_metadata = self.service.files().get(
                fileId=file_id, 
                fields='size, md5Checksum, mimeType, name'
            ).execute()
            
            total_size = int(file_metadata.get('size', 0))
            mime_type = file_metadata.get('mimeType', '')
            
            print(f"[GDrive] 文件类型: {mime_type}, 大小: {total_size} bytes")
            
            # Google Docs 类型需要导出
            if 'google-apps' in mime_type:
                print(f"[GDrive] 检测到 Google Docs 文件，执行导出...")
                return self._export_google_doc(file_id, local_path, mime_type, progress_callback)
            
            # 使用 requests 库进行下载（更稳定的 SSL 支持）
            print(f"[GDrive] 使用 requests 库下载...")
            
            # 创建授权会话
            from google.auth.transport.requests import AuthorizedSession
            session = AuthorizedSession(self.creds)
            
            # 构建下载 URL
            download_url = f'https://www.googleapis.com/drive/v3/files/{file_id}?alt=media'
            
            # 设置请求头支持断点续传
            headers = {}
            if resume_from > 0:
                headers['Range'] = f'bytes={resume_from}-'
                print(f"[GDrive] 断点续传，从 {resume_from} bytes 开始")
            
            # 执行下载
            mode = 'ab' if resume_from > 0 else 'wb'
            downloaded = resume_from
            
            print(f"[GDrive] 开始下载，模式: {mode}")
            
            retry_count = 0
            max_retries = 3
            
            while retry_count <= max_retries:
                try:
                    # 发起请求
                    response = session.get(download_url, headers=headers, stream=True, timeout=30)
                    response.raise_for_status()
                    
                    # 写入文件
                    with open(local_path, mode) as f:
                        for chunk in response.iter_content(chunk_size=chunk_size):
                            if chunk:
                                f.write(chunk)
                                downloaded += len(chunk)
                                
                                if progress_callback and total_size > 0:
                                    progress_callback(downloaded, total_size)
                                
                                if downloaded % (chunk_size * 10) == 0:  # 每10个块打印一次
                                    progress_pct = (downloaded / total_size * 100) if total_size > 0 else 0
                                    print(f"[GDrive] 下载进度: {progress_pct:.1f}% ({downloaded}/{total_size} bytes)")
                    
                    print(f"[GDrive] ✓ 下载完成")
                    return True
                    
                except requests.exceptions.RequestException as e:
                    retry_count += 1
                    print(f"[GDrive] 下载失败 (重试 {retry_count}/{max_retries}): {e}")
                    
                    if retry_count > max_retries:
                        print(f"[GDrive] 达到最大重试次数，放弃下载")
                        raise
                    
                    # 指数退避
                    import time
                    wait_time = 2 ** retry_count
                    print(f"[GDrive] 等待 {wait_time} 秒后重试...")
                    time.sleep(wait_time)
                    
                    # 更新断点位置
                    if os.path.exists(local_path):
                        downloaded = os.path.getsize(local_path)
                        headers['Range'] = f'bytes={downloaded}-'
                        mode = 'ab'
            
            return False
            
        except Exception as e:
            print(f"[GDrive] ✗ 下载失败: {e}")
            import traceback
            traceback.print_exc()
            return False
    
    def _export_google_doc(self, file_id: str, local_path: str, mime_type: str,
                          progress_callback: Optional[Callable] = None) -> bool:
        """导出 Google Docs 文件"""
        try:
            # 根据类型选择导出格式
            export_formats = {
                'application/vnd.google-apps.document': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
                'application/vnd.google-apps.spreadsheet': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                'application/vnd.google-apps.presentation': 'application/vnd.openxmlformats-officedocument.presentationml.presentation'
            }
            
            export_mime = export_formats.get(mime_type, 'application/pdf')
            
            request = self.service.files().export_media(fileId=file_id, mimeType=export_mime)
            
            local_path = get_safe_path(local_path)
            with open(local_path, 'wb') as fh:
                downloader = MediaIoBaseDownload(fh, request)
                done = False
                while not done:
                    status, done = downloader.next_chunk()
                    if status and progress_callback:
                        progress_callback(int(status.progress() * 100), 100)
            
            return True
        except Exception as e:
            print(f"导出 Google 文档失败: {e}")
            return False
    
    def get_file_metadata(self, file_id: str) -> Dict:
        """获取文件元数据"""
        if not self.service:
            raise Exception("未认证，请先调用 authenticate()")
        
        return self.service.files().get(
            fileId=file_id,
            fields='id, name, mimeType, size, modifiedTime, md5Checksum, parents'
        ).execute()
    
    def search_folders(self, query: str) -> List[FileInfo]:
        """
        搜索文件夹
        
        Args:
            query: 搜索关键词
        
        Returns:
            匹配的文件夹列表
        """
        if not self.service:
            raise Exception("未认证，请先调用 authenticate()")
        
        search_query = f"mimeType='application/vnd.google-apps.folder' and name contains '{query}' and trashed=false"
        
        results = self.service.files().list(
            q=search_query,
            fields='files(id, name, mimeType, parents)',
            pageSize=100
        ).execute()
        
        files = results.get('files', [])
        return [FileInfo(f) for f in files]
