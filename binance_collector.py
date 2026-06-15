"""
binance_collector.py — Binance USDT-M futures + spot data collector.

Publishes to the same Kafka topic as data_collector.py for cross-exchange analysis:
  - Spot CVD:    Binance spot aggTrade → exchange='binance', market_type='spot'
  - Futures CVD: Binance USDT-M aggTrade → exchange='binance', market_type='futures'
  - OI:          /fapi/v1/openInterest (REST, 60s)
  - Funding:     /fapi/v1/premiumIndex (REST, 60s)
  - LSR:         /futures/data/topLongShortAccountRatio (REST, 300s)

CRITICAL — isBuyerMaker mapping (unit-tested at import):
  m=True  → taker was SELLER → side='Sell'
  m=False → taker was BUYER  → side='Buy'
"""

import os
import json
import logging
import logging.handlers
import signal
import threading
import time
import collections
import math
from typing import Dict, List, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from kafka import KafkaProducer
from websocket import WebSocketApp
from dotenv import load_dotenv

load_dotenv()

# ── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)8s] %(name)-24s | %(message)s",
)
logger = logging.getLogger("binance_collector")

# Dead-letter log — one file per process start, never mixed with main log
_dead_letter_log: Optional[logging.Logger] = None
_dead_letter_lock = threading.Lock()

def _get_dead_letter_log() -> logging.Logger:
    global _dead_letter_log
    with _dead_letter_lock:
        if _dead_letter_log is None:
            os.makedirs("logs", exist_ok=True)
            ts  = time.strftime("%Y%m%d_%H%M%S")
            hdl = logging.FileHandler(f"logs/binance_dead_letter_{ts}.log")
            hdl.setFormatter(logging.Formatter("%(asctime)s | %(message)s"))
            _dead_letter_log = logging.getLogger(f"binance_dl_{ts}")
            _dead_letter_log.addHandler(hdl)
            _dead_letter_log.setLevel(logging.ERROR)
            _dead_letter_log.propagate = False
        return _dead_letter_log


# ── Configuration ─────────────────────────────────────────────────────────────

SYMBOLS         = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "MNTUSDT"]
KAFKA_BOOTSTRAP = os.getenv("KAFKA_BOOTSTRAP", "localhost:19092")

# MNTUSDT trades on Bybit but NOT on Binance USDT-M Futures (FAPI).
# Exclude from FAPI REST polling AND from futures WS (mntusdt@aggTrade on
# fstream.binance.com returns nothing / reconnect-loops).
FAPI_SYMBOLS    = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"]

TRADE_FILTER_THRESHOLD = 1000   # min notional (price × size) — matches data_collector.py

# REST polling intervals
OI_POLL_INTERVAL   = 60     # open interest + funding every 60s
LSR_POLL_INTERVAL  = 300    # long/short ratio every 5 min

# Binance endpoints
SPOT_WS_BASE      = "wss://stream.binance.com:9443/ws"
FUTURES_WS_BASE   = "wss://fstream.binance.com/ws"
FUTURES_REST_BASE = "https://fapi.binance.com"

# Stale feed alarm — warn if a feed hasn't received a message in this many seconds
STALE_FEED_THRESHOLD_S = 300   # 5 minutes

# Status report interval
STATUS_INTERVAL_S = 60


# ── Thread-safe metrics ───────────────────────────────────────────────────────

_metrics: Dict[str, int] = collections.defaultdict(int)
_metrics_lock = threading.Lock()

def _inc(key: str, n: int = 1):
    with _metrics_lock:
        _metrics[key] += n


# ── isBuyerMaker mapping — unit-tested at import ─────────────────────────────

def _get_side(is_buyer_maker: bool) -> str:
    return "Sell" if is_buyer_maker else "Buy"

assert _get_side(True)  == "Sell", "isBuyerMaker=True must map to Sell"
assert _get_side(False) == "Buy",  "isBuyerMaker=False must map to Buy"


# ── Internal message queue (WS thread → Kafka drain thread) ──────────────────
# WS callbacks must never block. If producer.send() is slow (Kafka back-pressure),
# calling it from the WS callback thread stalls all incoming messages.
# Solution: WS appends to a non-blocking deque; a dedicated drain thread reads it.

_WS_QUEUE: collections.deque = collections.deque(maxlen=50_000)


