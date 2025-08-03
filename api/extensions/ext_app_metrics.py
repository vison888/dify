import json
import os
import threading

from flask import Response

from configs import dify_config
from dify_app import DifyApp


def init_app(app: DifyApp):
    """
    初始化应用指标监控扩展
    
    为Flask应用添加以下功能：
    1. 响应头中添加版本和环境信息
    2. 健康检查端点
    3. 线程监控端点
    4. 数据库连接池状态监控
    
    Args:
        app (DifyApp): Flask应用实例
    """
    
    @app.after_request
    def after_request(response):
        """
        在每个响应后添加版本和环境信息到响应头
        
        Args:
            response: Flask响应对象
            
        Returns:
            Response: 添加了版本信息的响应对象
        """
        response.headers.add("X-Version", dify_config.project.version)
        response.headers.add("X-Env", dify_config.DEPLOY_ENV)
        return response

    @app.route("/health")
    def health():
        """
        健康检查端点
        
        返回应用的基本健康状态信息，包括：
        - 进程ID
        - 应用状态
        - 应用版本
        
        Returns:
            Response: JSON格式的健康状态信息
        """
        return Response(
            json.dumps({"pid": os.getpid(), "status": "ok", "version": dify_config.project.version}),
            status=200,
            content_type="application/json",
        )

    @app.route("/threads")
    def threads():
        """
        线程监控端点
        
        返回当前应用的所有线程信息，包括：
        - 线程总数
        - 每个线程的名称、ID和存活状态
        
        Returns:
            dict: 包含线程详细信息的字典
        """
        num_threads = threading.active_count()
        threads = threading.enumerate()

        thread_list = []
        for thread in threads:
            thread_name = thread.name
            thread_id = thread.ident
            is_alive = thread.is_alive()

            thread_list.append(
                {
                    "name": thread_name,
                    "id": thread_id,
                    "is_alive": is_alive,
                }
            )

        return {
            "pid": os.getpid(),
            "thread_num": num_threads,
            "threads": thread_list,
        }

    @app.route("/db-pool-stat")
    def pool_stat():
        """
        数据库连接池状态监控端点
        
        返回数据库连接池的详细统计信息，包括：
        - 连接池大小
        - 已检查入/出的连接数
        - 溢出连接数
        - 连接超时设置
        - 连接回收时间
        
        Returns:
            dict: 包含数据库连接池统计信息的字典
        """
        from extensions.ext_database import db

        engine = db.engine
        # TODO: 修复类型错误
        # FIXME 可能是SQLAlchemy版本问题
        return {
            "pid": os.getpid(),
            "pool_size": engine.pool.size(),  # type: ignore
            "checked_in_connections": engine.pool.checkedin(),  # type: ignore
            "checked_out_connections": engine.pool.checkedout(),  # type: ignore
            "overflow_connections": engine.pool.overflow(),  # type: ignore
            "connection_timeout": engine.pool.timeout(),  # type: ignore
            "recycle_time": db.engine.pool._recycle,  # type: ignore
        }
