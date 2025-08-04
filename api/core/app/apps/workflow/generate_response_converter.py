from collections.abc import Generator
from typing import cast

from core.app.apps.base_app_generate_response_converter import AppGenerateResponseConverter
from core.app.entities.task_entities import (
    AppStreamResponse,
    ErrorStreamResponse,
    NodeFinishStreamResponse,
    NodeStartStreamResponse,
    PingStreamResponse,
    WorkflowAppBlockingResponse,
    WorkflowAppStreamResponse,
)


class WorkflowAppGenerateResponseConverter(AppGenerateResponseConverter):
    """
    工作流应用生成响应转换器
    
    专门用于处理工作流应用响应的转换器。继承自AppGenerateResponseConverter，
    针对工作流应用的特殊响应格式进行定制化处理。
    
    主要功能：
    1. 将内部的响应对象转换为API响应格式
    2. 处理流式和阻塞式两种响应模式
    3. 支持完整响应和简化响应两种格式
    4. 处理工作流特有的响应字段（如workflow_run_id）
    """
    
    # 指定阻塞响应的类型
    _blocking_response_type = WorkflowAppBlockingResponse

    @classmethod
    def convert_blocking_full_response(cls, blocking_response: WorkflowAppBlockingResponse) -> dict:  # type: ignore[override]
        """
        转换阻塞式完整响应
        
        将WorkflowAppBlockingResponse对象转换为字典格式的完整响应，
        包含工作流执行的所有详细信息。
        
        Args:
            blocking_response: 工作流应用阻塞响应对象
            
        Returns:
            dict: 包含完整响应信息的字典
        """
        return dict(blocking_response.to_dict())

    @classmethod
    def convert_blocking_simple_response(cls, blocking_response: WorkflowAppBlockingResponse) -> dict:  # type: ignore[override]
        """
        转换阻塞式简化响应
        
        将WorkflowAppBlockingResponse对象转换为字典格式的简化响应。
        对于工作流应用，简化响应与完整响应相同。
        
        Args:
            blocking_response: 工作流应用阻塞响应对象
            
        Returns:
            dict: 包含简化响应信息的字典
        """
        return cls.convert_blocking_full_response(blocking_response)

    @classmethod
    def convert_stream_full_response(
        cls, stream_response: Generator[AppStreamResponse, None, None]
    ) -> Generator[dict | str, None, None]:
        """
        转换流式完整响应
        
        将工作流应用的流式响应生成器转换为字典格式的响应流。
        包含工作流执行过程中的所有详细事件信息。
        
        Args:
            stream_response: 应用流响应生成器
            
        Yields:
            dict | str: 响应块字典或ping字符串
        """
        for chunk in stream_response:
            # 转换为工作流应用流响应类型
            chunk = cast(WorkflowAppStreamResponse, chunk)
            sub_stream_response = chunk.stream_response

            # 处理ping响应，用于保持连接活跃
            if isinstance(sub_stream_response, PingStreamResponse):
                yield "ping"
                continue

            # 构建基础响应块，包含事件类型和工作流运行ID
            response_chunk = {
                "event": sub_stream_response.event.value,
                "workflow_run_id": chunk.workflow_run_id,
            }

            # 处理错误响应
            if isinstance(sub_stream_response, ErrorStreamResponse):
                data = cls._error_to_stream_response(sub_stream_response.err)
                response_chunk.update(data)
            else:
                # 处理其他类型的响应，转换为字典格式
                response_chunk.update(sub_stream_response.to_dict())
            yield response_chunk

    @classmethod
    def convert_stream_simple_response(
        cls, stream_response: Generator[AppStreamResponse, None, None]
    ) -> Generator[dict | str, None, None]:
        """
        转换流式简化响应
        
        将工作流应用的流式响应生成器转换为简化格式的响应流。
        对节点开始和结束事件进行简化处理，减少传输的数据量。
        
        Args:
            stream_response: 应用流响应生成器
            
        Yields:
            dict | str: 简化的响应块字典或ping字符串
        """
        for chunk in stream_response:
            # 转换为工作流应用流响应类型
            chunk = cast(WorkflowAppStreamResponse, chunk)
            sub_stream_response = chunk.stream_response

            # 处理ping响应，用于保持连接活跃
            if isinstance(sub_stream_response, PingStreamResponse):
                yield "ping"
                continue

            # 构建基础响应块，包含事件类型和工作流运行ID
            response_chunk = {
                "event": sub_stream_response.event.value,
                "workflow_run_id": chunk.workflow_run_id,
            }

            # 处理错误响应
            if isinstance(sub_stream_response, ErrorStreamResponse):
                data = cls._error_to_stream_response(sub_stream_response.err)
                response_chunk.update(data)
            # 对节点开始和结束事件进行简化处理
            elif isinstance(sub_stream_response, NodeStartStreamResponse | NodeFinishStreamResponse):
                response_chunk.update(sub_stream_response.to_ignore_detail_dict())
            else:
                # 处理其他类型的响应，转换为字典格式
                response_chunk.update(sub_stream_response.to_dict())
            yield response_chunk
