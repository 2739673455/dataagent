"""邮件发送 — SMTP 实现"""

from email.header import Header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr

import aiosmtplib

from app.core.settings import EmailCfg
from app.domain.ports import EmailSender


class SmtpEmailSender(EmailSender):
    """SMTP 邮件发送"""

    def __init__(self, email_config: EmailCfg) -> None:
        self._cfg = email_config

    async def send_verification_code(self, to: str, code: str, code_type: str) -> None:
        """发送验证码邮件"""
        type_text = {
            "register": "注册",
            "reset_email": "重置邮箱",
            "reset_password": "重置密码",
        }[code_type]

        subject = f"您的{type_text}验证码"
        html = f"""\
<html>
<body style="font-family: Arial, sans-serif; padding: 20px;">
    <h2 style="color: #333;">{type_text}验证码</h2>
    <p>您的{type_text}验证码是：</p>
    <p style="font-size: 24px; font-weight: bold; color: #007bff; letter-spacing: 4px;">
        {code}
    </p>
    <p style="color: #666; font-size: 14px;">
        验证码有效期为 10 分钟，请尽快使用。
    </p>
    <p style="color: #999; font-size: 12px;">
        如果您没有进行此操作，请忽略此邮件。
    </p>
</body>
</html>"""

        msg = MIMEMultipart()
        msg["From"] = (
            formataddr(
                (
                    Header(self._cfg.from_name, "utf-8").encode(),
                    self._cfg.from_email,
                )
            )
            if self._cfg.from_name
            else self._cfg.from_email
        )
        msg["To"] = to
        msg["Subject"] = subject
        msg.attach(MIMEText(html, "html", "utf-8"))

        await aiosmtplib.send(
            msg,
            hostname=self._cfg.smtp_host,
            port=self._cfg.smtp_port,
            username=self._cfg.smtp_user,
            password=self._cfg.smtp_password,
            use_tls=True,
        )
