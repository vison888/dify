from collections.abc import Mapping, Sequence
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator

from constants import UUID_NIL
from core.app.app_config.entities import EasyUIBasedAppConfig, WorkflowUIBasedAppConfig
from core.entities.provider_configuration import ProviderModelBundle
from core.file import File, FileUploadConfig
from core.model_runtime.entities.model_entities import AIModelEntity
from core.ops.ops_trace_manager import TraceQueueManager


class InvokeFrom(Enum):
    """
    调用来源枚举
    
    定义了应用调用的不同来源，用于权限控制和行为差异化。
    不同的调用来源会影响：
    1. 权限验证策略
    2. 限流策略
    3. 数据持久化行为
    4. 错误处理方式
    """

    # SERVICE_API 表示从服务API调用Dify应用
    # 这是外部系统集成的主要方式，需要API密钥认证
    #
    # Dify文档中的服务API说明：
    # https://docs.dify.ai/en/guides/application-publishing/developing-with-apis
    SERVICE_API = "service-api"

    # WEB_APP 表示从工作流（或聊天流）的Web应用调用
    # 这是最终用户使用应用的主要方式
    #
    # Dify文档中的Web应用说明：
    # https://docs.dify.ai/en/guides/application-publishing/launch-your-webapp-quickly/README
    WEB_APP = "web-app"

    # EXPLORE 表示从工作流（或聊天流）的探索页面调用
    # 用于用户在发布前体验和测试应用
    EXPLORE = "explore"
    
    # DEBUGGER 表示从工作流（或聊天流）的编辑页面调用
    # 用于开发者调试和测试工作流逻辑
    DEBUGGER = "debugger"

    @classmethod
    def value_of(cls, value: str):
        """
        Get value of given mode.

        :param value: mode value
        :return: mode
        """
        for mode in cls:
            if mode.value == value:
                return mode
        raise ValueError(f"invalid invoke from value {value}")

    def to_source(self) -> str:
        """
        Get source of invoke from.

        :return: source
        """
        if self == InvokeFrom.WEB_APP:
            return "web_app"
        elif self == InvokeFrom.DEBUGGER:
            return "dev"
        elif self == InvokeFrom.EXPLORE:
            return "explore_app"
        elif self == InvokeFrom.SERVICE_API:
            return "api"

        return "dev"


class ModelConfigWithCredentialsEntity(BaseModel):
    """
    Model Config With Credentials Entity.
    """

    provider: str
    model: str
    model_schema: AIModelEntity
    mode: str
    provider_model_bundle: ProviderModelBundle
    credentials: dict[str, Any] = Field(default_factory=dict)
    parameters: dict[str, Any] = Field(default_factory=dict)
    stop: list[str] = Field(default_factory=list)

    # pydantic configs
    model_config = ConfigDict(protected_namespaces=())


class AppGenerateEntity(BaseModel):
    """
    App Generate Entity.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    task_id: str

    # app config
    app_config: Any
    file_upload_config: Optional[FileUploadConfig] = None

    inputs: Mapping[str, Any]
    files: Sequence[File]
    user_id: str

    # extras
    stream: bool
    invoke_from: InvokeFrom

    # invoke call depth
    call_depth: int = 0

    # extra parameters, like: auto_generate_conversation_name
    extras: dict[str, Any] = Field(default_factory=dict)

    # tracing instance
    trace_manager: Optional[TraceQueueManager] = None


class EasyUIBasedAppGenerateEntity(AppGenerateEntity):
    """
    Chat Application Generate Entity.
    """

    # app config
    app_config: EasyUIBasedAppConfig
    model_conf: ModelConfigWithCredentialsEntity

    query: Optional[str] = None

    # pydantic configs
    model_config = ConfigDict(protected_namespaces=())


class ConversationAppGenerateEntity(AppGenerateEntity):
    """
    Base entity for conversation-based app generation.
    """

    conversation_id: Optional[str] = None
    parent_message_id: Optional[str] = Field(
        default=None,
        description=(
            "Starting from v0.9.0, parent_message_id is used to support message regeneration for internal chat API."
            "For service API, we need to ensure its forward compatibility, "
            "so passing in the parent_message_id as request arg is not supported for now. "
            "It needs to be set to UUID_NIL so that the subsequent processing will treat it as legacy messages."
        ),
    )

    @field_validator("parent_message_id")
    @classmethod
    def validate_parent_message_id(cls, v, info: ValidationInfo):
        if info.data.get("invoke_from") == InvokeFrom.SERVICE_API and v != UUID_NIL:
            raise ValueError("parent_message_id should be UUID_NIL for service API")
        return v


class ChatAppGenerateEntity(ConversationAppGenerateEntity, EasyUIBasedAppGenerateEntity):
    """
    Chat Application Generate Entity.
    """

    pass


class CompletionAppGenerateEntity(EasyUIBasedAppGenerateEntity):
    """
    Completion Application Generate Entity.
    """

    pass


class AgentChatAppGenerateEntity(ConversationAppGenerateEntity, EasyUIBasedAppGenerateEntity):
    """
    Agent Chat Application Generate Entity.
    """

    pass


class AdvancedChatAppGenerateEntity(ConversationAppGenerateEntity):
    """
    Advanced Chat Application Generate Entity.
    """

    # app config
    app_config: WorkflowUIBasedAppConfig

    workflow_run_id: Optional[str] = None
    query: str

    class SingleIterationRunEntity(BaseModel):
        """
        Single Iteration Run Entity.
        """

        node_id: str
        inputs: Mapping

    single_iteration_run: Optional[SingleIterationRunEntity] = None

    class SingleLoopRunEntity(BaseModel):
        """
        Single Loop Run Entity.
        """

        node_id: str
        inputs: Mapping

    single_loop_run: Optional[SingleLoopRunEntity] = None


class WorkflowAppGenerateEntity(AppGenerateEntity):
    """
    工作流应用生成实体
    
    这是工作流执行过程中的核心数据结构，包含了执行工作流所需的所有信息。
    继承自AppGenerateEntity，增加了工作流特有的字段：
    1. 工作流执行ID
    2. 单次迭代运行配置
    3. 单次循环运行配置
    
    主要用途：
    - 在工作流执行的各个阶段传递执行上下文
    - 作为工作流生成器和处理管道之间的数据载体
    - 提供调试和监控所需的执行信息
    """

    # 应用配置，包含工作流的UI配置和变量定义
    app_config: WorkflowUIBasedAppConfig
    
    # 工作流执行ID，用于标识和跟踪具体的工作流执行实例
    workflow_execution_id: str

    class SingleIterationRunEntity(BaseModel):
        """
        单次迭代运行实体
        
        用于调试模式下的单节点迭代执行，允许开发者：
        - 测试特定迭代节点的行为
        - 验证迭代逻辑的正确性
        - 调试复杂的循环处理
        """

        node_id: str    # 要执行的迭代节点ID
        inputs: dict    # 传入迭代节点的输入数据

    # 单次迭代运行配置，仅在调试单个迭代节点时使用
    single_iteration_run: Optional[SingleIterationRunEntity] = None

    class SingleLoopRunEntity(BaseModel):
        """
        单次循环运行实体
        
        用于调试模式下的单节点循环执行，允许开发者：
        - 测试特定循环节点的行为
        - 验证循环条件和退出逻辑
        - 调试循环体内的处理逻辑
        """

        node_id: str    # 要执行的循环节点ID
        inputs: dict    # 传入循环节点的输入数据

    # 单次循环运行配置，仅在调试单个循环节点时使用
    single_loop_run: Optional[SingleLoopRunEntity] = None
