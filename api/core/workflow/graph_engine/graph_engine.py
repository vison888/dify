# 导入标准库
import contextvars  # 上下文变量，用于线程间传递上下文信息
import logging  # 日志记录
import queue  # 队列，用于线程间通信
import time  # 时间相关操作
import uuid  # 生成唯一标识符
from collections.abc import Generator, Mapping  # 类型提示用的抽象基类
from concurrent.futures import ThreadPoolExecutor, wait  # 线程池执行器
from copy import copy, deepcopy  # 浅拷贝和深拷贝
from datetime import UTC, datetime  # 日期时间处理
from typing import Any, Optional, cast  # 类型提示

# 导入Flask相关
from flask import Flask, current_app

# 导入配置
from configs import dify_config

# 导入应用异常
from core.app.apps.exc import GenerateTaskStoppedError
from core.app.entities.app_invoke_entities import InvokeFrom

# 导入工作流相关实体
from core.workflow.entities.node_entities import AgentNodeStrategyInit, NodeRunResult
from core.workflow.entities.variable_pool import VariablePool, VariableValue
from core.workflow.entities.workflow_node_execution import WorkflowNodeExecutionMetadataKey, WorkflowNodeExecutionStatus

# 导入条件管理器
from core.workflow.graph_engine.condition_handlers.condition_manager import ConditionManager

# 导入图引擎事件
from core.workflow.graph_engine.entities.event import (
    BaseAgentEvent,
    BaseIterationEvent,
    BaseLoopEvent,
    GraphEngineEvent,
    GraphRunFailedEvent,
    GraphRunPartialSucceededEvent,
    GraphRunStartedEvent,
    GraphRunSucceededEvent,
    NodeRunExceptionEvent,
    NodeRunFailedEvent,
    NodeRunRetrieverResourceEvent,
    NodeRunRetryEvent,
    NodeRunStartedEvent,
    NodeRunStreamChunkEvent,
    NodeRunSucceededEvent,
    ParallelBranchRunFailedEvent,
    ParallelBranchRunStartedEvent,
    ParallelBranchRunSucceededEvent,
)

# 导入图引擎相关实体
from core.workflow.graph_engine.entities.graph import Graph, GraphEdge
from core.workflow.graph_engine.entities.graph_init_params import GraphInitParams
from core.workflow.graph_engine.entities.graph_runtime_state import GraphRuntimeState
from core.workflow.graph_engine.entities.runtime_route_state import RouteNodeState

# 导入节点相关
from core.workflow.nodes import NodeType
from core.workflow.nodes.agent.agent_node import AgentNode
from core.workflow.nodes.agent.entities import AgentNodeData
from core.workflow.nodes.answer.answer_stream_processor import AnswerStreamProcessor
from core.workflow.nodes.answer.base_stream_processor import StreamProcessor
from core.workflow.nodes.base import BaseNode
from core.workflow.nodes.end.end_stream_processor import EndStreamProcessor
from core.workflow.nodes.enums import ErrorStrategy, FailBranchSourceHandle
from core.workflow.nodes.event import RunCompletedEvent, RunRetrieverResourceEvent, RunStreamChunkEvent

# 导入工具函数
from core.workflow.utils import variable_utils
from libs.flask_utils import preserve_flask_contexts

# 导入模型
from models.enums import UserFrom
from models.workflow import WorkflowType

# 创建日志记录器
logger = logging.getLogger(__name__)


class GraphEngineThreadPool(ThreadPoolExecutor):
    """
    图引擎线程池类
    继承自ThreadPoolExecutor，提供线程池管理功能，并增加任务提交数量限制
    """
    def __init__(
        self,
        max_workers=None,  # 最大工作线程数
        thread_name_prefix="",  # 线程名称前缀
        initializer=None,  # 线程初始化函数
        initargs=(),  # 初始化函数参数
        max_submit_count=dify_config.MAX_SUBMIT_COUNT,  # 最大提交任务数
    ) -> None:
        """
        初始化图引擎线程池
        
        Args:
            max_workers: 最大工作线程数
            thread_name_prefix: 线程名称前缀
            initializer: 线程初始化函数
            initargs: 初始化函数参数
            max_submit_count: 最大提交任务数量限制
        """
        super().__init__(max_workers, thread_name_prefix, initializer, initargs)
        self.max_submit_count = max_submit_count  # 设置最大提交任务数
        self.submit_count = 0  # 当前已提交任务数

    def submit(self, fn, /, *args, **kwargs):
        """
        提交任务到线程池
        
        Args:
            fn: 要执行的函数
            *args: 函数的位置参数
            **kwargs: 函数的关键字参数
            
        Returns:
            Future对象
            
        Raises:
            ValueError: 当提交的任务数超过最大限制时
        """
        self.submit_count += 1  # 增加提交任务计数
        self.check_is_full()  # 检查是否达到最大提交数

        return super().submit(fn, *args, **kwargs)

    def task_done_callback(self, future):
        """
        任务完成回调函数
        当任务完成时减少提交计数
        
        Args:
            future: 已完成的Future对象
        """
        self.submit_count -= 1  # 减少提交任务计数

    def check_is_full(self) -> None:
        """
        检查线程池是否已满
        如果当前提交的任务数超过最大限制，抛出异常
        
        Raises:
            ValueError: 当提交的任务数超过最大限制时
        """
        if self.submit_count > self.max_submit_count:
            raise ValueError(f"Max submit count {self.max_submit_count} of workflow thread pool reached.")


