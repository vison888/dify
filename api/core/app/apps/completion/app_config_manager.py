from typing import Optional

from core.app.app_config.base_app_config_manager import BaseAppConfigManager
from core.app.app_config.common.sensitive_word_avoidance.manager import SensitiveWordAvoidanceConfigManager
from core.app.app_config.easy_ui_based_app.dataset.manager import DatasetConfigManager
from core.app.app_config.easy_ui_based_app.model_config.manager import ModelConfigManager
from core.app.app_config.easy_ui_based_app.prompt_template.manager import PromptTemplateConfigManager
from core.app.app_config.easy_ui_based_app.variables.manager import BasicVariablesConfigManager
from core.app.app_config.entities import EasyUIBasedAppConfig, EasyUIBasedAppModelConfigFrom
from core.app.app_config.features.file_upload.manager import FileUploadConfigManager
from core.app.app_config.features.more_like_this.manager import MoreLikeThisConfigManager
from core.app.app_config.features.text_to_speech.manager import TextToSpeechConfigManager
from models.model import App, AppMode, AppModelConfig


class CompletionAppConfig(EasyUIBasedAppConfig):
    """
    补全应用配置实体
    
    继承自EasyUIBasedAppConfig，专门用于处理补全类型应用的配置信息。
    补全应用是基于提示模板生成文本完成的应用类型，支持变量替换、
    数据集检索、敏感词过滤等功能。
    """

    pass


class CompletionAppConfigManager(BaseAppConfigManager):
    """
    补全应用配置管理器
    
    负责补全应用配置的创建、验证和管理。补全应用配置包括模型配置、
    提示模板、变量管理、数据集配置、文件上传、敏感词过滤等功能模块。
    
    主要功能：
    1. 将应用模型配置转换为补全应用配置对象
    2. 验证配置参数的合法性和完整性
    3. 设置默认值和处理配置转换
    4. 管理变量和外部数据源配置
    """
    
    @classmethod
    def get_app_config(
        cls, app_model: App, app_model_config: AppModelConfig, override_config_dict: Optional[dict] = None
    ) -> CompletionAppConfig:
        """
        获取补全应用配置
        
        将应用模型和应用模型配置转换为CompletionAppConfig实例，
        支持配置覆盖，用于调试模式下的参数修改。
        
        Args:
            app_model: 应用模型，包含应用的基本信息
            app_model_config: 应用模型配置，包含具体的配置参数
            override_config_dict: 可选的配置覆盖字典，用于调试模式
            
        Returns:
            CompletionAppConfig: 完整的补全应用配置对象
        """
        # 确定配置来源
        if override_config_dict:
            config_from = EasyUIBasedAppModelConfigFrom.ARGS          # 来自参数覆盖
        else:
            config_from = EasyUIBasedAppModelConfigFrom.APP_LATEST_CONFIG  # 来自应用最新配置

        # 根据配置来源获取配置字典
        if config_from != EasyUIBasedAppModelConfigFrom.ARGS:
            # 使用应用模型配置
            app_model_config_dict = app_model_config.to_dict()
            config_dict = app_model_config_dict.copy()
        else:
            # 使用覆盖配置
            config_dict = override_config_dict or {}

        # 转换应用模式
        app_mode = AppMode.value_of(app_model.mode)
        
        # 创建补全应用配置对象
        app_config = CompletionAppConfig(
            tenant_id=app_model.tenant_id,                           # 租户ID
            app_id=app_model.id,                                     # 应用ID
            app_mode=app_mode,                                       # 应用模式
            app_model_config_from=config_from,                       # 配置来源
            app_model_config_id=app_model_config.id,                 # 配置ID
            app_model_config_dict=config_dict,                       # 配置字典
            # 各功能模块配置转换
            model=ModelConfigManager.convert(config=config_dict),                           # 模型配置
            prompt_template=PromptTemplateConfigManager.convert(config=config_dict),        # 提示模板配置
            sensitive_word_avoidance=SensitiveWordAvoidanceConfigManager.convert(config=config_dict),  # 敏感词过滤配置
            dataset=DatasetConfigManager.convert(config=config_dict),                       # 数据集配置
            additional_features=cls.convert_features(config_dict, app_mode),                # 附加特性配置
        )

        # 转换变量配置和外部数据变量配置
        app_config.variables, app_config.external_data_variables = BasicVariablesConfigManager.convert(
            config=config_dict
        )

        return app_config

    @classmethod
    def config_validate(cls, tenant_id: str, config: dict) -> dict:
        """
        验证补全应用模型配置
        
        对补全应用的配置参数进行全面验证，包括模型配置、用户输入表单、
        文件上传、提示模板、数据集、语音合成、更多类似内容、敏感词过滤等
        各个模块的配置验证，确保配置的正确性和完整性。

        Args:
            tenant_id: 租户ID，用于权限和资源验证
            config: 应用模型配置参数字典
            
        Returns:
            dict: 验证后的配置字典，包含默认值和过滤后的参数
        """
        # 设置应用模式为补全模式
        app_mode = AppMode.COMPLETION

        # 收集所有相关的配置键名
        related_config_keys = []

        # 模型配置验证
        # 验证LLM模型相关配置并设置默认值
        config, current_related_config_keys = ModelConfigManager.validate_and_set_defaults(tenant_id, config)
        related_config_keys.extend(current_related_config_keys)

        # 用户输入表单验证
        # 验证用户输入变量配置并设置默认值
        config, current_related_config_keys = BasicVariablesConfigManager.validate_and_set_defaults(tenant_id, config)
        related_config_keys.extend(current_related_config_keys)

        # 文件上传功能验证
        # 验证文件上传相关配置并设置默认值
        config, current_related_config_keys = FileUploadConfigManager.validate_and_set_defaults(config)
        related_config_keys.extend(current_related_config_keys)

        # 提示模板验证
        # 验证提示模板配置并设置默认值
        config, current_related_config_keys = PromptTemplateConfigManager.validate_and_set_defaults(app_mode, config)
        related_config_keys.extend(current_related_config_keys)

        # 数据集查询变量验证
        # 验证数据集检索相关配置并设置默认值
        config, current_related_config_keys = DatasetConfigManager.validate_and_set_defaults(
            tenant_id, app_mode, config
        )
        related_config_keys.extend(current_related_config_keys)

        # 文本转语音功能验证
        # 验证TTS相关配置并设置默认值
        config, current_related_config_keys = TextToSpeechConfigManager.validate_and_set_defaults(config)
        related_config_keys.extend(current_related_config_keys)

        # 更多类似内容功能验证
        # 验证"更多类似内容"功能配置并设置默认值
        config, current_related_config_keys = MoreLikeThisConfigManager.validate_and_set_defaults(config)
        related_config_keys.extend(current_related_config_keys)

        # 敏感词过滤功能验证
        # 验证内容审核相关配置并设置默认值
        config, current_related_config_keys = SensitiveWordAvoidanceConfigManager.validate_and_set_defaults(
            tenant_id, config
        )
        related_config_keys.extend(current_related_config_keys)

        # 去重配置键名列表
        related_config_keys = list(set(related_config_keys))

        # 过滤掉额外的参数，只保留相关的配置
        filtered_config = {key: config.get(key) for key in related_config_keys}

        return filtered_config
