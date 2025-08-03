import logging
from collections.abc import Callable, Generator
from typing import Literal, Union, overload

from flask import Flask

from configs import dify_config
from dify_app import DifyApp
from extensions.storage.base_storage import BaseStorage
from extensions.storage.storage_type import StorageType

logger = logging.getLogger(__name__)


class Storage:
    """
    文件存储管理类
    
    提供统一的文件存储接口，支持多种存储后端：
    - S3: AWS S3存储
    - OpenDAL: 通用数据访问层
    - Local: 本地文件系统
    - Azure Blob: 微软Azure存储
    - 阿里云OSS、腾讯云COS等
    
    支持流式读取和一次性读取两种模式。
    """
    
    def init_app(self, app: Flask):
        """
        初始化存储系统
        
        根据配置的存储类型创建相应的存储实例。
        
        Args:
            app (Flask): Flask应用实例
        """
        storage_factory = self.get_storage_factory(dify_config.STORAGE_TYPE)
        with app.app_context():
            self.storage_runner = storage_factory()

    @staticmethod
    def get_storage_factory(storage_type: str) -> Callable[[], BaseStorage]:
        """
        获取存储工厂函数
        
        根据存储类型返回相应的存储类工厂函数。
        
        Args:
            storage_type: 存储类型字符串
            
        Returns:
            Callable: 返回存储实例的工厂函数
            
        Raises:
            ValueError: 不支持的存储类型
        """
        match storage_type:
            case StorageType.S3:
                # AWS S3存储
                from extensions.storage.aws_s3_storage import AwsS3Storage
                return AwsS3Storage
                
            case StorageType.OPENDAL:
                # OpenDAL通用存储
                from extensions.storage.opendal_storage import OpenDALStorage
                return lambda: OpenDALStorage(dify_config.OPENDAL_SCHEME)
                
            case StorageType.LOCAL:
                # 本地文件系统存储
                from extensions.storage.opendal_storage import OpenDALStorage
                return lambda: OpenDALStorage(scheme="fs", root=dify_config.STORAGE_LOCAL_PATH)
                
            case StorageType.AZURE_BLOB:
                # 微软Azure Blob存储
                from extensions.storage.azure_blob_storage import AzureBlobStorage
                return AzureBlobStorage
                
            case StorageType.ALIYUN_OSS:
                # 阿里云对象存储
                from extensions.storage.aliyun_oss_storage import AliyunOssStorage
                return AliyunOssStorage
                
            case StorageType.GOOGLE_STORAGE:
                # 谷歌云存储
                from extensions.storage.google_cloud_storage import GoogleCloudStorage
                return GoogleCloudStorage
                
            case StorageType.TENCENT_COS:
                # 腾讯云对象存储
                from extensions.storage.tencent_cos_storage import TencentCosStorage
                return TencentCosStorage
                
            case StorageType.OCI_STORAGE:
                # 甲骨文云存储
                from extensions.storage.oracle_oci_storage import OracleOCIStorage
                return OracleOCIStorage
                
            case StorageType.HUAWEI_OBS:
                # 华为云对象存储
                from extensions.storage.huawei_obs_storage import HuaweiObsStorage
                return HuaweiObsStorage
                
            case StorageType.BAIDU_OBS:
                # 百度云对象存储
                from extensions.storage.baidu_obs_storage import BaiduObsStorage
                return BaiduObsStorage
                
            case StorageType.VOLCENGINE_TOS:
                # 火山引擎对象存储
                from extensions.storage.volcengine_tos_storage import VolcengineTosStorage
                return VolcengineTosStorage
                
            case StorageType.SUPBASE:
                # Supabase存储
                from extensions.storage.supabase_storage import SupabaseStorage
                return SupabaseStorage
                
            case _:
                raise ValueError(f"unsupported storage type {storage_type}")

    def save(self, filename, data):
        """
        保存文件
        
        Args:
            filename: 文件名
            data: 文件数据
        """
        self.storage_runner.save(filename, data)

    @overload
    def load(self, filename: str, /, *, stream: Literal[False] = False) -> bytes: ...

    @overload
    def load(self, filename: str, /, *, stream: Literal[True]) -> Generator: ...

    def load(self, filename: str, /, *, stream: bool = False) -> Union[bytes, Generator]:
        """
        加载文件
        
        Args:
            filename: 文件名
            stream: 是否使用流式读取
            
        Returns:
            bytes|Generator: 文件内容或生成器
        """
        if stream:
            return self.load_stream(filename)
        else:
            return self.load_once(filename)

    def load_once(self, filename: str) -> bytes:
        """
        一次性读取文件
        
        Args:
            filename: 文件名
            
        Returns:
            bytes: 文件内容
        """
        return self.storage_runner.load_once(filename)

    def load_stream(self, filename: str) -> Generator:
        """
        流式读取文件
        
        Args:
            filename: 文件名
            
        Returns:
            Generator: 文件内容生成器
        """
        return self.storage_runner.load_stream(filename)

    def download(self, filename, target_filepath):
        """
        下载文件到本地
        
        Args:
            filename: 远程文件名
            target_filepath: 本地目标路径
        """
        self.storage_runner.download(filename, target_filepath)

    def exists(self, filename):
        """
        检查文件是否存在
        
        Args:
            filename: 文件名
            
        Returns:
            bool: 文件是否存在
        """
        return self.storage_runner.exists(filename)

    def delete(self, filename):
        """
        删除文件
        
        Args:
            filename: 文件名
            
        Returns:
            bool: 删除是否成功
        """
        return self.storage_runner.delete(filename)

    def scan(self, path: str, files: bool = True, directories: bool = False) -> list[str]:
        """
        扫描目录
        
        Args:
            path: 目录路径
            files: 是否包含文件
            directories: 是否包含目录
            
        Returns:
            list[str]: 文件或目录列表
        """
        return self.storage_runner.scan(path, files=files, directories=directories)


# 创建全局存储实例
storage = Storage()


def init_app(app: DifyApp):
    """
    初始化存储扩展
    
    创建存储实例并初始化。
    
    Args:
        app (DifyApp): Flask应用实例
    """
    storage.init_app(app)
