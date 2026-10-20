"""Notification fan-out. Run as: python -m vigil.notifier

Every notification lands in the in-app inbox (Postgres). It is additionally sent
to ntfy (NTFY_URL) and Telegram (TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID) when set.
"""

from __future__ import annotations

import json
import logging
import signal
import time
from urllib.parse import urlsplit, urlunsplit

import httpx
from prometheus_client import start_http_server

from . import queue
from .config import Settings, load_settings
from .metrics import NOTIFICATIONS_TOTAL

log = logging.getLogger("vigil.notifier")


def ntfy_target(url: str) -> tuple[str, str] | None:
    """Split https://ntfy.sh/<topic> into (base, topic) for JSON publishing."""
    parts = urlsplit(url)
    path = parts.path.strip("/")
    if parts.scheme not in ("http", "https") or not path:
        return None
    base_path, _, topic = path.rpartition("/")
    base = urlunsplit((parts.scheme, parts.netloc, "/" + base_path if base_path else "", "", ""))
    return base, topic


class Notifier:
    def __init__(self, store, r, settings: Settings, http: httpx.Client | None = None):
        self.store = store
        self.r = r
        self.settings = settings
        self.http = http or httpx.Client(timeout=10.0)
        self.stopping = False

    def _send_ntfy(self, title: str, body: str) -> bool:
        target = ntfy_target(self.settings.ntfy_url)
        if not target:
            return False
        base, topic = target
        # JSON publishing keeps non-ASCII titles (e.g. ₹) intact, unlike HTTP headers.
        resp = self.http.post(
            base or self.settings.ntfy_url, json={"topic": topic, "title": title, "message": body, "tags": ["eyes"]}
        )
        resp.raise_for_status()
        return True

    def _send_telegram(self, title: str, body: str) -> bool:
        token, chat = self.settings.telegram_bot_token, self.settings.telegram_chat_id
        if not (token and chat):
            return False
        resp = self.http.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat, "text": f"{title}\n\n{body}", "disable_web_page_preview": True},
        )
        resp.raise_for_status()
        return True

    def deliver(self, title: str, body: str) -> list[str]:
        """Push to the external channels (each at most once). Returns the channels that worked."""
        delivered: list[str] = []
        for channel, send in (("ntfy", self._send_ntfy), ("telegram", self._send_telegram)):
            try:
                if send(title, body):
                    delivered.append(channel)
                    NOTIFICATIONS_TOTAL.labels(channel=channel).inc()
            except Exception as exc:  # never log URLs: the Telegram one contains the bot token
                log.warning("%s delivery failed: %s", channel, type(exc).__name__)
        return delivered

    def process(self, msg: dict) -> dict:
        title = str(msg.get("title") or "Vigil")[:300]
        body = str(msg.get("body") or "")[:4000]
        # A message re-queued after a database outage was already pushed to ntfy/Telegram:
        # don't page the user twice, only store it in the inbox.
        external = msg.get("delivered")
        if not isinstance(external, list):
            external = self.deliver(title, body)
            msg["delivered"] = external
        delivered = ["inbox", *[str(c) for c in external]]
        row = self.store.add_notification(msg.get("errand_id"), title, body, ",".join(delivered))
        NOTIFICATIONS_TOTAL.labels(channel="inbox").inc()
        return row

    def run_forever(self) -> None:
        while not self.stopping:
            try:
                msg = queue.pop_notify(self.r, timeout_s=5)
            except Exception:
                log.warning("redis unavailable; retrying")
                time.sleep(2)
                continue
            if not msg:
                continue
            for attempt in range(3):
                try:
                    self.process(msg)  # external channels are sent on the first attempt only
                    break
                except Exception:
                    log.warning("could not store notification (attempt %d)", attempt + 1)
                    time.sleep(2)
            else:
                # keep it (with its "delivered" list) for later instead of losing it
                try:
                    self.r.rpush(queue.NOTIFY, json.dumps(msg, default=str))
                except Exception:
                    log.error("could not re-queue a notification; it is lost")
                time.sleep(5)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # httpx logs full URLs at INFO
    from .db import PgStore

    settings = load_settings()
    start_http_server(settings.notifier_metrics_port)
    notifier = Notifier(PgStore(settings.database_url, max_size=2), queue.connect(settings.redis_url), settings)

    def _stop(signum, _frame):
        notifier.stopping = True

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    log.info(
        "notifier started (ntfy=%s, telegram=%s)",
        bool(settings.ntfy_url),
        bool(settings.telegram_bot_token and settings.telegram_chat_id),
    )
    notifier.run_forever()


if __name__ == "__main__":
    main()
