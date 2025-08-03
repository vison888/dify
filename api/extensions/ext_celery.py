from datetime import timedelta

import pytz
from celery import Celery, Task  # type: ignore
from celery.schedules import crontab  # type: ignore

from configs import dify_config
from dify_app import DifyApp


def init_app(app: DifyApp) -> Celery:
    """
    初始化Celery后台任务扩展
    
    配置Celery应用，包括：
    1. 任务类配置（支持Flask应用上下文）
    2. 消息代理和结果后端配置
    3. SSL配置（如果启用）
    4. 定时任务调度配置
    5. 各种清理和维护任务
    
    Args:
        app (DifyApp): Flask应用实例
        
    Returns:
        Celery: 配置好的Celery应用实例
    """
    
    class FlaskTask(Task):
        """
        自定义Celery任务类，支持Flask应用上下文
        
        确保每个Celery任务都能访问Flask应用上下文，
        从而能够使用数据库连接、配置等Flask功能。
        """
        def __call__(self, *args: object, **kwargs: object) -> object:
            with app.app_context():
                return self.run(*args, **kwargs)

    # 配置消息代理传输选项
    broker_transport_options = {}

    # 如果使用Redis Sentinel，配置Sentinel相关选项
    if dify_config.CELERY_USE_SENTINEL:
        broker_transport_options = {
            "master_name": dify_config.CELERY_SENTINEL_MASTER_NAME,
            "sentinel_kwargs": {
                "socket_timeout": dify_config.CELERY_SENTINEL_SOCKET_TIMEOUT,
                "password": dify_config.CELERY_SENTINEL_PASSWORD,
            },
        }

    # 创建Celery应用实例
    celery_app = Celery(
        app.name,
        task_cls=FlaskTask,  # 使用自定义任务类
        broker=dify_config.CELERY_BROKER_URL,      # 消息代理URL
        backend=dify_config.CELERY_BACKEND,        # 结果后端URL
        task_ignore_result=True,                   # 忽略任务结果（节省内存）
    )

    # 配置SSL选项（用于安全连接）
    ssl_options = {
        "ssl_cert_reqs": None,
        "ssl_ca_certs": None,
        "ssl_certfile": None,
        "ssl_keyfile": None,
    }

    # 更新Celery配置
    celery_app.conf.update(
        result_backend=dify_config.CELERY_RESULT_BACKEND,
        broker_transport_options=broker_transport_options,
        broker_connection_retry_on_startup=True,   # 启动时重试连接
        worker_log_format=dify_config.LOG_FORMAT,  # 工作进程日志格式
        worker_task_log_format=dify_config.LOG_FORMAT,  # 任务日志格式
        worker_hijack_root_logger=False,           # 不劫持根日志器
        timezone=pytz.timezone(dify_config.LOG_TZ or "UTC"),  # 时区设置
    )

    # 如果启用SSL，添加SSL配置
    if dify_config.BROKER_USE_SSL:
        celery_app.conf.update(
            broker_use_ssl=ssl_options,  # 为消息代理添加SSL选项
        )

    # 如果指定了日志文件，配置工作进程日志文件
    if dify_config.LOG_FILE:
        celery_app.conf.update(
            worker_logfile=dify_config.LOG_FILE,
        )

    # 设置为默认Celery应用
    celery_app.set_default()
    # 将Celery实例存储到Flask扩展中
    app.extensions["celery"] = celery_app

    # 配置定时任务导入和调度
    imports = []
    day = dify_config.CELERY_BEAT_SCHEDULER_TIME

    # 如果添加新任务，请在CeleryScheduleTasksConfig中添加开关
    beat_schedule = {}
    
    # 清理嵌入缓存任务
    if dify_config.ENABLE_CLEAN_EMBEDDING_CACHE_TASK:
        imports.append("schedule.clean_embedding_cache_task")
        beat_schedule["clean_embedding_cache_task"] = {
            "task": "schedule.clean_embedding_cache_task.clean_embedding_cache_task",
            "schedule": timedelta(days=day),
        }
    
    # 清理未使用数据集任务
    if dify_config.ENABLE_CLEAN_UNUSED_DATASETS_TASK:
        imports.append("schedule.clean_unused_datasets_task")
        beat_schedule["clean_unused_datasets_task"] = {
            "task": "schedule.clean_unused_datasets_task.clean_unused_datasets_task",
            "schedule": timedelta(days=day),
        }
    
    # 创建TiDB Serverless任务
    if dify_config.ENABLE_CREATE_TIDB_SERVERLESS_TASK:
        imports.append("schedule.create_tidb_serverless_task")
        beat_schedule["create_tidb_serverless_task"] = {
            "task": "schedule.create_tidb_serverless_task.create_tidb_serverless_task",
            "schedule": crontab(minute="0", hour="*"),  # 每小时执行
        }
    
    # 更新TiDB Serverless状态任务
    if dify_config.ENABLE_UPDATE_TIDB_SERVERLESS_STATUS_TASK:
        imports.append("schedule.update_tidb_serverless_status_task")
        beat_schedule["update_tidb_serverless_status_task"] = {
            "task": "schedule.update_tidb_serverless_status_task.update_tidb_serverless_status_task",
            "schedule": timedelta(minutes=10),  # 每10分钟执行
        }
    
    # 清理消息任务
    if dify_config.ENABLE_CLEAN_MESSAGES:
        imports.append("schedule.clean_messages")
        beat_schedule["clean_messages"] = {
            "task": "schedule.clean_messages.clean_messages",
            "schedule": timedelta(days=day),
        }
    
    # 邮件清理文档通知任务
    if dify_config.ENABLE_MAIL_CLEAN_DOCUMENT_NOTIFY_TASK:
        imports.append("schedule.mail_clean_document_notify_task")
        beat_schedule["mail_clean_document_notify_task"] = {
            "task": "schedule.mail_clean_document_notify_task.mail_clean_document_notify_task",
            "schedule": crontab(minute="0", hour="10", day_of_week="1"),  # 每周一上午10点
        }
    
    # 数据集队列监控任务
    if dify_config.ENABLE_DATASETS_QUEUE_MONITOR:
        imports.append("schedule.queue_monitor_task")
        beat_schedule["datasets-queue-monitor"] = {
            "task": "schedule.queue_monitor_task.queue_monitor_task",
            "schedule": timedelta(
                minutes=dify_config.QUEUE_MONITOR_INTERVAL if dify_config.QUEUE_MONITOR_INTERVAL else 30
            ),
        }
    
    # 检查可升级插件任务
    if dify_config.ENABLE_CHECK_UPGRADABLE_PLUGIN_TASK:
        imports.append("schedule.check_upgradable_plugin_task")
        beat_schedule["check_upgradable_plugin_task"] = {
            "task": "schedule.check_upgradable_plugin_task.check_upgradable_plugin_task",
            "schedule": crontab(minute="*/15"),  # 每15分钟执行
        }

    # 更新Celery配置，添加定时任务调度和导入
    celery_app.conf.update(beat_schedule=beat_schedule, imports=imports)

    return celery_app
