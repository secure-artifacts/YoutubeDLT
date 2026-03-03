import os

def get_safe_path(path: str) -> str:
    """
    处理 Windows 下的长路径问题 (绕过 MAX_PATH 260 字符限制)
    返回带有 \\?\ 前缀的绝对路径。
    """
    if os.name != 'nt' or not path:
        return path
        
    abs_path = os.path.abspath(path)
    
    # 网络共享路径 \\server\share 需要转换为 \\?\UNC\server\share
    if abs_path.startswith('\\\\') and not abs_path.startswith('\\\\?\\'):
        return '\\\\?\\UNC\\' + abs_path[2:]
        
    # 普通本地路径 C:\a\b 转为 \\?\C:\a\b
    if not abs_path.startswith('\\\\?\\'):
        return '\\\\?\\' + abs_path
        
    return abs_path
