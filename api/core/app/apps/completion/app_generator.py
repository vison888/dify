import logging
import threading
import uuid
from collections.abc import Generator, Mapping
from typing import Any, Literal, Union, overload

from flask import Flask, copy_current_request_context, current_app
from pydantic import ValidationError

from configs import dify_config
from core.app.app_config.easy_ui_based_app.model_config.converter import ModelConfigConverter
from core.app.app_config.features.file_upload.manager import FileUploadConfigManager
from core.app.apps.base_app_queue_manager import AppQueueManager, PublishFrom
from core.app.apps.completion.app_config_manager import CompletionAppConfigManager
from core.app.apps.completion.app_runner import CompletionAppRunner
from core.app.apps.completion.generate_response_converter import CompletionAppGenerateResponseConverter
from core.app.apps.exc import GenerateTaskStoppedError
from core.app.apps.message_based_app_generator import MessageBasedAppGenerator
from core.app.apps.message_based_app_queue_manager import MessageBasedAppQueueManager
from core.app.entities.app_invoke_entities import CompletionAppGenerateEntity, InvokeFrom
from core.model_runtime.errors.invoke import InvokeAuthorizationError
from core.ops.ops_trace_manager import TraceQueueManager
from extensions.ext_database import db
from factories import file_factory
from models import Account, App, EndUser, Message
from services.errors.app import MoreLikeThisDisabledError
from services.errors.message import MessageNotExistsError

logger = logging.getLogger(__name__)


