import logging
import time

from configs import dify_config
from contexts.wrapper import RecyclableContextVar
from dify_app import DifyApp


# ----------------------------
# 应用工厂函数
# ----------------------------
def create_flask_app_with_configs() -> DifyApp:
    """
    创建一个基础的Flask应用实例，并加载配置文件
    
    这个函数创建一个"原始"的Flask应用，只包含基本的配置，
    不包含任何扩展。主要用于数据库迁移等轻量级操作。
    
    Returns:
        DifyApp: 配置了基本设置的Flask应用实例
    """
    dify_app = DifyApp(__name__)
    # 从dify_config加载所有配置到Flask应用
    dify_app.config.from_mapping(dify_config.model_dump())

    # 添加请求前处理钩子
    @dify_app.before_request
    def before_request():
        # 为每个请求添加唯一的线程回收标识符
        # 用于跟踪请求的生命周期和资源管理
        RecyclableContextVar.increment_thread_recycles()

    return dify_app


def create_app() -> DifyApp:
    """
    创建完整的Flask应用实例
    
    这是主要的应用工厂函数，会创建应用并初始化所有必要的扩展。
    包含性能监控，记录应用创建时间。
    
    Returns:
        DifyApp: 完整的Flask应用实例，包含所有扩展
    """
    start_time = time.perf_counter()
    # 创建基础应用
    app = create_flask_app_with_configs()
    # 初始化所有扩展
    initialize_extensions(app)
    end_time = time.perf_counter()
    
    # 在调试模式下记录应用创建时间
    if dify_config.DEBUG:
        logging.info(f"Finished create_app ({round((end_time - start_time) * 1000, 2)} ms)")
    return app


def initialize_extensions(app: DifyApp):
    """
    初始化所有Flask扩展
    
    按照特定的顺序初始化扩展，确保依赖关系正确。
    每个扩展的初始化时间都会被记录（在调试模式下）。
    
    Args:
        app (DifyApp): 要初始化扩展的Flask应用实例
    """
    # 导入所有扩展模块
    from extensions import (
        ext_app_metrics,  # 应用指标监控
        ext_blueprints,  # 蓝图注册
        ext_celery,  # Celery后台任务
        ext_code_based_extension,  # 代码扩展系统
        ext_commands,  # CLI命令
        ext_compress,  # 响应压缩
        ext_database,  # 数据库连接
        ext_hosting_provider,  # 托管提供商配置
        ext_import_modules,  # 模块导入
        ext_logging,  # 日志系统
        ext_login,  # 用户认证
        ext_mail,  # 邮件服务
        ext_migrate,  # 数据库迁移
        ext_otel,  # OpenTelemetry监控
        ext_proxy_fix,  # 代理修复
        ext_redis,  # Redis缓存
        ext_request_logging,  # 请求日志
        ext_sentry,  # 错误监控
        ext_set_secretkey,  # 密钥设置
        ext_storage,  # 文件存储
        ext_timezone,  # 时区设置
        ext_warnings,  # 警告处理
    )

    # 定义扩展初始化顺序（考虑依赖关系）
    extensions = [
        ext_timezone,        # 1. 首先设置时区
        ext_logging,         # 2. 初始化日志系统
        ext_warnings,        # 3. 配置警告处理
        ext_import_modules,  # 4. 导入必要模块
        ext_set_secretkey,   # 5. 设置应用密钥
        ext_compress,        # 6. 配置响应压缩
        ext_code_based_extension,  # 7. 代码扩展系统
        ext_database,        # 8. 数据库连接
        ext_app_metrics,     # 9. 应用指标
        ext_migrate,         # 10. 数据库迁移
        ext_redis,           # 11. Redis缓存
        ext_storage,         # 12. 文件存储
        ext_celery,          # 13. Celery后台任务
        ext_login,           # 14. 用户认证
        ext_mail,            # 15. 邮件服务
        ext_hosting_provider,  # 16. 托管提供商
        ext_sentry,          # 17. 错误监控
        ext_proxy_fix,       # 18. 代理修复
        ext_blueprints,      # 19. 注册蓝图
        ext_commands,        # 20. CLI命令
        ext_otel,            # 21. OpenTelemetry
        ext_request_logging, # 22. 请求日志
    ]
    
    # 逐个初始化扩展
    for ext in extensions:
        short_name = ext.__name__.split(".")[-1]
        # 检查扩展是否启用（如果扩展有is_enabled方法）
        is_enabled = ext.is_enabled() if hasattr(ext, "is_enabled") else True
        if not is_enabled:
            if dify_config.DEBUG:
                logging.info(f"Skipped {short_name}")
            continue

        # 记录扩展初始化时间
        start_time = time.perf_counter()
        ext.init_app(app)
        end_time = time.perf_counter()
        if dify_config.DEBUG:
            logging.info(f"Loaded {short_name} ({round((end_time - start_time) * 1000, 2)} ms)")


def create_migrations_app():
    """
    创建用于数据库迁移的轻量级应用
    
    这个函数创建一个只包含数据库相关扩展的Flask应用，
    用于执行数据库迁移命令，避免加载不必要的扩展。
    
    Returns:
        DifyApp: 仅包含数据库扩展的Flask应用实例
    """
    app = create_flask_app_with_configs()
    from extensions import ext_database, ext_migrate

    # 只初始化必要的扩展：数据库和迁移
    ext_database.init_app(app)
    ext_migrate.init_app(app)

    return app
