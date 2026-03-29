import requests

from app.utils.config import Settings
from app.utils.logger import get_logger


class AlertService:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._logger = get_logger(__name__)

    def send_tool_deployed_alert(self, tool_name: str, port: int) -> None:
        subject = f"Tool Deployed: {tool_name}"
        html = f"<p>Tool <b>{tool_name}</b> is running on port <b>{port}</b>.</p>"
        self._send(subject, html)

    def send_build_failed_alert(self, request_id: str, reason: str) -> None:
        subject = f"Tool Build Failed: {request_id}"
        html = f"<p>Tool build failed for request <b>{request_id}</b>.</p><pre>{reason}</pre>"
        self._send(subject, html)

    def send_tool_crashed_alert(self, tool_name: str, container_id: str) -> None:
        subject = f"Tool Crashed: {tool_name}"
        html = f"<p>Tool <b>{tool_name}</b> crashed. Container ID: <b>{container_id}</b></p>"
        self._send(subject, html)

    def _send(self, subject: str, html_content: str) -> None:
        if not self._settings.brevo_api_key:
            self._logger.warning("Brevo API key is not configured; skipping alert: %s", subject)
            return
        if not self._settings.brevo_sender_email or not self._settings.alert_recipient_email:
            self._logger.warning("Brevo sender/recipient is not configured; skipping alert: %s", subject)
            return

        payload = {
            "sender": {"email": self._settings.brevo_sender_email},
            "to": [{"email": self._settings.alert_recipient_email}],
            "subject": subject,
            "htmlContent": html_content,
        }
        headers = {
            "accept": "application/json",
            "api-key": self._settings.brevo_api_key,
            "content-type": "application/json",
        }
        try:
            requests.post(
                "https://api.brevo.com/v3/smtp/email",
                json=payload,
                headers=headers,
                timeout=15,
            ).raise_for_status()
        except requests.RequestException as exc:
            self._logger.error("Failed to send Brevo alert: %s", exc)

