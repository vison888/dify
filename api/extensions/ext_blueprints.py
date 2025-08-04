from configs import dify_config
from dify_app import DifyApp


def init_app(app: DifyApp):
    """
    初始化蓝图注册扩展
    
    注册所有Flask蓝图并配置相应的CORS策略。
    每个蓝图代表不同的API模块，具有不同的访问控制和CORS配置。
    
    Args:
        app (DifyApp): Flask应用实例
    """
    # 注册蓝图路由器

    from flask_cors import CORS  # type: ignore

    # 导入各个模块的蓝图
    from controllers.console import bp as console_app_bp  # 控制台API
    from controllers.files import bp as files_bp  # 文件处理API
    from controllers.inner_api import bp as inner_api_bp  # 内部API
    from controllers.mcp import bp as mcp_bp  # MCP协议API
    from controllers.service_api import bp as service_api_bp  # 服务API
    from controllers.web import bp as web_bp  # Web API

    # 配置服务API蓝图的CORS
    # 允许跨域请求，支持常用的HTTP方法和自定义头部
    CORS(
        service_api_bp,
        allow_headers=["Content-Type", "Authorization", "X-App-Code"],
        methods=["GET", "PUT", "POST", "DELETE", "OPTIONS", "PATCH"],
    )
    app.register_blueprint(service_api_bp)

    # 配置Web API蓝图的CORS
    # 支持凭据传递，允许特定的源，暴露版本和环境信息
    CORS(
        web_bp,
        resources={r"/*": {"origins": dify_config.WEB_API_CORS_ALLOW_ORIGINS}},
        supports_credentials=True,
        allow_headers=["Content-Type", "Authorization", "X-App-Code"],
        methods=["GET", "PUT", "POST", "DELETE", "OPTIONS", "PATCH"],
        expose_headers=["X-Version", "X-Env"],
    )

    app.register_blueprint(web_bp)

    # 配置控制台API蓝图的CORS
    # 控制台API通常用于管理界面，需要更严格的CORS配置
    CORS(
        console_app_bp,
        resources={r"/*": {"origins": dify_config.CONSOLE_CORS_ALLOW_ORIGINS}},
        supports_credentials=True,
        allow_headers=["Content-Type", "Authorization"],
        methods=["GET", "PUT", "POST", "DELETE", "OPTIONS", "PATCH"],
        expose_headers=["X-Version", "X-Env"],
    )

    app.register_blueprint(console_app_bp)

    # 配置文件API蓝图的CORS
    # 文件API主要用于文件上传下载，允许基本的HTTP方法
    CORS(files_bp, allow_headers=["Content-Type"], methods=["GET", "PUT", "POST", "DELETE", "OPTIONS", "PATCH"])
    app.register_blueprint(files_bp)

    # 注册内部API和MCP蓝图（不需要CORS配置，因为是内部使用）
    app.register_blueprint(inner_api_bp)
    app.register_blueprint(mcp_bp)