def _queue_envelope(symbol: str, envelope: dict):
    """Non-blocking enqueue from WS callback thread."""
    _WS_QUEUE.append((symbol, envelope))
    _inc("ws_queued")


def _start_drain_thread(producer: KafkaProducer, stop: threading.Event):
    """Daemon thread that drains _WS_QUEUE → Kafka at up to 1000 msg/s."""
    def _drain():
        while not stop.is_set() or _WS_QUEUE:
            if not _WS_QUEUE:
                stop.wait(0.005)   # 5ms idle poll
                continue
            sym, envelope = _WS_QUEUE.popleft()
            _kafka_send(producer, sym, envelope)
    t = threading.Thread(target=_drain, daemon=True, name="Bnb-KafkaDrain")
    t.start()
    return t


# ── Kafka helpers ─────────────────────────────────────────────────────────────

def _make_producer() -> KafkaProducer:
    return KafkaProducer(
        bootstrap_servers=KAFKA_BOOTSTRAP,
        value_serializer=lambda v: json.dumps(v).encode("utf-8"),
        key_serializer=lambda k: k if isinstance(k, bytes) else k.encode("utf-8"),
        # No api_version override — auto-negotiate with Redpanda via ApiVersions handshake.
        # Specifying (2,5,0) skips the handshake → Redpanda TCP-resets → socket disconnect storm.
        acks=1,
        retries=5,
        max_in_flight_requests_per_connection=1,  # prevent kafka-python heapq crash (sender.py:204)
        linger_ms=5,
        batch_size=32768,
    )


def _kafka_send(producer: KafkaProducer, symbol: str, envelope: dict):
    """Send to Kafka with dead-letter fallback on failure."""
    try:
        producer.send("bybit-market-data", key=symbol.encode(), value=envelope)
        _inc("kafka_sent")
    except Exception as e:
        _inc("kafka_errors")
        try:
            _get_dead_letter_log().error(
                f"topic={envelope.get('topic','?')} | error={e} | "
                f"payload={str(envelope)[:400]}"
            )
        except Exception:
            pass
        logger.error(f"[Kafka] Send failed for {symbol}: {e}")


# ── HTTP session with connection-level retry adapter ─────────────────────────

def _make_session() -> requests.Session:
    """Session with automatic retry on TCP-level connection failures."""
    session = requests.Session()
    session.headers.update({"Accept": "application/json"})
    adapter = HTTPAdapter(
        max_retries=Retry(
            total=3,
            backoff_factor=1.0,
            status_forcelist=[500, 502, 503, 504],
            allowed_methods=["GET"],
            raise_on_status=False,
        )
    )
    session.mount("https://", adapter)
    session.mount("http://",  adapter)
    return session


# ── WebSocket feed ────────────────────────────────────────────────────────────

