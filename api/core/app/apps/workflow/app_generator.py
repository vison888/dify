import contextvars
import logging
import threading
import uuid
from collections.abc import Generator, Mapping, Sequence
from typing import Any, Literal, Optional, Union, overload

from flask import Flask, current_app
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

import contexts
from configs import dify_config
from core.app.app_config.features.file_upload.manager import FileUploadConfigManager
from core.app.apps.base_app_generator import BaseAppGenerator
from core.app.apps.base_app_queue_manager import AppQueueManager, PublishFrom
from core.app.apps.exc import GenerateTaskStoppedError
from core.app.apps.workflow.app_config_manager import WorkflowAppConfigManager
from core.app.apps.workflow.app_queue_manager import WorkflowAppQueueManager
from core.app.apps.workflow.app_runner import WorkflowAppRunner
from core.app.apps.workflow.generate_response_converter import WorkflowAppGenerateResponseConverter
from core.app.apps.workflow.generate_task_pipeline import WorkflowAppGenerateTaskPipeline
from core.app.entities.app_invoke_entities import InvokeFrom, WorkflowAppGenerateEntity
from core.app.entities.task_entities import WorkflowAppBlockingResponse, WorkflowAppStreamResponse
from core.helper.trace_id_helper import extract_external_trace_id_from_args
from core.model_runtime.errors.invoke import InvokeAuthorizationError
from core.ops.ops_trace_manager import TraceQueueManager
from core.repositories import DifyCoreRepositoryFactory
from core.workflow.repositories.draft_variable_repository import DraftVariableSaverFactory
from core.workflow.repositories.workflow_execution_repository import WorkflowExecutionRepository
from core.workflow.repositories.workflow_node_execution_repository import WorkflowNodeExecutionRepository
from core.workflow.variable_loader import DUMMY_VARIABLE_LOADER, VariableLoader
from extensions.ext_database import db
from factories import file_factory
from libs.flask_utils import preserve_flask_contexts
from models import Account, App, EndUser, Workflow, WorkflowNodeExecutionTriggeredFrom
from models.enums import WorkflowRunTriggeredFrom
from services.workflow_draft_variable_service import DraftVarLoader, WorkflowDraftVariableService

logger = logging.getLogger(__name__)


