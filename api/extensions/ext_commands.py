from dify_app import DifyApp


def init_app(app: DifyApp):
    """
    初始化CLI命令扩展
    
    注册所有Flask CLI命令，这些命令可以通过 `flask` 命令行工具执行。
    包括系统管理、数据迁移、维护等命令。
    
    Args:
        app (DifyApp): Flask应用实例
    """
    # 导入所有CLI命令模块
    from commands import (
        add_qdrant_index,                    # 添加Qdrant向量索引
        clear_free_plan_tenant_expired_logs, # 清理免费计划租户过期日志
        clear_orphaned_file_records,         # 清理孤立文件记录
        convert_to_agent_apps,               # 转换为代理应用
        create_tenant,                       # 创建租户
        extract_plugins,                     # 提取插件
        extract_unique_plugins,              # 提取唯一插件
        fix_app_site_missing,                # 修复应用站点缺失
        install_plugins,                     # 安装插件
        migrate_data_for_plugin,             # 为插件迁移数据
        old_metadata_migration,              # 旧元数据迁移
        remove_orphaned_files_on_storage,    # 从存储中删除孤立文件
        reset_email,                         # 重置邮箱
        reset_encrypt_key_pair,              # 重置加密密钥对
        reset_password,                      # 重置密码
        setup_system_tool_oauth_client,      # 设置系统工具OAuth客户端
        upgrade_db,                          # 升级数据库
        vdb_migrate,                         # 向量数据库迁移
    )

    # 定义要注册的命令列表
    cmds_to_register = [
        reset_password,                      # 密码重置命令
        reset_email,                         # 邮箱重置命令
        reset_encrypt_key_pair,              # 加密密钥重置命令
        vdb_migrate,                         # 向量数据库迁移命令
        convert_to_agent_apps,               # 应用转换命令
        add_qdrant_index,                    # 索引添加命令
        create_tenant,                       # 租户创建命令
        upgrade_db,                          # 数据库升级命令
        fix_app_site_missing,                # 应用站点修复命令
        migrate_data_for_plugin,             # 插件数据迁移命令
        extract_plugins,                     # 插件提取命令
        extract_unique_plugins,              # 唯一插件提取命令
        install_plugins,                     # 插件安装命令
        old_metadata_migration,              # 元数据迁移命令
        clear_free_plan_tenant_expired_logs, # 日志清理命令
        clear_orphaned_file_records,         # 文件记录清理命令
        remove_orphaned_files_on_storage,    # 存储文件清理命令
        setup_system_tool_oauth_client,      # OAuth客户端设置命令
    ]
    
    # 逐个注册命令到Flask CLI
    for cmd in cmds_to_register:
        app.cli.add_command(cmd)