class GraphEngine:
    """
    图引擎类
    负责工作流图的执行，包括节点的运行、并行分支处理、错误处理和重试机制
    """
    # 工作流线程池映射，存储所有活跃的线程池
    workflow_thread_pool_mapping: dict[str, GraphEngineThreadPool] = {}

    def __init__(
        self,
        tenant_id: str,  # 租户ID
        app_id: str,  # 应用ID
        workflow_type: WorkflowType,  # 工作流类型
        workflow_id: str,  # 工作流ID
        user_id: str,  # 用户ID
        user_from: UserFrom,  # 用户来源
        invoke_from: InvokeFrom,  # 调用来源
        call_depth: int,  # 调用深度
        graph: Graph,  # 工作流图对象
        graph_config: Mapping[str, Any],  # 图配置
        graph_runtime_state: GraphRuntimeState,  # 图运行时状态
        max_execution_steps: int,  # 最大执行步数
        max_execution_time: int,  # 最大执行时间（秒）
        thread_pool_id: Optional[str] = None,  # 线程池ID，可选
    ) -> None:
        """
        初始化图引擎
        
        Args:
            tenant_id: 租户ID
            app_id: 应用ID
            workflow_type: 工作流类型（聊天或完成）
            workflow_id: 工作流ID
            user_id: 用户ID
            user_from: 用户来源
            invoke_from: 调用来源
            call_depth: 调用深度，用于防止无限递归
            graph: 工作流图对象
            graph_config: 图配置信息
            graph_runtime_state: 图运行时状态
            max_execution_steps: 最大执行步数限制
            max_execution_time: 最大执行时间限制（秒）
            thread_pool_id: 可选的线程池ID，如果提供则使用现有线程池
        """
        # 设置线程池配置
        thread_pool_max_submit_count = dify_config.MAX_SUBMIT_COUNT  # 最大提交任务数
        thread_pool_max_workers = 10  # 最大工作线程数

        # 初始化线程池
        if thread_pool_id:
            # 如果提供了线程池ID，使用现有的线程池
            if thread_pool_id not in GraphEngine.workflow_thread_pool_mapping:
                raise ValueError(f"Max submit count {thread_pool_max_submit_count} of workflow thread pool reached.")

            self.thread_pool_id = thread_pool_id
            self.thread_pool = GraphEngine.workflow_thread_pool_mapping[thread_pool_id]
            self.is_main_thread_pool = False  # 标记为非主线程池
        else:
            # 创建新的线程池
            self.thread_pool = GraphEngineThreadPool(
                max_workers=thread_pool_max_workers, max_submit_count=thread_pool_max_submit_count
            )
            self.thread_pool_id = str(uuid.uuid4())  # 生成唯一线程池ID
            self.is_main_thread_pool = True  # 标记为主线程池
            GraphEngine.workflow_thread_pool_mapping[self.thread_pool_id] = self.thread_pool

        # 设置图对象
        self.graph = graph
        
        # 初始化图初始化参数
        self.init_params = GraphInitParams(
            tenant_id=tenant_id,
            app_id=app_id,
            workflow_type=workflow_type,
            workflow_id=workflow_id,
            graph_config=graph_config,
            user_id=user_id,
            user_from=user_from,
            invoke_from=invoke_from,
            call_depth=call_depth,
        )

        # 设置图运行时状态
        self.graph_runtime_state = graph_runtime_state

        # 设置执行限制
        self.max_execution_steps = max_execution_steps  # 最大执行步数
        self.max_execution_time = max_execution_time  # 最大执行时间

    def run(self) -> Generator[GraphEngineEvent, None, None]:
        """
        运行工作流图
        
        这是图引擎的主要入口方法，负责执行整个工作流图
        
        Returns:
            Generator[GraphEngineEvent, None, None]: 图引擎事件的生成器
            
        Yields:
            GraphEngineEvent: 各种图执行事件，包括启动、成功、失败等
        """
        # 触发图运行开始事件
        yield GraphRunStartedEvent()
        handle_exceptions: list[str] = []  # 存储处理的异常信息
        stream_processor: StreamProcessor  # 流处理器

        try:
            # 根据工作流类型选择相应的流处理器
            if self.init_params.workflow_type == WorkflowType.CHAT:
                # 聊天类型工作流使用答案流处理器
                stream_processor = AnswerStreamProcessor(
                    graph=self.graph, variable_pool=self.graph_runtime_state.variable_pool
                )
            else:
                # 其他类型工作流使用结束流处理器
                stream_processor = EndStreamProcessor(
                    graph=self.graph, variable_pool=self.graph_runtime_state.variable_pool
                )

            # 运行图，从根节点开始执行
            generator = stream_processor.process(
                self._run(start_node_id=self.graph.root_node_id, handle_exceptions=handle_exceptions)
            )
            
            # 处理图运行过程中产生的所有事件
            for item in generator:
                try:
                    yield item  # 向上层传递事件
                    
                    # 处理节点运行失败事件
                    if isinstance(item, NodeRunFailedEvent):
                        yield GraphRunFailedEvent(
                            error=item.route_node_state.failed_reason or "Unknown error.",
                            exceptions_count=len(handle_exceptions),
                        )
                        return
                    # 处理节点运行成功事件
                    elif isinstance(item, NodeRunSucceededEvent):
                        # 如果是END节点，设置图的输出
                        if item.node_type == NodeType.END:
                            self.graph_runtime_state.outputs = (
                                dict(item.route_node_state.node_run_result.outputs)
                                if item.route_node_state.node_run_result
                                and item.route_node_state.node_run_result.outputs
                                else {}
                            )
                        # 如果是ANSWER节点，累积答案输出
                        elif item.node_type == NodeType.ANSWER:
                            if "answer" not in self.graph_runtime_state.outputs:
                                self.graph_runtime_state.outputs["answer"] = ""

                            # 将新的答案内容追加到现有答案中
                            self.graph_runtime_state.outputs["answer"] += "\n" + (
                                item.route_node_state.node_run_result.outputs.get("answer", "")
                                if item.route_node_state.node_run_result
                                and item.route_node_state.node_run_result.outputs
                                else ""
                            )

                            # 去除答案前后的空白字符
                            self.graph_runtime_state.outputs["answer"] = self.graph_runtime_state.outputs[
                                "answer"
                            ].strip()
                except Exception as e:
                    # 处理事件处理过程中的异常
                    logger.exception("Graph run failed")
                    yield GraphRunFailedEvent(error=str(e), exceptions_count=len(handle_exceptions))
                    return
                    
            # 根据异常数量判断执行结果
            if len(handle_exceptions) > 0:
                # 有异常但部分成功
                yield GraphRunPartialSucceededEvent(
                    exceptions_count=len(handle_exceptions), outputs=self.graph_runtime_state.outputs
                )
            else:
                # 完全成功，触发图运行成功事件
                yield GraphRunSucceededEvent(outputs=self.graph_runtime_state.outputs)
            
            # 释放线程资源
            self._release_thread()
        except GraphRunFailedError as e:
            # 处理图运行失败异常
            yield GraphRunFailedEvent(error=e.error, exceptions_count=len(handle_exceptions))
            self._release_thread()
            return
        except Exception as e:
            # 处理未知异常
            logger.exception("Unknown Error when graph running")
            yield GraphRunFailedEvent(error=str(e), exceptions_count=len(handle_exceptions))
            self._release_thread()
            raise e

    def _release_thread(self):
        """
        释放线程池资源
        
        如果当前实例是主线程池的拥有者，则从全局映射中删除该线程池
        """
        if self.is_main_thread_pool and self.thread_pool_id in GraphEngine.workflow_thread_pool_mapping:
            del GraphEngine.workflow_thread_pool_mapping[self.thread_pool_id]

    def _run(
        self,
        start_node_id: str,  # 起始节点ID
        in_parallel_id: Optional[str] = None,  # 并行执行ID
        parent_parallel_id: Optional[str] = None,  # 父并行执行ID
        parent_parallel_start_node_id: Optional[str] = None,  # 父并行起始节点ID
        handle_exceptions: list[str] = [],  # 处理的异常列表
    ) -> Generator[GraphEngineEvent, None, None]:
        """
        执行工作流图的核心方法
        
        从指定的起始节点开始，按照图的连接关系依次执行节点，
        处理条件分支、并行执行等复杂逻辑
        
        Args:
            start_node_id: 起始节点ID
            in_parallel_id: 当前并行执行的ID（如果在并行分支中）
            parent_parallel_id: 父级并行执行的ID
            parent_parallel_start_node_id: 父级并行执行的起始节点ID
            handle_exceptions: 用于收集异常信息的列表
            
        Yields:
            GraphEngineEvent: 节点执行过程中产生的各种事件
            
        Raises:
            GraphRunFailedError: 当执行步数或时间超限，或节点配置错误时
        """
        # 如果在并行执行中，设置并行起始节点ID
        parallel_start_node_id = None
        if in_parallel_id:
            parallel_start_node_id = start_node_id

        next_node_id = start_node_id  # 下一个要执行的节点ID
        previous_route_node_state: Optional[RouteNodeState] = None  # 前一个节点的路由状态
        
        # 主执行循环，逐个执行节点直到到达终点
        while True:
            # 检查是否超过最大执行步数
            if self.graph_runtime_state.node_run_steps > self.max_execution_steps:
                raise GraphRunFailedError("Max steps {} reached.".format(self.max_execution_steps))

            # 检查是否超过最大执行时间
            if self._is_timed_out(
                start_at=self.graph_runtime_state.start_at, max_execution_time=self.max_execution_time
            ):
                raise GraphRunFailedError("Max execution time {}s reached.".format(self.max_execution_time))

            # 为当前节点创建路由状态
            route_node_state = self.graph_runtime_state.node_run_state.create_node_state(node_id=next_node_id)

            # 获取节点配置信息
            node_id = route_node_state.node_id
            node_config = self.graph.node_id_config_mapping.get(node_id)
            if not node_config:
                raise GraphRunFailedError(f"Node {node_id} config not found.")

            # 解析节点类型和版本
            node_type = NodeType(node_config.get("data", {}).get("type"))
            node_version = node_config.get("data", {}).get("version", "1")

            # 动态导入节点映射（避免循环导入）
            from core.workflow.nodes.node_mapping import NODE_TYPE_CLASSES_MAPPING

            # 获取对应的节点类
            node_cls = NODE_TYPE_CLASSES_MAPPING[node_type][node_version]

            # 获取前一个节点的ID
            previous_node_id = previous_route_node_state.node_id if previous_route_node_state else None

            # 创建节点实例
            node = node_cls(
                id=route_node_state.id,
                config=node_config,
                graph_init_params=self.init_params,
                graph=self.graph,
                graph_runtime_state=self.graph_runtime_state,
                previous_node_id=previous_node_id,
                thread_pool_id=self.thread_pool_id,
            )
            # 初始化节点数据
            node.init_node_data(node_config.get("data", {}))
            
            try:
                # 运行节点
                generator = self._run_node(
                    node=node,
                    route_node_state=route_node_state,
                    parallel_id=in_parallel_id,
                    parallel_start_node_id=parallel_start_node_id,
                    parent_parallel_id=parent_parallel_id,
                    parent_parallel_start_node_id=parent_parallel_start_node_id,
                    handle_exceptions=handle_exceptions,
                )

                # 处理节点运行过程中产生的事件
                for item in generator:
                    if isinstance(item, NodeRunStartedEvent):
                        # 增加运行步数计数
                        self.graph_runtime_state.node_run_steps += 1
                        item.route_node_state.index = self.graph_runtime_state.node_run_steps

                    yield item

                # 保存节点状态到全局状态映射
                self.graph_runtime_state.node_run_state.node_state_mapping[route_node_state.id] = route_node_state

                # 添加路由连接关系
                if previous_route_node_state:
                    self.graph_runtime_state.node_run_state.add_route(
                        source_node_state_id=previous_route_node_state.id, target_node_state_id=route_node_state.id
                    )
            except Exception as e:
                # 处理节点运行异常
                route_node_state.status = RouteNodeState.Status.FAILED
                route_node_state.failed_reason = str(e)
                yield NodeRunFailedEvent(
                    error=str(e),
                    id=node.id,
                    node_id=next_node_id,
                    node_type=node_type,
                    node_data=node.get_base_node_data(),
                    route_node_state=route_node_state,
                    parallel_id=in_parallel_id,
                    parallel_start_node_id=parallel_start_node_id,
                    parent_parallel_id=parent_parallel_id,
                    parent_parallel_start_node_id=parent_parallel_start_node_id,
                    node_version=node.version(),
                )
                raise e

            # 检查是否到达END节点，如果是则结束执行
            if (
                self.graph.node_id_config_mapping[next_node_id].get("data", {}).get("type", "").lower()
                == NodeType.END.value
            ):
                break

            # 更新前一个节点状态
            previous_route_node_state = route_node_state

            # 获取当前节点的所有出边
            edge_mappings = self.graph.edge_mapping.get(next_node_id)
            if not edge_mappings:
                # 没有出边，结束执行
                break

            # 根据出边数量决定执行策略
            if len(edge_mappings) == 1:
                # 单条出边的情况：简单的顺序执行
                edge = edge_mappings[0]
                
                # 检查错误处理策略：如果前一个节点异常且采用分支失败策略，则停止执行
                if (
                    previous_route_node_state.status == RouteNodeState.Status.EXCEPTION
                    and node.error_strategy == ErrorStrategy.FAIL_BRANCH
                    and edge.run_condition is None
                ):
                    break
                    
                # 如果边有运行条件，检查条件是否满足
                if edge.run_condition:
                    result = ConditionManager.get_condition_handler(
                        init_params=self.init_params,
                        graph=self.graph,
                        run_condition=edge.run_condition,
                    ).check(
                        graph_runtime_state=self.graph_runtime_state,
                        previous_route_node_state=previous_route_node_state,
                    )

                    # 条件不满足，停止执行
                    if not result:
                        break

                # 设置下一个要执行的节点
                next_node_id = edge.target_node_id
            else:
                # 多条出边的情况：需要处理条件分支或并行执行
                final_node_id = None

                # 检查是否有带条件的边
                if any(edge.run_condition for edge in edge_mappings):
                    # 有条件边：根据条件结果选择执行分支
                    # 按条件哈希值对边进行分组
                    condition_edge_mappings: dict[str, list[GraphEdge]] = {}
                    for edge in edge_mappings:
                        if edge.run_condition:
                            run_condition_hash = edge.run_condition.hash
                            if run_condition_hash not in condition_edge_mappings:
                                condition_edge_mappings[run_condition_hash] = []

                            condition_edge_mappings[run_condition_hash].append(edge)

                    # 遍历每组条件边，找到第一个满足条件的组
                    for _, sub_edge_mappings in condition_edge_mappings.items():
                        if len(sub_edge_mappings) == 0:
                            continue

                        edge = cast(GraphEdge, sub_edge_mappings[0])
                        if edge.run_condition is None:
                            logger.warning(f"Edge {edge.target_node_id} run condition is None")
                            continue

                        # 检查运行条件
                        result = ConditionManager.get_condition_handler(
                            init_params=self.init_params,
                            graph=self.graph,
                            run_condition=edge.run_condition,
                        ).check(
                            graph_runtime_state=self.graph_runtime_state,
                            previous_route_node_state=previous_route_node_state,
                        )

                        # 条件不满足，继续检查下一组
                        if not result:
                            continue

                        # 条件满足，决定执行策略
                        if len(sub_edge_mappings) == 1:
                            # 单个目标节点：直接执行
                            final_node_id = edge.target_node_id
                        else:
                            # 多个目标节点：并行执行
                            parallel_generator = self._run_parallel_branches(
                                edge_mappings=sub_edge_mappings,
                                in_parallel_id=in_parallel_id,
                                parallel_start_node_id=parallel_start_node_id,
                                handle_exceptions=handle_exceptions,
                            )

                            # 处理并行执行结果
                            for parallel_result in parallel_generator:
                                if isinstance(parallel_result, str):
                                    # 字符串结果表示最终节点ID
                                    final_node_id = parallel_result
                                else:
                                    # 事件对象，向上传递
                                    yield parallel_result

                        break  # 找到满足条件的分支，退出循环

                    # 没有找到满足条件的分支，停止执行
                    if not final_node_id:
                        break

                    next_node_id = final_node_id
                elif (
                    # 错误处理：如果节点设置了继续执行错误且前一个节点异常
                    node.continue_on_error
                    and node.error_strategy == ErrorStrategy.FAIL_BRANCH
                    and previous_route_node_state.status == RouteNodeState.Status.EXCEPTION
                ):
                    break
                else:
                    # 无条件边：并行执行所有分支
                    parallel_generator = self._run_parallel_branches(
                        edge_mappings=edge_mappings,
                        in_parallel_id=in_parallel_id,
                        parallel_start_node_id=parallel_start_node_id,
                        handle_exceptions=handle_exceptions,
                    )

                    # 处理并行执行结果
                    for generated_item in parallel_generator:
                        if isinstance(generated_item, str):
                            # 字符串结果表示最终节点ID
                            final_node_id = generated_item
                        else:
                            # 事件对象，向上传递
                            yield generated_item

                    # 并行执行完成但没有最终节点，停止执行
                    if not final_node_id:
                        break

                    next_node_id = final_node_id

            # 检查是否退出并行执行范围
            # 如果当前在并行执行中，但下一个节点不属于当前并行组，则退出
            if in_parallel_id and self.graph.node_parallel_mapping.get(next_node_id, "") != in_parallel_id:
                break

    def _run_parallel_branches(
        self,
        edge_mappings: list[GraphEdge],  # 要并行执行的边列表
        in_parallel_id: Optional[str] = None,  # 当前所在的并行ID
        parallel_start_node_id: Optional[str] = None,  # 当前并行的起始节点ID
        handle_exceptions: list[str] = [],  # 异常处理列表
    ) -> Generator[GraphEngineEvent | str, None, None]:
        """
        执行并行分支
        
        这个方法负责启动多个线程来并行执行多个分支，
        通过队列收集各个分支的执行结果
        
        Args:
            edge_mappings: 要并行执行的边映射列表
            in_parallel_id: 当前所在的并行执行ID
            parallel_start_node_id: 当前并行执行的起始节点ID
            handle_exceptions: 用于收集异常信息的列表
            
        Yields:
            GraphEngineEvent | str: 图引擎事件或最终节点ID字符串
            
        Raises:
            GraphRunFailedError: 当并行配置错误或分支执行失败时
        """
        # 获取并行执行的ID，所有目标节点应该属于同一个并行组
        parallel_id = self.graph.node_parallel_mapping.get(edge_mappings[0].target_node_id)
        if not parallel_id:
            # 如果没有找到并行ID，说明配置有误
            node_id = edge_mappings[0].target_node_id
            node_config = self.graph.node_id_config_mapping.get(node_id)
            if not node_config:
                raise GraphRunFailedError(
                    f"Node {node_id} related parallel not found or incorrectly connected to multiple parallel branches."
                )

            node_title = node_config.get("data", {}).get("title")
            raise GraphRunFailedError(
                f"Node {node_title} related parallel not found or incorrectly connected to multiple parallel branches."
            )

        # 获取并行配置对象
        parallel = self.graph.parallel_mapping.get(parallel_id)
        if not parallel:
            raise GraphRunFailedError(f"Parallel {parallel_id} not found.")

        # 创建队列用于线程间通信，收集各分支的执行结果
        q: queue.Queue = queue.Queue()

        # 存储所有提交的Future对象
        futures = []

        # 为每个边创建新线程来并行执行
        for edge in edge_mappings:
            # 检查目标节点是否属于当前并行组
            if (
                edge.target_node_id not in self.graph.node_parallel_mapping
                or self.graph.node_parallel_mapping.get(edge.target_node_id, "") != parallel_id
            ):
                continue

            # 提交并行节点执行任务到线程池
            future = self.thread_pool.submit(
                self._run_parallel_node,
                **{
                    "flask_app": current_app._get_current_object(),  # 传递Flask应用上下文
                    "q": q,  # 队列用于收集结果
                    "context": contextvars.copy_context(),  # 复制当前上下文变量
                    "parallel_id": parallel_id,
                    "parallel_start_node_id": edge.target_node_id,
                    "parent_parallel_id": in_parallel_id,
                    "parent_parallel_start_node_id": parallel_start_node_id,
                    "handle_exceptions": handle_exceptions,
                },
            )

            # 设置任务完成回调
            future.add_done_callback(self.thread_pool.task_done_callback)

            futures.append(future)

        # 监控并行分支的执行状态
        succeeded_count = 0  # 成功完成的分支数量
        while True:
            try:
                # 从队列中获取事件，超时时间为1秒
                event = q.get(timeout=1)
                if event is None:
                    # 收到None表示所有分支都已完成
                    break

                yield event  # 向上传递事件
                
                # 处理并行分支相关的事件
                if not isinstance(event, BaseAgentEvent) and event.parallel_id == parallel_id:
                    if isinstance(event, ParallelBranchRunSucceededEvent):
                        # 分支成功完成
                        succeeded_count += 1
                        if succeeded_count == len(futures):
                            # 所有分支都成功完成，发送结束信号
                            q.put(None)

                        continue
                    elif isinstance(event, ParallelBranchRunFailedEvent):
                        # 分支执行失败，抛出异常
                        raise GraphRunFailedError(event.error)
            except queue.Empty:
                # 队列为空，继续等待
                continue

        # 等待所有线程完成
        wait(futures)

        # 获取并行执行完成后的最终节点ID
        final_node_id = parallel.end_to_node_id
        if final_node_id:
            yield final_node_id

    def _run_parallel_node(
        self,
        flask_app: Flask,  # Flask应用实例
        context: contextvars.Context,  # 上下文变量
        q: queue.Queue,  # 用于通信的队列
        parallel_id: str,  # 并行执行ID
        parallel_start_node_id: str,  # 并行分支的起始节点ID
        parent_parallel_id: Optional[str] = None,  # 父级并行ID
        parent_parallel_start_node_id: Optional[str] = None,  # 父级并行起始节点ID
        handle_exceptions: list[str] = [],  # 异常处理列表
    ) -> None:
        """
        在新线程中运行并行节点
        
        这个方法在独立的线程中执行，负责运行单个并行分支的节点序列。
        它会保持Flask上下文，执行节点序列，并通过队列向主线程报告状态。
        
        Args:
            flask_app: Flask应用实例，用于保持应用上下文
            context: 上下文变量，包含从主线程传递过来的上下文信息
            q: 队列，用于向主线程传递事件和结果
            parallel_id: 当前并行执行的唯一标识符
            parallel_start_node_id: 当前并行分支的起始节点ID
            parent_parallel_id: 父级并行执行的ID（嵌套并行时使用）
            parent_parallel_start_node_id: 父级并行执行的起始节点ID
            handle_exceptions: 用于收集异常信息的列表
        """

        # 在Flask应用上下文中执行，确保线程中能正常访问Flask相关功能
        with preserve_flask_contexts(flask_app, context_vars=context):
            try:
                # 向队列发送并行分支开始事件
                q.put(
                    ParallelBranchRunStartedEvent(
                        parallel_id=parallel_id,
                        parallel_start_node_id=parallel_start_node_id,
                        parent_parallel_id=parent_parallel_id,
                        parent_parallel_start_node_id=parent_parallel_start_node_id,
                    )
                )

                # 执行节点序列，从指定的起始节点开始
                generator = self._run(
                    start_node_id=parallel_start_node_id,
                    in_parallel_id=parallel_id,
                    parent_parallel_id=parent_parallel_id,
                    parent_parallel_start_node_id=parent_parallel_start_node_id,
                    handle_exceptions=handle_exceptions,
                )

                # 将执行过程中产生的所有事件放入队列
                for item in generator:
                    q.put(item)

                # 分支执行完成，发送成功事件
                q.put(
                    ParallelBranchRunSucceededEvent(
                        parallel_id=parallel_id,
                        parallel_start_node_id=parallel_start_node_id,
                        parent_parallel_id=parent_parallel_id,
                        parent_parallel_start_node_id=parent_parallel_start_node_id,
                    )
                )
            except GraphRunFailedError as e:
                # 捕获图运行失败异常，发送失败事件
                q.put(
                    ParallelBranchRunFailedEvent(
                        parallel_id=parallel_id,
                        parallel_start_node_id=parallel_start_node_id,
                        parent_parallel_id=parent_parallel_id,
                        parent_parallel_start_node_id=parent_parallel_start_node_id,
                        error=e.error,
                    )
                )
            except Exception as e:
                # 捕获其他未知异常，记录日志并发送失败事件
                logger.exception("Unknown Error when generating in parallel")
                q.put(
                    ParallelBranchRunFailedEvent(
                        parallel_id=parallel_id,
                        parallel_start_node_id=parallel_start_node_id,
                        parent_parallel_id=parent_parallel_id,
                        parent_parallel_start_node_id=parent_parallel_start_node_id,
                        error=str(e),
                    )
                )

    def _run_node(
        self,
        node: BaseNode,  # 要运行的节点实例
        route_node_state: RouteNodeState,  # 节点的路由状态
        parallel_id: Optional[str] = None,  # 并行执行ID
        parallel_start_node_id: Optional[str] = None,  # 并行起始节点ID
        parent_parallel_id: Optional[str] = None,  # 父级并行ID
        parent_parallel_start_node_id: Optional[str] = None,  # 父级并行起始节点ID
        handle_exceptions: list[str] = [],  # 异常处理列表
    ) -> Generator[GraphEngineEvent, None, None]:
        """
        运行单个节点
        
        这是执行单个节点的核心方法，负责节点的完整生命周期管理，
        包括启动、重试、错误处理、状态更新等
        
        Args:
            node: 要执行的节点实例
            route_node_state: 节点的路由状态对象
            parallel_id: 当前并行执行的ID（如果在并行分支中）
            parallel_start_node_id: 当前并行分支的起始节点ID
            parent_parallel_id: 父级并行执行的ID（嵌套并行时使用）
            parent_parallel_start_node_id: 父级并行执行的起始节点ID
            handle_exceptions: 用于收集异常信息的列表
            
        Yields:
            GraphEngineEvent: 节点执行过程中产生的各种事件
        """
        # 准备Agent策略信息（仅对Agent节点）
        agent_strategy = (
            AgentNodeStrategyInit(
                name=cast(AgentNodeData, node.get_base_node_data()).agent_strategy_name,
                icon=cast(AgentNode, node).agent_strategy_icon,
            )
            if node.type_ == NodeType.AGENT
            else None
        )
        
        # 触发节点运行开始事件
        yield NodeRunStartedEvent(
            id=node.id,
            node_id=node.node_id,
            node_type=node.type_,
            node_data=node.get_base_node_data(),
            route_node_state=route_node_state,
            predecessor_node_id=node.previous_node_id,
            parallel_id=parallel_id,
            parallel_start_node_id=parallel_start_node_id,
            parent_parallel_id=parent_parallel_id,
            parent_parallel_start_node_id=parent_parallel_start_node_id,
            agent_strategy=agent_strategy,
            node_version=node.version(),
        )

        # 获取重试配置
        max_retries = node.retry_config.max_retries  # 最大重试次数
        retry_interval = node.retry_config.retry_interval_seconds  # 重试间隔（秒）
        retries = 0  # 当前重试次数
        should_continue_retry = True  # 是否应该继续重试
        
        # 重试循环：在允许的重试次数内尝试执行节点
        while should_continue_retry and retries <= max_retries:
            try:
                # 记录重试开始时间
                retry_start_at = datetime.now(UTC).replace(tzinfo=None)
                # 让出控制权给其他线程，避免阻塞
                time.sleep(0.001)
                # 执行节点，获取事件流
                event_stream = node.run()
                # 处理节点执行过程中产生的事件流
                for event in event_stream:
                    if isinstance(event, GraphEngineEvent):
                        # 如果是图引擎事件，需要添加并行执行的相关信息
                        if isinstance(event, BaseIterationEvent | BaseLoopEvent):
                            # 为迭代和循环事件添加并行信息
                            event.parallel_id = parallel_id
                            event.parallel_start_node_id = parallel_start_node_id
                            event.parent_parallel_id = parent_parallel_id
                            event.parent_parallel_start_node_id = parent_parallel_start_node_id
                        yield event  # 向上传递图引擎事件
                    else:
                        # 处理节点特定的事件
                        if isinstance(event, RunCompletedEvent):
                            # 节点运行完成事件
                            run_result = event.run_result
                            
                            # 处理失败情况
                            if run_result.status == WorkflowNodeExecutionStatus.FAILED:
                                # HTTP请求节点的特殊处理：如果达到最大重试次数且有输出，视为成功
                                if (
                                    retries == max_retries
                                    and node.type_ == NodeType.HTTP_REQUEST
                                    and run_result.outputs
                                    and not node.continue_on_error
                                ):
                                    run_result.status = WorkflowNodeExecutionStatus.SUCCEEDED
                                
                                # 检查是否需要重试
                                if node.retry and retries < max_retries:
                                    retries += 1  # 增加重试计数
                                    route_node_state.node_run_result = run_result
                                    
                                    # 发送重试事件
                                    yield NodeRunRetryEvent(
                                        id=str(uuid.uuid4()),
                                        node_id=node.node_id,
                                        node_type=node.type_,
                                        node_data=node.get_base_node_data(),
                                        route_node_state=route_node_state,
                                        predecessor_node_id=node.previous_node_id,
                                        parallel_id=parallel_id,
                                        parallel_start_node_id=parallel_start_node_id,
                                        parent_parallel_id=parent_parallel_id,
                                        parent_parallel_start_node_id=parent_parallel_start_node_id,
                                        error=run_result.error or "Unknown error",
                                        retry_index=retries,
                                        start_at=retry_start_at,
                                        node_version=node.version(),
                                    )
                                    # 等待重试间隔时间
                                    time.sleep(retry_interval)
                                    break  # 跳出当前循环，开始重试
                            
                            # 设置节点完成状态
                            route_node_state.set_finished(run_result=run_result)

                            if run_result.status == WorkflowNodeExecutionStatus.FAILED:
                                if node.continue_on_error:
                                    # if run failed, handle error
                                    run_result = self._handle_continue_on_error(
                                        node,
                                        event.run_result,
                                        self.graph_runtime_state.variable_pool,
                                        handle_exceptions=handle_exceptions,
                                    )
                                    route_node_state.node_run_result = run_result
                                    route_node_state.status = RouteNodeState.Status.EXCEPTION
                                    if run_result.outputs:
                                        for variable_key, variable_value in run_result.outputs.items():
                                            # append variables to variable pool recursively
                                            self._append_variables_recursively(
                                                node_id=node.node_id,
                                                variable_key_list=[variable_key],
                                                variable_value=variable_value,
                                            )
                                    yield NodeRunExceptionEvent(
                                        error=run_result.error or "System Error",
                                        id=node.id,
                                        node_id=node.node_id,
                                        node_type=node.type_,
                                        node_data=node.get_base_node_data(),
                                        route_node_state=route_node_state,
                                        parallel_id=parallel_id,
                                        parallel_start_node_id=parallel_start_node_id,
                                        parent_parallel_id=parent_parallel_id,
                                        parent_parallel_start_node_id=parent_parallel_start_node_id,
                                        node_version=node.version(),
                                    )
                                    should_continue_retry = False
                                else:
                                    yield NodeRunFailedEvent(
                                        error=route_node_state.failed_reason or "Unknown error.",
                                        id=node.id,
                                        node_id=node.node_id,
                                        node_type=node.type_,
                                        node_data=node.get_base_node_data(),
                                        route_node_state=route_node_state,
                                        parallel_id=parallel_id,
                                        parallel_start_node_id=parallel_start_node_id,
                                        parent_parallel_id=parent_parallel_id,
                                        parent_parallel_start_node_id=parent_parallel_start_node_id,
                                        node_version=node.version(),
                                    )
                                should_continue_retry = False
                            elif run_result.status == WorkflowNodeExecutionStatus.SUCCEEDED:
                                if (
                                    node.continue_on_error
                                    and self.graph.edge_mapping.get(node.node_id)
                                    and node.error_strategy is ErrorStrategy.FAIL_BRANCH
                                ):
                                    run_result.edge_source_handle = FailBranchSourceHandle.SUCCESS
                                if run_result.metadata and run_result.metadata.get(
                                    WorkflowNodeExecutionMetadataKey.TOTAL_TOKENS
                                ):
                                    # plus state total_tokens
                                    self.graph_runtime_state.total_tokens += int(
                                        run_result.metadata.get(WorkflowNodeExecutionMetadataKey.TOTAL_TOKENS)  # type: ignore[arg-type]
                                    )

                                if run_result.llm_usage:
                                    # use the latest usage
                                    self.graph_runtime_state.llm_usage += run_result.llm_usage

                                # append node output variables to variable pool
                                if run_result.outputs:
                                    for variable_key, variable_value in run_result.outputs.items():
                                        # append variables to variable pool recursively
                                        self._append_variables_recursively(
                                            node_id=node.node_id,
                                            variable_key_list=[variable_key],
                                            variable_value=variable_value,
                                        )

                                # When setting metadata, convert to dict first
                                if not run_result.metadata:
                                    run_result.metadata = {}

                                if parallel_id and parallel_start_node_id:
                                    metadata_dict = dict(run_result.metadata)
                                    metadata_dict[WorkflowNodeExecutionMetadataKey.PARALLEL_ID] = parallel_id
                                    metadata_dict[WorkflowNodeExecutionMetadataKey.PARALLEL_START_NODE_ID] = (
                                        parallel_start_node_id
                                    )
                                    if parent_parallel_id and parent_parallel_start_node_id:
                                        metadata_dict[WorkflowNodeExecutionMetadataKey.PARENT_PARALLEL_ID] = (
                                            parent_parallel_id
                                        )
                                        metadata_dict[
                                            WorkflowNodeExecutionMetadataKey.PARENT_PARALLEL_START_NODE_ID
                                        ] = parent_parallel_start_node_id
                                    run_result.metadata = metadata_dict

                                yield NodeRunSucceededEvent(
                                    id=node.id,
                                    node_id=node.node_id,
                                    node_type=node.type_,
                                    node_data=node.get_base_node_data(),
                                    route_node_state=route_node_state,
                                    parallel_id=parallel_id,
                                    parallel_start_node_id=parallel_start_node_id,
                                    parent_parallel_id=parent_parallel_id,
                                    parent_parallel_start_node_id=parent_parallel_start_node_id,
                                    node_version=node.version(),
                                )
                                should_continue_retry = False

                            break
                        elif isinstance(event, RunStreamChunkEvent):
                            yield NodeRunStreamChunkEvent(
                                id=node.id,
                                node_id=node.node_id,
                                node_type=node.type_,
                                node_data=node.get_base_node_data(),
                                chunk_content=event.chunk_content,
                                from_variable_selector=event.from_variable_selector,
                                route_node_state=route_node_state,
                                parallel_id=parallel_id,
                                parallel_start_node_id=parallel_start_node_id,
                                parent_parallel_id=parent_parallel_id,
                                parent_parallel_start_node_id=parent_parallel_start_node_id,
                                node_version=node.version(),
                            )
                        elif isinstance(event, RunRetrieverResourceEvent):
                            yield NodeRunRetrieverResourceEvent(
                                id=node.id,
                                node_id=node.node_id,
                                node_type=node.type_,
                                node_data=node.get_base_node_data(),
                                retriever_resources=event.retriever_resources,
                                context=event.context,
                                route_node_state=route_node_state,
                                parallel_id=parallel_id,
                                parallel_start_node_id=parallel_start_node_id,
                                parent_parallel_id=parent_parallel_id,
                                parent_parallel_start_node_id=parent_parallel_start_node_id,
                                node_version=node.version(),
                            )
            except GenerateTaskStoppedError:
                # trigger node run failed event
                route_node_state.status = RouteNodeState.Status.FAILED
                route_node_state.failed_reason = "Workflow stopped."
                yield NodeRunFailedEvent(
                    error="Workflow stopped.",
                    id=node.id,
                    node_id=node.node_id,
                    node_type=node.type_,
                    node_data=node.get_base_node_data(),
                    route_node_state=route_node_state,
                    parallel_id=parallel_id,
                    parallel_start_node_id=parallel_start_node_id,
                    parent_parallel_id=parent_parallel_id,
                    parent_parallel_start_node_id=parent_parallel_start_node_id,
                    node_version=node.version(),
                )
                return
            except Exception as e:
                logger.exception(f"Node {node.title} run failed")
                raise e

    def _append_variables_recursively(self, node_id: str, variable_key_list: list[str], variable_value: VariableValue):
        """
        递归添加变量到变量池
        
        将节点的输出变量递归地添加到图的运行时变量池中，
        支持嵌套的变量结构
        
        Args:
            node_id: 节点ID
            variable_key_list: 变量键列表，支持嵌套路径
            variable_value: 变量值
        """
        variable_utils.append_variables_recursively(
            self.graph_runtime_state.variable_pool,
            node_id,
            variable_key_list,
            variable_value,
        )

    def _is_timed_out(self, start_at: float, max_execution_time: int) -> bool:
        """
        检查是否超时
        
        通过比较当前时间与开始时间的差值来判断是否超过了最大执行时间限制
        
        Args:
            start_at: 开始时间（使用time.perf_counter()获取）
            max_execution_time: 最大执行时间限制（秒）
            
        Returns:
            bool: 如果超时返回True，否则返回False
        """
        return time.perf_counter() - start_at > max_execution_time

    def create_copy(self):
        """
        创建图引擎的副本
        
        创建一个新的图引擎实例，具有独立的变量池和重置的token计数，
        用于需要隔离执行环境的场景
        
        Returns:
            GraphEngine: 新的图引擎实例，包含独立的变量池和重置的token计数
        """
        new_instance = copy(self)  # 浅拷贝图引擎实例
        new_instance.graph_runtime_state = copy(self.graph_runtime_state)  # 浅拷贝运行时状态
        new_instance.graph_runtime_state.variable_pool = deepcopy(self.graph_runtime_state.variable_pool)  # 深拷贝变量池
        new_instance.graph_runtime_state.total_tokens = 0  # 重置token计数
        return new_instance

    def _handle_continue_on_error(
        self,
        node: BaseNode,  # 出错的节点
        error_result: NodeRunResult,  # 错误结果
        variable_pool: VariablePool,  # 变量池
        handle_exceptions: list[str] = [],  # 异常处理列表
    ) -> NodeRunResult:
        """
        处理"继续执行"错误策略
        
        当节点设置了继续执行错误策略时，这个方法会根据具体的错误策略
        来处理错误，并生成相应的节点运行结果
        
        Args:
            node: 出错的节点实例
            error_result: 节点运行失败的结果
            variable_pool: 图的变量池，用于存储错误信息
            handle_exceptions: 用于收集异常信息的列表
            
        Returns:
            NodeRunResult: 处理后的节点运行结果
        """
        # 将错误信息添加到变量池中，供后续节点使用
        variable_pool.add([node.node_id, "error_message"], error_result.error)
        variable_pool.add([node.node_id, "error_type"], error_result.error_type)
        
        # 将错误信息添加到异常处理列表
        handle_exceptions.append(error_result.error or "")
        
        # 构建节点错误结果的基础参数
        node_error_args: dict[str, Any] = {
            "status": WorkflowNodeExecutionStatus.EXCEPTION,  # 状态设为异常
            "error": error_result.error,
            "inputs": error_result.inputs,
            "metadata": {
                WorkflowNodeExecutionMetadataKey.ERROR_STRATEGY: node.error_strategy,
            },
        }

        # 根据错误策略处理错误
        if node.error_strategy is ErrorStrategy.DEFAULT_VALUE:
            # 默认值策略：返回节点配置的默认值
            return NodeRunResult(
                **node_error_args,
                outputs={
                    **node.default_value_dict,  # 使用节点的默认值字典
                    "error_message": error_result.error,
                    "error_type": error_result.error_type,
                },
            )
        elif node.error_strategy is ErrorStrategy.FAIL_BRANCH:
            # 失败分支策略：设置失败分支标识，继续执行失败分支
            if self.graph.edge_mapping.get(node.node_id):
                node_error_args["edge_source_handle"] = FailBranchSourceHandle.FAILED
            return NodeRunResult(
                **node_error_args,
                outputs={
                    "error_message": error_result.error,
                    "error_type": error_result.error_type,
                },
            )
        # 其他情况直接返回原错误结果
        return error_result


class GraphRunFailedError(Exception):
    """
    图运行失败异常
    
    当工作流图执行过程中遇到不可恢复的错误时抛出此异常，
    例如超时、超过最大步数、节点配置错误等
    """
    def __init__(self, error: str):
        """
        初始化图运行失败异常
        
        Args:
            error: 错误描述信息
        """
        self.error = error
