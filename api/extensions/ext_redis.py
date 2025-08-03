import functools
import logging
from collections.abc import Callable
from typing import Any, Union

import redis
from redis import RedisError
from redis.cache import CacheConfig
from redis.cluster import ClusterNode, RedisCluster
from redis.connection import Connection, SSLConnection
from redis.sentinel import Sentinel

from configs import dify_config
from dify_app import DifyApp

logger = logging.getLogger(__name__)


class RedisClientWrapper:
    """
    Redis客户端包装器
    
    解决全局redis_client变量在Sentinel返回新Redis实例时无法更新的问题。
    这个包装器类允许延迟初始化Redis客户端，使客户端能够在必要时重新初始化。
    这在Redis实例可能动态变化的场景中特别有用，比如在Sentinel管理的Redis设置中发生故障转移时。
    
    属性:
        _client (redis.Redis): 实际的Redis客户端实例。在通过initialize方法初始化之前保持为None。
    
    方法:
        initialize(client): 如果尚未初始化，则初始化Redis客户端。
        __getattr__(item): 将属性访问委托给Redis客户端，如果客户端未初始化则引发错误。
    """

    def __init__(self):
        """初始化Redis客户端包装器"""
        self._client = None

    def initialize(self, client):
        """
        初始化Redis客户端
        
        Args:
            client: Redis客户端实例
        """
        if self._client is None:
            self._client = client

    def __getattr__(self, item):
        """
        代理属性访问到Redis客户端
        
        Args:
            item: 要访问的属性名
            
        Returns:
            属性值
            
        Raises:
            RuntimeError: 如果客户端未初始化
        """
        if self._client is None:
            raise RuntimeError("Redis client is not initialized. Call init_app first.")
        return getattr(self._client, item)


# 创建全局Redis客户端包装器实例
redis_client = RedisClientWrapper()


def init_app(app: DifyApp):
    """
    初始化Redis扩展
    
    根据配置创建Redis客户端，支持：
    - 单机Redis
    - Redis Sentinel（高可用）
    - Redis Cluster（集群）
    - SSL连接
    - 客户端缓存
    
    Args:
        app (DifyApp): Flask应用实例
    """
    global redis_client
    
    # 选择连接类（普通连接或SSL连接）
    connection_class: type[Union[Connection, SSLConnection]] = Connection
    if dify_config.REDIS_USE_SSL:
        connection_class = SSLConnection
    
    # 配置RESP协议版本
    resp_protocol = dify_config.REDIS_SERIALIZATION_PROTOCOL
    
    # 配置客户端缓存（仅RESP3支持）
    if dify_config.REDIS_ENABLE_CLIENT_SIDE_CACHE:
        if resp_protocol >= 3:
            clientside_cache_config = CacheConfig()
        else:
            raise ValueError("Client side cache is only supported in RESP3")
    else:
        clientside_cache_config = None

    # 基础Redis参数
    redis_params: dict[str, Any] = {
        "username": dify_config.REDIS_USERNAME,
        "password": dify_config.REDIS_PASSWORD or None,  # 临时修复空密码问题
        "db": dify_config.REDIS_DB,
        "encoding": "utf-8",
        "encoding_errors": "strict",
        "decode_responses": False,
        "protocol": resp_protocol,
        "cache_config": clientside_cache_config,
    }

    # 根据配置选择Redis部署模式
    if dify_config.REDIS_USE_SENTINEL:
        # Redis Sentinel模式（高可用）
        assert dify_config.REDIS_SENTINELS is not None, "REDIS_SENTINELS must be set when REDIS_USE_SENTINEL is True"
        sentinel_hosts = [
            (node.split(":")[0], int(node.split(":")[1])) for node in dify_config.REDIS_SENTINELS.split(",")
        ]
        sentinel = Sentinel(
            sentinel_hosts,
            sentinel_kwargs={
                "socket_timeout": dify_config.REDIS_SENTINEL_SOCKET_TIMEOUT,
                "username": dify_config.REDIS_SENTINEL_USERNAME,
                "password": dify_config.REDIS_SENTINEL_PASSWORD,
            },
        )
        master = sentinel.master_for(dify_config.REDIS_SENTINEL_SERVICE_NAME, **redis_params)
        redis_client.initialize(master)
        
    elif dify_config.REDIS_USE_CLUSTERS:
        # Redis Cluster模式
        assert dify_config.REDIS_CLUSTERS is not None, "REDIS_CLUSTERS must be set when REDIS_USE_CLUSTERS is True"
        nodes = [
            ClusterNode(host=node.split(":")[0], port=int(node.split(":")[1]))
            for node in dify_config.REDIS_CLUSTERS.split(",")
        ]
        redis_client.initialize(
            RedisCluster(
                startup_nodes=nodes,
                password=dify_config.REDIS_CLUSTERS_PASSWORD,
                protocol=resp_protocol,
                cache_config=clientside_cache_config,
            )
        )
    else:
        # 单机Redis模式
        redis_params.update(
            {
                "host": dify_config.REDIS_HOST,
                "port": dify_config.REDIS_PORT,
                "connection_class": connection_class,
                "protocol": resp_protocol,
                "cache_config": clientside_cache_config,
            }
        )
        pool = redis.ConnectionPool(**redis_params)
        redis_client.initialize(redis.Redis(connection_pool=pool))

    # 将Redis客户端存储到Flask扩展中
    app.extensions["redis"] = redis_client


def redis_fallback(default_return: Any = None):
    """
    Redis操作异常处理装饰器
    
    当Redis不可用时，返回默认值而不是抛出异常。
    这对于提高应用容错性很有用。
    
    Args:
        default_return: Redis操作失败时返回的值，默认为None
        
    Returns:
        装饰器函数
    """

    def decorator(func: Callable):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except RedisError as e:
                logger.warning(f"Redis operation failed in {func.__name__}: {str(e)}", exc_info=True)
                return default_return

        return wrapper

    return decorator