class BinanceWsFeed:
    """
    One WebSocket stream per symbol.
    Exponential backoff on reconnect (1→2→4→…→30s cap).
    Tracks last-message timestamp for stale-feed detection.
    """

    def __init__(self, url: str, symbol: str, market_type: str,
                 producer: KafkaProducer):
        self.url         = url
        self.symbol      = symbol
        self.market_type = market_type
        self.producer    = producer

        self._thread:           Optional[threading.Thread] = None
        self._last_message_ts:  float = 0.0        # epoch seconds, updated on every message
        self._messages_recv:    int   = 0
        self._messages_dropped: int   = 0

    def start(self):
        self._thread = threading.Thread(
            target=self._run_loop,
            daemon=True,
            name=f"Bnb-{self.market_type[:3].upper()}-{self.symbol}",
        )
        self._thread.start()

    def _run_loop(self):
        backoff = 1
        while True:
            try:
                ws = WebSocketApp(
                    self.url,
                    on_open=self._on_open,
                    on_message=self._on_message,
                    on_error=self._on_error,
                    on_close=self._on_close,
                )
                ws.run_forever(ping_interval=30, ping_timeout=10)
            except Exception as e:
                logger.error(f"[{self.market_type}/{self.symbol}] WS crash: {e}")
                _inc(f"ws_crashes_{self.market_type}_{self.symbol}")

            logger.info(
                f"[{self.market_type}/{self.symbol}] Reconnecting in {backoff}s "
                f"(recv={self._messages_recv})"
            )
            time.sleep(backoff)
            backoff = min(backoff * 2, 30)

    def _on_open(self, ws):
        logger.info(f"[{self.market_type}/{self.symbol}] WS connected")
        _inc("ws_connects")

    def _on_error(self, ws, err):
        logger.warning(f"[{self.market_type}/{self.symbol}] WS error: {err}")
        _inc("ws_errors")

    def _on_close(self, ws, code, msg):
        logger.info(f"[{self.market_type}/{self.symbol}] WS closed (code={code})")
        _inc("ws_disconnects")

    def _on_message(self, ws, raw: str):
        self._last_message_ts = time.monotonic()
        self._messages_recv  += 1
        _inc(f"ws_recv_{self.market_type}")

        try:
            msg = json.loads(raw)

            if msg.get("e") != "aggTrade":
                return

            # Field validation — reject missing or zero price/size
            raw_price = msg.get("p")
            raw_size  = msg.get("q")
            raw_ts    = msg.get("T")
            raw_id    = msg.get("a")
            raw_maker = msg.get("m")

            if raw_price is None or raw_size is None or raw_ts is None:
                logger.debug(
                    f"[{self.market_type}/{self.symbol}] Missing fields: {msg}"
                )
                _inc("ws_parse_errors")
                return

            price = float(raw_price)
            size  = float(raw_size)

            if price <= 0 or size <= 0:
                return

            # Notional filter — same threshold as data_collector.py
            if price * size < TRADE_FILTER_THRESHOLD:
                self._messages_dropped += 1
                _inc(f"ws_filtered_{self.market_type}")
                return

            # Prefix avoids trade_id collision across Binance spot vs futures
            prefix   = "bnbs" if self.market_type == "spot" else "bnbf"
            trade_id = f"{prefix}_{raw_id}"

            data_item = {
                "symbol":      self.symbol,
                "trade_id":    trade_id,
                "timestamp":   int(raw_ts),
                "side":        _get_side(bool(raw_maker)),
                "price":       price,
                "size":        size,
                "exchange":    "binance",
                "market_type": self.market_type,
            }

            envelope = {
                "topic":     f"publicTrade.binance.{self.market_type}.{self.symbol}",
                "source":    "ws",
                "timestamp": int(raw_ts),
                "data":      [data_item],
            }

            # Non-blocking enqueue — never block the WS callback thread
            _queue_envelope(self.symbol, envelope)

        except (ValueError, KeyError, TypeError) as e:
            _inc("ws_parse_errors")
            logger.warning(
                f"[{self.market_type}/{self.symbol}] Parse error: {e} | raw={raw[:200]}"
            )
            try:
                _get_dead_letter_log().error(
                    f"ws_parse | {self.market_type}/{self.symbol} | error={e} | raw={raw[:400]}"
                )
            except Exception:
                pass
        except Exception as e:
            _inc("ws_parse_errors")
            logger.error(f"[{self.market_type}/{self.symbol}] Unexpected error: {e}")

    @property
    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def is_stale(self) -> bool:
        """True if connected but no message received recently."""
        if not self.is_alive:
            return False
        if self._last_message_ts == 0.0:
            return False   # not yet received first message — give it time
        return (time.monotonic() - self._last_message_ts) > STALE_FEED_THRESHOLD_S

    @property
    def seconds_since_last_message(self) -> float:
        if self._last_message_ts == 0.0:
            return float("inf")
        return time.monotonic() - self._last_message_ts


# ── REST poller ───────────────────────────────────────────────────────────────

