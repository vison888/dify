from dify_app import DifyApp


def init_app(app: DifyApp):
    """
    初始化数据库迁移扩展
    
    配置Flask-Migrate，用于数据库架构版本管理。
    支持数据库架构的版本控制、升级和回滚操作。
    
    Args:
        app (DifyApp): Flask应用实例
    """
    import flask_migrate  # type: ignore

    from extensions.ext_database import db

    # 初始化Flask-Migrate，绑定Flask应用和SQLAlchemy实例
    flask_migrate.Migrate(app, db)
