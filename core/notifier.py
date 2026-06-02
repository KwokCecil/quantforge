from abc import ABC, abstractmethod
from datetime import datetime
import smtplib
from email.mime.text import MIMEText

import requests
from loguru import logger

try:
    from quantforge.tokens.wechat_webhook import webhook_url
    from quantforge.tokens.email_config import sender as email_sender, receiver as email_receiver, authorization_code
except ModuleNotFoundError:
    from tokens.wechat_webhook import webhook_url
    from tokens.email_config import sender as email_sender, receiver as email_receiver, authorization_code


class Notifier(ABC):
    @abstractmethod
    def notify(self, title: str, content: str, level: str = "info"):
        pass


class WeChatNotifier(Notifier):
    """企业微信群机器人通知。通过 webhook_url 发送文本消息。"""

    def __init__(self):
        self.webhook_url = webhook_url

    def notify(self, title: str, content: str, level: str = "info"):
        now = datetime.now().strftime('%H:%M')
        message = {
            "msgtype": "text",
            "text": {"content": f"【{title}】{now}\n{content}"}
        }
        try:
            resp = requests.post(self.webhook_url, json=message, timeout=10)
            if resp.status_code != 200 or resp.json().get('errcode', 0) != 0:
                logger.error(f"微信通知发送失败: {resp.text}")
        except Exception as e:
            logger.error(f"微信通知异常: {e}")


class EmailNotifier(Notifier):
    """163邮箱SMTP通知。SSL连接，端口465。"""

    def __init__(self):
        self.sender = email_sender
        self.receiver = email_receiver
        self.auth_code = authorization_code

    def notify(self, title: str, content: str, level: str = "info"):
        msg = MIMEText(content, 'plain', 'utf-8')
        msg['Subject'] = 'QuantForge 监控提醒'
        msg['From'] = self.sender
        msg['To'] = ', '.join(self.receiver)

        try:
            with smtplib.SMTP_SSL('smtp.163.com', 465, timeout=10) as server:
                server.login(self.sender, self.auth_code)
                server.sendmail(self.sender, self.receiver, msg.as_string())
        except Exception as e:
            logger.error(f"邮件通知异常: {e}")
