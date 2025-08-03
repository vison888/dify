from configs import dify_config
from dify_app import DifyApp


def init_app(app: DifyApp):
    """
    初始化Sentry错误监控扩展
    
    配置Sentry SDK用于错误监控和性能分析。
    只有在配置了SENTRY_DSN时才会初始化。
    
    Args:
        app (DifyApp): Flask应用实例
    """
    if dify_config.SENTRY_DSN:
        import openai
        import sentry_sdk
        from langfuse import parse_error  # type: ignore
        from sentry_sdk.integrations.celery import CeleryIntegration
        from sentry_sdk.integrations.flask import FlaskIntegration
        from werkzeug.exceptions import HTTPException

        from core.model_runtime.errors.invoke import InvokeRateLimitError

        def before_send(event, hint):
            """
            发送前过滤器
            
            在发送事件到Sentry之前进行过滤，避免发送不必要的错误。
            
            Args:
                event: 要发送的事件
                hint: 事件提示信息
                
            Returns:
                dict|None: 如果应该发送事件返回事件对象，否则返回None
            """
            if "exc_info" in hint:
                exc_type, exc_value, tb = hint["exc_info"]
                # 过滤掉Langfuse的默认错误响应
                if parse_error.defaultErrorResponse in str(exc_value):
                    return None

            return event

        # 初始化Sentry SDK
        sentry_sdk.init(
            dsn=dify_config.SENTRY_DSN,  # Sentry项目DSN
            integrations=[
                FlaskIntegration(),  # Flask集成
                CeleryIntegration()  # Celery集成
            ],
            # 忽略特定类型的错误
            ignore_errors=[
                HTTPException,           # HTTP异常（4xx, 5xx）
                ValueError,              # 值错误
                FileNotFoundError,       # 文件未找到
                openai.APIStatusError,   # OpenAI API状态错误
                InvokeRateLimitError,    # 调用频率限制错误
                parse_error.defaultErrorResponse,  # Langfuse默认错误响应
            ],
            traces_sample_rate=dify_config.SENTRY_TRACES_SAMPLE_RATE,  # 跟踪采样率
            profiles_sample_rate=dify_config.SENTRY_PROFILES_SAMPLE_RATE,  # 性能分析采样率
            environment=dify_config.DEPLOY_ENV,  # 部署环境
            release=f"dify-{dify_config.project.version}-{dify_config.COMMIT_SHA}",  # 发布版本
            before_send=before_send,  # 发送前过滤器
        )
