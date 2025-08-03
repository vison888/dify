from dify_app import DifyApp


def init_app(app: DifyApp):
    """
    初始化模块导入扩展
    
    在应用启动时导入必要的模块，确保：
    - 事件处理器被正确加载
    - 所有必要的模块都被初始化
    - 避免循环导入问题
    
    Args:
        app (DifyApp): Flask应用实例
    """
    # 导入事件处理器模块
    # 这会触发事件处理器的注册和初始化
    from events import event_handlers  # noqa: F401
