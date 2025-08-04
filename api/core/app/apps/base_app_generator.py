import json
from collections.abc import Generator, Mapping, Sequence
from typing import TYPE_CHECKING, Any, Optional, Union, final

from sqlalchemy.orm import Session

from core.app.app_config.entities import VariableEntityType
from core.app.entities.app_invoke_entities import InvokeFrom
from core.file import File, FileUploadConfig
from core.workflow.nodes.enums import NodeType
from core.workflow.repositories.draft_variable_repository import (
    DraftVariableSaver,
    DraftVariableSaverFactory,
    NoopDraftVariableSaver,
)
from factories import file_factory
from services.workflow_draft_variable_service import DraftVariableSaver as DraftVariableSaverImpl

if TYPE_CHECKING:
    from core.app.app_config.entities import VariableEntity


class BaseAppGenerator:
    """
    基础应用生成器
    
    为所有类型的应用生成器提供通用功能，包括：
    1. 用户输入验证和处理
    2. 文件类型转换
    3. 变量验证和清理
    4. 草稿变量保存工厂管理
    
    这是一个抽象基类，定义了应用生成的通用接口和实现。
    """
    
    def _prepare_user_inputs(
        self,
        *,
        user_inputs: Optional[Mapping[str, Any]],
        variables: Sequence["VariableEntity"],
        tenant_id: str,
        strict_type_validation: bool = False,
    ) -> Mapping[str, Any]:
        """
        准备和验证用户输入
        
        这个方法是所有应用生成器的核心输入处理逻辑，负责：
        1. 根据变量配置验证用户输入
        2. 处理必填字段、默认值和选项值
        3. 将文件映射转换为File对象
        4. 清理和标准化输入值
        
        Args:
            user_inputs: 原始用户输入映射
            variables: 变量实体序列，定义了输入的结构和约束
            tenant_id: 租户ID，用于文件处理
            strict_type_validation: 是否启用严格类型验证
            
        Returns:
            处理后的用户输入映射，包含验证过的值和转换后的文件对象
            
        Raises:
            ValueError: 当输入验证失败或类型不匹配时
        """
        user_inputs = user_inputs or {}
        
        # 第一步：根据表单配置过滤输入变量，处理必填字段、默认值和选项值
        user_inputs = {
            var.variable: self._validate_inputs(value=user_inputs.get(var.variable), variable_entity=var)
            for var in variables
        }
        
        # 第二步：清理输入值（移除空字符等）
        user_inputs = {k: self._sanitize_value(v) for k, v in user_inputs.items()}
        
        # 第三步：文件处理 - 将输入中的文件转换为File对象
        entity_dictionary = {item.variable: item for item in variables}
        
        # 转换单个文件为File对象
        files_inputs = {
            k: file_factory.build_from_mapping(
                mapping=v,
                tenant_id=tenant_id,
                config=FileUploadConfig(
                    allowed_file_types=entity_dictionary[k].allowed_file_types,
                    allowed_file_extensions=entity_dictionary[k].allowed_file_extensions,
                    allowed_file_upload_methods=entity_dictionary[k].allowed_file_upload_methods,
                ),
                strict_type_validation=strict_type_validation,
            )
            for k, v in user_inputs.items()
            if isinstance(v, dict) and entity_dictionary[k].type == VariableEntityType.FILE
        }
        
        # 转换文件列表为File对象列表
        file_list_inputs = {
            k: file_factory.build_from_mappings(
                mappings=v,
                tenant_id=tenant_id,
                config=FileUploadConfig(
                    allowed_file_types=entity_dictionary[k].allowed_file_types,
                    allowed_file_extensions=entity_dictionary[k].allowed_file_extensions,
                    allowed_file_upload_methods=entity_dictionary[k].allowed_file_upload_methods,
                ),
            )
            for k, v in user_inputs.items()
            if isinstance(v, list)
            # 确保跳过List<File>类型
            and all(isinstance(item, dict) for item in v)
            and entity_dictionary[k].type == VariableEntityType.FILE_LIST
        }
        
        # 第四步：合并所有输入
        user_inputs = {**user_inputs, **files_inputs, **file_list_inputs}

        # 第五步：验证所有文件都已正确转换为File对象
        if any(filter(lambda v: isinstance(v, dict), user_inputs.values())):
            raise ValueError("Invalid input type")
        if any(
            filter(lambda v: isinstance(v, dict), filter(lambda item: isinstance(item, list), user_inputs.values()))
        ):
            raise ValueError("Invalid input type")

        return user_inputs

    def _validate_inputs(
        self,
        *,
        variable_entity: "VariableEntity",
        value: Any,
    ):
        if value is None:
            if variable_entity.required:
                raise ValueError(f"{variable_entity.variable} is required in input form")
            return value

        if variable_entity.type in {
            VariableEntityType.TEXT_INPUT,
            VariableEntityType.SELECT,
            VariableEntityType.PARAGRAPH,
        } and not isinstance(value, str):
            raise ValueError(
                f"(type '{variable_entity.type}') {variable_entity.variable} in input form must be a string"
            )

        if variable_entity.type == VariableEntityType.NUMBER and isinstance(value, str):
            # handle empty string case
            if not value.strip():
                return None
            # may raise ValueError if user_input_value is not a valid number
            try:
                if "." in value:
                    return float(value)
                else:
                    return int(value)
            except ValueError:
                raise ValueError(f"{variable_entity.variable} in input form must be a valid number")

        match variable_entity.type:
            case VariableEntityType.SELECT:
                if value not in variable_entity.options:
                    raise ValueError(
                        f"{variable_entity.variable} in input form must be one of the following: "
                        f"{variable_entity.options}"
                    )
            case VariableEntityType.TEXT_INPUT | VariableEntityType.PARAGRAPH:
                if variable_entity.max_length and len(value) > variable_entity.max_length:
                    raise ValueError(
                        f"{variable_entity.variable} in input form must be less than {variable_entity.max_length} "
                        "characters"
                    )
            case VariableEntityType.FILE:
                if not isinstance(value, dict) and not isinstance(value, File):
                    raise ValueError(f"{variable_entity.variable} in input form must be a file")
            case VariableEntityType.FILE_LIST:
                # if number of files exceeds the limit, raise ValueError
                if not (
                    isinstance(value, list)
                    and (all(isinstance(item, dict) for item in value) or all(isinstance(item, File) for item in value))
                ):
                    raise ValueError(f"{variable_entity.variable} in input form must be a list of files")

                if variable_entity.max_length and len(value) > variable_entity.max_length:
                    raise ValueError(
                        f"{variable_entity.variable} in input form must be less than {variable_entity.max_length} files"
                    )

        return value

    def _sanitize_value(self, value: Any) -> Any:
        if isinstance(value, str):
            return value.replace("\x00", "")
        return value

    @classmethod
    def convert_to_event_stream(cls, generator: Union[Mapping, Generator[Mapping | str, None, None]]):
        """
        Convert messages into event stream
        """
        if isinstance(generator, dict):
            return generator
        else:

            def gen():
                for message in generator:
                    if isinstance(message, Mapping | dict):
                        yield f"data: {json.dumps(message)}\n\n"
                    else:
                        yield f"event: {message}\n\n"

            return gen()

    @final
    @staticmethod
    def _get_draft_var_saver_factory(invoke_from: InvokeFrom) -> DraftVariableSaverFactory:
        if invoke_from == InvokeFrom.DEBUGGER:

            def draft_var_saver_factory(
                session: Session,
                app_id: str,
                node_id: str,
                node_type: NodeType,
                node_execution_id: str,
                enclosing_node_id: str | None = None,
            ) -> DraftVariableSaver:
                return DraftVariableSaverImpl(
                    session=session,
                    app_id=app_id,
                    node_id=node_id,
                    node_type=node_type,
                    node_execution_id=node_execution_id,
                    enclosing_node_id=enclosing_node_id,
                )
        else:

            def draft_var_saver_factory(
                session: Session,
                app_id: str,
                node_id: str,
                node_type: NodeType,
                node_execution_id: str,
                enclosing_node_id: str | None = None,
            ) -> DraftVariableSaver:
                return NoopDraftVariableSaver()

        return draft_var_saver_factory
