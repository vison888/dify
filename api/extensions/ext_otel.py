import atexit
import logging
import os
import platform
import socket
import sys
from typing import Union

import flask
from celery.signals import worker_init  # type: ignore
from flask_login import user_loaded_from_request, user_logged_in  # type: ignore

from configs import dify_config
from dify_app import DifyApp
from libs.helper import extract_tenant_id
from models import Account, EndUser


@user_logged_in.connect
@user_loaded_from_request.connect
def on_user_loaded(_sender, user: Union["Account", "EndUser"]):
    """
    用户加载事件处理器
    
    当用户登录或从请求中加载时，为当前跟踪span添加用户和租户属性。
    这些属性用于在监控系统中识别和过滤特定用户或租户的请求。
    
    Args:
        _sender: 事件发送者
        user: 加载的用户对象（Account或EndUser）
    """
    if dify_config.ENABLE_OTEL:
        from opentelemetry.trace import get_current_span

        if user:
            try:
                current_span = get_current_span()
                tenant_id = extract_tenant_id(user)
                if not tenant_id:
                    return
                if current_span:
                    # 设置租户和用户属性到当前span
                    current_span.set_attribute("service.tenant.id", tenant_id)
                    current_span.set_attribute("service.user.id", user.id)
            except Exception:
                logging.exception("Error setting tenant and user attributes")
                pass