class CompletionAppGenerator(MessageBasedAppGenerator):
    """
    补全应用生成器
    
    负责补全应用的生成和执行，是补全应用执行流程的核心组件。
    继承自MessageBasedAppGenerator，专门处理基于消息的补全应用。
    
    主要职责包括：
    1. 解析文件配置和用户输入
    2. 创建补全应用执行实体
    3. 设置多线程执行环境
    4. 管理执行生命周期
    5. 处理流式和阻塞式响应
    6. 支持"更多类似内容"功能
    """
    
    @overload
    def generate(
        self,
        app_model: App,
        user: Union[Account, EndUser],
        args: Mapping[str, Any],
        invoke_from: InvokeFrom,
        streaming: Literal[True],
    ) -> Generator[str | Mapping[str, Any], None, None]: ...

    @overload
    def generate(
        self,
        app_model: App,
        user: Union[Account, EndUser],
        args: Mapping[str, Any],
        invoke_from: InvokeFrom,
        streaming: Literal[False],
    ) -> Mapping[str, Any]: ...

    @overload
    def generate(
        self,
        app_model: App,
        user: Union[Account, EndUser],
        args: Mapping[str, Any],
        invoke_from: InvokeFrom,
        streaming: bool = False,
    ) -> Union[Mapping[str, Any], Generator[str | Mapping[str, Any], None, None]]: ...

    def generate(
        self,
        app_model: App,
        user: Union[Account, EndUser],
        args: Mapping[str, Any],
        invoke_from: InvokeFrom,
        streaming: bool = True,
    ) -> Union[Mapping[str, Any], Generator[str | Mapping[str, Any], None, None]]:
        """
        生成补全应用响应
        
        这是补全应用生成的主入口方法，负责整个补全应用生成流程的协调。
        执行步骤包括：
        1. 输入验证和预处理
        2. 获取应用模型配置
        3. 验证覆盖配置（调试模式）
        4. 文件解析和处理
        5. 创建应用配置和生成实体
        6. 多线程执行
        7. 响应处理

        Args:
            app_model: 应用模型，包含应用的基本信息
            user: 执行用户，可以是Account或EndUser
            args: 请求参数，包含查询、输入、文件等
            invoke_from: 调用来源，影响权限和行为
            streaming: 是否启用流式响应
            
        Returns:
            流式生成器或最终结果映射
            
        Raises:
            ValueError: 当查询不是字符串或只有调试模式可以覆盖模型配置时
        """
        # 第一步：验证和预处理查询内容
        query = args["query"]
        if not isinstance(query, str):
            raise ValueError("query must be a string")

        # 清理查询中的空字符，避免模型处理异常
        query = query.replace("\x00", "")
        inputs = args["inputs"]

        # 第二步：获取对话上下文（补全应用通常没有对话）
        conversation = None

        # 第三步：获取应用模型配置
        app_model_config = self._get_app_model_config(app_model=app_model, conversation=conversation)

        # 第四步：验证覆盖模型配置（仅调试模式支持）
        override_model_config_dict = None
        if args.get("model_config"):
            # 只有在调试模式下才允许覆盖模型配置
            if invoke_from != InvokeFrom.DEBUGGER:
                raise ValueError("Only in App debug mode can override model config")

            # 验证覆盖配置的有效性
            override_model_config_dict = CompletionAppConfigManager.config_validate(
                tenant_id=app_model.tenant_id, config=args.get("model_config", {})
            )

        # 第五步：解析文件配置
        # TODO(QuantumGhost): 将文件解析逻辑移到API控制器层
        # 以便更好地分离关注点
        #
        # 实现参考可以看 `_parse_file` 函数和
        # `DraftWorkflowNodeRunApi` 类的正确处理方式
        files = args["files"] if args.get("files") else []
        # 获取文件上传配置，优先使用覆盖配置，否则使用模型配置
        file_extra_config = FileUploadConfigManager.convert(override_model_config_dict or app_model_config.to_dict())
        if file_extra_config:
            # 根据文件映射构建文件对象
            file_objs = file_factory.build_from_mappings(
                mappings=files,
                tenant_id=app_model.tenant_id,
                config=file_extra_config,
            )
        else:
            file_objs = []

        # 第六步：转换为应用配置
        # 将应用模型配置转换为补全应用可用的配置格式
        app_config = CompletionAppConfigManager.get_app_config(
            app_model=app_model, app_model_config=app_model_config, override_config_dict=override_model_config_dict
        )

        # 第七步：获取跟踪实例
        # 用于监控和追踪应用执行过程
        trace_manager = TraceQueueManager(app_model.id)

        # 第八步：初始化应用生成实体
        # 创建补全应用生成实体，包含执行所需的所有配置和参数
        application_generate_entity = CompletionAppGenerateEntity(
            task_id=str(uuid.uuid4()),                          # 任务唯一标识
            app_config=app_config,                              # 应用配置
            model_conf=ModelConfigConverter.convert(app_config), # 模型配置转换
            file_upload_config=file_extra_config,               # 文件上传配置
            inputs=self._prepare_user_inputs(                   # 处理后的用户输入
                user_inputs=inputs, variables=app_config.variables, tenant_id=app_model.tenant_id
            ),
            query=query,                                        # 查询内容
            files=list(file_objs),                              # 文件对象列表
            user_id=user.id,                                    # 用户ID
            stream=streaming,                                   # 流式响应标志
            invoke_from=invoke_from,                            # 调用来源
            extras={},                                          # 额外参数
            trace_manager=trace_manager,                        # 跟踪管理器
        )

        # 第九步：初始化生成记录
        # 创建对话和消息记录，用于跟踪执行过程
        (conversation, message) = self._init_generate_records(application_generate_entity)

        # 第十步：初始化队列管理器
        # 创建基于消息的应用队列管理器，用于事件通信
        queue_manager = MessageBasedAppQueueManager(
            task_id=application_generate_entity.task_id,
            user_id=application_generate_entity.user_id,
            invoke_from=application_generate_entity.invoke_from,
            conversation_id=conversation.id,
            app_mode=conversation.mode,
            message_id=message.id,
        )

        # 第十一步：创建工作线程
        # 使用Flask请求上下文的工作线程，确保上下文信息传递
        @copy_current_request_context
        def worker_with_context():
            return self._generate_worker(
                flask_app=current_app._get_current_object(),  # type: ignore  # Flask应用实例
                application_generate_entity=application_generate_entity,      # 生成实体
                queue_manager=queue_manager,                                  # 队列管理器
                message_id=message.id,                                       # 消息ID
            )

        # 创建并启动工作线程
        worker_thread = threading.Thread(target=worker_with_context)
        worker_thread.start()

        # 第十二步：处理响应
        # 从队列管理器监听事件，并转换为适当的响应格式
        response = self._handle_response(
            application_generate_entity=application_generate_entity,
            queue_manager=queue_manager,
            conversation=conversation,
            message=message,
            user=user,
            stream=streaming,
        )

        # 第十三步：转换响应格式
        # 根据调用来源转换为最终的响应格式
        return CompletionAppGenerateResponseConverter.convert(response=response, invoke_from=invoke_from)

    def _generate_worker(
        self,
        flask_app: Flask,
        application_generate_entity: CompletionAppGenerateEntity,
        queue_manager: AppQueueManager,
        message_id: str,
    ) -> None:
        """
        工作线程中的生成器
        
        在独立线程中执行补全应用的实际生成逻辑，包括异常处理和资源清理。
        这个方法确保即使在生成过程中出现异常，也能正确地清理资源并通知客户端。
        
        Args:
            flask_app: Flask应用实例，用于创建应用上下文
            application_generate_entity: 补全应用生成实体
            queue_manager: 队列管理器，用于事件通信和错误报告
            message_id: 消息ID，用于获取消息记录
        """
        # 在Flask应用上下文中执行
        with flask_app.app_context():
            try:
                # 获取消息记录
                message = self._get_message(message_id)

                # 创建补全应用运行器并执行
                runner = CompletionAppRunner()
                runner.run(
                    application_generate_entity=application_generate_entity,
                    queue_manager=queue_manager,
                    message=message,
                )
            except GenerateTaskStoppedError:
                # 生成任务被停止，正常情况，不需要特殊处理
                pass
            except InvokeAuthorizationError:
                # API密钥错误，发布授权错误
                queue_manager.publish_error(
                    InvokeAuthorizationError("Incorrect API key provided"), PublishFrom.APPLICATION_MANAGER
                )
            except ValidationError as e:
                # 数据验证错误，记录异常并发布错误
                logger.exception("Validation Error when generating")
                queue_manager.publish_error(e, PublishFrom.APPLICATION_MANAGER)
            except ValueError as e:
                # 值错误，在调试模式下记录详细异常
                if dify_config.DEBUG:
                    logger.exception("Error when generating")
                queue_manager.publish_error(e, PublishFrom.APPLICATION_MANAGER)
            except Exception as e:
                # 未知异常，记录异常并发布错误
                logger.exception("Unknown Error when generating")
                queue_manager.publish_error(e, PublishFrom.APPLICATION_MANAGER)
            finally:
                # 确保关闭数据库会话，释放连接资源
                db.session.close()

    def generate_more_like_this(
        self,
        app_model: App,
        message_id: str,
        user: Union[Account, EndUser],
        invoke_from: InvokeFrom,
        stream: bool = True,
    ) -> Union[Mapping, Generator[Mapping | str, None, None]]:
        """
        生成更多类似内容
        
        基于已有的消息重新生成类似的内容，用于"更多类似内容"功能。
        这个功能允许用户在不改变输入的情况下，重新生成不同的答案。
        
        主要特点：
        1. 使用较高的温度参数(0.9)增加随机性
        2. 复用原消息的输入和配置
        3. 验证功能是否启用
        4. 支持流式和阻塞式响应
        
        Args:
            app_model: 应用模型
            message_id: 原始消息ID，用于获取原始输入和配置
            user: 执行用户
            invoke_from: 调用来源
            stream: 是否流式响应
            
        Returns:
            生成的响应映射或流式生成器
            
        Raises:
            MessageNotExistsError: 当消息不存在时
            MoreLikeThisDisabledError: 当功能未启用时
        """
        message = (
            db.session.query(Message)
            .filter(
                Message.id == message_id,
                Message.app_id == app_model.id,
                Message.from_source == ("api" if isinstance(user, EndUser) else "console"),
                Message.from_end_user_id == (user.id if isinstance(user, EndUser) else None),
                Message.from_account_id == (user.id if isinstance(user, Account) else None),
            )
            .first()
        )

        if not message:
            raise MessageNotExistsError()

        current_app_model_config = app_model.app_model_config
        more_like_this = current_app_model_config.more_like_this_dict

        if not current_app_model_config.more_like_this or more_like_this.get("enabled", False) is False:
            raise MoreLikeThisDisabledError()

        app_model_config = message.app_model_config
        override_model_config_dict = app_model_config.to_dict()
        model_dict = override_model_config_dict["model"]
        completion_params = model_dict.get("completion_params")
        completion_params["temperature"] = 0.9
        model_dict["completion_params"] = completion_params
        override_model_config_dict["model"] = model_dict

        # parse files
        file_extra_config = FileUploadConfigManager.convert(override_model_config_dict)
        if file_extra_config:
            file_objs = file_factory.build_from_mappings(
                mappings=message.message_files,
                tenant_id=app_model.tenant_id,
                config=file_extra_config,
            )
        else:
            file_objs = []

        # convert to app config
        app_config = CompletionAppConfigManager.get_app_config(
            app_model=app_model, app_model_config=app_model_config, override_config_dict=override_model_config_dict
        )

        # init application generate entity
        application_generate_entity = CompletionAppGenerateEntity(
            task_id=str(uuid.uuid4()),
            app_config=app_config,
            model_conf=ModelConfigConverter.convert(app_config),
            inputs=message.inputs,
            query=message.query,
            files=list(file_objs),
            user_id=user.id,
            stream=stream,
            invoke_from=invoke_from,
            extras={},
        )

        # init generate records
        (conversation, message) = self._init_generate_records(application_generate_entity)

        # init queue manager
        queue_manager = MessageBasedAppQueueManager(
            task_id=application_generate_entity.task_id,
            user_id=application_generate_entity.user_id,
            invoke_from=application_generate_entity.invoke_from,
            conversation_id=conversation.id,
            app_mode=conversation.mode,
            message_id=message.id,
        )

        # new thread with request context
        @copy_current_request_context
        def worker_with_context():
            return self._generate_worker(
                flask_app=current_app._get_current_object(),  # type: ignore
                application_generate_entity=application_generate_entity,
                queue_manager=queue_manager,
                message_id=message.id,
            )

        worker_thread = threading.Thread(target=worker_with_context)

        worker_thread.start()

        # return response or stream generator
        response = self._handle_response(
            application_generate_entity=application_generate_entity,
            queue_manager=queue_manager,
            conversation=conversation,
            message=message,
            user=user,
            stream=stream,
        )

        return CompletionAppGenerateResponseConverter.convert(response=response, invoke_from=invoke_from)
