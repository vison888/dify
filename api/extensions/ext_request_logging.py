import json
import logging

import flask
import werkzeug.http
from flask import Flask
from flask.signals import request_finished, request_started

from configs import dify_config

_logger = logging.getLogger(__name__)


def _is_content_type_json(content_type: str) -> bool:
    """
    检查内容类型是否为JSON
    
    Args:
        content_type: HTTP内容类型字符串
        
    Returns:
        bool: 如果是JSON内容类型返回True，否则返回False
    """
    if not content_type:
        return False
    content_type_no_option, _ = werkzeug.http.parse_options_header(content_type)
    return content_type_no_option.lower() == "application/json"


def _log_request_started(_sender, **_extra):
    """
    记录请求开始
    
    当请求开始时调用，记录请求方法和路径。
    如果是JSON请求且有请求体，还会记录请求体内容。
    
    Args:
        _sender: 信号发送者
        **_extra: 额外参数
    """
    if not _logger.isEnabledFor(logging.DEBUG):
        return

    request = flask.request
    if not (_is_content_type_json(request.content_type) and request.data):
        # 非JSON请求或没有请求体，只记录基本信息
        _logger.debug("Received Request %s -> %s", request.method, request.path)
        return
    
    # JSON请求且有请求体，记录详细信息
    try:
        json_data = json.loads(request.data)
    except (TypeError, ValueError):
        _logger.exception("Failed to parse JSON request")
        return
    
    # 格式化JSON数据以便阅读
    formatted_json = json.dumps(json_data, ensure_ascii=False, indent=2)
    _logger.debug(
        "Received Request %s -> %s, Request Body:\n%s",
        request.method,
        request.path,
        formatted_json,
    )


def _log_request_finished(_sender, response, **_extra):
    """
    记录请求结束
    
    当请求结束时调用，记录响应状态和内容类型。
    如果是JSON响应，还会记录响应体内容。
    
    Args:
        _sender: 信号发送者
        response: Flask响应对象
        **_extra: 额外参数
    """
    if not _logger.isEnabledFor(logging.DEBUG) or response is None:
        return

    if not _is_content_type_json(response.content_type):
        # 非JSON响应，只记录基本信息
        _logger.debug("Response %s %s", response.status, response.content_type)
        return

    # JSON响应，记录详细信息
    response_data = response.get_data(as_text=True)
    try:
        json_data = json.loads(response_data)
    except (TypeError, ValueError):
        _logger.exception("Failed to parse JSON response")
        return
    
    # 格式化JSON数据以便阅读
    formatted_json = json.dumps(json_data, ensure_ascii=False, indent=2)
    _logger.debug(
        "Response %s %s, Response Body:\n%s",
        response.status,
        response.content_type,
        formatted_json,
    )


def init_app(app: Flask):
    """
    初始化请求日志扩展
    
    配置请求和响应的详细日志记录。
    只有在启用请求日志记录时才会注册信号处理器。
    
    Args:
        app (Flask): Flask应用实例
    """
    if not dify_config.ENABLE_REQUEST_LOGGING:
        return
    
    # 注册请求开始和结束的信号处理器
    request_started.connect(_log_request_started, app)
    request_finished.connect(_log_request_finished, app)
