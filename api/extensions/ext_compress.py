from configs import dify_config
from dify_app import DifyApp


def is_enabled() -> bool:
    """
    检查响应压缩功能是否启用
    
    根据配置决定是否启用API响应压缩功能。
    压缩可以减少网络传输量，提高响应速度。
    
    Returns:
        bool: 如果启用压缩返回True，否则返回False
    """
    return dify_config.API_COMPRESSION_ENABLED


def init_app(app: DifyApp):
    """
    初始化响应压缩扩展
    
    配置Flask-Compress扩展，用于压缩HTTP响应。
    支持gzip、deflate等压缩算法，可以显著减少响应大小。
    
    Args:
        app (DifyApp): Flask应用实例
    """
    from flask_compress import Compress  # type: ignore

    # 创建压缩实例并初始化
    compress = Compress()
    compress.init_app(app)
