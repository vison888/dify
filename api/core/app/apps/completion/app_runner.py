import logging
from typing import cast

from core.app.apps.base_app_queue_manager import AppQueueManager
from core.app.apps.base_app_runner import AppRunner
from core.app.apps.completion.app_config_manager import CompletionAppConfig
from core.app.entities.app_invoke_entities import (
    CompletionAppGenerateEntity,
)
from core.callback_handler.index_tool_callback_handler import DatasetIndexToolCallbackHandler
from core.model_manager import ModelInstance
from core.model_runtime.entities.message_entities import ImagePromptMessageContent
from core.moderation.base import ModerationError
from core.rag.retrieval.dataset_retrieval import DatasetRetrieval
from extensions.ext_database import db
from models.model import App, Message

logger = logging.getLogger(__name__)


class CompletionAppRunner(AppRunner):
    """
    补全应用运行器
    
    负责执行补全类型应用的核心运行逻辑。继承自AppRunner，
    专门处理基于提示模板的文本补全应用执行流程。
    
    主要职责：
    1. 组织提示消息（包括模板、输入、查询、文件等）
    2. 执行内容审核和敏感词过滤
    3. 从外部数据工具填充输入变量
    4. 执行数据集检索获取上下文
    5. 调用LLM模型进行文本生成
    6. 处理生成结果并返回响应
    """

    def run(
        self, application_generate_entity: CompletionAppGenerateEntity, queue_manager: AppQueueManager, message: Message
    ) -> None:
        """
        运行补全应用
        
        执行补全应用的完整流程，包括输入处理、内容审核、数据检索、
        模型调用等步骤，最终生成文本补全结果。
        
        Args:
            application_generate_entity: 补全应用生成实体，包含执行所需的配置和参数
            queue_manager: 应用队列管理器，用于事件通信和状态管理
            message: 消息对象，用于记录执行过程和结果
        """
        # 获取应用配置并转换为补全应用配置类型
        app_config = application_generate_entity.app_config
        app_config = cast(CompletionAppConfig, app_config)

        # 查询应用记录
        app_record = db.session.query(App).filter(App.id == app_config.app_id).first()
        if not app_record:
            raise ValueError("App not found")

        # 提取执行参数
        inputs = application_generate_entity.inputs          # 用户输入变量
        query = application_generate_entity.query            # 查询内容
        files = application_generate_entity.files            # 上传的文件

        # 配置图像详细级别
        # 用于控制视觉模型处理图像的详细程度
        image_detail_config = (
            application_generate_entity.file_upload_config.image_config.detail
            if (
                application_generate_entity.file_upload_config
                and application_generate_entity.file_upload_config.image_config
            )
            else None
        )
        # 默认使用低详细级别以节省token消耗
        image_detail_config = image_detail_config or ImagePromptMessageContent.DETAIL.LOW

        # 第一阶段：组织所有输入和模板为提示消息
        # 包括：提示模板、输入变量、查询内容（可选）、文件（可选）
        prompt_messages, stop = self.organize_prompt_messages(
            app_record=app_record,
            model_config=application_generate_entity.model_conf,
            prompt_template_entity=app_config.prompt_template,
            inputs=inputs,
            files=files,
            query=query,
            image_detail_config=image_detail_config,
        )

        # 第二阶段：内容审核
        try:
            # 处理敏感词过滤和内容审核
            # 对用户输入和查询内容进行敏感词检测和内容安全审核
            _, inputs, query = self.moderation_for_inputs(
                app_id=app_record.id,
                tenant_id=app_config.tenant_id,
                app_generate_entity=application_generate_entity,
                inputs=inputs,
                query=query or "",
                message_id=message.id,
            )
        except ModerationError as e:
            # 如果内容审核失败，直接输出错误信息并终止执行
            self.direct_output(
                queue_manager=queue_manager,
                app_generate_entity=application_generate_entity,
                prompt_messages=prompt_messages,
                text=str(e),
                stream=application_generate_entity.stream,
            )
            return

        # 第三阶段：从外部数据工具填充输入变量
        # 如果配置了外部数据变量，从外部数据源获取数据并填充到输入变量中
        external_data_tools = app_config.external_data_variables
        if external_data_tools:
            inputs = self.fill_in_inputs_from_external_data_tools(
                tenant_id=app_record.tenant_id,
                app_id=app_record.id,
                external_data_tools=external_data_tools,
                inputs=inputs,
                query=query,
            )

        # 第四阶段：从数据集获取上下文
        # 如果配置了数据集，执行检索获取相关上下文信息
        context = None
        if app_config.dataset and app_config.dataset.dataset_ids:
            # 创建数据集索引工具回调处理器
            # 用于处理数据集检索过程中的回调事件
            hit_callback = DatasetIndexToolCallbackHandler(
                queue_manager,
                app_record.id,
                message.id,
                application_generate_entity.user_id,
                application_generate_entity.invoke_from,
            )

            # 获取数据集配置
            dataset_config = app_config.dataset
            # 如果配置了查询变量，从输入变量中获取查询内容
            if dataset_config and dataset_config.retrieve_config.query_variable:
                query = inputs.get(dataset_config.retrieve_config.query_variable, "")

            # 执行数据集检索
            dataset_retrieval = DatasetRetrieval(application_generate_entity)
            context = dataset_retrieval.retrieve(
                app_id=app_record.id,
                user_id=application_generate_entity.user_id,
                tenant_id=app_record.tenant_id,
                model_config=application_generate_entity.model_conf,
                config=dataset_config,
                query=query or "",
                invoke_from=application_generate_entity.invoke_from,
                show_retrieve_source=app_config.additional_features.show_retrieve_source,
                hit_callback=hit_callback,
                message_id=message.id,
                inputs=inputs,
            )

        # 第五阶段：重新组织所有输入和模板为提示消息
        # 包括：提示模板、输入变量、查询内容（可选）、文件（可选）
        #       记忆（可选）、外部数据、数据集上下文（可选）
        prompt_messages, stop = self.organize_prompt_messages(
            app_record=app_record,
            model_config=application_generate_entity.model_conf,
            prompt_template_entity=app_config.prompt_template,
            inputs=inputs,
            files=files,
            query=query,
            context=context,                    # 加入检索到的数据集上下文
            image_detail_config=image_detail_config,
        )

        # 第六阶段：检查托管平台内容审核
        # 对最终的提示消息进行平台级别的内容审核
        hosting_moderation_result = self.check_hosting_moderation(
            application_generate_entity=application_generate_entity,
            queue_manager=queue_manager,
            prompt_messages=prompt_messages,
        )

        # 如果托管审核失败，终止执行
        if hosting_moderation_result:
            return

        # 第七阶段：重新计算最大token数
        # 如果提示token数 + 最大生成token数超过模型限制，重新调整最大token数
        self.recalc_llm_max_tokens(model_config=application_generate_entity.model_conf, prompt_messages=prompt_messages)

        # 第八阶段：调用LLM模型
        # 创建模型实例并执行文本生成
        model_instance = ModelInstance(
            provider_model_bundle=application_generate_entity.model_conf.provider_model_bundle,
            model=application_generate_entity.model_conf.model,
        )

        # 关闭数据库会话，避免长时间持有连接
        db.session.close()

        # 调用模型生成文本
        invoke_result = model_instance.invoke_llm(
            prompt_messages=prompt_messages,                        # 组织好的提示消息
            model_parameters=application_generate_entity.model_conf.parameters,  # 模型参数
            stop=stop,                                             # 停止词
            stream=application_generate_entity.stream,             # 是否流式输出
            user=application_generate_entity.user_id,              # 用户ID
        )

        # 第九阶段：处理调用结果
        # 将模型生成结果转换为应用响应并发送给客户端
        self._handle_invoke_result(
            invoke_result=invoke_result, queue_manager=queue_manager, stream=application_generate_entity.stream
        )
