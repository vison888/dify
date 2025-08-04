"""
Repository factory for dynamically creating repository instances based on configuration.

This module provides a Django-like settings system for repository implementations,
allowing users to configure different repository backends through string paths.
"""

import importlib
import inspect
import logging
from typing import Protocol, Union

from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker

from configs import dify_config
from core.workflow.repositories.workflow_execution_repository import WorkflowExecutionRepository
from core.workflow.repositories.workflow_node_execution_repository import WorkflowNodeExecutionRepository
from models import Account, EndUser
from models.enums import WorkflowRunTriggeredFrom
from models.workflow import WorkflowNodeExecutionTriggeredFrom

logger = logging.getLogger(__name__)


class RepositoryImportError(Exception):
    """Raised when a repository implementation cannot be imported or instantiated."""

    pass


class DifyCoreRepositoryFactory:
    """
    Dify核心仓库工厂
    
    负责根据配置动态创建仓库实例的工厂类。采用Django风格的配置系统，
    允许通过模块路径字符串指定仓库实现（例如：'module.submodule.ClassName'）。
    
    主要特性：
    1. 动态类导入和实例化
    2. 接口一致性验证
    3. 构造函数签名验证
    4. 配置驱动的实现切换
    
    设计优势：
    - 支持不同环境使用不同的仓库实现
    - 便于单元测试时注入Mock实现
    - 支持插件化的仓库扩展
    - 遵循依赖注入原则
    """

    @staticmethod
    def _import_class(class_path: str) -> type:
        """
        Import a class from a module path string.

        Args:
            class_path: Full module path to the class (e.g., 'module.submodule.ClassName')

        Returns:
            The imported class

        Raises:
            RepositoryImportError: If the class cannot be imported
        """
        try:
            module_path, class_name = class_path.rsplit(".", 1)
            module = importlib.import_module(module_path)
            repo_class = getattr(module, class_name)
            assert isinstance(repo_class, type)
            return repo_class
        except (ValueError, ImportError, AttributeError) as e:
            raise RepositoryImportError(f"Cannot import repository class '{class_path}': {e}") from e

    @staticmethod
    def _validate_repository_interface(repository_class: type, expected_interface: type[Protocol]) -> None:  # type: ignore
        """
        Validate that a class implements the expected repository interface.

        Args:
            repository_class: The class to validate
            expected_interface: The expected interface/protocol

        Raises:
            RepositoryImportError: If the class doesn't implement the interface
        """
        # Check if the class has all required methods from the protocol
        required_methods = [
            method
            for method in dir(expected_interface)
            if not method.startswith("_") and callable(getattr(expected_interface, method, None))
        ]

        missing_methods = []
        for method_name in required_methods:
            if not hasattr(repository_class, method_name):
                missing_methods.append(method_name)

        if missing_methods:
            raise RepositoryImportError(
                f"Repository class '{repository_class.__name__}' does not implement required methods "
                f"{missing_methods} from interface '{expected_interface.__name__}'"
            )

    @staticmethod
    def _validate_constructor_signature(repository_class: type, required_params: list[str]) -> None:
        """
        Validate that a repository class constructor accepts required parameters.

        Args:
            repository_class: The class to validate
            required_params: List of required parameter names

        Raises:
            RepositoryImportError: If the constructor doesn't accept required parameters
        """

        try:
            # MyPy may flag the line below with the following error:
            #
            # > Accessing "__init__" on an instance is unsound, since
            # > instance.__init__ could be from an incompatible subclass.
            #
            # Despite this, we need to ensure that the constructor of `repository_class`
            # has a compatible signature.
            signature = inspect.signature(repository_class.__init__)  # type: ignore[misc]
            param_names = list(signature.parameters.keys())

            # Remove 'self' parameter
            if "self" in param_names:
                param_names.remove("self")

            missing_params = [param for param in required_params if param not in param_names]
            if missing_params:
                raise RepositoryImportError(
                    f"Repository class '{repository_class.__name__}' constructor does not accept required parameters: "
                    f"{missing_params}. Expected parameters: {required_params}"
                )
        except Exception as e:
            raise RepositoryImportError(
                f"Failed to validate constructor signature for '{repository_class.__name__}': {e}"
            ) from e

    @classmethod
    def create_workflow_execution_repository(
        cls,
        session_factory: Union[sessionmaker, Engine],
        user: Union[Account, EndUser],
        app_id: str,
        triggered_from: WorkflowRunTriggeredFrom,
    ) -> WorkflowExecutionRepository:
        """
        创建工作流执行仓库实例
        
        根据配置文件中指定的实现类路径，动态创建WorkflowExecutionRepository实例。
        这个仓库负责管理工作流执行的生命周期，包括：
        1. 工作流执行记录的创建和更新
        2. 执行状态的持久化
        3. 执行结果的存储
        4. 执行历史的查询
        
        Args:
            session_factory: SQLAlchemy会话工厂或引擎，用于数据库操作
            user: 执行用户，可以是Account或EndUser
            app_id: 应用ID，标识具体的应用实例
            triggered_from: 触发来源，区分不同的执行场景
            
        Returns:
            配置好的WorkflowExecutionRepository实例
            
        Raises:
            RepositoryImportError: 当无法创建配置的仓库时
        """
        # 从配置中获取仓库实现类路径
        class_path = dify_config.CORE_WORKFLOW_EXECUTION_REPOSITORY
        logger.debug(f"Creating WorkflowExecutionRepository from: {class_path}")

        try:
            # 第一步：动态导入仓库类
            repository_class = cls._import_class(class_path)
            
            # 第二步：验证接口一致性
            cls._validate_repository_interface(repository_class, WorkflowExecutionRepository)
            
            # 第三步：验证构造函数签名
            cls._validate_constructor_signature(
                repository_class, ["session_factory", "user", "app_id", "triggered_from"]
            )

            # 第四步：创建并返回仓库实例
            return repository_class(  # type: ignore[no-any-return]
                session_factory=session_factory,
                user=user,
                app_id=app_id,
                triggered_from=triggered_from,
            )
        except RepositoryImportError:
            # 重新抛出我们的自定义错误
            raise
        except Exception as e:
            logger.exception("Failed to create WorkflowExecutionRepository")
            raise RepositoryImportError(f"Failed to create WorkflowExecutionRepository from '{class_path}': {e}") from e

    @classmethod
    def create_workflow_node_execution_repository(
        cls,
        session_factory: Union[sessionmaker, Engine],
        user: Union[Account, EndUser],
        app_id: str,
        triggered_from: WorkflowNodeExecutionTriggeredFrom,
    ) -> WorkflowNodeExecutionRepository:
        """
        Create a WorkflowNodeExecutionRepository instance based on configuration.

        Args:
            session_factory: SQLAlchemy sessionmaker or engine
            user: Account or EndUser object
            app_id: Application ID
            triggered_from: Source of the execution trigger

        Returns:
            Configured WorkflowNodeExecutionRepository instance

        Raises:
            RepositoryImportError: If the configured repository cannot be created
        """
        class_path = dify_config.CORE_WORKFLOW_NODE_EXECUTION_REPOSITORY
        logger.debug(f"Creating WorkflowNodeExecutionRepository from: {class_path}")

        try:
            repository_class = cls._import_class(class_path)
            cls._validate_repository_interface(repository_class, WorkflowNodeExecutionRepository)
            cls._validate_constructor_signature(
                repository_class, ["session_factory", "user", "app_id", "triggered_from"]
            )

            return repository_class(  # type: ignore[no-any-return]
                session_factory=session_factory,
                user=user,
                app_id=app_id,
                triggered_from=triggered_from,
            )
        except RepositoryImportError:
            # Re-raise our custom errors as-is
            raise
        except Exception as e:
            logger.exception("Failed to create WorkflowNodeExecutionRepository")
            raise RepositoryImportError(
                f"Failed to create WorkflowNodeExecutionRepository from '{class_path}': {e}"
            ) from e
