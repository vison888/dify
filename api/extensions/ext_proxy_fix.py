from configs import dify_config
from dify_app import DifyApp


def init_app(app: DifyApp):
    """
    初始化代理修复扩展
    
    当应用运行在反向代理（如Nginx、Apache）后面时，
    修复请求中的IP地址和端口信息。
    
    Args:
        app (DifyApp): Flask应用实例
    """
    if dify_config.RESPECT_XFORWARD_HEADERS_ENABLED:
        from werkzeug.middleware.proxy_fix import ProxyFix

        # 配置代理修复中间件
        # x_port=1 表示信任X-Forwarded-Port头部
        app.wsgi_app = ProxyFix(app.wsgi_app, x_port=1)  # type: ignore