class BinanceRestPoller:
    """
    Polls Binance USDT-M Futures REST endpoints:
      - /fapi/v1/openInterest      every 60s
      - /fapi/v1/premiumIndex      every 60s  (funding + mark price)
      - /futures/data/topLongShortAccountRatio  every 300s

    Rate limit handling:
      - 429: read Retry-After header, sleep exactly that long, then retry
      - 418: IP banned — sleep Retry-After (default 120s), then skip cycle
      - Connection errors: exponential backoff up to 3 retries per request
    """

    def __init__(self, symbols: List[str], producer: KafkaProducer):
        self.symbols  = symbols
        self.producer = producer
        self._session = _make_session()
        self._stop    = threading.Event()
        self._threads: List[threading.Thread] = []

    def start(self):
        pollers = [
            ("OI+Funding", self._poll_oi_and_funding, 0),
            ("LSR",        self._poll_lsr,             30),   # stagger 30s
        ]
        for name, fn, delay in pollers:
            t = threading.Thread(
                target=self._run_with_delay(fn, delay),
                daemon=True,
                name=f"Bnb-REST-{name}",
            )
            t.start()
            self._threads.append(t)
        logger.info(f"Binance REST pollers started ({len(self.symbols)} symbols: {self.symbols})")

    def stop(self):
        self._stop.set()

    def _run_with_delay(self, fn, delay_s: int):
        def wrapper():
            if delay_s:
                self._stop.wait(delay_s)
            fn()
        return wrapper

    def _get(self, url: str, params: dict = None) -> Optional[object]:
        """
        HTTP GET with:
          - 418 IP ban detection (long sleep, abort cycle)
          - 429 rate-limit with Retry-After header
          - Connection-level retry via HTTPAdapter (3 attempts, exp backoff)
          - Application-level retry loop (up to 3) for transient failures
        """
        MAX_ATTEMPTS = 3
        backoff      = 2.0

        for attempt in range(MAX_ATTEMPTS):
            if self._stop.is_set():
                return None
            try:
                r = self._session.get(url, params=params, timeout=10)

                if r.status_code == 418:
                    # Binance IP ban — respect Retry-After exactly
                    wait = int(r.headers.get("Retry-After", 120))
                    logger.error(
                        f"[BinanceREST] 418 IP banned — sleeping {wait}s. "
                        f"Reduce poll frequency or check request patterns."
                    )
                    _inc("rest_418s")
                    self._stop.wait(wait)
                    return None   # skip this cycle entirely

                if r.status_code == 429:
                    wait = int(r.headers.get("Retry-After", 60))
                    logger.warning(
                        f"[BinanceREST] 429 rate limited — sleeping {wait}s "
                        f"(attempt {attempt+1}/{MAX_ATTEMPTS})"
                    )
                    _inc("rest_429s")
                    self._stop.wait(wait)
                    backoff = min(backoff * 2, 120)
                    continue   # retry after wait

                r.raise_for_status()
                return r.json()

            except requests.exceptions.ConnectionError as e:
                _inc("rest_conn_errors")
                logger.warning(
                    f"[BinanceREST] Connection error (attempt {attempt+1}/{MAX_ATTEMPTS}): {e}"
                )
                self._stop.wait(backoff)
                backoff = min(backoff * 2, 30)

            except requests.exceptions.Timeout:
                _inc("rest_timeouts")
                logger.warning(
                    f"[BinanceREST] Timeout on {url} (attempt {attempt+1}/{MAX_ATTEMPTS})"
                )
                self._stop.wait(backoff)
                backoff = min(backoff * 2, 30)

            except Exception as e:
                _inc("rest_errors")
                logger.warning(f"[BinanceREST] GET {url} failed: {e}")
                return None   # non-retryable

        logger.error(f"[BinanceREST] All {MAX_ATTEMPTS} attempts failed for {url}")
        _inc("rest_exhausted")
        return None

    # ── OI + Funding ──────────────────────────────────────────────────────────

    def _poll_oi_and_funding(self):
        while not self._stop.is_set():
            for symbol in self.symbols:
                if self._stop.is_set():
                    break

                # Open Interest
                oi = self._get(
                    f"{FUTURES_REST_BASE}/fapi/v1/openInterest",
                    params={"symbol": symbol},
                )
                if oi and isinstance(oi, dict):
                    ts_ms  = int(oi.get("time", time.time() * 1000))
                    oi_val = float(oi.get("openInterest", 0))
                    if oi_val > 0:
                        _kafka_send(self.producer, symbol, {
                            "topic":     f"open_interest.5min.{symbol}",
                            "source":    "rest",
                            "timestamp": ts_ms,
                            "data": [{
                                "symbol":        symbol,
                                "interval":      "5min",
                                "timestamp":     ts_ms,
                                "open_interest": oi_val,
                                "exchange":      "binance",
                            }],
                        })
                        _inc("oi_sent")

                # Premium Index (funding rate + mark price)
                pi = self._get(
                    f"{FUTURES_REST_BASE}/fapi/v1/premiumIndex",
                    params={"symbol": symbol},
                )
                if pi and isinstance(pi, dict):
                    ts_ms        = int(pi.get("time", time.time() * 1000))
                    funding_rate = float(pi.get("lastFundingRate", 0))
                    next_funding = int(pi.get("nextFundingTime", ts_ms))
                    fund_ts      = next_funding - 8 * 3600 * 1000  # approx last funding time

                    _kafka_send(self.producer, symbol, {
                        "topic":     f"funding_rates.{symbol}",
                        "source":    "rest",
                        "timestamp": fund_ts,
                        "data": [{
                            "symbol":            symbol,
                            "funding_timestamp": fund_ts,
                            "funding_rate":      funding_rate,
                            "interval":          "480",   # 8h = 480 min
                            "exchange":          "binance",
                        }],
                    })
                    _inc("funding_sent")

                # Brief inter-symbol pause — ~10 symbols × 2 req = 20 req/cycle,
                # well within Binance FAPI 2400 weight/min limit.
                self._stop.wait(0.1)

            self._stop.wait(OI_POLL_INTERVAL)

    # ── Long/Short Ratio ──────────────────────────────────────────────────────

    def _poll_lsr(self):
        while not self._stop.is_set():
            for symbol in self.symbols:
                if self._stop.is_set():
                    break

                lsr = self._get(
                    f"{FUTURES_REST_BASE}/futures/data/topLongShortAccountRatio",
                    params={"symbol": symbol, "period": "5m", "limit": 1},
                )
                if lsr and isinstance(lsr, list) and len(lsr) > 0:
                    row        = lsr[0]
                    ts_ms      = int(row.get("timestamp", time.time() * 1000))
                    buy_ratio  = float(row.get("longAccount",  0))
                    sell_ratio = float(row.get("shortAccount", 0))

                    # Validate ratios are in expected [0,1] range
                    if not (0.0 <= buy_ratio <= 1.0 and 0.0 <= sell_ratio <= 1.0):
                        logger.warning(
                            f"[BinanceREST] LSR out of range for {symbol}: "
                            f"buy={buy_ratio:.4f} sell={sell_ratio:.4f}"
                        )
                        _inc("lsr_invalid")
                        self._stop.wait(0.2)
                        continue

                    _kafka_send(self.producer, symbol, {
                        "topic":     f"long_short_ratio.5min.{symbol}",
                        "source":    "rest",
                        "timestamp": ts_ms,
                        "data": [{
                            "symbol":     symbol,
                            "interval":   "5min",
                            "timestamp":  ts_ms,
                            "buy_ratio":  buy_ratio,
                            "sell_ratio": sell_ratio,
                            "exchange":   "binance",
                        }],
                    })
                    _inc("lsr_sent")

                self._stop.wait(0.2)

            self._stop.wait(LSR_POLL_INTERVAL)


