import logging
from typing import Optional

from flask import Flask

from configs import dify_config
from dify_app import DifyApp


class Mail:
    """
    邮件服务类
    
    支持多种邮件服务提供商：
    - Resend: 现代化的邮件API服务
    - SMTP: 传统SMTP服务器
    - SendGrid: 企业级邮件服务
    
    提供统一的邮件发送接口，自动处理不同提供商的配置差异。
    """
    
    def __init__(self):
        """初始化邮件服务"""
        self._client = None
        self._default_send_from = None

    def is_inited(self) -> bool:
        """
        检查邮件服务是否已初始化
        
        Returns:
            bool: 如果已初始化返回True，否则返回False
        """
        return self._client is not None

    def init_app(self, app: Flask):
        """
        初始化邮件服务
        
        根据配置的邮件类型初始化相应的邮件客户端。
        
        Args:
            app (Flask): Flask应用实例
            
        Raises:
            ValueError: 配置错误时抛出
        """
        mail_type = dify_config.MAIL_TYPE
        if not mail_type:
            logging.warning("MAIL_TYPE is not set")
            return

        # 设置默认发件人
        if dify_config.MAIL_DEFAULT_SEND_FROM:
            self._default_send_from = dify_config.MAIL_DEFAULT_SEND_FROM

        # 根据邮件类型初始化相应的客户端
        match mail_type:
            case "resend":
                # Resend邮件服务配置
                import resend

                api_key = dify_config.RESEND_API_KEY
                if not api_key:
                    raise ValueError("RESEND_API_KEY is not set")

                # 设置自定义API URL（如果配置了）
                api_url = dify_config.RESEND_API_URL
                if api_url:
                    resend.api_url = api_url

                resend.api_key = api_key
                self._client = resend.Emails
                
            case "smtp":
                # SMTP邮件服务配置
                from libs.smtp import SMTPClient

                if not dify_config.SMTP_SERVER or not dify_config.SMTP_PORT:
                    raise ValueError("SMTP_SERVER and SMTP_PORT are required for smtp mail type")
                if not dify_config.SMTP_USE_TLS and dify_config.SMTP_OPPORTUNISTIC_TLS:
                    raise ValueError("SMTP_OPPORTUNISTIC_TLS is not supported without enabling SMTP_USE_TLS")
                
                self._client = SMTPClient(
                    server=dify_config.SMTP_SERVER,
                    port=dify_config.SMTP_PORT,
                    username=dify_config.SMTP_USERNAME or "",
                    password=dify_config.SMTP_PASSWORD or "",
                    _from=dify_config.MAIL_DEFAULT_SEND_FROM or "",
                    use_tls=dify_config.SMTP_USE_TLS,
                    opportunistic_tls=dify_config.SMTP_OPPORTUNISTIC_TLS,
                )
                
            case "sendgrid":
                # SendGrid邮件服务配置
                from libs.sendgrid import SendGridClient

                if not dify_config.SENDGRID_API_KEY:
                    raise ValueError("SENDGRID_API_KEY is required for SendGrid mail type")

                self._client = SendGridClient(
                    sendgrid_api_key=dify_config.SENDGRID_API_KEY, 
                    _from=dify_config.MAIL_DEFAULT_SEND_FROM or ""
                )
                
            case _:
                raise ValueError("Unsupported mail type {}".format(mail_type))

    def send(self, to: str, subject: str, html: str, from_: Optional[str] = None):
        """
        发送邮件
        
        Args:
            to (str): 收件人邮箱地址
            subject (str): 邮件主题
            html (str): 邮件HTML内容
            from_ (Optional[str]): 发件人邮箱地址，如果未提供则使用默认发件人
            
        Raises:
            ValueError: 参数错误或邮件服务未初始化时抛出
        """
        if not self._client:
            raise ValueError("Mail client is not initialized")

        # 设置发件人
        if not from_ and self._default_send_from:
            from_ = self._default_send_from

        # 参数验证
        if not from_:
            raise ValueError("mail from is not set")

        if not to:
            raise ValueError("mail to is not set")

        if not subject:
            raise ValueError("mail subject is not set")

        if not html:
            raise ValueError("mail html is not set")

        # 发送邮件
        self._client.send(
            {
                "from": from_,
                "to": to,
                "subject": subject,
                "html": html,
            }
        )


def is_enabled() -> bool:
    """
    检查邮件服务是否启用
    
    Returns:
        bool: 如果配置了邮件类型且不为空返回True，否则返回False
    """
    return dify_config.MAIL_TYPE is not None and dify_config.MAIL_TYPE != ""


def init_app(app: DifyApp):
    """
    初始化邮件扩展
    
    创建邮件服务实例并初始化。
    
    Args:
        app (DifyApp): Flask应用实例
    """
    mail.init_app(app)


# 创建全局邮件服务实例
mail = Mail()