class WorkflowAppGenerator(BaseAppGenerator):
    """
    工作流应用生成器
    
    负责工作流类型应用的生成和执行，是工作流执行流程的核心组件。
    主要职责包括：
    1. 解析文件配置和用户输入
    2. 创建工作流执行实体
    3. 设置多线程执行环境
    4. 管理执行生命周期
    5. 处理流式和阻塞式响应
    """
    
    @overload
    def generate(
        self,
        *,
        app_model: App,
        workflow: Workflow,
        user: Union[Account, EndUser],
        args: Mapping[str, Any],
        invoke_from: InvokeFrom,
        streaming: Literal[True],
        call_depth: int,
        workflow_thread_pool_id: Optional[str],
    ) -> Generator[Mapping | str, None, None]: ...

    @overload
    def generate(
        self,
        *,
        app_model: App,
        workflow: Workflow,
        user: Union[Account, EndUser],
        args: Mapping[str, Any],
        invoke_from: InvokeFrom,
        streaming: Literal[False],
        call_depth: int,
        workflow_thread_pool_id: Optional[str],
    ) -> Mapping[str, Any]: ...

    @overload
    def generate(
        self,
        *,
        app_model: App,
        workflow: Workflow,
        user: Union[Account, EndUser],
        args: Mapping[str, Any],
        invoke_from: InvokeFrom,
        streaming: bool,
        call_depth: int,
        workflow_thread_pool_id: Optional[str],
    ) -> Union[Mapping[str, Any], Generator[Mapping | str, None, None]]: ...

    def generate(
        self,
        *,
        app_model: App,
        workflow: Workflow,
        user: Union[Account, EndUser],
        args: Mapping[str, Any],
        invoke_from: InvokeFrom,
        streaming: bool = True,
        call_depth: int = 0,
        workflow_thread_pool_id: Optional[str] = None,
    ) -> Union[Mapping[str, Any], Generator[Mapping | str, None, None]]:
        """
        工作流应用生成的主入口方法
        
        这个方法是工作流执行的核心入口，负责整个工作流生成流程的协调。
        执行步骤包括：
        1. 文件解析和配置提取
        2. 应用配置转换
        3. 跟踪管理器初始化
        4. 工作流执行实体创建
        5. 数据库仓库初始化
        6. 调用内部_generate方法
        
        Args:
            app_model: 应用模型，包含应用的基本信息
            workflow: 工作流配置，包含图结构和特性
            user: 执行用户，可以是Account或EndUser
            args: 请求参数，包含用户输入和文件
            invoke_from: 调用来源，影响权限和行为
            streaming: 是否启用流式响应
            call_depth: 调用深度，防止无限递归
            workflow_thread_pool_id: 线程池ID，用于并发控制
            
        Returns:
            流式生成器或最终结果映射
        """
        # 第一步：提取和解析文件信息
        files: Sequence[Mapping[str, Any]] = args.get("files") or []

        # 解析文件配置
        # TODO(QuantumGhost): Move file parsing logic to the API controller layer
        # for better separation of concerns.
        #
        # For implementation reference, see the `_parse_file` function and
        # `DraftWorkflowNodeRunApi` class which handle this properly.
        file_extra_config = FileUploadConfigManager.convert(workflow.features_dict, is_vision=False)
        # 根据文件映射构建文件对象
        system_files = file_factory.build_from_mappings(
            mappings=files,
            tenant_id=app_model.tenant_id,
            config=file_extra_config,
            # 服务API调用需要严格的类型验证
            strict_type_validation=True if invoke_from == InvokeFrom.SERVICE_API else False,
        )

        # 第二步：转换为应用配置
        # 将工作流配置转换为应用生成器可用的配置格式
        app_config = WorkflowAppConfigManager.get_app_config(
            app_model=app_model,
            workflow=workflow,
        )

        # 第三步：初始化跟踪管理器
        # 用于监控和追踪工作流执行过程
        trace_manager = TraceQueueManager(
            app_id=app_model.id,
            user_id=user.id if isinstance(user, Account) else user.session_id,
        )

        # 第四步：准备用户输入和额外参数
        inputs: Mapping[str, Any] = args["inputs"]
        extras = {
            **extract_external_trace_id_from_args(args),
        }
        workflow_run_id = str(uuid.uuid4())  # 生成唯一的工作流执行ID
        
        # 第五步：创建工作流应用生成实体
        # 这是整个工作流执行过程的核心数据结构
        application_generate_entity = WorkflowAppGenerateEntity(
            task_id=str(uuid.uuid4()),                          # 任务唯一标识
            app_config=app_config,                              # 应用配置
            file_upload_config=file_extra_config,               # 文件上传配置
            inputs=self._prepare_user_inputs(                   # 处理后的用户输入
                user_inputs=inputs,
                variables=app_config.variables,
                tenant_id=app_model.tenant_id,
                strict_type_validation=True if invoke_from == InvokeFrom.SERVICE_API else False,
            ),
            files=list(system_files),                           # 系统文件列表
            user_id=user.id,                                    # 用户ID
            stream=streaming,                                   # 流式响应标志
            invoke_from=invoke_from,                            # 调用来源
            call_depth=call_depth,                              # 调用深度
            trace_manager=trace_manager,                        # 跟踪管理器
            workflow_execution_id=workflow_run_id,              # 工作流执行ID
            extras=extras,                                      # 额外参数
        )

        # 第六步：初始化插件工具提供者上下文
        # 设置插件工具提供者的线程本地存储
        contexts.plugin_tool_providers.set({})
        contexts.plugin_tool_providers_lock.set(threading.Lock())

        # 第七步：创建数据库仓库
        # 创建数据库会话工厂，用于数据持久化
        session_factory = sessionmaker(bind=db.engine, expire_on_commit=False)
        
        # 根据调用来源确定触发源类型
        if invoke_from == InvokeFrom.DEBUGGER:
            workflow_triggered_from = WorkflowRunTriggeredFrom.DEBUGGING
        else:
            workflow_triggered_from = WorkflowRunTriggeredFrom.APP_RUN
            
        # 创建工作流执行仓库（管理工作流运行记录）
        workflow_execution_repository = DifyCoreRepositoryFactory.create_workflow_execution_repository(
            session_factory=session_factory,
            user=user,
            app_id=application_generate_entity.app_config.app_id,
            triggered_from=workflow_triggered_from,
        )
        
        # 创建工作流节点执行仓库（管理节点执行记录）
        workflow_node_execution_repository = DifyCoreRepositoryFactory.create_workflow_node_execution_repository(
            session_factory=session_factory,
            user=user,
            app_id=application_generate_entity.app_config.app_id,
            triggered_from=WorkflowNodeExecutionTriggeredFrom.WORKFLOW_RUN,
        )

        # 第八步：调用内部生成方法
        # 将控制权转交给内部_generate方法进行实际执行
        return self._generate(
            app_model=app_model,
            workflow=workflow,
            user=user,
            application_generate_entity=application_generate_entity,
            invoke_from=invoke_from,
            workflow_execution_repository=workflow_execution_repository,
            workflow_node_execution_repository=workflow_node_execution_repository,
            streaming=streaming,
            workflow_thread_pool_id=workflow_thread_pool_id,
        )

    def _generate(
        self,
        *,
        app_model: App,
        workflow: Workflow,
        user: Union[Account, EndUser],
        application_generate_entity: WorkflowAppGenerateEntity,
        invoke_from: InvokeFrom,
        workflow_execution_repository: WorkflowExecutionRepository,
        workflow_node_execution_repository: WorkflowNodeExecutionRepository,
        streaming: bool = True,
        workflow_thread_pool_id: Optional[str] = None,
        variable_loader: VariableLoader = DUMMY_VARIABLE_LOADER,
    ) -> Union[Mapping[str, Any], Generator[str | Mapping[str, Any], None, None]]:
        """
        工作流生成的内部实现方法
        
        负责工作流执行的核心协调，包括：
        1. 队列管理器初始化
        2. 多线程执行环境设置
        3. 工作线程启动
        4. 响应处理管道
        5. 结果转换
        
        这个方法采用生产者-消费者模式：
        - 工作线程作为生产者执行工作流并产生事件
        - 主线程作为消费者处理事件并生成响应
        
        Args:
            app_model: 应用模型
            workflow: 工作流配置
            user: 执行用户
            application_generate_entity: 应用生成实体
            invoke_from: 调用来源
            workflow_execution_repository: 工作流执行仓库
            workflow_node_execution_repository: 节点执行仓库
            streaming: 是否流式响应
            workflow_thread_pool_id: 线程池ID
            variable_loader: 变量加载器
            
        Returns:
            流式生成器或最终结果
        """
        # 第一步：初始化队列管理器
        # 队列管理器负责工作线程和主线程之间的事件通信
        queue_manager = WorkflowAppQueueManager(
            task_id=application_generate_entity.task_id,
            user_id=application_generate_entity.user_id,
            invoke_from=application_generate_entity.invoke_from,
            app_mode=app_model.mode,
        )

        # 第二步：准备多线程执行环境
        # 复制当前的上下文变量，确保工作线程能访问Flask应用上下文
        context = contextvars.copy_context()

        # 第三步：释放数据库连接
        # 因为后续的工作线程操作可能耗时较长，先释放主线程的数据库连接
        # 工作线程会创建自己的数据库会话
        db.session.close()

        # 第四步：创建并启动工作线程
        # 工作线程负责实际的工作流执行逻辑
        worker_thread = threading.Thread(
            target=self._generate_worker,
            kwargs={
                "flask_app": current_app._get_current_object(),  # type: ignore  # Flask应用实例
                "application_generate_entity": application_generate_entity,      # 生成实体
                "queue_manager": queue_manager,                                  # 队列管理器
                "context": context,                                             # 上下文变量
                "workflow_thread_pool_id": workflow_thread_pool_id,             # 线程池ID
                "variable_loader": variable_loader,                             # 变量加载器
            },
        )

        # 启动工作线程，开始异步执行工作流
        worker_thread.start()

        # 第五步：创建草稿变量保存工厂
        # 根据调用来源决定是否需要保存草稿变量
        draft_var_saver_factory = self._get_draft_var_saver_factory(
            invoke_from,
        )

        # 第六步：处理响应流
        # 从队列管理器监听事件，并转换为适当的响应格式
        response = self._handle_response(
            application_generate_entity=application_generate_entity,
            workflow=workflow,
            queue_manager=queue_manager,
            user=user,
            workflow_execution_repository=workflow_execution_repository,
            workflow_node_execution_repository=workflow_node_execution_repository,
            draft_var_saver_factory=draft_var_saver_factory,
            stream=streaming,
        )

        # 第七步：转换响应格式
        # 根据调用来源转换为最终的响应格式
        return WorkflowAppGenerateResponseConverter.convert(response=response, invoke_from=invoke_from)

    def single_iteration_generate(
        self,
        app_model: App,
        workflow: Workflow,
        node_id: str,
        user: Account | EndUser,
        args: Mapping[str, Any],
        streaming: bool = True,
    ) -> Mapping[str, Any] | Generator[str | Mapping[str, Any], None, None]:
        """
        Generate App response.

        :param app_model: App
        :param workflow: Workflow
        :param node_id: the node id
        :param user: account or end user
        :param args: request args
        :param streaming: is streamed
        """
        if not node_id:
            raise ValueError("node_id is required")

        if args.get("inputs") is None:
            raise ValueError("inputs is required")

        # convert to app config
        app_config = WorkflowAppConfigManager.get_app_config(app_model=app_model, workflow=workflow)

        # init application generate entity
        application_generate_entity = WorkflowAppGenerateEntity(
            task_id=str(uuid.uuid4()),
            app_config=app_config,
            inputs={},
            files=[],
            user_id=user.id,
            stream=streaming,
            invoke_from=InvokeFrom.DEBUGGER,
            extras={"auto_generate_conversation_name": False},
            single_iteration_run=WorkflowAppGenerateEntity.SingleIterationRunEntity(
                node_id=node_id, inputs=args["inputs"]
            ),
            workflow_execution_id=str(uuid.uuid4()),
        )
        contexts.plugin_tool_providers.set({})
        contexts.plugin_tool_providers_lock.set(threading.Lock())

        # Create repositories
        #
        # Create session factory
        session_factory = sessionmaker(bind=db.engine, expire_on_commit=False)
        # Create workflow execution(aka workflow run) repository
        workflow_execution_repository = DifyCoreRepositoryFactory.create_workflow_execution_repository(
            session_factory=session_factory,
            user=user,
            app_id=application_generate_entity.app_config.app_id,
            triggered_from=WorkflowRunTriggeredFrom.DEBUGGING,
        )
        # Create workflow node execution repository
        workflow_node_execution_repository = DifyCoreRepositoryFactory.create_workflow_node_execution_repository(
            session_factory=session_factory,
            user=user,
            app_id=application_generate_entity.app_config.app_id,
            triggered_from=WorkflowNodeExecutionTriggeredFrom.SINGLE_STEP,
        )
        draft_var_srv = WorkflowDraftVariableService(db.session())
        draft_var_srv.prefill_conversation_variable_default_values(workflow)
        var_loader = DraftVarLoader(
            engine=db.engine,
            app_id=application_generate_entity.app_config.app_id,
            tenant_id=application_generate_entity.app_config.tenant_id,
        )

        return self._generate(
            app_model=app_model,
            workflow=workflow,
            user=user,
            invoke_from=InvokeFrom.DEBUGGER,
            application_generate_entity=application_generate_entity,
            workflow_execution_repository=workflow_execution_repository,
            workflow_node_execution_repository=workflow_node_execution_repository,
            streaming=streaming,
            variable_loader=var_loader,
        )

    def single_loop_generate(
        self,
        app_model: App,
        workflow: Workflow,
        node_id: str,
        user: Account | EndUser,
        args: Mapping[str, Any],
        streaming: bool = True,
    ) -> Mapping[str, Any] | Generator[str | Mapping[str, Any], None, None]:
        """
        Generate App response.

        :param app_model: App
        :param workflow: Workflow
        :param node_id: the node id
        :param user: account or end user
        :param args: request args
        :param streaming: is streamed
        """
        if not node_id:
            raise ValueError("node_id is required")

        if args.get("inputs") is None:
            raise ValueError("inputs is required")

        # convert to app config
        app_config = WorkflowAppConfigManager.get_app_config(app_model=app_model, workflow=workflow)

        # init application generate entity
        application_generate_entity = WorkflowAppGenerateEntity(
            task_id=str(uuid.uuid4()),
            app_config=app_config,
            inputs={},
            files=[],
            user_id=user.id,
            stream=streaming,
            invoke_from=InvokeFrom.DEBUGGER,
            extras={"auto_generate_conversation_name": False},
            single_loop_run=WorkflowAppGenerateEntity.SingleLoopRunEntity(node_id=node_id, inputs=args["inputs"]),
            workflow_execution_id=str(uuid.uuid4()),
        )
        contexts.plugin_tool_providers.set({})
        contexts.plugin_tool_providers_lock.set(threading.Lock())

        # Create repositories
        #
        # Create session factory
        session_factory = sessionmaker(bind=db.engine, expire_on_commit=False)
        # Create workflow execution(aka workflow run) repository
        workflow_execution_repository = DifyCoreRepositoryFactory.create_workflow_execution_repository(
            session_factory=session_factory,
            user=user,
            app_id=application_generate_entity.app_config.app_id,
            triggered_from=WorkflowRunTriggeredFrom.DEBUGGING,
        )
        # Create workflow node execution repository
        workflow_node_execution_repository = DifyCoreRepositoryFactory.create_workflow_node_execution_repository(
            session_factory=session_factory,
            user=user,
            app_id=application_generate_entity.app_config.app_id,
            triggered_from=WorkflowNodeExecutionTriggeredFrom.SINGLE_STEP,
        )
        draft_var_srv = WorkflowDraftVariableService(db.session())
        draft_var_srv.prefill_conversation_variable_default_values(workflow)
        var_loader = DraftVarLoader(
            engine=db.engine,
            app_id=application_generate_entity.app_config.app_id,
            tenant_id=application_generate_entity.app_config.tenant_id,
        )
        return self._generate(
            app_model=app_model,
            workflow=workflow,
            user=user,
            invoke_from=InvokeFrom.DEBUGGER,
            application_generate_entity=application_generate_entity,
            workflow_execution_repository=workflow_execution_repository,
            workflow_node_execution_repository=workflow_node_execution_repository,
            streaming=streaming,
            variable_loader=var_loader,
        )

    def _generate_worker(
        self,
        flask_app: Flask,
        application_generate_entity: WorkflowAppGenerateEntity,
        queue_manager: AppQueueManager,
        context: contextvars.Context,
        variable_loader: VariableLoader,
        workflow_thread_pool_id: Optional[str] = None,
    ) -> None:
        """
        Generate worker in a new thread.
        :param flask_app: Flask app
        :param application_generate_entity: application generate entity
        :param queue_manager: queue manager
        :param workflow_thread_pool_id: workflow thread pool id
        :return:
        """

        with preserve_flask_contexts(flask_app, context_vars=context):
            with Session(db.engine, expire_on_commit=False) as session:
                workflow = session.scalar(
                    select(Workflow).where(
                        Workflow.tenant_id == application_generate_entity.app_config.tenant_id,
                        Workflow.app_id == application_generate_entity.app_config.app_id,
                        Workflow.id == application_generate_entity.app_config.workflow_id,
                    )
                )
                if workflow is None:
                    raise ValueError("Workflow not found")

                # Determine system_user_id based on invocation source
                is_external_api_call = application_generate_entity.invoke_from in {
                    InvokeFrom.WEB_APP,
                    InvokeFrom.SERVICE_API,
                }

                if is_external_api_call:
                    # For external API calls, use end user's session ID
                    end_user = session.scalar(select(EndUser).where(EndUser.id == application_generate_entity.user_id))
                    system_user_id = end_user.session_id if end_user else ""
                else:
                    # For internal calls, use the original user ID
                    system_user_id = application_generate_entity.user_id

            runner = WorkflowAppRunner(
                application_generate_entity=application_generate_entity,
                queue_manager=queue_manager,
                workflow_thread_pool_id=workflow_thread_pool_id,
                variable_loader=variable_loader,
                workflow=workflow,
                system_user_id=system_user_id,
            )

            try:
                runner.run()
            except GenerateTaskStoppedError as e:
                logger.warning(f"Task stopped: {str(e)}")
                pass
            except InvokeAuthorizationError:
                queue_manager.publish_error(
                    InvokeAuthorizationError("Incorrect API key provided"), PublishFrom.APPLICATION_MANAGER
                )
            except ValidationError as e:
                logger.exception("Validation Error when generating")
                queue_manager.publish_error(e, PublishFrom.APPLICATION_MANAGER)
            except ValueError as e:
                if dify_config.DEBUG:
                    logger.exception("Error when generating")
                queue_manager.publish_error(e, PublishFrom.APPLICATION_MANAGER)
            except Exception as e:
                logger.exception("Unknown Error when generating")
                queue_manager.publish_error(e, PublishFrom.APPLICATION_MANAGER)

    def _handle_response(
        self,
        application_generate_entity: WorkflowAppGenerateEntity,
        workflow: Workflow,
        queue_manager: AppQueueManager,
        user: Union[Account, EndUser],
        workflow_execution_repository: WorkflowExecutionRepository,
        workflow_node_execution_repository: WorkflowNodeExecutionRepository,
        draft_var_saver_factory: DraftVariableSaverFactory,
        stream: bool = False,
    ) -> Union[WorkflowAppBlockingResponse, Generator[WorkflowAppStreamResponse, None, None]]:
        """
        Handle response.
        :param application_generate_entity: application generate entity
        :param workflow: workflow
        :param queue_manager: queue manager
        :param user: account or end user
        :param stream: is stream
        :param workflow_node_execution_repository: optional repository for workflow node execution
        :return:
        """
        # init generate task pipeline
        generate_task_pipeline = WorkflowAppGenerateTaskPipeline(
            application_generate_entity=application_generate_entity,
            workflow=workflow,
            queue_manager=queue_manager,
            user=user,
            workflow_execution_repository=workflow_execution_repository,
            workflow_node_execution_repository=workflow_node_execution_repository,
            draft_var_saver_factory=draft_var_saver_factory,
            stream=stream,
        )

        try:
            return generate_task_pipeline.process()
        except ValueError as e:
            if len(e.args) > 0 and e.args[0] == "I/O operation on closed file.":  # ignore this error
                raise GenerateTaskStoppedError()
            else:
                logger.exception(
                    f"Fails to process generate task pipeline, task_id: {application_generate_entity.task_id}"
                )
                raise e
