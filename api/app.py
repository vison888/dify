import os
import sys


def is_db_command():
    """
    检查当前是否为数据库命令（如flask db migrate等）
    
    Returns:
        bool: 如果是数据库命令返回True，否则返回False
    """
    if len(sys.argv) > 1 and sys.argv[0].endswith("flask") and sys.argv[1] == "db":
        return True
    return False


# 创建Flask应用实例
if is_db_command():
    # 如果是数据库命令，创建仅包含数据库相关扩展的轻量级应用
    from app_factory import create_migrations_app

    app = create_migrations_app()
else:
    # 创建完整的Flask应用
    # 注意：JetBrains Python调试器与gevent兼容性不好
    # 如果在调试模式下且设置了GEVENT_SUPPORT=True，可以在调试时使用gevent
    if (flask_debug := os.environ.get("FLASK_DEBUG", "0")) and flask_debug.lower() in {"false", "0", "no"}:
        # 在非调试模式下启用gevent以支持异步操作
        from gevent import monkey

        # 对标准库进行monkey patch，使其支持gevent
        monkey.patch_all()

        from grpc.experimental import gevent as grpc_gevent  # type: ignore

        # 初始化gRPC的gevent支持
        grpc_gevent.init_gevent()

        import psycogreen.gevent  # type: ignore

        # 为PostgreSQL连接池添加gevent支持
        psycogreen.gevent.patch_psycopg()

    # 导入并创建完整的Flask应用
    from app_factory import create_app

    app = create_app()
    # 获取Celery实例用于后台任务处理
    celery = app.extensions["celery"]

# 应用入口点
if __name__ == "__main__":
    # 直接运行应用（开发模式）
    app.run(host="0.0.0.0", port=5001)
