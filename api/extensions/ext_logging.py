import logging
import os
import sys
import uuid
from logging.handlers import RotatingFileHandler

import flask

from configs import dify_config
from dify_app import DifyApp


def init_app(app: DifyApp):
    """
    初始化日志系统扩展
    
    配置完整的日志系统，包括：
    - 文件日志和控制台日志
    - 日志轮转和大小限制
    - 请求ID跟踪
    - 时区支持
    - 日志格式化和过滤
    
    Args:
        app (DifyApp): Flask应用实例
    """
    # 初始化日志处理器列表
    log_handlers: list[logging.Handler] = []
    log_file = dify_config.LOG_FILE
    
    # 如果配置了日志文件，添加文件日志处理器
    if log_file:
        log_dir = os.path.dirname(log_file)
        os.makedirs(log_dir, exist_ok=True)
        log_handlers.append(
            RotatingFileHandler(
                filename=log_file,
                maxBytes=dify_config.LOG_FILE_MAX_SIZE * 1024 * 1024,  # 转换为字节
                backupCount=dify_config.LOG_FILE_BACKUP_COUNT,
            )
        )

    # 始终添加控制台日志处理器
    sh = logging.StreamHandler(sys.stdout)
    log_handlers.append(sh)

    # 为所有处理器添加请求ID过滤器
    for handler in log_handlers:
        handler.addFilter(RequestIdFilter())

    # 配置基础日志设置
    logging.basicConfig(
        level=dify_config.LOG_LEVEL,
        format=dify_config.LOG_FORMAT,
        datefmt=dify_config.LOG_DATEFORMAT,
        handlers=log_handlers,
        force=True,  # 强制重新配置日志系统
    )

    # 应用请求ID格式化器到所有处理器
    apply_request_id_formatter()

    # 禁用嘈杂的日志器传播，避免重复日志
    logging.getLogger("sqlalchemy.engine").propagate = False
    
    # 配置时区支持
    log_tz = dify_config.LOG_TZ
    if log_tz:
        from datetime import datetime

        import pytz

        timezone = pytz.timezone(log_tz)

        def time_converter(seconds):
            """将时间戳转换为指定时区的时间"""
            return datetime.fromtimestamp(seconds, tz=timezone).timetuple()

        # 为所有根处理器设置时区转换器
        for handler in logging.root.handlers:
            if handler.formatter:
                handler.formatter.converter = time_converter


def get_request_id():
    """
    获取或生成请求ID
    
    为每个HTTP请求生成唯一的标识符，用于日志跟踪。
    如果请求上下文中已存在请求ID，则返回现有ID。
    
    Returns:
        str: 10位十六进制请求ID
    """
    if getattr(flask.g, "request_id", None):
        return flask.g.request_id

    # 生成新的请求ID（取UUID的前10位）
    new_uuid = uuid.uuid4().hex[:10]
    flask.g.request_id = new_uuid

    return new_uuid


class RequestIdFilter(logging.Filter):
    """
    请求ID日志过滤器
    
    这个过滤器使请求ID在日志格式中可用。
    注意：我们检查是否在请求上下文中，因为我们可能想在Flask完全加载之前记录日志。
    """
    
    def filter(self, record):
        """
        过滤日志记录，添加请求ID
        
        Args:
            record: 日志记录对象
            
        Returns:
            bool: 始终返回True，表示不过滤任何记录
        """
        record.req_id = get_request_id() if flask.has_request_context() else ""
        return True


class RequestIdFormatter(logging.Formatter):
    """
    请求ID日志格式化器
    
    自定义日志格式化器，确保请求ID字段存在。
    """
    
    def format(self, record):
        """
        格式化日志记录
        
        Args:
            record: 日志记录对象
            
        Returns:
            str: 格式化后的日志字符串
        """
        if not hasattr(record, "req_id"):
            record.req_id = ""
        return super().format(record)


def apply_request_id_formatter():
    """
    应用请求ID格式化器
    
    为所有根日志处理器应用自定义格式化器，
    确保所有日志都包含请求ID信息。
    """
    for handler in logging.root.handlers:
        if handler.formatter:
            handler.formatter = RequestIdFormatter(dify_config.LOG_FORMAT, dify_config.LOG_DATEFORMAT)