def init_app(app: DifyApp):
    """
    初始化OpenTelemetry监控扩展
    
    配置完整的可观测性系统，包括：
    1. 分布式跟踪（Tracing）
    2. 指标监控（Metrics）
    3. 日志关联（Logging）
    4. 异常监控
    5. 性能分析
    
    Args:
        app (DifyApp): Flask应用实例
    """
    from opentelemetry.semconv.trace import SpanAttributes

    def is_celery_worker():
        """检查当前进程是否为Celery工作进程"""
        return "celery" in sys.argv[0].lower()

    def instrument_exception_logging():
        """配置异常日志记录器"""
        exception_handler = ExceptionLoggingHandler()
        logging.getLogger().addHandler(exception_handler)

    def init_flask_instrumentor(app: DifyApp):
        """
        初始化Flask应用监控
        
        配置HTTP请求监控，包括：
        - 请求计数
        - 响应时间
        - 状态码统计
        - 路由信息
        """
        meter = get_meter("http_metrics", version=dify_config.project.version)
        _http_response_counter = meter.create_counter(
            "http.server.response.count",
            description="Total number of HTTP responses by status code, method and target",
            unit="{response}",
        )

        def response_hook(span: Span, status: str, response_headers: list):
            """
            HTTP响应钩子函数
            
            在每个HTTP响应完成后调用，用于：
            - 设置span状态
            - 记录HTTP指标
            - 添加请求属性
            """
            if span and span.is_recording():
                try:
                    # 根据状态码设置span状态
                    if status.startswith("2"):
                        span.set_status(StatusCode.OK)
                    else:
                        span.set_status(StatusCode.ERROR, status)

                    # 解析状态码并记录指标
                    status = status.split(" ")[0]
                    status_code = int(status)
                    status_class = f"{status_code // 100}xx"
                    attributes: dict[str, str | int] = {"status_code": status_code, "status_class": status_class}
                    
                    # 添加请求信息
                    request = flask.request
                    if request and request.url_rule:
                        attributes[SpanAttributes.HTTP_TARGET] = str(request.url_rule.rule)
                    if request and request.method:
                        attributes[SpanAttributes.HTTP_METHOD] = str(request.method)
                    
                    # 增加HTTP响应计数器
                    _http_response_counter.add(1, attributes)
                except Exception:
                    logging.exception("Error setting status and attributes")
                    pass

        instrumentor = FlaskInstrumentor()
        if dify_config.DEBUG:
            logging.info("Initializing Flask instrumentor")
        instrumentor.instrument_app(app, response_hook=response_hook)

    def init_sqlalchemy_instrumentor(app: DifyApp):
        """
        初始化SQLAlchemy监控
        
        监控数据库查询性能，包括：
        - 查询执行时间
        - SQL语句
        - 连接池状态
        """
        with app.app_context():
            engines = list(app.extensions["sqlalchemy"].engines.values())
            SQLAlchemyInstrumentor().instrument(enable_commenter=True, engines=engines)

    def setup_context_propagation():
        """
        设置上下文传播
        
        配置分布式跟踪的上下文传播机制，支持：
        - W3C Trace Context（标准格式）
        - B3格式（兼容性格式）
        """
        # 配置传播器
        set_global_textmap(
            CompositePropagator(
                [
                    TraceContextTextMapPropagator(),  # W3C跟踪上下文
                    B3Format(),  # B3传播（被许多系统使用）
                ]
            )
        )

    def shutdown_tracer():
        """关闭跟踪器时的清理函数"""
        provider = trace.get_tracer_provider()
        if hasattr(provider, "force_flush"):
            provider.force_flush()

    class ExceptionLoggingHandler(logging.Handler):
        """
        自定义日志处理器，为logging.exception()调用创建span
        
        当调用logging.exception()时，自动创建跟踪span并记录异常信息。
        """

        def emit(self, record: logging.LogRecord):
            """
            处理日志记录
            
            Args:
                record: 日志记录对象
            """
            try:
                if record.exc_info:
                    tracer = get_tracer_provider().get_tracer("dify.exception.logging")
                    with tracer.start_as_current_span(
                        "log.exception",
                        attributes={
                            "log.level": record.levelname,
                            "log.message": record.getMessage(),
                            "log.logger": record.name,
                            "log.file.path": record.pathname,
                            "log.file.line": record.lineno,
                        },
                    ) as span:
                        span.set_status(StatusCode.ERROR)
                        if record.exc_info[1]:
                            span.record_exception(record.exc_info[1])
                            span.set_attribute("exception.message", str(record.exc_info[1]))
                        if record.exc_info[0]:
                            span.set_attribute("exception.type", record.exc_info[0].__name__)

            except Exception:
                pass

    # 导入OpenTelemetry相关模块
    from opentelemetry import trace
    from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter as GRPCMetricExporter
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter as GRPCSpanExporter
    from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter as HTTPMetricExporter
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter as HTTPSpanExporter
    from opentelemetry.instrumentation.celery import CeleryInstrumentor
    from opentelemetry.instrumentation.flask import FlaskInstrumentor
    from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
    from opentelemetry.metrics import get_meter, get_meter_provider, set_meter_provider
    from opentelemetry.propagate import set_global_textmap
    from opentelemetry.propagators.b3 import B3Format
    from opentelemetry.propagators.composite import CompositePropagator
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import ConsoleMetricExporter, PeriodicExportingMetricReader
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import (
        BatchSpanProcessor,
        ConsoleSpanExporter,
    )
    from opentelemetry.sdk.trace.sampling import ParentBasedTraceIdRatio
    from opentelemetry.semconv.resource import ResourceAttributes
    from opentelemetry.trace import Span, get_tracer_provider, set_tracer_provider
    from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
    from opentelemetry.trace.status import StatusCode

    # 设置上下文传播
    setup_context_propagation()
    
    # 初始化OpenTelemetry
    # 遵循语义约定1.32.0定义资源属性
    resource = Resource(
        attributes={
            ResourceAttributes.SERVICE_NAME: dify_config.APPLICATION_NAME,
            ResourceAttributes.SERVICE_VERSION: f"dify-{dify_config.project.version}-{dify_config.COMMIT_SHA}",
            ResourceAttributes.PROCESS_PID: os.getpid(),
            ResourceAttributes.DEPLOYMENT_ENVIRONMENT: f"{dify_config.DEPLOY_ENV}-{dify_config.EDITION}",
            ResourceAttributes.HOST_NAME: socket.gethostname(),
            ResourceAttributes.HOST_ARCH: platform.machine(),
            "custom.deployment.git_commit": dify_config.COMMIT_SHA,
            ResourceAttributes.HOST_ID: platform.node(),
            ResourceAttributes.OS_TYPE: platform.system().lower(),
            ResourceAttributes.OS_DESCRIPTION: platform.platform(),
            ResourceAttributes.OS_VERSION: platform.version(),
        }
    )
    
    # 配置采样器
    sampler = ParentBasedTraceIdRatio(dify_config.OTEL_SAMPLING_RATE)
    provider = TracerProvider(resource=resource, sampler=sampler)
    set_tracer_provider(provider)
    
    # 配置导出器
    exporter: Union[GRPCSpanExporter, HTTPSpanExporter, ConsoleSpanExporter]
    metric_exporter: Union[GRPCMetricExporter, HTTPMetricExporter, ConsoleMetricExporter]
    protocol = (dify_config.OTEL_EXPORTER_OTLP_PROTOCOL or "").lower()
    
    if dify_config.OTEL_EXPORTER_TYPE == "otlp":
        # OTLP导出器配置
        if protocol == "grpc":
            # gRPC协议导出器
            exporter = GRPCSpanExporter(
                endpoint=dify_config.OTLP_BASE_ENDPOINT,
                # 头部字段名必须由小写字母组成，检查RFC7540
                headers=(("authorization", f"Bearer {dify_config.OTLP_API_KEY}"),),
                insecure=True,
            )
            metric_exporter = GRPCMetricExporter(
                endpoint=dify_config.OTLP_BASE_ENDPOINT,
                headers=(("authorization", f"Bearer {dify_config.OTLP_API_KEY}"),),
                insecure=True,
            )
        else:
            # HTTP协议导出器
            headers = {"Authorization": f"Bearer {dify_config.OTLP_API_KEY}"} if dify_config.OTLP_API_KEY else None

            trace_endpoint = dify_config.OTLP_TRACE_ENDPOINT
            if not trace_endpoint:
                trace_endpoint = dify_config.OTLP_BASE_ENDPOINT + "/v1/traces"
            exporter = HTTPSpanExporter(
                endpoint=trace_endpoint,
                headers=headers,
            )

            metric_endpoint = dify_config.OTLP_METRIC_ENDPOINT
            if not metric_endpoint:
                metric_endpoint = dify_config.OTLP_BASE_ENDPOINT + "/v1/metrics"
            metric_exporter = HTTPMetricExporter(
                endpoint=metric_endpoint,
                headers=headers,
            )
    else:
        # 控制台导出器（开发/调试用）
        exporter = ConsoleSpanExporter()
        metric_exporter = ConsoleMetricExporter()

    # 配置span处理器
    provider.add_span_processor(
        BatchSpanProcessor(
            exporter,
            max_queue_size=dify_config.OTEL_MAX_QUEUE_SIZE,
            schedule_delay_millis=dify_config.OTEL_BATCH_EXPORT_SCHEDULE_DELAY,
            max_export_batch_size=dify_config.OTEL_MAX_EXPORT_BATCH_SIZE,
            export_timeout_millis=dify_config.OTEL_BATCH_EXPORT_TIMEOUT,
        )
    )
    
    # 配置指标读取器
    reader = PeriodicExportingMetricReader(
        metric_exporter,
        export_interval_millis=dify_config.OTEL_METRIC_EXPORT_INTERVAL,
        export_timeout_millis=dify_config.OTEL_METRIC_EXPORT_TIMEOUT,
    )
    set_meter_provider(MeterProvider(resource=resource, metric_readers=[reader]))
    
    # 如果不是Celery工作进程，初始化Flask和Celery监控
    if not is_celery_worker():
        init_flask_instrumentor(app)
        CeleryInstrumentor(tracer_provider=get_tracer_provider(), meter_provider=get_meter_provider()).instrument()
    
    # 配置异常日志记录和SQLAlchemy监控
    instrument_exception_logging()
    init_sqlalchemy_instrumentor(app)
    
    # 注册关闭时的清理函数
    atexit.register(shutdown_tracer)


def is_enabled():
    """
    检查OpenTelemetry是否启用
    
    Returns:
        bool: 如果启用OTEL返回True，否则返回False
    """
    return dify_config.ENABLE_OTEL


@worker_init.connect(weak=False)
def init_celery_worker(*args, **kwargs):
    """
    Celery工作进程初始化函数
    
    当Celery工作进程启动时，初始化OpenTelemetry监控。
    确保工作进程也能进行分布式跟踪。
    """
    if dify_config.ENABLE_OTEL:
        from opentelemetry.instrumentation.celery import CeleryInstrumentor
        from opentelemetry.metrics import get_meter_provider
        from opentelemetry.trace import get_tracer_provider

        tracer_provider = get_tracer_provider()
        metric_provider = get_meter_provider()
        if dify_config.DEBUG:
            logging.info("Initializing OpenTelemetry for Celery worker")
        CeleryInstrumentor(tracer_provider=tracer_provider, meter_provider=metric_provider).instrument()
