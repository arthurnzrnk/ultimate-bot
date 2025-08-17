"""Email notification stub for the Ultimate Bot.

Currently, this module simply prints email notifications to stdout. You can
wire in an actual SMTP server or third‑party provider like SendGrid by
replacing the implementation of ``notify`` with code that sends real
emails. The ``settings`` in ``app.config`` provide placeholders for SMTP
credentials and a default sender address.
"""

from .config import settings


def notify(email_to: str, subject: str, body: str) -> None:
    """Send an email notification.

    This stub prints the email to console. Modify this function to send
    real emails via SMTP or another service.

    Args:
        email_to: Recipient email address.
        subject: Email subject line.
        body: Email body.
    """
    # In a real implementation you would use smtplib or a third‑party API
    print(f"[EMAIL to {email_to}] {subject}\n{body}\n")