# ── Stale feed watchdog ───────────────────────────────────────────────────────

class StaleFeedWatchdog:
    """
    Background thread that checks every 60s whether any WS feed has stopped
    delivering messages. Logs a WARNING with exact staleness duration.
    Does NOT attempt restart (the feed's own reconnect loop handles that).
    """

    def __init__(self, feeds: List[BinanceWsFeed], stop_event: threading.Event):
        self._feeds = feeds
        self._stop  = stop_event

    def start(self):
        t = threading.Thread(target=self._run, daemon=True, name="Bnb-Watchdog")
        t.start()

    def _run(self):
        # Give feeds time to connect before first check
        self._stop.wait(120)
        while not self._stop.is_set():
            for feed in self._feeds:
                if not feed.is_alive:
                    logger.error(
                        f"[Watchdog] Feed thread DEAD: {feed.market_type}/{feed.symbol}"
                    )
                    _inc("watchdog_dead_threads")
                elif feed.is_stale:
                    age = feed.seconds_since_last_message
                    logger.warning(
                        f"[Watchdog] Stale feed: {feed.market_type}/{feed.symbol} "
                        f"— no message for {age:.0f}s (threshold={STALE_FEED_THRESHOLD_S}s)"
                    )
                    _inc("watchdog_stale_feeds")
            self._stop.wait(60)


# ── Historical backfill (startup) ─────────────────────────────────────────────
# Without this, Spot CVD has no history until 4 hours of live data accumulates.
# Bybit backfills 90 days via data_collector.py — Binance must do the same.
# We fetch the last 4 hours of aggTrades from Binance REST API on startup.

