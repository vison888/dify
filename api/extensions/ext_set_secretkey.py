from configs import dify_config
from dify_app import DifyApp


def init_app(app: DifyApp):
    """
    初始化密钥设置扩展
    
    为Flask应用设置密钥，用于：
    - 会话加密
    - CSRF令牌生成
    - 其他需要加密签名的功能
    
    Args:
        app (DifyApp): Flask应用实例
    """
    # 从配置中设置Flask应用的密钥
    app.secret_key = dify_config.SECRET_KEY
