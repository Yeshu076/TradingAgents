import time
import logging
import json
import os
from typing import Dict

logger = logging.getLogger("liveness_monitor")


class DataLivenessMonitor:
    """
    Monitors data feed timestamps to prevent the trading agents from making decisions
    based on stale / frozen market data.
    """
    _instance = None

    def __init__(self):
        self.feed_timestamps: Dict[str, float] = {}
        # Max seconds without a tick before a feed is considered dead
        self.stale_thresholds = {
            "dhan_nifty": 5.0,     # High frequency options
            "delta_btc": 15.0,     # Crypto options
            "mt5_xauusd": 10.0,    # Forex
            "default": 30.0
        }
        # GAP-07: Graceful Redis connection — don't crash if Redis is unavailable
        self.redis_client = None
        try:
            import redis as _redis
            self.redis_client = _redis.Redis(
                host=os.getenv("REDIS_HOST", "127.0.0.1"),
                port=int(os.getenv("REDIS_PORT", "6379")),
                password=os.getenv("REDIS_PASSWORD"),
                decode_responses=True,
                socket_connect_timeout=3,
            )
            self.redis_client.ping()
        except Exception as e:
            logger.warning(f"DataLivenessMonitor: Redis unavailable ({e}). SYSTEM_HALT publishing disabled.")
            self.redis_client = None

    @classmethod
    def get_instance(cls):
        if not cls._instance:
            cls._instance = cls()
        return cls._instance

    def ping(self, feed_name: str):
        """Called by WebSocket/Polling handlers whenever a new price quote arrives."""
        self.feed_timestamps[feed_name] = time.time()

    def is_feed_alive(self, feed_name: str) -> bool:
        """Check if a specific feed is alive."""
        last_ping = self.feed_timestamps.get(feed_name, 0.0)
        if last_ping == 0.0:
            return False  # Never received data

        threshold = self.stale_thresholds.get(feed_name, self.stale_thresholds["default"])
        time_since_last_tick = time.time() - last_ping

        if time_since_last_tick > threshold:
            logger.warning(f"[STALE DATA] {feed_name} feed hasn't ticked in {time_since_last_tick:.2f}s (Threshold: {threshold}s)")
            return False

        return True

    def validate_all_feeds_or_halt(self) -> bool:
        """
        Checks all active feeds. If any primary feed is dead, triggers a system-wide halt
        to prevent execution on disjointed cross-market signals.

        GAP-07: On cold start (no feeds have ever pinged), returns False with a warning
        instead of vacuously passing. This prevents trading on zero data.
        """
        # Cold-start guard: if no feeds have ever pinged, block execution
        if not self.feed_timestamps:
            logger.warning(
                "[LIVENESS] No data feeds have reported yet. "
                "Blocking execution until at least one feed is alive."
            )
            return False

        stale_feeds = []
        for feed, last_ping in self.feed_timestamps.items():
            threshold = self.stale_thresholds.get(feed, self.stale_thresholds["default"])
            if time.time() - last_ping > threshold:
                stale_feeds.append(feed)

        if stale_feeds:
            reason = f"STALE_DATA_ON_{','.join(stale_feeds)}"
            logger.critical(f"🚨 FREEZING EXECUTION: {reason}")
            if self.redis_client:
                try:
                    self.redis_client.publish(
                        "SYSTEM_HALT",
                        json.dumps({"reason": reason, "timestamp": time.time()})
                    )
                except Exception as e:
                    logger.error(f"Failed to publish SYSTEM_HALT to Redis: {e}")
            return False
        return True
