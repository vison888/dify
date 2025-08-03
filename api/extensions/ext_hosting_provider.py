from core.hosting_configuration import HostingConfiguration

# 创建全局托管配置实例
# 用于管理不同托管提供商（如Dify Cloud、自托管等）的配置
hosting_configuration = HostingConfiguration()


from dify_app import DifyApp


def init_app(app: DifyApp):
    """
    初始化托管提供商扩展
    
    配置托管提供商相关的设置，包括：
    - 托管环境检测
    - 提供商特定配置
    - 功能开关管理
    - 服务端点配置
    
    Args:
        app (DifyApp): Flask应用实例
    """
    # 初始化托管配置
    hosting_configuration.init_app(app)