BACKFILL_HOURS = 4          # how many hours of history to seed on startup
SPOT_REST_BASE = "https://api.binance.com"
FAPI_REST_BASE = "https://fapi.binance.com"


class BinanceBackfill:
    """Fetches recent aggTrade history from Binance REST on startup."""

    def __init__(self, producer: KafkaProducer, session: requests.Session,
                 stop: threading.Event):
        self._producer = producer
        self._session  = session
        self._stop     = stop

    def run_async(self, symbols: List[str], fapi_symbols: List[str]):
        """Start backfill in a daemon thread so it doesn't block WS startup."""
        t = threading.Thread(
            target=self._backfill_all,
            args=(symbols, fapi_symbols),
            daemon=True,
            name="Bnb-Backfill",
        )
        t.start()
        return t

    def _backfill_all(self, symbols: List[str], fapi_symbols: List[str]):
        end_ts   = int(time.time() * 1000)
        start_ts = end_ts - BACKFILL_HOURS * 3600 * 1000
        logger.info(f"[Backfill] Starting {BACKFILL_HOURS}H backfill for "
                    f"{len(symbols)} spot + {len(fapi_symbols)} futures symbols...")

        for sym in symbols:
            if self._stop.is_set():
                return
            self._backfill_symbol(sym, "spot",    SPOT_REST_BASE, "api/v3/aggTrades",
                                  "bnbs", start_ts, end_ts)
            time.sleep(0.3)

        for sym in fapi_symbols:
            if self._stop.is_set():
                return
            self._backfill_symbol(sym, "futures", FAPI_REST_BASE, "fapi/v1/aggTrades",
                                  "bnbf", start_ts, end_ts)
            time.sleep(0.3)

        with _metrics_lock:
            total = _metrics.get("backfill_sent", 0)
        logger.info(f"[Backfill] Complete — {total:,} historical trades sent to Kafka")

    def _backfill_symbol(self, symbol: str, market_type: str,
                         base_url: str, endpoint: str, prefix: str,
                         start_ts: int, end_ts: int):
        """Fetch aggTrades for one symbol in 1H pages."""
        cursor   = start_ts
        sent     = 0
        MAX_PER_SYMBOL = 50_000   # hard cap — don't block forever

        while cursor < end_ts and sent < MAX_PER_SYMBOL:
            if self._stop.is_set():
                return
            try:
                r = self._session.get(
                    f"{base_url}/{endpoint}",
                    params={"symbol": symbol, "startTime": cursor,
                            "endTime": min(cursor + 3600_000, end_ts), "limit": 1000},
                    timeout=15,
                )
                if r.status_code == 429:
                    self._stop.wait(30)
                    continue
                if r.status_code != 200:
                    break

                trades = r.json()
                if not trades:
                    break

                for t in trades:
                    price = float(t["p"])
                    size  = float(t["q"])
                    if price * size < TRADE_FILTER_THRESHOLD:
                        continue
                    trade_id  = f"{prefix}_{t['a']}"
                    data_item = {
                        "symbol":      symbol,
                        "trade_id":    trade_id,
                        "timestamp":   int(t["T"]),
                        "side":        _get_side(bool(t["m"])),
                        "price":       price,
                        "size":        size,
                        "exchange":    "binance",
                        "market_type": market_type,
                    }
                    _queue_envelope(symbol, {
                        "topic":     f"publicTrade.binance.{market_type}.{symbol}",
                        "source":    "backfill",
                        "timestamp": int(t["T"]),
                        "data":      [data_item],
                    })
                    sent += 1
                    _inc("backfill_sent")

                cursor = int(trades[-1]["T"]) + 1
                if len(trades) < 1000:
                    break

            except Exception as e:
                logger.warning(f"[Backfill] {symbol} {market_type}: {e}")
                break

        if sent:
            logger.info(f"[Backfill] {symbol} {market_type}: {sent:,} trades → Kafka")


# ── Collector manager ─────────────────────────────────────────────────────────

