from flask import Flask


class DifyApp(Flask):
    """
    Dify应用的Flask子类
    
    这个类继承自Flask，为Dify项目提供自定义的Flask应用实例。
    目前是一个简单的继承，可以根据需要在未来添加Dify特定的功能。
    
    主要用途：
    - 提供统一的Flask应用基类
    - 便于后续添加Dify特定的应用行为
    - 确保所有扩展和配置都使用相同的应用类型
    """
    pass
