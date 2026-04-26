import os
import requests
import logging
from typing import Optional

logger = logging.getLogger(__name__)

DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
PAGERDUTY_ROUTING_KEY = os.getenv("PAGERDUTY_ROUTING_KEY")  # Added for critical alerts

def send_notification(message: str, level: str = "INFO") -> None:
    """
    General purpose notification for trade fills, info, warnings.
    """
    prefix = f"[{level.upper()}] "
    formatted_msg = prefix + message
    
    # Send to Discord
    if DISCORD_WEBHOOK_URL:
        try:
            payload = {"content": formatted_msg}
            requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=5)
        except Exception as e:
            logger.error(f"Failed to send Discord notification: {e}")
            
    # Send to Telegram
    if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
            payload = {"chat_id": TELEGRAM_CHAT_ID, "text": formatted_msg}
            requests.post(url, json=payload, timeout=5)
        except Exception as e:
            logger.error(f"Failed to send Telegram notification: {e}")
            
    logger.info(f"Notification Sent: {formatted_msg}")

def send_critical_alert(summary: str, source: str = "TradingAgent", details: Optional[dict] = None) -> None:
    """
    Triggers a PagerDuty Incident (or high-priority Slack/Discord ping) 
    designed to wake up an on-call engineer during a SYSTEM_HALT or severe failure.
    """
    logger.critical(f"🚨 CRITICAL ALERT TRIGGERED: {summary} 🚨")
    
    # Fallback to standard high-priority message if PagerDuty isn't configured
    send_notification(f"🚨 CRITICAL: {summary}", level="CRITICAL")

    # PagerDuty Events API v2
    if PAGERDUTY_ROUTING_KEY:
        try:
            payload = {
                "routing_key": PAGERDUTY_ROUTING_KEY,
                "event_action": "trigger",
                "payload": {
                    "summary": summary,
                    "source": source,
                    "severity": "critical",
                    "custom_details": details or {}
                }
            }
            resp = requests.post("https://events.pagerduty.com/v2/enqueue", json=payload, timeout=5)
            resp.raise_for_status()
            logger.info("PagerDuty incident created successfully.")
        except Exception as e:
            logger.error(f"Failed to trigger PagerDuty alert: {e}")