class BinanceCollectorManager:

    def __init__(self, symbols: List[str], producer: KafkaProducer):
        self.symbols  = symbols
        self.producer = producer
        self._feeds:        List[BinanceWsFeed]         = []
        self._rest_poller:  Optional[BinanceRestPoller] = None
        self._watchdog:     Optional[StaleFeedWatchdog] = None
        self._stop          = threading.Event()

    def start(self):
        session = _make_session()

        # ── Kafka drain thread (WS queue → Kafka, never blocks WS callbacks) ──
        _start_drain_thread(self.producer, self._stop)

        # ── Historical backfill — seeds CVD immediately instead of waiting 4H ─
        BinanceBackfill(self.producer, session, self._stop).run_async(
            symbols=self.symbols, fapi_symbols=FAPI_SYMBOLS
        )

        # ── WS feeds ──────────────────────────────────────────────────────────
        for sym in self.symbols:
            sym_lower = sym.lower()

            spot = BinanceWsFeed(
                url=f"{SPOT_WS_BASE}/{sym_lower}@aggTrade",
                symbol=sym, market_type="spot", producer=self.producer,
            )
            spot.start()
            self._feeds.append(spot)
            time.sleep(0.2)

            # Futures aggTrade — FAPI symbols only (MNTUSDT not on Binance USDT-M)
            if sym in FAPI_SYMBOLS:
                fut = BinanceWsFeed(
                    url=f"{FUTURES_WS_BASE}/{sym_lower}@aggTrade",
                    symbol=sym, market_type="futures", producer=self.producer,
                )
                fut.start()
                self._feeds.append(fut)
                time.sleep(0.2)

        # ── REST poller (OI, funding, LSR) ───────────────────────────────────
        self._rest_poller = BinanceRestPoller(
            symbols=FAPI_SYMBOLS, producer=self.producer
        )
        self._rest_poller.start()

        # ── Stale feed watchdog ───────────────────────────────────────────────
        self._watchdog = StaleFeedWatchdog(self._feeds, self._stop)
        self._watchdog.start()

        logger.info(
            f"Binance collector started: {len(self._feeds)} WS feeds "
            f"({len([f for f in self._feeds if f.market_type=='spot'])} spot + "
            f"{len([f for f in self._feeds if f.market_type=='futures'])} futures) "
            f"+ {BACKFILL_HOURS}H backfill running + REST pollers for {FAPI_SYMBOLS}"
        )

    def stop(self):
        self._stop.set()
        if self._rest_poller:
            self._rest_poller.stop()

    @property
    def active_ws_count(self) -> int:
        return sum(1 for f in self._feeds if f.is_alive)

    def status_line(self) -> str:
        total    = len(self._feeds)
        active   = self.active_ws_count
        stale    = sum(1 for f in self._feeds if f.is_stale)
        with _metrics_lock:
            snap = dict(_metrics)
        return (
            f"WS: {active}/{total} active | stale: {stale} | "
            f"recv_spot={snap.get('ws_recv_spot',0)} "
            f"recv_futures={snap.get('ws_recv_futures',0)} | "
            f"kafka_sent={snap.get('kafka_sent',0)} "
            f"kafka_err={snap.get('kafka_errors',0)} | "
            f"rest_429={snap.get('rest_429s',0)} "
            f"rest_418={snap.get('rest_418s',0)} "
            f"rest_err={snap.get('rest_errors',0)} | "
            f"oi_sent={snap.get('oi_sent',0)} "
            f"lsr_sent={snap.get('lsr_sent',0)} "
            f"funding_sent={snap.get('funding_sent',0)}"
        )


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    logger.info(f"Starting Binance collector — symbols: {SYMBOLS}")
    logger.info(f"FAPI symbols (futures REST + WS): {FAPI_SYMBOLS}")

    producer = _make_producer()
    manager  = BinanceCollectorManager(symbols=SYMBOLS, producer=producer)
    manager.start()

    _stop = threading.Event()

    def _shutdown(signum, frame):
        logger.info("Shutting down Binance collector...")
        manager.stop()
        _stop.set()
        try:
            producer.flush(timeout=5)
            producer.close()
        except Exception:
            pass
        logger.info(f"Final stats | {manager.status_line()}")

    signal.signal(signal.SIGINT,  _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    while not _stop.is_set():
        _stop.wait(timeout=STATUS_INTERVAL_S)
        if not _stop.is_set():
            logger.info(f"[Status] {manager.status_line()}")


if __name__ == "__main__":
    main()
