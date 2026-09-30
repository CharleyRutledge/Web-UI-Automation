from __future__ import annotations

import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ui_automation.config import EmailSettings
    from ui_automation.reporting.summary import RunSummary


def _is_unresolved(value: str) -> bool:
    return "${" in value


def send_run_email(
    summary: RunSummary,
    email: EmailSettings,
    *,
    ai_summary: str | None = None,
    attachment: Path | None = None,
) -> None:
    # ai_summary is part of the attached report.
    if not email.enabled or not email.smtp_host or not email.to_addrs:
        return
    if not email.from_addr:
        raise ValueError("notifications.email.from_addr is required when email is enabled")
    if _is_unresolved(email.smtp_user) or _is_unresolved(email.smtp_password):
        print("Email notification skipped: smtp_user/smtp_password not resolved (missing env vars)")
        return

    subject = f"[UI Automation] {summary.headline()}"
    body_lines = [summary.headline(), ""]
    for test in summary.problems[:10]:
        where = f' at step "{test.last_step}"' if test.last_step else ""
        body_lines.append(f"- {test.title}{where}: {test.message or 'no error message'}")
    if summary.problems:
        body_lines.append("")
    body_lines.append("Full details are in the attached report.")
    if summary.run_url:
        body_lines.append(f"CI run: {summary.run_url}")

    msg = MIMEMultipart()
    msg["Subject"] = subject
    msg["From"] = email.from_addr
    msg["To"] = ", ".join(email.to_addrs)
    msg.attach(MIMEText("\n".join(body_lines), "plain", "utf-8"))
    if attachment is not None and attachment.is_file():
        part = MIMEText(attachment.read_text(encoding="utf-8"), "html", "utf-8")
        part.add_header("Content-Disposition", "attachment", filename="ui-test-report.html")
        msg.attach(part)

    # smtplib does not verify server certificates unless given a context; without this the SMTP
    # password can be intercepted by anyone able to sit between us and the mail server.
    tls_context = ssl.create_default_context()
    implicit_tls = email.use_ssl if email.use_ssl is not None else email.smtp_port == 465
    if implicit_tls:
        smtp_cm = smtplib.SMTP_SSL(
            email.smtp_host, email.smtp_port, timeout=email.timeout_seconds, context=tls_context
        )
    else:
        smtp_cm = smtplib.SMTP(email.smtp_host, email.smtp_port, timeout=email.timeout_seconds)
    with smtp_cm as server:
        if email.use_tls and not implicit_tls:
            server.starttls(context=tls_context)
        if email.smtp_user:
            server.login(email.smtp_user, email.smtp_password)
        refused = server.sendmail(email.from_addr, list(email.to_addrs), msg.as_string())
    if refused:
        # sendmail only raises when every recipient is refused; otherwise it returns the ones it dropped.
        who = ", ".join(f"{addr} ({code} {reply.decode(errors='replace')})" for addr, (code, reply) in refused.items())
        print(f"Email not delivered to: {who}")
