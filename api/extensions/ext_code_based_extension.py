from core.extension.extension import Extension
from dify_app import DifyApp


def init_app(app: DifyApp):
    """
    初始化代码扩展系统
    
    这个扩展允许通过代码方式动态扩展应用功能，
    而不是通过配置文件或数据库。主要用于：
    - 动态加载自定义模块
    - 运行时功能扩展
    - 插件系统支持
    
    Args:
        app (DifyApp): Flask应用实例
    """
    # 初始化代码扩展系统
    code_based_extension.init()


# 创建全局扩展实例
# 这个实例用于管理所有代码扩展
code_based_extension = Extension()
