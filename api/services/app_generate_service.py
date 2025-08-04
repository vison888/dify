from collections.abc import Generator, Mapping
from typing import Any, Union

from openai._exceptions import RateLimitError

from configs import dify_config
from core.app.apps.advanced_chat.app_generator import AdvancedChatAppGenerator
from core.app.apps.agent_chat.app_generator import AgentChatAppGenerator
from core.app.apps.chat.app_generator import ChatAppGenerator
from core.app.apps.completion.app_generator import CompletionAppGenerator
from core.app.apps.workflow.app_generator import WorkflowAppGenerator
from core.app.entities.app_invoke_entities import InvokeFrom
from core.app.features.rate_limiting import RateLimit
from libs.helper import RateLimiter
from models.model import Account, App, AppMode, EndUser
from models.workflow import Workflow
from services.billing_service import BillingService
from services.errors.llm import InvokeRateLimitError
from services.workflow_service import WorkflowService


class AppGenerateService:
    """
    应用生成服务
    
    负责管理各种类型应用的生成流程，包括工作流、聊天、完成等。
    提供统一的限流、路由和生命周期管理。
    """
    
    # 系统级别的日限流器，用于控制免费用户的每日请求次数
    system_rate_limiter = RateLimiter("app_daily_rate_limiter", dify_config.APP_DAILY_RATE_LIMIT, 86400)

    @classmethod
    def generate(
        cls,
        app_model: App,
        user: Union[Account, EndUser],
        args: Mapping[str, Any],
        invoke_from: InvokeFrom,
        streaming: bool = True,
    ):
        """
        应用内容生成的核心入口方法
        
        负责应用生成的整体流程控制，包括：
        1. 系统级和应用级限流控制
        2. 根据应用模式路由到对应的生成器
        3. 流式响应的生命周期管理
        4. 异常处理和资源清理
        
        Args:
            app_model: 应用模型，包含应用的配置信息
            user: 用户对象，可以是Account或EndUser
            args: 请求参数，包含用户输入、文件等
            invoke_from: 调用来源，用于权限控制和行为差异化
            streaming: 是否启用流式响应
            
        Returns:
            生成器对象，产生流式响应或最终结果
            
        Raises:
            InvokeRateLimitError: 当触发限流时
            ValueError: 当应用模式无效时
        """
        
        # 第一步：系统级限流检查
        # 仅对启用计费且为沙箱计划的租户进行限流
        if dify_config.BILLING_ENABLED:
            # 检查是否为免费计划（沙箱计划）
            limit_info = BillingService.get_info(app_model.tenant_id)
            if limit_info["subscription"]["plan"] == "sandbox":
                # 检查是否触发日限流
                if cls.system_rate_limiter.is_rate_limited(app_model.tenant_id):
                    raise InvokeRateLimitError(
                        "Rate limit exceeded, please upgrade your plan "
                        f"or your RPD was {dify_config.APP_DAILY_RATE_LIMIT} requests/day"
                    )
                # 增加限流计数
                cls.system_rate_limiter.increment_rate_limit(app_model.tenant_id)

        # 第二步：应用级限流控制
        # 控制应用的并发执行数量，防止资源耗尽
        max_active_request = AppGenerateService._get_max_active_requests(app_model)
        rate_limit = RateLimit(app_model.id, max_active_request)
        request_id = RateLimit.gen_request_key()
        
        try:
            # 进入限流队列，获取执行权限
            request_id = rate_limit.enter(request_id)
            
            # 第三步：根据应用模式路由到对应的生成器
            if app_model.mode == AppMode.COMPLETION.value:
                # 文本完成模式
                return rate_limit.generate(
                    CompletionAppGenerator.convert_to_event_stream(
                        CompletionAppGenerator().generate(
                            app_model=app_model, user=user, args=args, invoke_from=invoke_from, streaming=streaming
                        ),
                    ),
                    request_id=request_id,
                )
                
            elif app_model.mode == AppMode.AGENT_CHAT.value or app_model.is_agent:
                # 智能体聊天模式
                return rate_limit.generate(
                    AgentChatAppGenerator.convert_to_event_stream(
                        AgentChatAppGenerator().generate(
                            app_model=app_model, user=user, args=args, invoke_from=invoke_from, streaming=streaming
                        ),
                    ),
                    request_id,
                )
                
            elif app_model.mode == AppMode.CHAT.value:
                # 基础聊天模式
                return rate_limit.generate(
                    ChatAppGenerator.convert_to_event_stream(
                        ChatAppGenerator().generate(
                            app_model=app_model, user=user, args=args, invoke_from=invoke_from, streaming=streaming
                        ),
                    ),
                    request_id=request_id,
                )
                
            elif app_model.mode == AppMode.ADVANCED_CHAT.value:
                # 高级聊天模式（基于工作流）
                workflow = cls._get_workflow(app_model, invoke_from)
                return rate_limit.generate(
                    AdvancedChatAppGenerator.convert_to_event_stream(
                        AdvancedChatAppGenerator().generate(
                            app_model=app_model,
                            workflow=workflow,
                            user=user,
                            args=args,
                            invoke_from=invoke_from,
                            streaming=streaming,
                        ),
                    ),
                    request_id=request_id,
                )
                
            elif app_model.mode == AppMode.WORKFLOW.value:
                # 工作流模式 - 这是我们重点关注的模式
                workflow = cls._get_workflow(app_model, invoke_from)
                return rate_limit.generate(
                    # 将工作流生成器的输出转换为事件流
                    WorkflowAppGenerator.convert_to_event_stream(
                        WorkflowAppGenerator().generate(
                            app_model=app_model,
                            workflow=workflow,
                            user=user,
                            args=args,
                            invoke_from=invoke_from,
                            streaming=streaming,
                            call_depth=0,                    # 调用深度，用于防止无限递归
                            workflow_thread_pool_id=None,    # 线程池ID，用于并发控制
                        ),
                    ),
                    request_id,
                )
            else:
                raise ValueError(f"Invalid app mode {app_model.mode}")
                
        except RateLimitError as e:
            # 处理OpenAI等服务的限流错误
            raise InvokeRateLimitError(str(e))
        except Exception:
            # 发生异常时释放限流资源
            rate_limit.exit(request_id)
            raise
        finally:
            # 非流式响应时立即释放限流资源
            # 流式响应会在流结束时自动释放
            if not streaming:
                rate_limit.exit(request_id)

    @staticmethod
    def _get_max_active_requests(app: App) -> int:
        """
        Get the maximum number of active requests allowed for an app.

        Returns the smaller value between app's custom limit and global config limit.
        A value of 0 means infinite (no limit).

        Args:
            app: The App model instance

        Returns:
            The maximum number of active requests allowed
        """
        app_limit = app.max_active_requests or 0
        config_limit = dify_config.APP_MAX_ACTIVE_REQUESTS

        # Filter out infinite (0) values and return the minimum, or 0 if both are infinite
        limits = [limit for limit in [app_limit, config_limit] if limit > 0]
        return min(limits) if limits else 0

    @classmethod
    def generate_single_iteration(cls, app_model: App, user: Account, node_id: str, args: Any, streaming: bool = True):
        if app_model.mode == AppMode.ADVANCED_CHAT.value:
            workflow = cls._get_workflow(app_model, InvokeFrom.DEBUGGER)
            return AdvancedChatAppGenerator.convert_to_event_stream(
                AdvancedChatAppGenerator().single_iteration_generate(
                    app_model=app_model, workflow=workflow, node_id=node_id, user=user, args=args, streaming=streaming
                )
            )
        elif app_model.mode == AppMode.WORKFLOW.value:
            workflow = cls._get_workflow(app_model, InvokeFrom.DEBUGGER)
            return AdvancedChatAppGenerator.convert_to_event_stream(
                WorkflowAppGenerator().single_iteration_generate(
                    app_model=app_model, workflow=workflow, node_id=node_id, user=user, args=args, streaming=streaming
                )
            )
        else:
            raise ValueError(f"Invalid app mode {app_model.mode}")

    @classmethod
    def generate_single_loop(cls, app_model: App, user: Account, node_id: str, args: Any, streaming: bool = True):
        if app_model.mode == AppMode.ADVANCED_CHAT.value:
            workflow = cls._get_workflow(app_model, InvokeFrom.DEBUGGER)
            return AdvancedChatAppGenerator.convert_to_event_stream(
                AdvancedChatAppGenerator().single_loop_generate(
                    app_model=app_model, workflow=workflow, node_id=node_id, user=user, args=args, streaming=streaming
                )
            )
        elif app_model.mode == AppMode.WORKFLOW.value:
            workflow = cls._get_workflow(app_model, InvokeFrom.DEBUGGER)
            return AdvancedChatAppGenerator.convert_to_event_stream(
                WorkflowAppGenerator().single_loop_generate(
                    app_model=app_model, workflow=workflow, node_id=node_id, user=user, args=args, streaming=streaming
                )
            )
        else:
            raise ValueError(f"Invalid app mode {app_model.mode}")

    @classmethod
    def generate_more_like_this(
        cls,
        app_model: App,
        user: Union[Account, EndUser],
        message_id: str,
        invoke_from: InvokeFrom,
        streaming: bool = True,
    ) -> Union[Mapping, Generator]:
        """
        Generate more like this
        :param app_model: app model
        :param user: user
        :param message_id: message id
        :param invoke_from: invoke from
        :param streaming: streaming
        :return:
        """
        return CompletionAppGenerator().generate_more_like_this(
            app_model=app_model, message_id=message_id, user=user, invoke_from=invoke_from, stream=streaming
        )

    @classmethod
    def _get_workflow(cls, app_model: App, invoke_from: InvokeFrom) -> Workflow:
        """
        Get workflow
        :param app_model: app model
        :param invoke_from: invoke from
        :return:
        """
        workflow_service = WorkflowService()
        if invoke_from == InvokeFrom.DEBUGGER:
            # fetch draft workflow by app_model
            workflow = workflow_service.get_draft_workflow(app_model=app_model)

            if not workflow:
                raise ValueError("Workflow not initialized")
        else:
            # fetch published workflow by app_model
            workflow = workflow_service.get_published_workflow(app_model=app_model)

            if not workflow:
                raise ValueError("Workflow not published")

        return workflow
