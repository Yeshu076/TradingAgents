import os
import redis
import json
import time
import requests
import threading
from datetime import datetime, timedelta
from dotenv import load_settings, load_dotenv

load_dotenv()

REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD")
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")

# Heartbeat tracking dictionary
service_heartbeats = {
    "Dhan_Bot": datetime.now(),
    "Delta_Algo": datetime.now(),
    "MT5_Bridge": datetime.now()
}

def send_discord_message(content, title="Notification", color=3447003):
    if not DISCORD_WEBHOOK_URL:
        print("DISCORD_WEBHOOK_URL not set!")
        return

    data = {
        "embeds": [
            {
                "title": title,
                "description": content,
                "color": color
            }
        ]
    }
    try:
        response = requests.post(DISCORD_WEBHOOK_URL, json=data)
        response.raise_for_status()
    except Exception as e:
        print(f"Failed to send to Discord: {e}")


def heartbeat_monitor():
    """Background thread to monitor service heartbeats."""
    print("Heartbeat monitor started.")
    alerted_services = set()
    while True:
        time.sleep(60) # Check every minute
        now = datetime.now()
        for service, last_seen in service_heartbeats.items():
            # If a service hasn't checked in for 15 minutes, blast an alert
            if now - last_seen > timedelta(minutes=15):
                if service not in alerted_services:
                    msg = f"**CRITICAL:** `{service}` has not sent a heartbeat in over 15 minutes. It may be offline or frozen!"
                    print(msg)
                    send_discord_message(content=msg, title="🚨 Watchdog Alert 🚨", color=15158332)
                    alerted_services.add(service)
            else:
                # If it recovered, remove from alerted set
                if service in alerted_services:
                    msg = f"**RECOVERY:** `{service}` is back online and sending heartbeats."
                    print(msg)
                    send_discord_message(content=msg, title="✅ Service Recovered", color=3066993)
                    alerted_services.remove(service)


def main():
    print("Starting Notifier Service...")
    threading.Thread(target=heartbeat_monitor, daemon=True).start()

    while True:
        try:
            r = redis.Redis(
                host=REDIS_HOST,
                port=REDIS_PORT,
                password=REDIS_PASSWORD,
                decode_responses=True
            )
            r.ping()
            print("Connected to Redis for Notifications.")

            pubsub = r.pubsub()
            pubsub.subscribe("SYSTEM_ALERTS", "TRADE_SETTLEMENTS", "HEARTBEATS")

            for message in pubsub.listen():
                if message['type'] == 'message':
                    channel = message['channel']
                    try:
                        data = json.loads(message['data'])
                        if channel == "HEARTBEATS":
                            service_name = data.get("service")
                            if service_name:
                                service_heartbeats[service_name] = datetime.now()
                        elif channel == "SYSTEM_ALERTS":
                            send_discord_message(
                                content=f"**Alert Level:** {data.get('level', 'INFO')}\n**Message:** {data.get('message', '')}",
                                title="System Alert",
                                color=15158332 if data.get('level') in ['CRITICAL', 'ERROR'] else 3066993
                            )
                        elif channel == "TRADE_SETTLEMENTS":
                            pnl = data.get('pnl', 0)
                            color = 3066993 if float(pnl) >= 0 else 15158332
                            send_discord_message(
                                content=f"**Symbol:** {data.get('symbol')}\n**Action:** {data.get('action')}\n**PnL:** {pnl}",
                                title="Trade Settlement",
                                color=color
                            )
                    except json.JSONDecodeError:
                        send_discord_message(content=str(message['data']), title=f"Raw msg on {channel}")
                    
        except redis.ConnectionError as e:
            print(f"Redis connection error: {e}. Retrying in 5s...")
            time.sleep(5)
        except Exception as e:
            print(f"Unexpected error: {e}")
            time.sleep(5)

if __name__ == "__main__":
    main()