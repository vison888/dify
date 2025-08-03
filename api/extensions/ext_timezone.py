import os
import time

from dify_app import DifyApp


def init_app(app: DifyApp):
    """
    初始化时区设置扩展
    
    设置应用的系统时区为UTC，确保时间处理的一致性。
    注意：Windows平台不支持tzset函数。
    
    Args:
        app (DifyApp): Flask应用实例
    """
    # 设置环境变量TZ为UTC
    os.environ["TZ"] = "UTC"
    
    # Windows平台不支持tzset
    # 在Unix/Linux系统上，这会立即应用时区设置
    if hasattr(time, "tzset"):
        time.tzset()
