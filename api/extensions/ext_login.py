import json

import flask_login  # type: ignore
from flask import Response, request
from flask_login import user_loaded_from_request, user_logged_in
from werkzeug.exceptions import NotFound, Unauthorized

from configs import dify_config
from dify_app import DifyApp
from extensions.ext_database import db
from libs.passport import PassportService
from models.account import Account, Tenant, TenantAccountJoin
from models.model import AppMCPServer, EndUser
from services.account_service import AccountService

# 创建Flask-Login管理器实例
login_manager = flask_login.LoginManager()


# Flask-Login配置
@login_manager.request_loader
def load_user_from_request(request_from_flask_login):
    """
    从请求中加载用户
    
    支持多种认证方式：
    1. 管理员API密钥认证
    2. 控制台和内部API的JWT令牌认证
    3. Web API的终端用户认证
    4. MCP协议的服务器认证
    
    Args:
        request_from_flask_login: Flask-Login传递的请求对象
        
    Returns:
        Account|EndUser: 认证成功的用户对象
        
    Raises:
        Unauthorized: 认证失败时抛出
    """
    auth_header = request.headers.get("Authorization", "")
    auth_token: str | None = None
    
    # 解析Authorization头部
    if auth_header:
        if " " not in auth_header:
            raise Unauthorized("Invalid Authorization header format. Expected 'Bearer <api-key>' format.")
        auth_scheme, auth_token = auth_header.split(maxsplit=1)
        auth_scheme = auth_scheme.lower()
        if auth_scheme != "bearer":
            raise Unauthorized("Invalid Authorization header format. Expected 'Bearer <api-key>' format.")
    else:
        # 从查询参数获取令牌（兼容旧版本）
        auth_token = request.args.get("_token")

    # 检查管理员API密钥认证（优先级最高）
    if dify_config.ADMIN_API_KEY_ENABLE and auth_header:
        admin_api_key = dify_config.ADMIN_API_KEY
        if admin_api_key and admin_api_key == auth_token:
            workspace_id = request.headers.get("X-WORKSPACE-ID")
            if workspace_id:
                # 查找指定工作空间的拥有者账户
                tenant_account_join = (
                    db.session.query(Tenant, TenantAccountJoin)
                    .filter(Tenant.id == workspace_id)
                    .filter(TenantAccountJoin.tenant_id == Tenant.id)
                    .filter(TenantAccountJoin.role == "owner")
                    .one_or_none()
                )
                if tenant_account_join:
                    tenant, ta = tenant_account_join
                    account = db.session.query(Account).filter_by(id=ta.account_id).first()
                    if account:
                        account.current_tenant = tenant
                        return account

    # 根据蓝图类型进行不同的认证
    if request.blueprint in {"console", "inner_api"}:
        # 控制台和内部API认证
        if not auth_token:
            raise Unauthorized("Invalid Authorization token.")
        
        # 验证JWT令牌
        decoded = PassportService().verify(auth_token)
        user_id = decoded.get("user_id")
        source = decoded.get("token_source")
        
        # 检查令牌来源（防止使用错误的令牌类型）
        if source:
            raise Unauthorized("Invalid Authorization token.")
        if not user_id:
            raise Unauthorized("Invalid Authorization token.")

        # 加载已登录的账户
        logged_in_account = AccountService.load_logged_in_account(account_id=user_id)
        return logged_in_account
        
    elif request.blueprint == "web":
        # Web API终端用户认证
        decoded = PassportService().verify(auth_token)
        end_user_id = decoded.get("end_user_id")
        if not end_user_id:
            raise Unauthorized("Invalid Authorization token.")
        
        # 查找终端用户
        end_user = db.session.query(EndUser).filter(EndUser.id == decoded["end_user_id"]).first()
        if not end_user:
            raise NotFound("End user not found.")
        return end_user
        
    elif request.blueprint == "mcp":
        # MCP协议服务器认证
        server_code = request.view_args.get("server_code") if request.view_args else None
        if not server_code:
            raise Unauthorized("Invalid Authorization token.")
        
        # 查找MCP服务器
        app_mcp_server = db.session.query(AppMCPServer).filter(AppMCPServer.server_code == server_code).first()
        if not app_mcp_server:
            raise NotFound("App MCP server not found.")
        
        # 查找对应的终端用户
        end_user = (
            db.session.query(EndUser)
            .filter(EndUser.external_user_id == app_mcp_server.id, EndUser.type == "mcp")
            .first()
        )
        if not end_user:
            raise NotFound("End user not found.")
        return end_user


@user_logged_in.connect
@user_loaded_from_request.connect
def on_user_logged_in(_sender, user):
    """
    用户登录事件处理器
    
    当用户登录时调用。注意：AccountService.load_logged_in_account 
    会通过load_user方法填充user.current_tenant_id，该方法调用account.set_tenant_id()。
    
    Args:
        _sender: 事件发送者
        user: 登录的用户对象
    """
    # tenant_id上下文变量已移除 - 直接使用current_user.current_tenant_id
    pass


@login_manager.unauthorized_handler
def unauthorized_handler():
    """
    处理未授权请求
    
    当用户访问需要认证的页面但未提供有效凭据时调用。
    
    Returns:
        Response: 401未授权响应
    """
    return Response(
        json.dumps({"code": "unauthorized", "message": "Unauthorized."}),
        status=401,
        content_type="application/json",
    )


def init_app(app: DifyApp):
    """
    初始化用户认证扩展
    
    配置Flask-Login，设置用户加载器和未授权处理器。
    
    Args:
        app (DifyApp): Flask应用实例
    """
    login_manager.init_app(app)
