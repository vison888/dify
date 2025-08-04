import logging
from typing import Optional, cast

from configs import dify_config
from core.app.apps.base_app_queue_manager import AppQueueManager
from core.app.apps.workflow.app_config_manager import WorkflowAppConfig
from core.app.apps.workflow_app_runner import WorkflowBasedAppRunner
from core.app.entities.app_invoke_entities import (
    InvokeFrom,
    WorkflowAppGenerateEntity,
)
from core.workflow.callbacks import WorkflowCallback, WorkflowLoggingCallback
from core.workflow.entities.variable_pool import VariablePool
from core.workflow.system_variable import SystemVariable
from core.workflow.variable_loader import VariableLoader
from core.workflow.workflow_entry import WorkflowEntry
from models.enums import UserFrom
from models.workflow import Workflow, WorkflowType

logger = logging.getLogger(__name__)


class WorkflowAppRunner(WorkflowBasedAppRunner):
    """
    工作流应用运行器
    
    负责实际执行工作流应用的核心运行逻辑。继承自WorkflowBasedAppRunner，
    专门处理工作流类型应用的执行流程。
    
    主要职责：
    1. 初始化工作流执行环境和变量池
    2. 根据不同的运行模式（完整运行、单次迭代、单次循环）选择执行策略
    3. 设置工作流回调处理器
    4. 创建WorkflowEntry并启动工作流执行
    5. 处理工作流执行过程中的事件
    """

    def __init__(
        self,
        *,
        application_generate_entity: WorkflowAppGenerateEntity,
        queue_manager: AppQueueManager,
        variable_loader: VariableLoader,
        workflow_thread_pool_id: Optional[str] = None,
        workflow: Workflow,
        system_user_id: str,
    ) -> None:
        """
        初始化工作流应用运行器
        
        Args:
            application_generate_entity: 工作流应用生成实体，包含执行所需的所有配置
            queue_manager: 应用队列管理器，用于事件通信
            variable_loader: 变量加载器，用于加载运行时变量
            workflow_thread_pool_id: 工作流线程池ID，用于并发控制
            workflow: 工作流对象，包含图结构和配置
            system_user_id: 系统用户ID，用于系统变量
        """
        # 调用父类构造函数，传递必要的依赖
        super().__init__(
            queue_manager=queue_manager,
            variable_loader=variable_loader,
            app_id=application_generate_entity.app_config.app_id,
        )
        # 保存应用生成实体
        self.application_generate_entity = application_generate_entity
        # 保存线程池ID
        self.workflow_thread_pool_id = workflow_thread_pool_id
        # 保存工作流对象
        self._workflow = workflow
        # 保存系统用户ID
        self._sys_user_id = system_user_id

    def run(self) -> None:
        """
        运行工作流应用
        
        这是工作流执行的核心方法，负责：
        1. 根据执行类型选择不同的执行策略
        2. 初始化变量池和执行图
        3. 创建工作流入口点并启动执行
        4. 处理执行过程中产生的事件
        """
        # 获取应用配置并转换为工作流配置类型
        app_config = self.application_generate_entity.app_config
        app_config = cast(WorkflowAppConfig, app_config)

        # 初始化工作流回调列表
        workflow_callbacks: list[WorkflowCallback] = []
        # 在调试模式下添加日志回调
        if dify_config.DEBUG:
            workflow_callbacks.append(WorkflowLoggingCallback())

        # 根据执行类型选择不同的执行策略
        if self.application_generate_entity.single_iteration_run:
            # 单次迭代运行模式
            # 只执行指定节点的单次迭代，用于调试和测试
            graph, variable_pool = self._get_graph_and_variable_pool_of_single_iteration(
                workflow=self._workflow,
                node_id=self.application_generate_entity.single_iteration_run.node_id,
                user_inputs=self.application_generate_entity.single_iteration_run.inputs,
            )
        elif self.application_generate_entity.single_loop_run:
            # 单次循环运行模式
            # 只执行指定节点的单次循环，用于调试和测试
            graph, variable_pool = self._get_graph_and_variable_pool_of_single_loop(
                workflow=self._workflow,
                node_id=self.application_generate_entity.single_loop_run.node_id,
                user_inputs=self.application_generate_entity.single_loop_run.inputs,
            )
        else:
            # 完整工作流运行模式
            # 执行完整的工作流，从开始节点到结束节点
            inputs = self.application_generate_entity.inputs
            files = self.application_generate_entity.files

            # 创建系统变量对象
            # 系统变量包含文件、用户ID、应用ID等系统级信息
            system_inputs = SystemVariable(
                files=files,
                user_id=self._sys_user_id,
                app_id=app_config.app_id,
                workflow_id=app_config.workflow_id,
                workflow_execution_id=self.application_generate_entity.workflow_execution_id,
            )

            # 创建变量池
            # 变量池管理工作流执行过程中的所有变量
            variable_pool = VariablePool(
                system_variables=system_inputs,                      # 系统变量
                user_inputs=inputs,                                  # 用户输入变量
                environment_variables=self._workflow.environment_variables,  # 环境变量
                conversation_variables=[],                           # 对话变量（工作流中通常为空）
            )

            # 初始化执行图
            # 根据工作流的图配置创建可执行的图对象
            graph = self._init_graph(graph_config=self._workflow.graph_dict)

        # 创建工作流入口点
        # WorkflowEntry是工作流执行的核心入口，负责协调整个执行过程
        workflow_entry = WorkflowEntry(
            tenant_id=self._workflow.tenant_id,
            app_id=self._workflow.app_id,
            workflow_id=self._workflow.id,
            workflow_type=WorkflowType.value_of(self._workflow.type),
            graph=graph,
            graph_config=self._workflow.graph_dict,
            user_id=self.application_generate_entity.user_id,
            # 根据调用来源确定用户类型
            user_from=(
                UserFrom.ACCOUNT
                if self.application_generate_entity.invoke_from in {InvokeFrom.EXPLORE, InvokeFrom.DEBUGGER}
                else UserFrom.END_USER
            ),
            invoke_from=self.application_generate_entity.invoke_from,
            call_depth=self.application_generate_entity.call_depth,
            variable_pool=variable_pool,
            thread_pool_id=self.workflow_thread_pool_id,
        )

        # 启动工作流执行并获取事件生成器
        generator = workflow_entry.run(callbacks=workflow_callbacks)

        # 处理工作流执行过程中产生的每个事件
        for event in generator:
            self._handle_event(workflow_entry, event)
