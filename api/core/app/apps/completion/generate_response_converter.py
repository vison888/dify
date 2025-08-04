from collections.abc import Generator
from typing import cast

from core.app.apps.base_app_generate_response_converter import AppGenerateResponseConverter
from core.app.entities.task_entities import (
    AppStreamResponse,
    CompletionAppBlockingResponse,
    CompletionAppStreamResponse,
    ErrorStreamResponse,
    MessageEndStreamResponse,
    PingStreamResponse,
)


class CompletionAppGenerateResponseConverter(AppGenerateResponseConverter):
    """
    补全应用生成响应转换器
    
    专门用于处理补全应用响应的转换器。继承自AppGenerateResponseConverter，
    针对补全应用的特殊响应格式进行定制化处理。
    
    主要功能：
    1. 将内部的响应对象转换为API响应格式
    2. 处理流式和阻塞式两种响应模式
    3. 支持完整响应和简化响应两种格式
    4. 处理补全应用特有的响应字段（如message_id、answer等）
    5. 处理元数据的过滤和格式化
    """
    
    # 指定阻塞响应的类型
    _blocking_response_type = CompletionAppBlockingResponse

    @classmethod
    def convert_blocking_full_response(cls, blocking_response: CompletionAppBlockingResponse) -> dict:  # type: ignore[override]
        """
        转换阻塞式完整响应
        
        将CompletionAppBlockingResponse对象转换为字典格式的完整响应，
        包含补全应用执行的所有详细信息，如答案内容、元数据等。
        
        Args:
            blocking_response: 补全应用阻塞响应对象
            
        Returns:
            dict: 包含完整响应信息的字典
        """
        response = {
            "event": "message",                                     # 事件类型
            "task_id": blocking_response.task_id,                   # 任务ID
            "id": blocking_response.data.id,                        # 响应数据ID
            "message_id": blocking_response.data.message_id,        # 消息ID
            "mode": blocking_response.data.mode,                    # 应用模式
            "answer": blocking_response.data.answer,                # 生成的答案
            "metadata": blocking_response.data.metadata,            # 元数据信息
            "created_at": blocking_response.data.created_at,        # 创建时间
        }

        return response

    @classmethod
    def convert_blocking_simple_response(cls, blocking_response: CompletionAppBlockingResponse) -> dict:  # type: ignore[override]
        """
        转换阻塞式简化响应
        
        将CompletionAppBlockingResponse对象转换为字典格式的简化响应，
        对元数据进行简化处理，减少传输的数据量。
        
        Args:
            blocking_response: 补全应用阻塞响应对象
            
        Returns:
            dict: 包含简化响应信息的字典
        """
        # 先获取完整响应
        response = cls.convert_blocking_full_response(blocking_response)

        # 简化元数据
        metadata = response.get("metadata", {})
        response["metadata"] = cls._get_simple_metadata(metadata)

        return response

    @classmethod
    def convert_stream_full_response(
        cls, stream_response: Generator[AppStreamResponse, None, None]
    ) -> Generator[dict | str, None, None]:
        """
        转换流式完整响应
        
        将补全应用的流式响应生成器转换为字典格式的响应流。
        包含补全应用执行过程中的所有详细事件信息。
        
        Args:
            stream_response: 应用流响应生成器
            
        Yields:
            dict | str: 响应块字典或ping字符串
        """
        for chunk in stream_response:
            # 转换为补全应用流响应类型
            chunk = cast(CompletionAppStreamResponse, chunk)
            sub_stream_response = chunk.stream_response

            # 处理ping响应，用于保持连接活跃
            if isinstance(sub_stream_response, PingStreamResponse):
                yield "ping"
                continue

            # 构建基础响应块，包含事件类型、消息ID和创建时间
            response_chunk = {
                "event": sub_stream_response.event.value,
                "message_id": chunk.message_id,
                "created_at": chunk.created_at,
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
        
        将补全应用的流式响应生成器转换为简化格式的响应流。
        对消息结束事件的元数据进行简化处理，减少传输的数据量。
        
        Args:
            stream_response: 应用流响应生成器
            
        Yields:
            dict | str: 简化的响应块字典或ping字符串
        """
        for chunk in stream_response:
            # 转换为补全应用流响应类型
            chunk = cast(CompletionAppStreamResponse, chunk)
            sub_stream_response = chunk.stream_response

            # 处理ping响应，用于保持连接活跃
            if isinstance(sub_stream_response, PingStreamResponse):
                yield "ping"
                continue

            # 构建基础响应块，包含事件类型、消息ID和创建时间
            response_chunk = {
                "event": sub_stream_response.event.value,
                "message_id": chunk.message_id,
                "created_at": chunk.created_at,
            }

            # 对消息结束事件进行特殊处理，简化元数据
            if isinstance(sub_stream_response, MessageEndStreamResponse):
                sub_stream_response_dict = sub_stream_response.to_dict()
                metadata = sub_stream_response_dict.get("metadata", {})
                sub_stream_response_dict["metadata"] = cls._get_simple_metadata(metadata)
                response_chunk.update(sub_stream_response_dict)
            # 处理错误响应
            elif isinstance(sub_stream_response, ErrorStreamResponse):
                data = cls._error_to_stream_response(sub_stream_response.err)
                response_chunk.update(data)
            else:
                # 处理其他类型的响应，转换为字典格式
                response_chunk.update(sub_stream_response.to_dict())

            yield response_chunk
