from dify_app import DifyApp
from models import db


def init_app(app: DifyApp):
    """
    初始化数据库扩展
    
    配置SQLAlchemy数据库连接，包括：
    - 数据库连接池
    - 连接字符串配置
    - 事务管理
    - 模型注册
    
    Args:
        app (DifyApp): Flask应用实例
    """
    # 初始化SQLAlchemy数据库实例
    # 这会根据Flask应用的配置自动设置数据库连接
    db.init_app(app)
