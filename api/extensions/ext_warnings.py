from dify_app import DifyApp


def init_app(app: DifyApp):
    """
    初始化警告处理扩展
    
    配置Python警告过滤器，忽略特定的警告类型。
    主要用于抑制不必要的警告信息，保持日志的清洁。
    
    Args:
        app (DifyApp): Flask应用实例
    """
    import warnings

    # 忽略ResourceWarning警告
    # 这通常用于抑制资源清理相关的警告
    warnings.simplefilter("ignore", ResourceWarning)
