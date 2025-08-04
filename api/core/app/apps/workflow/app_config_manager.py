from core.app.app_config.base_app_config_manager import BaseAppConfigManager
from core.app.app_config.common.sensitive_word_avoidance.manager import SensitiveWordAvoidanceConfigManager
from core.app.app_config.entities import WorkflowUIBasedAppConfig
from core.app.app_config.features.file_upload.manager import FileUploadConfigManager
from core.app.app_config.features.text_to_speech.manager import TextToSpeechConfigManager
from core.app.app_config.workflow_ui_based_app.variables.manager import WorkflowVariablesConfigManager
from models.model import App, AppMode
from models.workflow import Workflow


class WorkflowAppConfig(WorkflowUIBasedAppConfig):
    """
    工作流应用配置实体
    
    继承自WorkflowUIBasedAppConfig，专门用于处理工作流类型应用的配置信息。
    这是一个数据容器类，封装了工作流应用运行所需的各种配置参数。
    """

    pass


class WorkflowAppConfigManager(BaseAppConfigManager):
    """
    工作流应用配置管理器
    
    负责工作流应用配置的创建、验证和管理。
    主要功能包括：
    1. 将应用模型和工作流配置转换为统一的应用配置对象
    2. 验证配置参数的合法性和完整性
    3. 设置默认值和处理配置转换
    """
    
    @classmethod
    def get_app_config(cls, app_model: App, workflow: Workflow) -> WorkflowAppConfig:
        """
        获取工作流应用配置
        
        将应用模型和工作流对象转换为WorkflowAppConfig实例，
        这个配置对象包含了工作流执行所需的所有配置信息。
        
        Args:
            app_model: 应用模型，包含应用的基本信息
            workflow: 工作流对象，包含工作流的图结构和特性配置
            
        Returns:
            WorkflowAppConfig: 完整的工作流应用配置对象
        """
        # 获取工作流的特性配置字典
        features_dict = workflow.features_dict

        # 转换应用模式枚举
        app_mode = AppMode.value_of(app_model.mode)
        
        # 创建工作流应用配置对象
        app_config = WorkflowAppConfig(
            tenant_id=app_model.tenant_id,                           # 租户ID
            app_id=app_model.id,                                     # 应用ID
            app_mode=app_mode,                                       # 应用模式
            workflow_id=workflow.id,                                 # 工作流ID
            # 敏感词规避配置转换
            sensitive_word_avoidance=SensitiveWordAvoidanceConfigManager.convert(config=features_dict),
            # 工作流变量配置转换
            variables=WorkflowVariablesConfigManager.convert(workflow=workflow),
            # 其他附加特性配置转换
            additional_features=cls.convert_features(features_dict, app_mode),
        )

        return app_config

    @classmethod
    def config_validate(cls, tenant_id: str, config: dict, only_structure_validate: bool = False) -> dict:
        """
        验证工作流应用模型配置
        
        对工作流应用的配置参数进行全面验证，包括文件上传、语音合成、
        敏感词过滤等各个模块的配置验证，确保配置的正确性和完整性。

        Args:
            tenant_id: 租户ID，用于权限和资源验证
            config: 应用模型配置参数字典
            only_structure_validate: 是否只进行结构验证，不进行深度验证
            
        Returns:
            dict: 验证后的配置字典，包含默认值和过滤后的参数
        """
        # 收集所有相关的配置键名
        related_config_keys = []

        # 文件上传功能验证
        # 验证文件上传相关配置并设置默认值
        config, current_related_config_keys = FileUploadConfigManager.validate_and_set_defaults(config=config)
        related_config_keys.extend(current_related_config_keys)

        # 文本转语音功能验证
        # 验证TTS相关配置并设置默认值
        config, current_related_config_keys = TextToSpeechConfigManager.validate_and_set_defaults(config)
        related_config_keys.extend(current_related_config_keys)

        # 敏感词过滤功能验证
        # 验证内容审核相关配置并设置默认值
        config, current_related_config_keys = SensitiveWordAvoidanceConfigManager.validate_and_set_defaults(
            tenant_id=tenant_id, config=config, only_structure_validate=only_structure_validate
        )
        related_config_keys.extend(current_related_config_keys)

        # 去重配置键名列表
        related_config_keys = list(set(related_config_keys))

        # 过滤掉额外的参数，只保留相关的配置
        filtered_config = {key: config.get(key) for key in related_config_keys}

        return filtered_config
