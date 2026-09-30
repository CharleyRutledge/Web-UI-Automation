from __future__ import annotations

import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
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
) -> None:
    if not email.enabled or not email.smtp_host or not email.to_addrs:
        return
    if not email.from_addr:
        raise ValueError("notifications.email.from_addr is required when email is enabled")
    if _is_unresolved(email.smtp_user) or _is_unresolved(email.smtp_password):
        print("Email notification skipped: smtp_user/smtp_password not resolved (missing env vars)")
        return

    subject = f"[UI Automation] {summary.short_status()}"
    body_lines = [
        summary.short_status(),
        "",
        f"Report: {summary.report_html or 'n/a'}",
        f"Run folder: {summary.run_dir or 'n/a'}",
        f"Videos: {len(summary.video_files)}",
    ]
    if summary.video_files:
        body_lines.append("Video files:")
        for v in summary.video_files[:20]:
            body_lines.append(f"  - {v}")
    if ai_summary:
        body_lines.extend(["", "--- Claude analysis ---", ai_summary])

    msg = MIMEMultipart()
    msg["Subject"] = subject
    msg["From"] = email.from_addr
    msg["To"] = ", ".join(email.to_addrs)
    msg.attach(MIMEText("\n".join(body_lines), "plain", "utf-8"))

    if email.smtp_port == 465:
        smtp_cm = smtplib.SMTP_SSL(email.smtp_host, email.smtp_port, timeout=30)
    else:
        smtp_cm = smtplib.SMTP(email.smtp_host, email.smtp_port, timeout=30)
    with smtp_cm as server:
        if email.use_tls and email.smtp_port != 465:
            server.starttls()
        if email.smtp_user:
            server.login(email.smtp_user, email.smtp_password)
        server.sendmail(email.from_addr, list(email.to_addrs), msg.as_string())
