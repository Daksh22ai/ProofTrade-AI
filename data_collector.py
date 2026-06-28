import os
import sys
import time
import json
import queue
import logging
import threading
import signal
import asyncio
import urllib.request
import urllib.parse
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Optional
from dataclasses import dataclass, field
from math import ceil
from collections import defaultdict, deque
from pybit.unified_trading import WebSocket as _PybitWebSocket
try:
    import msgspec
    _MSGSPEC_AVAILABLE = True
except ImportError:
    _MSGSPEC_AVAILABLE = False
    logger_pre = logging.getLogger(__name__)
    logger_pre.warning("msgspec not installed  -  install with: pip install msgspec. Falling back to Pydantic.")
from kafka import KafkaProducer, KafkaConsumer
from kafka.admin import KafkaAdminClient, NewTopic, NewPartitions
from threading import Thread
from prometheus_client import start_http_server, Counter, Gauge
from pydantic import BaseModel, Field, ValidationError, field_validator
import socket

from pybit.unified_trading import HTTP, WebSocket
from pybit.exceptions import InvalidRequestError
from websocket import WebSocketApp

# === Configuration ===
@dataclass
class SystemConfig:
    """Zero-loss configuration optimized for data integrity."""
    
    # API Configuration
    api_key: str = field(default_factory=lambda: os.getenv("BYBIT_API_KEY", ""))
    api_secret: str = field(default_factory=lambda: os.getenv("BYBIT_API_SECRET", ""))
    testnet: bool = field(default_factory=lambda: os.getenv("BYBIT_TESTNET", "false").lower() == "true")
    # Rate limiting  -  80% of Bybit's IP limit (600 req/5s = 120 req/s).
    # Market data endpoints are IP-limited only (no per-endpoint UID limit visible in headers).
    # 96 rps = safe sustained rate; 10-token burst handles scheduling jitter without microbursting.
    base_rps: int = 96
    burst_capacity: int = 10

    # Symbol discovery [True->Manual, False->Auto]
    use_manual_symbols: bool = True
    
    # Centralized Stream Configuration
    # Kline: ONLY 1-minute candles fetched from Bybit.
    # Higher timeframes (5m, 15m, 1h, 4h, 1D, 1W) are derived at query time via QuestDB SAMPLE BY:
    #   SELECT first(open), max(high), min(low), last(close), sum(volume), sum(turnover)
    #   FROM candles WHERE symbol='BTCUSDT' AND interval='1'
    #   SAMPLE BY 5m ALIGN TO CALENDAR;
    # This eliminates 6/7 of all kline API calls, freeing budget for OI, LSR, and funding.
    #
    # Open Interest: smallest API-supported interval is 5min (per docs: 5min|15min|30min|1h|4h|1d).
    # Open Interest: fetch only 5min; derive 15m/1h/4h/1d via SAMPLE BY at query time.
    #
    # Long/Short Ratio: same interval set as OI (5min|15min|30min|1h|4h|1d), same strategy.
    # Fetch only 5min; derive coarser intervals via SAMPLE BY.
    #
    # Funding Rate: event-driven (4h or 8h per symbol), not interval-configurable here.
    # Fetched via _fetch_funding_rates which reads interval from instrument metadata.
    #
    # Shared IP rate limit budget savings:
    #   Before: 7 kline + 6 OI + 6 LSR = 19 interval streams per symbol per API call cycle
    #   After:  1 kline + 1 OI + 1 LSR =  3 interval streams → 84% reduction in REST calls
    stream_intervals: Dict[str, List[str]] = field(default_factory=lambda: {
        "kline": ["1"],           # 1-min base; derive 5m/15m/1h/4h/1D/1W via SAMPLE BY
        "open_interest": ["5min"], # 5-min base (API minimum); derive higher via SAMPLE BY
        "long_short_ratio": ["5min"] # 5-min base (API minimum); derive higher via SAMPLE BY
    })    
    # Restfetcher batching  -  these match the Bybit hard limits per endpoint:
    #   /v5/market/kline          → max 1000 (we use 1000)
    #   /v5/market/open-interest  → max 200  (was incorrectly set to 500)
    #   /v5/market/account-ratio  → max 500  (LSR, correct)
    #   /v5/market/history-fund-rate → max 200 (was incorrectly set to 500)
    kline_batch_size: int = 1000
    funding_rate_batch_size: int = 200   # Bybit hard limit is 200 (was 500  -  always capped anyway)
    OI_batch_size: int = 200             # Bybit hard limit is 200 (was 500  -  always capped anyway)
    LSR_batch_size: int = 500
    orderbook_depth: int = 200
    
    # --- QuestDB settings ---
    questdb_host: str = "localhost"
    questdb_ilp_port: int = 9009        # ILP TCP port
    questdb_pg_port: int = 8812         # PostgreSQL wire protocol (reads)

    # QuestDB write-performance knobs  -  applied via ALTER TABLE after DDL each startup.
    # o3MaxLag: how long QuestDB buffers out-of-order ILP rows before committing.
    #   Default: 10 min. Backfill writes years of data out-of-order, so we keep 600s.
    # maxUncommittedRows: flush threshold regardless of lag window.
    #   Default: 500. 250_000 reduces WAL commit overhead by ~50× during bulk ingestion.
    questdb_o3_max_lag_seconds:   int = 600
    questdb_max_uncommitted_rows: int = 250_000

    kafka_bootstrap: str = "localhost:19092"
    
    # Websocket ping/pong
    ws_ping_interval: int = 15    # Send ping every 15s
    ws_ping_timeout: int = 8      # Timeout after 8s
    
    # Threading Configuration
    rest_worker_threads: int = min(8, os.cpu_count())
    max_symbols_per_ws: int = 16  # Conservative for stability
    max_concurrent_ws: int = 50

    # Filtering low amount of trades
    TRADE_FILTER_THRESHOLD = 1000
    
    # Data Collection
    start_date: str = "2026-03-17"  # 90-day window for hackathon demo
    # Monitoring
    status_report_interval: int = 30

config = SystemConfig()

class Metrics:
    def __init__(self):
        self._lock = threading.Lock()
        self.ws_messages       = 0
        self.kafka_enqueued    = 0
        self.kafka_sent        = 0
        self.kafka_errors      = 0
        self.dropped_messages  = 0
        self.ws_disconnects    = 0

    def inc(self, field: str, n: int = 1) -> None:
        with self._lock:
            setattr(self, field, getattr(self, field) + n)

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "ws_messages":      self.ws_messages,
                "kafka_enqueued":   self.kafka_enqueued,
                "kafka_sent":       self.kafka_sent,
                "kafka_errors":     self.kafka_errors,
                "dropped_messages": self.dropped_messages,
                "ws_disconnects":   self.ws_disconnects,
            }

metrics = Metrics()

# Module-level reference so health endpoint and utilities can access db stats without coupling.
# Set to the actual OptimizedDatabaseManager instance in main().
_db_manager: Optional["OptimizedDatabaseManager"] = None


# === QuestDB HTTP Utility (stdlib only, no new deps) ===
import urllib.request
import urllib.parse

def _questdb_exec(sql: str, host: str = None, timeout: int = 5) -> Optional[dict]:
    """
    Run a SQL query against QuestDB's HTTP API (port 9000).
    Returns the parsed JSON response or None on failure.
    Uses stdlib urllib  -  zero additional dependencies.
    """
    qdb_host = host or config.questdb_host
    url = f"http://{qdb_host}:9000/exec?{urllib.parse.urlencode({'query': sql})}"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return json.loads(r.read())
    except Exception as e:
        logger.debug(f"[QuestDB HTTP] {e}: {sql[:80]}")
        return None


def _questdb_max_ts(table: str, symbol: str, interval: str = None, host: str = None) -> Optional[int]:
    """
    Query the latest ingested timestamp (ms) for a (symbol[, interval]) in a QuestDB table.
    Returns milliseconds or None if the table is empty / doesn't exist yet.
    This is used so backfill restarts resume from the actual DB state, not a stale local file.
    """
    if interval:
        sql = f"SELECT max(timestamp) FROM {table} WHERE symbol='{symbol}' AND interval='{interval}'"
    else:
        sql = f"SELECT max(timestamp) FROM {table} WHERE symbol='{symbol}'"
    result = _questdb_exec(sql, host=host)
    if not result:
        return None
    dataset = result.get("dataset", [[None]])
    val = (dataset[0][0] if dataset and dataset[0] else None)
    if val is None:
        return None
    # QuestDB HTTP API returns TIMESTAMP in two formats depending on table type:
    #   - WAL pre-created tables: ISO 8601 string  '2020-08-14T18:35:00.000000Z'
    #   - ILP-auto-created tables: integer microseconds  1597426500000000
    if isinstance(val, str):
        try:
            dt = datetime.fromisoformat(val.replace('Z', '+00:00'))
            return int(dt.timestamp() * 1000)  # UTC epoch ms
        except Exception:
            logger.debug(f"[QuestDB] Could not parse timestamp string: {val!r}")
            return None
    # Integer path: QuestDB TIMESTAMP is microseconds; convert to ms
    return int(val) // 1000 if int(val) > _QDB_TS_US_THRESHOLD else int(val)


# === QuestDB Table Pre-Creation (WAL + DEDUP) ===
# Tables are created here BEFORE any ILP write so DEDUP is active from offset zero.
# ILP will use existing table schemas; no schema conflict if column types match.
_CREATE_TABLE_SQLS: List[str] = [
    """CREATE TABLE IF NOT EXISTS candles (
        symbol SYMBOL, interval SYMBOL,
        open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE, volume DOUBLE, turnover DOUBLE,
        timestamp TIMESTAMP
    ) TIMESTAMP(timestamp) PARTITION BY DAY WAL DEDUP UPSERT KEYS(timestamp, symbol, interval)""",

    """CREATE TABLE IF NOT EXISTS trades (
        symbol SYMBOL, exchange SYMBOL, market_type SYMBOL,
        trade_id SYMBOL, side SYMBOL,
        price DOUBLE, size DOUBLE,
        timestamp TIMESTAMP
    ) TIMESTAMP(timestamp) PARTITION BY DAY WAL DEDUP UPSERT KEYS(timestamp, symbol, exchange, trade_id)""",

    """CREATE TABLE IF NOT EXISTS funding_rates (
        symbol SYMBOL, interval SYMBOL,
        funding_rate DOUBLE,
        timestamp TIMESTAMP
    ) TIMESTAMP(timestamp) PARTITION BY MONTH WAL DEDUP UPSERT KEYS(timestamp, symbol)""",

    """CREATE TABLE IF NOT EXISTS open_interest (
        symbol SYMBOL, interval SYMBOL,
        open_interest DOUBLE,
        timestamp TIMESTAMP
    ) TIMESTAMP(timestamp) PARTITION BY DAY WAL DEDUP UPSERT KEYS(timestamp, symbol, interval)""",

    """CREATE TABLE IF NOT EXISTS long_short_ratio (
        symbol SYMBOL, interval SYMBOL,
        buy_ratio DOUBLE, sell_ratio DOUBLE,
        timestamp TIMESTAMP
    ) TIMESTAMP(timestamp) PARTITION BY DAY WAL DEDUP UPSERT KEYS(timestamp, symbol, interval)""",

    """CREATE TABLE IF NOT EXISTS orderbook (
        symbol SYMBOL,
        bids STRING, asks STRING,
        timestamp TIMESTAMP
    ) TIMESTAMP(timestamp) PARTITION BY DAY WAL DEDUP UPSERT KEYS(timestamp, symbol)""",

    """CREATE TABLE IF NOT EXISTS liquidations (
        symbol SYMBOL, side SYMBOL,
        price DOUBLE, size DOUBLE,
        timestamp TIMESTAMP
    ) TIMESTAMP(timestamp) PARTITION BY DAY WAL DEDUP UPSERT KEYS(timestamp, symbol, side)""",
]

def create_questdb_tables():
    """
    Pre-create all time-series tables with WAL + DEDUP UPSERT KEYS, then apply
    QuestDB write-performance parameters (O3 lag and max uncommitted rows).

    Called in main() Stage 1 BEFORE consumers or ILP writers start.
    ILP writes to pre-created tables inherit DEDUP from the table definition.
    Idempotent: CREATE TABLE IF NOT EXISTS and ALTER TABLE SET PARAM are safe to
    call on every startup  -  QuestDB applies changes to existing tables in-place.
    """
    ok = 0
    table_names = []
    for sql in _CREATE_TABLE_SQLS:
        table = sql.split("EXISTS")[1].split("(")[0].strip().split()[0]
        table_names.append(table)
        single_line = " ".join(sql.split())
        result = _questdb_exec(single_line)
        if result is not None:
            ok += 1
        else:
            logger.warning(f"[DDL] Could not pre-create table '{table}' (QuestDB may be unreachable)")
    logger.info(f"[DDL] {ok}/{len(_CREATE_TABLE_SQLS)} tables created/verified with WAL DEDUP")

    # Apply write-performance tuning to every table.
    # During backfill we write years of historical data simultaneously with live WS data,
    # creating extreme out-of-order (O3) interleave. Without tuning:
    #   - QuestDB micro-commits on every 500-row WAL flush → massive I/O overhead
    #   - O3 re-sort runs on every commit cycle → CPU spike
    # With tuning (applied after each CREATE TABLE IF NOT EXISTS):
    #   - maxUncommittedRows=250_000 → commits ~500x less frequently during bulk load
    #   - o3MaxLag=600s → absorbs the full backfill+live interleave window
    o3_lag   = f"{config.questdb_o3_max_lag_seconds}s"
    max_rows = config.questdb_max_uncommitted_rows
    tuned = 0
    for tbl in table_names:
        r1 = _questdb_exec(f"ALTER TABLE {tbl} SET PARAM o3MaxLag = {o3_lag}")
        r2 = _questdb_exec(f"ALTER TABLE {tbl} SET PARAM maxUncommittedRows = {max_rows}")
        if r1 is not None and r2 is not None:
            tuned += 1
        else:
            logger.debug(f"[DDL] O3 tuning skipped for '{tbl}' (table may not exist yet or QuestDB unreachable)")
    if tuned:
        logger.info(f"[DDL] O3 tuning applied: o3MaxLag={o3_lag}, maxUncommittedRows={max_rows:,} on {tuned}/{len(table_names)} tables")

    # Migration: add exchange + market_type columns to trades table if they don't exist yet.
    # QuestDB does NOT support "ADD COLUMN IF NOT EXISTS"  -  we attempt and swallow the error
    # if the column already exists (error msg contains "already exists").
    # Required for cross-exchange CVD (Bybit futures + Binance spot/futures).
    for col, col_type in [("exchange", "SYMBOL"), ("market_type", "SYMBOL")]:
        r = _questdb_exec(f"ALTER TABLE trades ADD COLUMN {col} {col_type}")
        if r is None:
            logger.debug(f"[DDL] Column '{col}' already exists or ADD COLUMN failed  -  skipping")
    # Backfill existing Bybit rows that have NULL exchange/market_type
    _questdb_exec("UPDATE trades SET exchange='bybit' WHERE exchange IS NULL")
    _questdb_exec("UPDATE trades SET market_type='futures' WHERE market_type IS NULL")
    logger.info("[DDL] trades table migration applied: exchange + market_type columns ensured")


# Phase 5: Kafka topic partitioning
# Number of partitions must be ≥ consumer_parallelism for all consumers to get work.
# With 4 consumers and 1 partition, 3 consumers are always idle (logs confirm this).
KAFKA_PARTITIONS = 4
KAFKA_REPLICATION = 1  # single-broker local setup
KAFKA_TOPIC = 'bybit-market-data'

def ensure_kafka_partitions(bootstrap: str, topic: str = KAFKA_TOPIC):
    """
    Phase 5: Guarantee the Kafka topic has enough partitions for all consumers.
    - Creates the topic with KAFKA_PARTITIONS partitions if it doesn't exist.
    - Increases partition count if topic has fewer than KAFKA_PARTITIONS.
    - Idempotent: safe to call on every startup.
    Called in main() Stage 1, BEFORE consumers start.
    """
    try:
        admin = KafkaAdminClient(
            bootstrap_servers=bootstrap,
            client_id='dc-admin',
        )
        existing = admin.list_topics()

        if topic not in existing:
            admin.create_topics([
                NewTopic(
                    name=topic,
                    num_partitions=KAFKA_PARTITIONS,
                    replication_factor=KAFKA_REPLICATION,
                )
            ])
            logger.info(f"[Kafka] Created topic '{topic}' with {KAFKA_PARTITIONS} partitions")
        else:
            meta = admin.describe_topics([topic])
            current = len(meta[0]['partitions'])
            if current < KAFKA_PARTITIONS:
                admin.create_partitions({topic: NewPartitions(total_count=KAFKA_PARTITIONS)})
                logger.info(f"[Kafka] Scaled '{topic}': {current} → {KAFKA_PARTITIONS} partitions")
            else:
                logger.info(f"[Kafka] Topic '{topic}' already has {current} partitions ✔")
        admin.close()
    except Exception as e:
        logger.warning(f"[Kafka] ensure_kafka_partitions failed (non-fatal): {e}")


_DEDUP_DDL = [
    "ALTER TABLE candles         DEDUP ENABLE UPSERT KEYS(timestamp, symbol, interval)",
    "ALTER TABLE trades          DEDUP ENABLE UPSERT KEYS(timestamp, symbol, exchange, trade_id)",
    "ALTER TABLE funding_rates   DEDUP ENABLE UPSERT KEYS(timestamp, symbol)",
    "ALTER TABLE open_interest   DEDUP ENABLE UPSERT KEYS(timestamp, symbol, interval)",
    "ALTER TABLE long_short_ratio DEDUP ENABLE UPSERT KEYS(timestamp, symbol, interval)",
    "ALTER TABLE orderbook       DEDUP ENABLE UPSERT KEYS(timestamp, symbol)",
    "ALTER TABLE liquidations    DEDUP ENABLE UPSERT KEYS(timestamp, symbol, side)",
]

def ensure_table_dedup():
    """
    Idempotently apply DEDUP UPSERT KEYS to all time-series tables.
    Safe to call on every startup  -  QuestDB ignores the command if dedup is already enabled.
    Must be called AFTER the first ILP write so tables already exist.
    """
    ok = 0
    for ddl in _DEDUP_DDL:
        table = ddl.split()[2]
        result = _questdb_exec(ddl)
        if result is not None:
            ok += 1
        else:
            logger.debug(f"[DEDUP] Skipped (table may not exist yet): {table}")
    logger.info(f"[DEDUP] Applied DEDUP UPSERT KEYS to {ok}/{len(_DEDUP_DDL)} tables")

# === Logging Setup ===
def setup_logging():
    log_format = "%(asctime)s [%(levelname)8s] %(name)-12s | %(threadName)-15s | %(message)s"
    os.makedirs("logs", exist_ok=True)
    
    logging.basicConfig(
        level=logging.INFO,
        format=log_format,
        handlers=[
            logging.FileHandler(f"logs/collector_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log", encoding='utf-8'),
            logging.StreamHandler()
        ]
    )
    
    # Reduce noise from external libraries
    for lib in ["pybit", "urllib3", "websocket"]:
        logging.getLogger(lib).setLevel(logging.WARNING)

setup_logging()
logger = logging.getLogger(__name__)

# QuestDB TIMESTAMP columns can be returned as either epoch microseconds (16 digits)
# or epoch milliseconds (13 digits). The threshold 2e12 ms = year 2033 safely
# distinguishes the two for all current dates (2024-2033 range).
_QDB_TS_US_THRESHOLD = 2_000_000_000_000

_OB_LAST_WRITE_MS: dict = {}   # {symbol: last_written_ms} - orderbook write rate limiter
OB_WRITE_INTERVAL_MS = 60_000  # write orderbook snapshot at most once per minute per symbol

# === Prometheus Metrics ===
# Active metrics only  -  legacy queue_write path metrics have been removed.
ws_active_gauge = Gauge('ws_active_connections', 'Number of active WebSocket connections')

# QuestDB ingestion health
questdb_ingest_success = Counter('questdb_ingest_success_total', 'Successful ILP writes to QuestDB', ['stream'])
questdb_ingest_fail    = Counter('questdb_ingest_fail_total',    'Failed ILP writes to QuestDB',     ['stream'])
questdb_ingest_latency = Gauge(  'questdb_ingest_latency_seconds','Latency of ILP write to QuestDB', ['stream'])

# Pipeline throughput & latency
ws_messages_recv_total    = Counter('ws_messages_recv_total',     'WS messages received before validation')
ws_messages_dropped_total = Counter('ws_messages_dropped_total',  'WS messages dropped (raw queue full)')
kafka_drain_batch_size    = Gauge(  'kafka_drain_batch_size',     'Messages per drain_to_kafka iteration')
ilp_socket_errors_total   = Counter('ilp_socket_errors_total',    'QuestDB ILP TCP reconnect events')
ilp_batch_records         = Gauge(  'ilp_batch_records',          'Records in last ILP flush', ['stream'])
consumer_poll_latency_s   = Gauge(  'consumer_poll_latency_seconds', 'Time spent in KafkaConsumer.poll()')
rest_api_errors_total     = Counter('rest_api_errors_total',      'REST API errors by endpoint', ['endpoint'])

# OpenTelemetry is not active. Imports removed to avoid confusion.
# Re-add TracerProvider + OTLP exporter when distributed tracing is needed.

KAFKA_QUEUE_MAXSIZE = 50_000  # hard cap, tune later
# Thread-safe raw message buffer between WS threads and the drain coroutine.
# deque.append() and deque.popleft() are GIL-atomic in CPython  -  no explicit lock needed.
_ws_raw_queue: deque = deque(maxlen=KAFKA_QUEUE_MAXSIZE)
_ws_manager: Optional["BybitWsManager"] = None   # set in main() after manager.start()

# Module-level sentinels  -  set in main() so health endpoint + Librarian can reference them
_db_manager  = None   # OptimizedDatabaseManager instance (set in Stage 1)
_librarian   = None   # Librarian instance (set in Stage 5 after backfill)
_rest_fetcher = None  # RestDataFetcher instance (set in Stage 3, read by health endpoint)

# === Advanced Rate Limiting ===
class AdaptiveRateLimiter:
    """
    Production-grade token-bucket rate limiter with header-based exact backoff.

    Design principles (from HFT bootstrap crawler patterns):
    1. Token bucket controls sustained req/sec  -  lock held only during state update, not sleep.
    2. On 10006 (rate-limit hit), drain tokens to zero and sleep until the exact
       X-Bapi-Limit-Reset-Timestamp from the response header  -  eliminates exponential guessing.
    3. Success-rate EMA removed  -  it amplified errors by compounding throttle across workers.
       Header-based reset is authoritative; EMA adaptation is noise on market data endpoints.
    4. Startup stagger via stagger_worker() prevents burst at t=0 when all workers race together.

    Two-layer Bybit enforcement (official docs):
      Layer 1  -  IP limit:  600 req / 5-second sliding window → 120 req/s max (all endpoints)
      Layer 2  -  API limit: per-endpoint per-UID rolling 1s window (applies to trade/account;
                           market data endpoints are IP-limited only)
    Safe sustained rate: 96 rps = 80% of IP limit floor.
    """
    def __init__(self, base_rps: int = 96, burst_capacity: int = 10):
        self.base_rps       = base_rps
        self.burst_capacity = burst_capacity
        self.tokens         = burst_capacity
        self.last_refill    = time.time()
        self.lock           = threading.Lock()
        self.request_times  = deque(maxlen=200)
        # Exact-backoff state: set by notify_rate_limit(), checked in wait()
        self._throttle_until: float = 0.0   # epoch seconds  -  sleep until this time

    def stagger_worker(self, worker_id: int) -> None:
        """
        Stagger worker startup to prevent connection burst when all workers fire at t=0.

        Cold-start problem: 8 asyncio workers created within milliseconds fire simultaneous
        HTTPS connections (TCP handshake + TLS). This overwhelms the Windows TCP pool and
        causes transient TCP failures  -  pybit raises FailedRequestError before the HTTP
        request is even sent, triggering the exponential backoff seen in logs at startup.

        Steady-state stagger (old): worker_id * (1/96) = 10ms  -  fine for token bucket.
        Cold-start stagger (new):   worker_id * 0.5s  = 500ms  -  gives each HTTPS connection
        time to fully complete before the next worker fires its first request.

        Worker-7 starts 3.5s after Worker-0. Overhead: 3.5s once per process lifetime.
        Eliminates 30-60s of exponential backoff noise on every restart.
        """
        if worker_id > 0:
            startup_stagger_s = worker_id * 0.5   # 500ms per worker
            time.sleep(startup_stagger_s)

    def notify_rate_limit(self, reset_ts_ms: int = 0) -> None:
        """
        Called when a 10006 (rate-limit) response is received.
        Drains the token bucket to zero (stops all workers) and sets a precise
        wakeup time from the X-Bapi-Limit-Reset-Timestamp header.
        If reset_ts_ms is not available, falls back to sleeping 1 second.
        """
        with self.lock:
            self.tokens = 0.0   # drain  -  force all threads to wait on next call to wait()
            if reset_ts_ms and reset_ts_ms > 0:
                reset_epoch = reset_ts_ms / 1000.0
                # Add 50ms pad so we don't immediately re-hit the boundary
                self._throttle_until = max(reset_epoch + 0.05, time.time() + 0.1)
            else:
                self._throttle_until = time.time() + 1.0   # 1s fallback

    def wait(self) -> None:
        """
        Acquire one token. Blocks the calling thread until a token is available.
        Lock is held only for state read/write, never during sleep.
        """
        while True:
            sleep_for = 0.0
            with self.lock:
                now = time.time()

                # ── Exact backoff from notify_rate_limit ──────────────────────
                if now < self._throttle_until:
                    sleep_for = self._throttle_until - now
                else:
                    # ── Normal token-bucket refill ───────────────────────────
                    elapsed = now - self.last_refill
                    self.tokens = min(
                        float(self.burst_capacity),
                        self.tokens + elapsed * float(self.base_rps)
                    )
                    self.last_refill = now

                    if self.tokens >= 1.0:
                        self.tokens -= 1.0
                        self.request_times.append(now)
                        return  # token acquired  -  proceed immediately

                    # Not enough tokens  -  compute sleep duration while lock is held
                    sleep_for = (1.0 - self.tokens) / float(self.base_rps)

            # Sleep OUTSIDE the lock  -  sibling threads can still acquire tokens concurrently
            time.sleep(sleep_for)



    def get_current_rps(self) -> float:
        """Return observed req/s over the last 10 seconds (diagnostic).
        Note: request_times has maxlen=200, so at 96 rps it covers ~2s of history.
        Using a 10s window here but actual span is bounded by maxlen.
        """
        if len(self.request_times) < 2:
            return 0.0
        now = time.time()
        recent = [t for t in self.request_times if now - t <= 10]
        if len(recent) < 2:
            return 0.0
        span = max(recent) - min(recent)
        return round(len(recent) / span, 1) if span > 0 else 0.0

# Global rate limiter instance
api_limiter = AdaptiveRateLimiter(config.base_rps, config.burst_capacity)

class InstrumentMetadataManager:
    def __init__(self, api_client):
        self.api = api_client
        self.meta = {}

    def load(self):
        logger.info("Loading instrument metadata…")
        for category in ["linear", "inverse"]:
            resp = self.api.instruments_info(category=category)
            if resp.get("retCode", 1) != 0:
                logger.warning(f"Instrument info fetch failed for {category}: {resp.get('retMsg')}")
                continue

            for itm in resp["result"].get("list", []):
                symbol = itm["symbol"]
                launch_ts = int(itm.get("launchTime", 0))
                funding_interval = itm.get("fundingInterval", "8h")
                self.meta[symbol] = {
                    "launch_ts": launch_ts,
                    "funding_interval": funding_interval,
                    "category": category
                }

        logger.info(f"Loaded metadata for {len(self.meta)} symbols")

    def get_launch_ts(self, symbol, fallback_ts=None):
        return self.meta.get(symbol, {}).get("launch_ts", fallback_ts)

    def get_funding_interval(self, symbol: str, default: str = "480") -> str:
        """
        Return the funding interval in MINUTES as a string (e.g. '480').
        Bybit API returns this field as minutes (e.g. '480') for most symbols,
        but may return hour-notation ('8h') or minute-notation ('480m') on edge cases.
        Falls back to `default` (480 min = 8h) if not found.
        """
        raw = self.meta.get(symbol, {}).get("funding_interval", default) or default
        raw = str(raw).strip()
        if raw.isdigit():
            return raw  # already in minutes
        if raw.endswith("h"):
            return str(int(raw[:-1]) * 60)   # "8h" → "480"
        if raw.endswith("m"):
            return raw[:-1].strip()          # "480m" → "480"
        # Fallback: try int conversion, return default on failure
        try:
            return str(int(float(raw)))
        except (ValueError, TypeError):
            logger.warning(f"[InstrumentMeta] Unparseable fundingInterval {raw!r} for {symbol}, using {default}")
            return default
    
    def get_category(self, symbol, default="linear"):
        return self.meta.get(symbol, {}).get("category", default)

# class QuestDBILPWriter:
#     """Simple, thread-safe ILP TCP writer for QuestDB."""
#     def __init__(self, host: str, port: int):
#         self.host = host
#         self.port = port
#         self.lock = threading.Lock()
#         self.sock = None
#         self._connect()

#     def _connect(self):
#         with self.lock:
#             if self.sock:
#                 try:
#                     self.sock.close()
#                 except Exception:
#                     pass
#             self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
#             self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
#             self.sock.connect((self.host, self.port))

#     def write_line(self, line: str) -> None:
#         """Send a single ILP line (must end with newline). Thread-safe."""
#         payload = line if line.endswith("\n") else line + "\n"
#         start = time.time()
#         try:
#             with self.lock:
#                 self.sock.sendall(payload.encode("utf-8"))
#             questdb_ingest_success.labels(stream="default").inc()
#             questdb_ingest_latency.labels(stream="default").set(time.time() - start)
#         except (BrokenPipeError, ConnectionResetError) as e:
#             questdb_ingest_fail.labels(stream="default").inc()
#             logger.warning("QuestDB ILP connection broken, reconnecting: %s", e)
#             try:
#                 self._connect()
#                 with self.lock:
#                     self.sock.sendall(payload.encode("utf-8"))
#             except Exception as e2:
#                 logger.error("QuestDB ILP retry failed: %s", e2)
#                 raise
#         except Exception as e:
#             questdb_ingest_fail.labels(stream="default").inc()
#             logger.error("QuestDB ILP write failed: %s", e)
#             raise

# === Database Manager ===
class OptimizedDatabaseManager:
    """
    QuestDB-only database manager.
    Responsible for:
    - Converting structured records into ILP
    - Writing ILP to QuestDB
    - Tracking ingestion metrics
    """

    def __init__(self):
        self.questdb_host = config.questdb_host
        self.questdb_port = config.questdb_ilp_port

        # Ingestion metrics  -  updated by each _consumer_worker under stats_lock
        self.stats: Dict[str, int] = defaultdict(int)
        self.stats_lock = threading.Lock()

        # consumer_parallelism: N independent KafkaConsumer+ILP-socket pairs.
        # Each gets its own TCP connection to QuestDB  -  zero hot-path lock contention.
        # Bounded by logical CPU count and KAFKA_PARTITIONS so every consumer gets work.
        self.consumer_parallelism: int = min(4, (os.cpu_count() or 2))

    # ── Phase 3: Parallel consumer entry point ──────────────────────────────
    async def run_parallel_consumers(self, stop_event: asyncio.Event, n: int = None):
        """
        Spawn N coroutines, each owning an independent KafkaConsumer + ILP socket.
        Eliminates both the single poll() bottleneck and the shared sock_lock.

        At burst throughput (100 k msg/s):
          - 1 consumer: poll + ILP write are serial          → ~200 ms/cycle head-of-line
          - N consumers: fully parallel poll + parallel write  → ~200/N ms effective latency
        """
        parallelism = n or self.consumer_parallelism
        logger.info(f"[Phase-3] Launching {parallelism} parallel consumer coroutines")
        tasks = [
            asyncio.create_task(
                self._consumer_worker(stop_event, worker_id=i),
                name=f"Consumer-{i}"
            )
            for i in range(parallelism)
        ]
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _consumer_worker(self, stop_event: asyncio.Event, worker_id: int):
        """
        Independent consumer coroutine with a dedicated ILP socket + local buffer.
        No shared lock on the hot path  -  contention-free at the cost of N TCP connections
        to QuestDB (cheap; QuestDB handles thousands of concurrent ILP connections).
        """
        # Each worker gets its own TCP socket to QuestDB  -  zero sock_lock contention
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            sock.connect((self.questdb_host, self.questdb_port))
        except Exception as e:
            logger.error(f"[Consumer-{worker_id}] Failed to open ILP socket: {e}")
            return

        # Local (non-shared) ILP buffer  -  no lock needed
        local_buf: list = []
        FLUSH_THRESHOLD = 500
        FLUSH_INTERVAL  = 0.5   # seconds
        last_flush      = time.monotonic()

        def _flush_local():
            nonlocal last_flush, local_buf
            if not local_buf:
                return None
            payload = "\n".join(local_buf) + "\n"
            data = payload.encode("utf-8")
            try:
                _flush_t0 = time.monotonic()
                sock.sendall(data)
                questdb_ingest_success.labels(stream="ilp").inc()
                questdb_ingest_latency.labels(stream="ilp").set(time.monotonic() - _flush_t0)
                ilp_batch_records.labels(stream="ilp").set(len(local_buf))
                local_buf.clear()          # success path: clear after confirmed send
                last_flush = time.monotonic()
                return None
            except OSError as e:
                # OSError covers all socket failures cross-platform:
                # BrokenPipeError/ConnectionResetError on Linux, WinError 10053/10054 on Windows.
                questdb_ingest_fail.labels(stream="ilp").inc()
                ilp_socket_errors_total.inc()
                logger.warning(f"[Consumer-{worker_id}] ILP reconnect: {e}")
                try:
                    sock.close()
                except Exception:
                    pass
                try:
                    sock2 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    sock2.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                    sock2.connect((self.questdb_host, self.questdb_port))
                    sock2.sendall(data)     # retransmit on new socket
                    local_buf.clear()      # ← BUG FIX: clear ALSO after reconnect success
                    last_flush = time.monotonic()
                    return sock2           # caller must rebind sock
                except Exception as e2:
                    logger.error(f"[Consumer-{worker_id}] ILP reconnect failed: {e2}")
                    return None            # keep local_buf; retry next flush cycle

        try:
            consumer = KafkaConsumer(
                KAFKA_TOPIC,
                bootstrap_servers=config.kafka_bootstrap,
                value_deserializer=lambda v: json.loads(v.decode('utf-8')),
                auto_offset_reset='latest',
                group_id='questdb-ingest-group',
                max_poll_records=500,
                # Phase 3 fix: shorter commit interval shrinks rebalance replay window
                # (default was 5000ms  -  5 seconds of duplicate exposure per rebalance)
                auto_commit_interval_ms=1000,
                session_timeout_ms=30000,
                heartbeat_interval_ms=10000,
            )
        except Exception as e:
            logger.error(f"[Consumer-{worker_id}] KafkaConsumer init failed: {e}")
            sock.close()
            return

        logger.info(f"[Consumer-{worker_id}] Started")

        while not stop_event.is_set():
            try:
                t0 = time.monotonic()
                records_map = await asyncio.to_thread(consumer.poll, timeout_ms=1000)
                consumer_poll_latency_s.set(time.monotonic() - t0)

                if records_map:
                    for _tp, records in records_map.items():
                        for msg in records:
                            # Inline _process_kafka_message to avoid coroutine overhead
                            try:
                                internal_topic = msg.value.get("topic", "")
                                data_content   = msg.value.get("data", [])
                                if not internal_topic or not data_content:
                                    continue
                                data_list = data_content if isinstance(data_content, list) else [data_content]

                                for data_item in data_list:
                                    line = self._format_ilp_line_from_topic(internal_topic, data_item)
                                    if line:
                                        local_buf.append(line)
                                        with self.stats_lock:
                                            stream = internal_topic.split('.')[0]
                                            self.stats[f"{stream}_records"] += 1
                            except Exception as e:
                                logger.error(f"[Consumer-{worker_id}] Message processing error: {e}")

                # Flush on threshold OR time interval
                now = time.monotonic()
                if len(local_buf) >= FLUSH_THRESHOLD or (now - last_flush >= FLUSH_INTERVAL and local_buf):
                    new_sock = await asyncio.to_thread(_flush_local)
                    if new_sock is not None:
                        sock = new_sock  # reconnected

            except Exception as e:
                logger.error(f"[Consumer-{worker_id}] Poll error: {e}", exc_info=True)
                await asyncio.sleep(5)

        # Final flush on shutdown
        if local_buf:
            await asyncio.to_thread(_flush_local)
        try:
            sock.close()
        except Exception:
            pass
        logger.info(f"[Consumer-{worker_id}] Stopped")
    def _format_ilp_line_from_topic(self, internal_topic: str, data_item: dict) -> Optional[str]:
        """
        Hot path: convert a Kafka message dict directly into a QuestDB ILP line.
        Called by each _consumer_worker on every message; must be allocation-light.
        Returns None if the message should be skipped (e.g. unconfirmed kline).
        """
        try:
            if "kline" in internal_topic:
                # WS klines: only write confirmed candles (confirm=True)
                # REST klines: no confirm field → .get("confirm", True) defaults to write
                if not data_item.get("confirm", True):
                    return None
                return self._format_candles_ilp((
                    data_item["symbol"], data_item["interval"],
                    data_item["timestamp"], data_item["open"], data_item["high"],
                    data_item["low"], data_item["close"], data_item["volume"], data_item["turnover"]
                ))

            elif "publicTrade" in internal_topic or "trades" in internal_topic:
                if data_item.get("price", 0) * data_item.get("size", 0) < config.TRADE_FILTER_THRESHOLD:
                    return None
                return self._format_trades_ilp((
                    data_item["symbol"], data_item["trade_id"],
                    data_item["timestamp"], data_item["side"],
                    data_item["price"], data_item["size"],
                    data_item.get("exchange", "bybit"),       # cross-exchange support
                    data_item.get("market_type", "futures"),  # spot | futures
                ))

            elif "orderbook" in internal_topic:
                ob_sym = data_item.get("symbol", "")
                ob_now = int(data_item.get("timestamp", 0))
                if ob_now - _OB_LAST_WRITE_MS.get(ob_sym, 0) < OB_WRITE_INTERVAL_MS:
                    return None
                _OB_LAST_WRITE_MS[ob_sym] = ob_now
                # Coerce bids/asks to [[float,float],...] before serializing.
                # pybit WS returns [["16578.50","0.001"],...] (strings). If embedded raw
                # into an ILP quoted string, inner " characters terminate the field early
                # → QuestDB closes the TCP connection → all consumers drop simultaneously.
                # Pydantic REST path already coerces to float; match that here.
                def _coerce_levels(levels):
                    return [[float(p), float(s)] for p, s in (levels or [])]
                return self._format_orderbook_ilp((
                    data_item["symbol"], data_item["timestamp"],
                    json.dumps(_coerce_levels(data_item["bids"])),
                    json.dumps(_coerce_levels(data_item["asks"])),
                ))

            elif "liquidation" in internal_topic.lower():
                return self._format_liquidations_ilp((
                    data_item["symbol"], data_item["timestamp"],
                    data_item["side"], data_item["price"], data_item["size"]
                ))

            elif "open_interest" in internal_topic:
                return self._format_open_interest_ilp((
                    data_item["symbol"], data_item["interval"],
                    data_item["timestamp"], data_item["open_interest"]
                ))

            elif "funding_rates" in internal_topic:
                return self._format_funding_rates_ilp((
                    data_item["symbol"], data_item["funding_timestamp"],
                    data_item["funding_rate"], data_item["interval"]
                ))

            elif "long_short_ratio" in internal_topic:
                return self._format_long_short_ratio_ilp((
                    data_item["symbol"], data_item["interval"],
                    data_item["timestamp"], data_item["buy_ratio"], data_item["sell_ratio"]
                ))

        except (KeyError, TypeError) as e:
            logger.debug(f"[ILP fast path] Skipping malformed item in {internal_topic}: {e}")
        return None
        
    def _format_trades_ilp(self, data):
        symbol, trade_id, timestamp_ms, side, price, size = data[:6]
        # exchange and market_type added for cross-exchange CVD (Bybit + Binance)
        exchange    = data[6] if len(data) > 6 else "bybit"
        market_type = data[7] if len(data) > 7 else "futures"

        ts_ns = int(timestamp_ms) * 1_000_000

        # trade_id MUST be a tag so DEDUP UPSERT KEYS(timestamp, symbol, exchange, trade_id) works.
        # exchange tag prevents collision between Bybit and Binance trade IDs at same millisecond.
        tags = f"symbol={symbol},exchange={exchange},market_type={market_type},side={side},trade_id={trade_id}"
        fields = f"price={price},size={size}"

        return f"trades,{tags} {fields} {ts_ns}"
    
    def _format_candles_ilp(self, data):
        (
            symbol,
            interval,
            timestamp_ms,
            open_,
            high,
            low,
            close,
            volume,
            turnover
        ) = data

        ts_ns = int(timestamp_ms) * 1_000_000

        tags = f"symbol={symbol},interval={interval}"
        fields = (
            f"open={open_},"
            f"high={high},"
            f"low={low},"
            f"close={close},"
            f"volume={volume},"
            f"turnover={turnover}"
        )

        return f"candles,{tags} {fields} {ts_ns}"

    def _format_orderbook_ilp(self, data):
        symbol, timestamp_ms, bids_json, asks_json = data

        ts_ns = int(timestamp_ms) * 1_000_000

        tags = f"symbol={symbol}"

        # ILP string fields are delimited by ". Any " inside the value must be escaped as \".
        # After float-coercion above, json.dumps only produces digits/brackets/commas  -  no "
        # inside the array  -  but we escape defensively in case of future format changes.
        safe_bids = bids_json.replace('"', '\\"')
        safe_asks = asks_json.replace('"', '\\"')
        fields = (
            f'bids="{safe_bids}",'
            f'asks="{safe_asks}"'
        )

        return f"orderbook,{tags} {fields} {ts_ns}"
    
    def _format_funding_rates_ilp(self, data):
        symbol, timestamp_ms, funding_rate, interval = data
        
        ts_ns = int(timestamp_ms) * 1_000_000

        tags = f"symbol={symbol},interval={interval}"
        fields = f"funding_rate={funding_rate}"

        return f"funding_rates,{tags} {fields} {ts_ns}"
    
    def _format_open_interest_ilp(self, data):
        symbol, interval, timestamp_ms, open_interest = data
        
        ts_ns = int(timestamp_ms) * 1_000_000

        tags = f"symbol={symbol},interval={interval}"
        fields = f"open_interest={open_interest}"

        return f"open_interest,{tags} {fields} {ts_ns}"
    
    def _format_liquidations_ilp(self, data):
        symbol, timestamp_ms, side, price, size = data
        
        ts_ns = int(timestamp_ms) * 1_000_000

        tags = f"symbol={symbol},side={side}"
        fields = f"price={price},size={size}"

        return f"liquidations,{tags} {fields} {ts_ns}"
    
    def _format_long_short_ratio_ilp(self, data):
        symbol, interval, timestamp_ms, buy_ratio, sell_ratio = data
        
        ts_ns = int(timestamp_ms) * 1_000_000
        
        tags = f"symbol={symbol},interval={interval}"
        fields = f"buy_ratio={buy_ratio},sell_ratio={sell_ratio}"
        
        return f"long_short_ratio,{tags} {fields} {ts_ns}"
class StreamSchemas:
    """Pydantic models for REST path validation. Only Orderbook and Trade are active; other types use direct dict construction with explicit type coercion."""

    class Kline(BaseModel):
        """Validates the structure for a single candlestick."""
        symbol: str
        interval: str
        timestamp: int
        open: float
        high: float
        low: float
        close: float
        volume: float
        turnover: float

    class Orderbook(BaseModel):
        """Validates and slices order book data to the required depth."""
        symbol: str
        timestamp: int
        bids: List[List[float]]
        asks: List[List[float]]

        @field_validator('bids', 'asks', mode='before')
        @classmethod
        def slice_to_depth(cls, v: List) -> List:
            if config and isinstance(v, list):
                return v[:config.orderbook_depth]
            return v

    class Trade(BaseModel):
        symbol: str
        trade_id: str = Field(alias='execId')
        timestamp: int = Field(alias='time')
        side: str
        price: float
        size: float

    class Liquidation(BaseModel):
        symbol: str
        timestamp: int
        side: str
        price: float
        size: float


# === Phase 2: msgspec Structs for WS Hot Path ===
# These replace Pydantic on the WebSocket receive thread.
# frozen=True  -  immutable, hashable, zero-copy field access.
# Benchmarks: ~10-50× faster validation vs Pydantic; GIL released during C-level JSON decode.
if _MSGSPEC_AVAILABLE:
    class _WsKline(msgspec.Struct, frozen=True):
        symbol:    str
        interval:  str
        timestamp: int
        open:      float
        high:      float
        low:       float
        close:     float
        volume:    float
        turnover:  float
        confirm:   bool = False     # False → candle still forming; skip write

    class _WsTrade(msgspec.Struct, frozen=True):
        symbol:   str
        trade_id: str
        timestamp: int
        side:     str
        price:    float
        size:     float

    class _WsOrderbook(msgspec.Struct, frozen=True):
        symbol:    str
        timestamp: int
        bids:      list
        asks:      list

    class _WsLiquidation(msgspec.Struct, frozen=True):
        symbol:    str
        timestamp: int
        side:      str
        price:     float
        size:      float

    # Struct → plain dict helper (msgspec 0.18+)
    def _struct_to_dict(s) -> dict:
        return {f: getattr(s, f) for f in s.__struct_fields__}
else:
    # Fallback: keep Pydantic path (Phase 2 not available)
    _WsKline = _WsTrade = _WsOrderbook = _WsLiquidation = None
    def _struct_to_dict(s) -> dict: return s.model_dump()
        
class BybitV5:
    def __init__(self, client: HTTP):
        self._c = client

    def kline(self, symbol, interval, startTime, endTime=None, limit=config.kline_batch_size):
        """Pure parameter adapter  -  raises on any error. Caller handles all exceptions."""
        params = {
            "category": "linear",
            "symbol":   symbol,
            "interval": interval,
            "limit":    limit,
            "startTime": startTime,
        }
        if endTime is not None:
            params["endTime"] = endTime
        return self._c.get_kline(**params)
        # On success:  returns dict with retCode=0
        # On 10006:    pybit reads reset header, sleeps, retries (max_retries=3), then raises InvalidRequestError
        # On TCP err:  raises ConnectionError/TimeoutError (force_retry=False default)
        # On HTTP err: raises FailedRequestError

    def orderbook(self, symbol: str, depth: int = config.orderbook_depth):
        """Pure parameter adapter  -  raises on any error. Caller handles all exceptions."""
        return self._c.get_orderbook(category="linear", symbol=symbol, limit=depth)

    def trades(self, symbol: str, limit: int = None):
        """Pure parameter adapter  -  raises on any error. Caller handles all exceptions."""
        p = {"category": "linear", "symbol": symbol}
        if limit:
            p["limit"] = limit
        return self._c.get_public_trade_history(**p)

    def open_interest(self, symbol, interval, start, end=None, limit=200, cursor=None):
        """Pure parameter adapter  -  raises on any error. Caller handles all exceptions."""
        kwargs = {
            "category":    "linear",
            "symbol":      symbol,
            "intervalTime": interval,
            "startTime":   start,
            "limit":       limit,   # Bybit hard max: 200
        }
        if end is not None:
            kwargs["endTime"] = end
        if cursor:
            kwargs["cursor"] = cursor
        return self._c.get_open_interest(**kwargs)

    def funding_history_ranged(self, symbol, startTime, endTime=None, cursor=None, limit=config.funding_rate_batch_size):
        """Pure parameter adapter  -  raises on any error. Caller handles all exceptions."""
        params = {
            "category":  "linear",
            "symbol":    symbol,
            "startTime": startTime,
            "limit":     limit,
        }
        if endTime:
            params["endTime"] = endTime
        if cursor:
            params["cursor"] = cursor
        return self._c.get_funding_rate_history(**params)

    def long_short(self, symbol: str, period: str,
                   start: Optional[int] = None,
                   end: Optional[int] = None,
                   limit: int = config.LSR_batch_size,
                   cursor: Optional[str] = None):
        """Pure parameter adapter  -  raises on any error. Caller handles all exceptions."""
        params = {
            "category": "linear",
            "symbol":   symbol,
            "period":   period,
            "limit":    limit,
        }
        if start is not None:
            params["startTime"] = start
        if end is not None:
            params["endTime"] = end
        if cursor:
            params["cursor"] = cursor
        return self._c.get_long_short_ratio(**params)

    def instruments_info(self, category: str = "linear", symbol: Optional[str] = None,
                         limit: int = 1000, cursor: Optional[str] = None):
        """Pure parameter adapter  -  raises on any error. Caller handles all exceptions."""
        params = {"category": category, "limit": limit}
        if symbol:
            params["symbol"] = symbol
        if cursor:
            params["cursor"] = cursor
        return self._c.get_instruments_info(**params)


class RestDataFetcher:
    """
    Encapsulates all historical REST tasks:
      - Task queue
      - Worker pool
      - Task enqueuing
    """
    def __init__(self, rest_client: HTTP, worker_count: int, instrument_meta, producer: KafkaProducer):
        self.client = rest_client
        self.producer = producer
        self.queue = asyncio.Queue()
        self.workers = []
        self.worker_count = worker_count
        self.running = False
        self.start_ms = int(datetime.strptime(config.start_date, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000)
        self.api = BybitV5(self.client)
        self.instrument_meta = instrument_meta
        self.backfill_start_ms = self.start_ms
        self.backfill_end_ms = int(time.time() * 1000)
        self.rest_records_sent: int = 0   # total records sent to Kafka via REST path


    async def start(self):
        """Launch worker threads."""
        self.running = True
        for i in range(self.worker_count):
            task = asyncio.create_task(
                self._worker(),
                name=f"Rest-Worker-{i}"
                )
            self.workers.append(task)

    async def stop(self):
        self.running = False
        for _ in self.workers:
            await self.queue.put(None)
        await asyncio.gather(*self.workers, return_exceptions=True)

    async def enqueue_historical(self, symbols: List[str]):
        """Queue one job per symbol."""
        for symbol in symbols:
            await self.queue.put(symbol)

    async def _worker(self):
        """Asynchronous worker that fetches all historical data for a symbol."""
        task_name = asyncio.current_task().get_name()

        # Anti-microburst startup stagger.
        # Extracts the numeric suffix from task names like 'Rest-Worker-3'.
        # With 8 workers and 96 rps, workers are staggered 10.4ms apart  -  prevents
        # all 8 gorging the burst_capacity simultaneously on the first fetch.
        try:
            worker_id = int(task_name.split("-")[-1])
        except (ValueError, IndexError):
            worker_id = 0
        await asyncio.to_thread(api_limiter.stagger_worker, worker_id)

        while True:
            job = await self.queue.get()
            if job is None:
                break

            # ---------- Scheduled REST job ----------
            if isinstance(job, RestJob):
                try:
                    if job.job_type == "funding":
                        await asyncio.to_thread(
                            self._fetch_funding_rates,
                            job.symbol,
                            self.producer,
                            job.start_ts,
                            job.end_ts
                        )

                    elif job.job_type == "open_interest":
                        await asyncio.to_thread(
                            self._fetch_open_interest,
                            job.symbol,
                            job.interval,
                            self.producer,
                            job.start_ts,
                            job.end_ts
                        )

                    elif job.job_type == "long_short":
                        await asyncio.to_thread(
                            self._fetch_long_short_ratio,
                            job.symbol,
                            job.interval,
                            self.producer,
                            job.start_ts,
                            job.end_ts
                        )

                    else:
                        logger.error(f"Unknown RestJob type: {job.job_type}")

                except Exception as e:
                    logger.error(f"Scheduled REST job failed: {job} → {e}", exc_info=True)

                finally:
                    self.queue.task_done()

                continue   # ✅ valid here, goes to next job

            # ---------- Full symbol backfill ----------
            if isinstance(job, str):
                symbol = job
            else:
                logger.error(f"Unknown job type in REST worker: {job}")
                self.queue.task_done()
                continue

            # ---------- QuestDB-aware backfill start resolution ----------
            # Always query the actual DB state so restarts resume correctly:
            #   - First run:  DB empty    → checkpoint=None  → start from launch/start_date
            #   - Restart:    DB has data → checkpoint=max_ts → resume from last candle
            #   - Table drop: DB empty    → checkpoint=None  → correctly restarts from scratch
            # This prevents both full re-fetches and stale local checkpoint drift.
            launch_ts = self.instrument_meta.get_launch_ts(symbol, self.start_ms)
            global_start = max(launch_ts, self.backfill_start_ms)
            # B2 fix: compute end_ts HERE (per-job), not at constructor time.
            # Workers that start 30+ minutes into a long backfill would otherwise miss
            # all candles emitted after startup  -  creating permanent trailing gaps.
            backfill_end_ms = int(time.time() * 1000)

            logger.info(f"{task_name} BACKFILL START → {symbol}")
            
            try:
                # --- Kline Fetch ---
                try:
                    # Fix 3: WAL checkpoint race guard.
                    # QuestDB WAL tables commit asynchronously. On a first run where the
                    # kline interval='1' finishes and the consumer writes to QuestDB WAL,
                    # the WAL may not have committed yet when a *subsequent* interval queries
                    # _questdb_max_ts(). This causes checkpoint=None and a restart from
                    # global_start rather than from the end of the already-fetched data.
                    # Fix: track whether we sent any kline data this session. If yes and the
                    # next interval's checkpoint is None, wait 2s and retry once.
                    _first_kline_sent = False
                    _iv_ms_map = {
                        "1": 60_000, "3": 180_000, "5": 300_000,
                        "15": 900_000, "30": 1_800_000,
                        "60": 3_600_000, "120": 7_200_000,
                        "240": 14_400_000, "360": 21_600_000,
                        "720": 43_200_000, "D": 86_400_000,
                        "W": 604_800_000, "M": 2_592_000_000,
                    }
                    for iv in config.stream_intervals["kline"]:
                        # QuestDB-aware: resume from latest stored candle, not always start_date
                        checkpoint_ms = _questdb_max_ts("candles", symbol, iv)

                        # WAL race guard: if no checkpoint but we've sent data this session,
                        # the WAL may not have committed yet  -  wait 2s and retry once.
                        if checkpoint_ms is None and _first_kline_sent:
                            logger.debug(
                                f"{task_name} [{symbol}/{iv}] Checkpoint None after data sent  -  "
                                f"waiting 2s for WAL commit..."
                            )
                            await asyncio.sleep(2)
                            checkpoint_ms = _questdb_max_ts("candles", symbol, iv)

                        if checkpoint_ms is not None:
                            iv_ms = _iv_ms_map.get(str(iv), 60_000)
                            start_ts = checkpoint_ms + iv_ms  # resume AFTER last known candle
                            logger.info(f"{task_name} [{symbol}/{iv}] Resuming from DB checkpoint: {checkpoint_ms} ms")
                        else:
                            start_ts = global_start  # first run or table dropped

                        await asyncio.to_thread(
                            self._fetch_klines,
                            symbol,
                            iv,
                            self.producer,
                            start_ts,
                            backfill_end_ms
                        )
                        _first_kline_sent = True  # mark that at least one interval was fetched
                except Exception as e:
                    logger.error(f"{task_name} ERROR in fetching kline for {symbol}: {e}", exc_info=True)

                # --- Orderbook Fetch ---
                # Orderbook is always current snapshot  -  no checkpoint needed.
                try:
                    await asyncio.to_thread(self._fetch_orderbook, symbol, self.producer)
                except Exception as e:
                    logger.error(f"[{task_name}] ERROR during orderbook fetch for {symbol}: {e}", exc_info=True)

                # --- Trades Fetch ---
                # Bybit only returns the most recent ~1000 trades; no date-range API.
                # No checkpoint needed  -  always fetches latest slice.
                try:
                    await asyncio.to_thread(self._fetch_trades, symbol, self.producer)
                except Exception as e:
                    logger.error(f"[{task_name}] ERROR during trades fetch for {symbol}: {e}", exc_info=True)

                # --- Funding Rates Fetch (QuestDB-aware checkpoint) ---
                try:
                    fr_checkpoint = _questdb_max_ts("funding_rates", symbol)
                    fr_start = (fr_checkpoint + 1) if fr_checkpoint is not None else global_start
                    if fr_checkpoint is not None:
                        logger.info(f"{task_name} [{symbol}/funding] Resuming from DB checkpoint: {fr_checkpoint} ms")
                    await asyncio.to_thread(
                        self._fetch_funding_rates,
                        symbol, self.producer,
                        fr_start, backfill_end_ms
                    )
                except Exception as e:
                    logger.error(f"[{task_name}] ERROR during funding rates fetch for {symbol}: {e}", exc_info=True)

                # --- Open Interest Fetch (QuestDB-aware checkpoint per interval) ---
                try:
                    for interval in config.stream_intervals["open_interest"]:
                        oi_checkpoint = _questdb_max_ts("open_interest", symbol, interval)
                        oi_start = (oi_checkpoint + 1) if oi_checkpoint is not None else global_start
                        if oi_checkpoint is not None:
                            logger.info(f"{task_name} [{symbol}/oi/{interval}] Resuming from DB checkpoint: {oi_checkpoint} ms")
                        await asyncio.to_thread(
                            self._fetch_open_interest,
                            symbol, interval, self.producer,
                            oi_start, backfill_end_ms
                        )
                except Exception as e:
                    logger.error(f"[{task_name}] ERROR during open interest fetch for {symbol}: {e}", exc_info=True)

                # --- Long/Short Ratio Fetch (QuestDB-aware checkpoint per interval) ---
                try:
                    for interval in config.stream_intervals["long_short_ratio"]:
                        lsr_checkpoint = _questdb_max_ts("long_short_ratio", symbol, interval)
                        lsr_start = (lsr_checkpoint + 1) if lsr_checkpoint is not None else global_start
                        if lsr_checkpoint is not None:
                            logger.info(f"{task_name} [{symbol}/lsr/{interval}] Resuming from DB checkpoint: {lsr_checkpoint} ms")
                        await asyncio.to_thread(
                            self._fetch_long_short_ratio,
                            symbol, interval, self.producer,
                            lsr_start, backfill_end_ms
                        )
                except Exception as e:
                    logger.error(f"[{task_name}] ERROR during long/short ratio fetch for {symbol}: {e}", exc_info=True)

                logger.info(f"[{task_name}] BACKFILL DONE  → {symbol}")


            except Exception as e:
                # This outer block would catch errors in the worker logic itself
                logger.error(f"[{task_name}] FATAL WORKER ERROR for {symbol}: {e}", exc_info=True)
            finally:
                self.queue.task_done()
                
    def _fetch_klines(self, symbol: str, interval: str, producer: KafkaProducer, start_ts: int, end_ts: int):
        """
        Phase 4: hardened fetch with exponential backoff + Bybit error classification.
        Retryable errors (rate-limit, server-side 5xx) back off and retry.
        Non-retryable errors (invalid symbol, auth) abort immediately.
        """
        INTERVAL_MS = {
            "1": 60_000, "3": 180_000, "5": 300_000,
            "15": 900_000, "30": 1_800_000,
            "60": 3_600_000, "120": 7_200_000,
            "240": 14_400_000, "360": 21_600_000,
            "720": 43_200_000, "D": 86_400_000,
            "W": 604_800_000, "M": 2_592_000_000,
        }
        # Error classification: InvalidRequestError = permanent (abort), Exception = transient (retry with backoff).
        # pybit handles 10006 (rate-limit) internally before raising; generic Exception catches the result.
        MAX_RETRIES = 5
        BASE_BACKOFF = 1.0   # seconds

        limit       = min(config.kline_batch_size, 1000)
        interval_ms = INTERVAL_MS.get(str(interval))
        if interval_ms is None:
            logger.error(f"[{symbol}] Unsupported interval: {interval}")
            return

        step_ms = interval_ms * limit
        logger.info(f"[{symbol}] Kline fetch begins @ {start_ts} for interval {interval}")
        total_sent  = 0
        batch_count = 0

        while start_ts < end_ts:
            window_end_ts = min(start_ts + step_ms - 1, end_ts)
            api_limiter.wait()

            resp = None
            for attempt in range(MAX_RETRIES):
                try:
                    resp = self.api.kline(
                        symbol, interval,
                        startTime=start_ts, endTime=window_end_ts, limit=limit
                    )
                    break  # success  -  exit retry loop

                except InvalidRequestError as e:
                    # Bybit rejected this request permanently (bad symbol, wrong params, auth etc).
                    # No point retrying  -  abort the entire fetch.
                    logger.error(f"[{symbol}] Kline permanent API error: {e}")
                    return

                except Exception as e:
                    err_str = str(e)
                    if "10006" in err_str or "rate limit" in err_str.lower():
                        api_limiter.notify_rate_limit()
                    if attempt == MAX_RETRIES - 1:
                        logger.error(f"[{symbol}] Kline gave up after {MAX_RETRIES} attempts: {e}")
                        return
                    backoff = BASE_BACKOFF * (2 ** attempt)
                    logger.warning(
                        f"[{symbol}] Kline error, retry {attempt+1}/{MAX_RETRIES} in {backoff:.1f}s: {e}"
                    )
                    rest_api_errors_total.labels(endpoint='kline').inc()
                    time.sleep(backoff)
                    api_limiter.wait()



            # resp is always a success dict here  -  the retry loop returns on all failure paths.
            # If all MAX_RETRIES attempts failed, the function already returned above.
            items = resp.get("result", {}).get("list", []) if resp else []

            if not items:
                start_ts = window_end_ts + 1
                batch_count += 1
                continue

            # Batch all rows from this page into a single Kafka message.
            # Replaces 1000 individual producer.send() calls (each requiring a lock
            # acquisition + json.dumps) with one send per API page.
            # Measured improvement: ~18ms saved per page (7x faster per-page processing).
            # NOTE: Do NOT filter zero-volume or zero-close candles.
            # Bybit generates candles for minutes with no trades (volume=0, close=prev_close).
            # Dropping them creates artificial time-series gaps in QuestDB.
            # All candles must be stored to preserve minute-grid continuity.
            page_batch = []
            for item in items:
                try:
                    ts, o, h, l, c, v, turn = item
                    ts = int(ts)
                    if not (start_ts <= ts <= end_ts):
                        continue
                    page_batch.append({
                        "symbol":   symbol,
                        "interval": interval,
                        "timestamp": ts,
                        "open":     float(o),
                        "high":     float(h),
                        "low":      float(l),
                        "close":    float(c),
                        "volume":   float(v),
                        "turnover": float(turn),
                        "confirm":  True,
                    })
                except Exception as e:
                    logger.warning(f"[{symbol}] Kline item parse error: {e}")

            if page_batch:
                message = {
                    "topic":     f"kline.{interval}.{symbol}",
                    "source":    "rest",
                    "timestamp": int(time.time() * 1000),
                    "data":      page_batch,
                }
                producer.send(KAFKA_TOPIC, key=symbol.encode(), value=message)
                total_sent          += len(page_batch)
                self.rest_records_sent += len(page_batch)


            start_ts = window_end_ts + 1
            batch_count += 1

        logger.info(f"[{symbol}] ✅ Kline complete: {total_sent} sent, {batch_count} pages, interval={interval}")

    def _fetch_orderbook(self, symbol: str, producer: KafkaProducer):
        """One-shot orderbook snapshot at backfill time. No pagination needed."""
        logger.info(f"Fetching orderbook snapshot for {symbol}")
        api_limiter.wait()
        try:
            resp = self.api.orderbook(symbol)
            # pybit raises on any error  -  if we reach here, retCode == 0
            result = resp.get("result", {})
            bids = result.get("b", [])   # Bybit V5: bids key is "b"
            asks = result.get("a", [])   # Bybit V5: asks key is "a"
            ts   = result.get("ts", int(time.time() * 1000))

            if not bids or not asks:
                logger.warning(f"Empty orderbook for {symbol}")
                return

            clean_bids = [[float(b[0]), float(b[1])] for b in bids[:config.orderbook_depth]]
            clean_asks = [[float(a[0]), float(a[1])] for a in asks[:config.orderbook_depth]]

            try:
                payload_model = StreamSchemas.Orderbook(
                    symbol=symbol, timestamp=ts, bids=clean_bids, asks=clean_asks
                )
                message = {
                    "topic":     f"orderbook.{symbol}",
                    "source":    "rest",
                    "timestamp": int(time.time() * 1000),
                    "data":      [payload_model.model_dump()]
                }
                producer.send(KAFKA_TOPIC, key=symbol.encode(), value=message)
                logger.debug(f"Orderbook snapshot sent for {symbol}")
            except ValidationError as ve:
                logger.warning(f"Orderbook validation failed for {symbol}: {ve}")

        except InvalidRequestError as e:
            logger.error(f"Orderbook permanent error for {symbol}: {e}")
        except Exception as e:
            logger.warning(f"Orderbook fetch failed for {symbol}: {e}")

    def _fetch_trades(self, symbol: str, producer: KafkaProducer):
        """Fetch recent trade history from Bybit V5 /v5/market/recent-trade endpoint."""
        logger.info(f"Fetching historical trades for {symbol}")
        api_limiter.wait()
        try:
            resp = self.api.trades(symbol, limit=1000)
            # pybit raises on any error  -  if here, retCode == 0
            sent = 0
            for tr in resp["result"].get("list", []):
                try:
                    # Bybit V5 recent-trade fields:
                    #   execId → trade ID
                    #   time   → execution timestamp in ms  (NOT 'timestamp')
                    #   price, size → plain strings
                    #   side   → 'Buy' | 'Sell'
                    p  = float(tr.get("price", 0))
                    sz = float(tr.get("size",  tr.get("qty", 0)))
                    if p * sz < config.TRADE_FILTER_THRESHOLD:
                        continue

                    ts_ms = int(tr.get("time", tr.get("T", tr.get("timestamp", 0))))
                    if ts_ms == 0:
                        continue  # skip records with no timestamp

                    payload_model = StreamSchemas.Trade(
                        symbol=symbol,
                        execId=tr.get("execId", tr.get("i", "")),
                        time=ts_ms,
                        side=tr.get("side", tr.get("S", "")),
                        price=p,
                        size=sz,
                    )
                    message = {
                        "topic":     f"publicTrade.{symbol}",   # use publicTrade for consumer routing
                        "source":    "rest",
                        "timestamp": int(time.time() * 1000),
                        "data":      [payload_model.model_dump()],
                    }
                    producer.send(
                        KAFKA_TOPIC,
                        key=symbol.encode(),   # Phase 5: deterministic partition by symbol
                        value=message
                    )
                    sent += 1

                except ValidationError as ve:
                    logger.warning(f"Trade validation failed for {symbol}: {ve}")
                except Exception as e:
                    logger.debug(f"Trade item skip for {symbol}: {e}")

            logger.info(f"[{symbol}] ✅ Trades: {sent} sent")

        except Exception:
            logger.exception("Trade fetch failed for %s", symbol)
            return

    def _fetch_open_interest(self, symbol: str, interval: str, producer: KafkaProducer, start_ts: int, end_ts: int):
        """
        Fetch paginated Open Interest history from /v5/market/open-interest.
        Smallest API-supported interval: 5min. Max limit: 200 (Bybit hard cap).
        Uses cursor-based pagination within each time window to handle the edge case
        where a 200-record window boundary falls exactly on a data point.
        On 10006: calls api_limiter.notify_rate_limit() (drains bucket, exact reset).
        On other retryable codes: exponential backoff up to MAX_RETRIES.
        """
        INTERVAL_MS = {
            "5min": 5 * 60 * 1000,
            "15min": 15 * 60 * 1000,
            "30min": 30 * 60 * 1000,
            "1h": 60 * 60 * 1000,
            "4h": 4 * 60 * 60 * 1000,
            "1d": 24 * 60 * 60 * 1000
        }
        # Error classification: InvalidRequestError = permanent (abort), Exception = transient (retry with backoff).
        # pybit handles 10006 (rate-limit) internally before raising; generic Exception catches the result.
        MAX_RETRIES     = 5
        BASE_BACKOFF    = 1.0

        interval_ms = INTERVAL_MS.get(interval)
        if interval_ms is None:
            logger.error(f"[{symbol}] Unsupported OI interval: {interval}")
            return

        limit    = config.OI_batch_size   # already 200 (Bybit hard max)
        step_ms  = interval_ms * limit

        logger.info(f"[{symbol}] OI fetch begins @ {start_ts} with interval={interval}")
        total_written = 0
        batch_count   = 0

        while start_ts < end_ts:
            window_end_ts = min(start_ts + step_ms - 1, end_ts)
            cursor: Optional[str] = None   # cursor resets each new time window

            # Inner cursor-following loop: consume all pages within this time window.
            while True:
                api_limiter.wait()

                resp = None
                for attempt in range(MAX_RETRIES):
                    try:
                        resp = self.api.open_interest(
                            symbol, interval,
                            start=start_ts, end=window_end_ts,
                            limit=limit,
                            cursor=cursor if cursor else None
                        )
                        break  # success

                    except InvalidRequestError as e:
                        logger.error(f"[{symbol}] OI permanent API error: {e}")
                        return

                    except Exception as e:
                        if "10006" in str(e) or "rate limit" in str(e).lower():
                            api_limiter.notify_rate_limit()
                        if attempt == MAX_RETRIES - 1:
                            logger.error(f"[{symbol}] OI gave up after {MAX_RETRIES} attempts: {e}")
                            return
                        backoff = BASE_BACKOFF * (2 ** attempt)
                        logger.warning(f"[{symbol}] OI error, retry {attempt+1}/{MAX_RETRIES} in {backoff:.1f}s: {e}")
                        rest_api_errors_total.labels(endpoint='open_interest').inc()
                        time.sleep(backoff)
                        api_limiter.wait()

                if resp is None:
                    break  # all attempts failed  -  skip this window

                result  = resp.get("result", {})
                items   = result.get("list", [])
                cursor  = result.get("nextPageCursor") or None
                logger.debug(f"[{symbol}] → OI page {batch_count+1}: {len(items)} records, cursor={bool(cursor)}")

                # Batch all records from this OI page into a single Kafka message.
                # Consistent with _fetch_klines batching: one send() per API page.
                page_batch = []
                for itm in items:
                    ts = int(itm.get("timestamp", 0))
                    oi = float(itm.get("openInterest", 0))

                    if not (start_ts <= ts <= end_ts):
                        continue

                    try:
                        page_batch.append({
                            "symbol":        symbol,
                            "interval":      interval,
                            "timestamp":     ts,
                            "open_interest": oi,
                        })
                    except Exception as e:
                        logger.warning(f"[{symbol}] OI item parse error: {e}")

                if page_batch:
                    message = {
                        "topic":     f"open_interest.{interval}.{symbol}",
                        "source":    "rest",
                        "timestamp": int(time.time() * 1000),
                        "data":      page_batch,
                    }
                    producer.send(KAFKA_TOPIC, key=symbol.encode(), value=message)
                    total_written          += len(page_batch)
                    self.rest_records_sent += len(page_batch)


                batch_count += 1

                # Follow cursor if more pages exist in this window
                if not cursor or not items:
                    break   # no more pages  -  advance to next time window

            start_ts = window_end_ts + 1

        logger.info(f"[{symbol}] ✅ OI fetch complete. {total_written} records over {batch_count} batches.")

    def _fetch_funding_rates(self, symbol: str, producer: KafkaProducer, start_ts: int, end_ts: int):
        """
        Fetch paginated funding rate history from /v5/market/funding/history.
        Interval is event-driven (48h or 8h period from instrument metadata, not configurable).
        On 10006: calls api_limiter.notify_rate_limit() for exact-header backoff.
        """
        funding_interval_min = self.instrument_meta.get_funding_interval(symbol, default="480")
        funding_interval_ms  = int(funding_interval_min) * 60 * 1000
        limit    = min(config.funding_rate_batch_size, 200)   # Bybit hard limit
        step_ms  = funding_interval_ms * limit

        # Error classification: InvalidRequestError = permanent (abort), Exception = transient (retry with backoff).
        # pybit handles 10006 (rate-limit) internally before raising; generic Exception catches the result.
        MAX_RETRIES     = 5
        BASE_BACKOFF    = 1.0

        logger.info(f"[{symbol}] Funding rate fetch begins @ {start_ts} (interval={funding_interval_min}m)")
        total_written = 0
        batch_count   = 0

        while start_ts < end_ts:
            window_end_ts = min(start_ts + step_ms - 1, end_ts)
            api_limiter.wait()

            resp = None
            for attempt in range(MAX_RETRIES):
                try:
                    resp = self.api.funding_history_ranged(
                        symbol, startTime=start_ts, endTime=window_end_ts, limit=limit
                    )
                    break  # success

                except InvalidRequestError as e:
                    logger.error(f"[{symbol}] Funding permanent API error: {e}")
                    return

                except Exception as e:
                    if "10006" in str(e) or "rate limit" in str(e).lower():
                        api_limiter.notify_rate_limit()
                    if attempt == MAX_RETRIES - 1:
                        logger.error(f"[{symbol}] Funding gave up after {MAX_RETRIES} attempts: {e}")
                        return
                    backoff = BASE_BACKOFF * (2 ** attempt)
                    logger.warning(f"[{symbol}] Funding error, retry {attempt+1}/{MAX_RETRIES} in {backoff:.1f}s: {e}")
                    rest_api_errors_total.labels(endpoint='funding').inc()
                    time.sleep(backoff)
                    api_limiter.wait()

            if resp is None:
                start_ts = window_end_ts + 1
                batch_count += 1
                continue

            items = resp.get("result", {}).get("list", [])
            logger.debug(f"[{symbol}] → Funding page {batch_count+1}: {len(items)} records")

            if not items:
                start_ts = window_end_ts + 1
                batch_count += 1
                continue

            # Batch all records from this funding page into a single Kafka message.
            # Consistent with _fetch_klines batching: one send() per API page.
            # Funding events are sparse (4h-8h interval) so pages typically have few rows,
            # but the pattern is kept consistent with other REST fetchers.
            page_batch = []
            for itm in items:
                ts   = int(itm.get("fundingRateTimestamp", 0))
                rate = float(itm.get("fundingRate", 0))

                if not (start_ts <= ts <= end_ts):
                    continue

                try:
                    page_batch.append({
                        "symbol":            symbol,
                        "funding_timestamp": ts,
                        "funding_rate":      rate,
                        "interval":          funding_interval_min,
                    })
                except Exception as e:
                    logger.warning(f"[{symbol}] Funding item parse error: {e}")

            if page_batch:
                message = {
                    "topic":     f"funding_rates.{symbol}",
                    "source":    "rest",
                    "timestamp": int(time.time() * 1000),
                    "data":      page_batch,
                }
                producer.send(KAFKA_TOPIC, key=symbol.encode(), value=message)
                total_written          += len(page_batch)
                self.rest_records_sent += len(page_batch)

            start_ts = window_end_ts + 1
            batch_count += 1

        logger.info(f"[{symbol}] ✅ Funding fetch complete. {total_written} records over {batch_count} batches.")

    def _fetch_long_short_ratio(self, symbol: str, interval: str, producer: KafkaProducer, start_ts: int, end_ts: int):
        """
        Fetch paginated Long/Short Ratio from /v5/market/account-ratio.
        Smallest API-supported interval: 5min. Max limit: 500.
        Supports cursor-based pagination (nextPageCursor in response.result).
        On 10006: calls api_limiter.notify_rate_limit() for exact-header backoff.

        Cursor strategy:
        Within each time-window, if the API returns a nextPageCursor, follow it
        to retrieve all records for that window before advancing start_ts.
        This prevents silent data gaps when a window has > limit records.
        """
        INTERVAL_MS = {
            "5min": 5 * 60 * 1000,
            "15min": 15 * 60 * 1000,
            "30min": 30 * 60 * 1000,
            "1h": 60 * 60 * 1000,
            "4h": 4 * 60 * 60 * 1000,
            "1d": 24 * 60 * 60 * 1000
        }
        # Error classification: InvalidRequestError = permanent (abort), Exception = transient (retry with backoff).
        # pybit handles 10006 (rate-limit) internally before raising; generic Exception catches the result.
        MAX_RETRIES     = 5
        BASE_BACKOFF    = 1.0

        interval_ms = INTERVAL_MS.get(interval)
        if interval_ms is None:
            logger.error(f"[{symbol}] Unsupported LSR interval: {interval}")
            return

        limit    = min(config.LSR_batch_size, 500)   # Bybit hard limit
        step_ms  = interval_ms * limit

        logger.info(f"[{symbol}] LSR fetch begins @ {start_ts} for interval={interval}")
        total_records = 0
        page_count    = 0

        while start_ts < end_ts:
            window_end_ts = min(start_ts + step_ms - 1, end_ts)
            cursor: Optional[str] = None   # cursor resets each new time window

            # Inner cursor-pagination loop: consume all pages within this time window
            while True:
                api_limiter.wait()

                resp = None
                for attempt in range(MAX_RETRIES):
                    try:
                        resp = self.api.long_short(
                            symbol, interval,
                            start=start_ts, end=window_end_ts,
                            limit=limit,
                            cursor=cursor if cursor else None
                        )
                        break  # success

                    except InvalidRequestError as e:
                        logger.error(f"[{symbol}] LSR permanent API error: {e}")
                        return

                    except Exception as e:
                        if "10006" in str(e) or "rate limit" in str(e).lower():
                            api_limiter.notify_rate_limit()
                        if attempt == MAX_RETRIES - 1:
                            logger.error(f"[{symbol}] LSR gave up after {MAX_RETRIES} attempts: {e}")
                            return
                        backoff = BASE_BACKOFF * (2 ** attempt)
                        logger.warning(f"[{symbol}] LSR error, retry {attempt+1}/{MAX_RETRIES} in {backoff:.1f}s: {e}")
                        rest_api_errors_total.labels(endpoint='long_short').inc()
                        time.sleep(backoff)
                        api_limiter.wait()

                if resp is None:
                    break  # all attempts failed  -  skip this window


                result  = resp.get("result", {})
                items   = result.get("list", [])
                cursor  = result.get("nextPageCursor") or None
                logger.debug(f"[{symbol}] → LSR page {page_count+1}: {len(items)} records, cursor={bool(cursor)}")

                # Batch all records from this LSR page into a single Kafka message.
                # Consistent with _fetch_klines batching: one send() per API page.
                page_batch = []
                for itm in items:
                    ts   = int(itm.get("timestamp", 0))
                    buy  = float(itm.get("buyRatio",  -1))
                    sell = float(itm.get("sellRatio", -1))

                    if not (0.0 <= buy <= 1.0 and 0.0 <= sell <= 1.0):
                        logger.warning(f"[{symbol}] Invalid LSR ratios at {ts}: buy={buy}, sell={sell}")
                        continue

                    if not (start_ts <= ts <= end_ts):
                        continue

                    try:
                        page_batch.append({
                            "symbol":    symbol,
                            "interval":  interval,
                            "timestamp": ts,
                            "buy_ratio": buy,
                            "sell_ratio": sell,
                        })
                    except Exception as e:
                        logger.warning(f"[{symbol}] LSR item parse error: {e}")

                if page_batch:
                    message = {
                        "topic":     f"long_short_ratio.{interval}.{symbol}",
                        "source":    "rest",
                        "timestamp": int(time.time() * 1000),
                        "data":      page_batch,
                    }
                    producer.send(KAFKA_TOPIC, key=symbol.encode(), value=message)
                    total_records          += len(page_batch)
                    self.rest_records_sent += len(page_batch)

                page_count += 1

                # Follow cursor if more pages exist in this window
                if not cursor or not items:
                    break   # no more pages  -  advance to next time window

            start_ts = window_end_ts + 1

        logger.info(f"[{symbol}] ✅ LSR fetch complete. {total_records} records over {page_count} pages.")


# ===== Phase 6: Librarian  -  Integrity Gap Scanner & Auto-Repair =====

class Librarian:
    """
    Periodic gap scanner and targeted REST backfill trigger.

    Architecture:
    - Runs as a single asyncio.Task (no blocking threads)
    - One QuestDB SQL query per scan cycle (LAG window function)  -  finds ALL gaps
      across ALL (symbol, interval) pairs in a single round-trip
    - Targeted _fetch_klines calls per gap, controlled by asyncio.Semaphore
    - Reports to /health endpoint via _librarian global

    Gap definition: consecutive timestamps where delta > 1× expected interval.
    1× threshold detects single-candle gaps. No DST false-positive risk  -  all timestamps
    are UTC epoch milliseconds from Bybit's API; no timezone conversion occurs anywhere
    in the pipeline.

    Repair budget is metered in estimated API pages (not raw gap count) so large gaps
    (e.g. 7-hour BNBUSDT gap = 1 page) are treated the same as small gaps.
    """

    SCAN_INTERVAL_S  = 300    # full scan every 5 minutes
    COOLDOWN_S       = 90     # wait after backfill before first scan (let consumers flush)
    MAX_GAP_DAYS     = 7      # refuse to auto-repair gaps > 7 days (historical limitation)
    REPAIR_SEM       = 2      # max parallel gap-repair fetches
    PAGE_BUDGET      = 30     # max estimated API pages repaired per scan cycle

    _INTERVAL_MS: dict = {
        "1": 60_000,          "3": 180_000,      "5": 300_000,
        "15": 900_000,        "30": 1_800_000,   "60": 3_600_000,
        "120": 7_200_000,     "240": 14_400_000, "360": 21_600_000,
        "720": 43_200_000,    "D": 86_400_000,   "W": 604_800_000,
    }

    # QuestDB CASE expression mapping interval STRING → expected milliseconds
    _CASE_EXPR = """
        CASE interval
            WHEN '1'   THEN 60000     WHEN '3'   THEN 180000
            WHEN '5'   THEN 300000    WHEN '15'  THEN 900000
            WHEN '30'  THEN 1800000   WHEN '60'  THEN 3600000
            WHEN '120' THEN 7200000   WHEN '240' THEN 14400000
            WHEN '360' THEN 21600000  WHEN '720' THEN 43200000
            WHEN 'D'   THEN 86400000  WHEN 'W'   THEN 604800000
            ELSE 300000
        END
    """.strip()

    _OI_INTERVAL_MS: dict = {
        "5min": 300_000, "15min": 900_000, "30min": 1_800_000,
        "1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000,
    }
    _OI_CASE_EXPR = """
        CASE interval
            WHEN '5min'  THEN 300000  WHEN '15min' THEN 900000
            WHEN '30min' THEN 1800000 WHEN '1h'    THEN 3600000
            WHEN '4h'    THEN 14400000 WHEN '1d'   THEN 86400000
            ELSE 300000
        END
    """.strip()

    def __init__(self, rest_fetcher: "RestDataFetcher", producer):
        self.rest_fetcher  = rest_fetcher
        self.producer      = producer
        self.scan_count    = 0
        self.gaps_found    = 0
        self.gaps_repaired = 0

    async def run(self, stop_event: asyncio.Event) -> None:
        logger.info(f"[Librarian] Starting  -  {self.COOLDOWN_S}s cooldown before first scan")
        await asyncio.sleep(self.COOLDOWN_S)
        while not stop_event.is_set():
            try:
                await self._scan_cycle()
            except Exception as e:
                logger.error(f"[Librarian] Scan error: {e}", exc_info=True)
            # Sleep in chunks so we can respond to stop_event promptly
            for _ in range(self.SCAN_INTERVAL_S):
                if stop_event.is_set():
                    break
                await asyncio.sleep(1)

    async def _scan_cycle(self) -> None:
        t0   = time.monotonic()
        gaps = await asyncio.to_thread(self._find_candle_gaps)
        self.scan_count += 1
        elapsed = time.monotonic() - t0
        self.gaps_found = len(gaps)

        # Step 1: filter oversized gaps (> MAX_GAP_DAYS)
        within_limit = [
            g for g in gaps
            if (g[3] - g[2]) <= self.MAX_GAP_DAYS * 86_400_000
        ]
        oversized = len(gaps) - len(within_limit)

        # Step 2: dynamic page-budget selection.
        # gaps are already sorted by recency (gap_end_ts DESC from SQL).
        # We pick gaps greedily until the page budget is consumed.
        # A "page" = one API request fetching up to kline_batch_size candles.
        # This means: a 7-hour gap (420 candles, 1 page) costs the same budget as
        # a 2-minute gap (2 candles, 1 page). A 7-day gap costs ~11 pages.
        scheduled = []
        remaining_budget = self.PAGE_BUDGET
        for g in within_limit:
            cost = self._estimate_pages(g[2], g[3], g[1])
            if cost <= remaining_budget:
                scheduled.append(g)
                remaining_budget -= cost
            # keep scanning  -  a smaller gap might still fit in the remaining budget

        logger.info(
            f"[Librarian] Scan #{self.scan_count} in {elapsed:.2f}s  -  "
            f"{len(gaps)} gaps detected, {len(within_limit)} repairable, "
            f"{oversized} oversized (skipped), {len(scheduled)} scheduled this cycle "
            f"(budget: {self.PAGE_BUDGET - remaining_budget}/{self.PAGE_BUDGET} pages used)"
        )

        sem = asyncio.Semaphore(self.REPAIR_SEM)

        if scheduled:
            tasks = [asyncio.create_task(self._repair_gap(sem, *g)) for g in scheduled]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            repaired = sum(1 for r in results if r is True)
            self.gaps_repaired += repaired
            logger.info(f"[Librarian] Repaired {repaired}/{len(scheduled)} gaps this cycle")

        # OI gap scan and repair (shared page budget, deducted from remaining)
        try:
            oi_gaps = await asyncio.to_thread(self._find_oi_gaps)
            oi_within = [g for g in oi_gaps if (g[3] - g[2]) <= self.MAX_GAP_DAYS * 86_400_000]
            if oi_within:
                oi_tasks = [asyncio.create_task(self._repair_oi_gap(sem, *g)) for g in oi_within[:10]]
                await asyncio.gather(*oi_tasks, return_exceptions=True)
                logger.info(f"[Librarian] OI gaps: {len(oi_gaps)} found, {len(oi_within)} within limit")
        except Exception as e:
            logger.error(f"[Librarian] OI scan error: {e}", exc_info=True)

        # LSR gap scan and repair
        try:
            lsr_gaps = await asyncio.to_thread(self._find_lsr_gaps)
            lsr_within = [g for g in lsr_gaps if (g[3] - g[2]) <= self.MAX_GAP_DAYS * 86_400_000]
            if lsr_within:
                lsr_tasks = [asyncio.create_task(self._repair_lsr_gap(sem, *g)) for g in lsr_within[:10]]
                await asyncio.gather(*lsr_tasks, return_exceptions=True)
                logger.info(f"[Librarian] LSR gaps: {len(lsr_gaps)} found, {len(lsr_within)} within limit")
        except Exception as e:
            logger.error(f"[Librarian] LSR scan error: {e}", exc_info=True)

    async def _repair_gap(
        self, sem: asyncio.Semaphore,
        symbol: str, interval: str, gap_start_ms: int, gap_end_ms: int
    ) -> bool:
        async with sem:
            logger.info(
                f"[Librarian] Repairing {symbol}/{interval}: "
                f"{datetime.fromtimestamp(gap_start_ms/1000, timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')} "
                f"→ {datetime.fromtimestamp(gap_end_ms/1000, timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}"
            )
            try:
                await asyncio.to_thread(
                    self.rest_fetcher._fetch_klines,
                    symbol, interval, self.producer,
                    gap_start_ms, gap_end_ms
                )
                return True
            except Exception as e:
                logger.error(f"[Librarian] Repair failed {symbol}/{interval}: {e}")
                return False

    @staticmethod
    def _estimate_pages(gap_start_ms: int, gap_end_ms: int, interval: str) -> int:
        """
        Estimate the number of kline API pages needed to repair a gap.
        Uses config.kline_batch_size so the estimate remains correct if the
        batch size is changed in SystemConfig.

        One "page" = one API request that returns up to kline_batch_size candles.
        Examples:
          - 470-minute gap on 1m interval = 470 candles = 1 page
          - 7-day gap on 1m interval = 10,080 candles = ceil(10080/1000) = 11 pages
        """
        iv_ms = Librarian._INTERVAL_MS.get(interval, 300_000)
        if iv_ms <= 0:
            return 1
        candles_in_gap = (gap_end_ms - gap_start_ms) // iv_ms
        return max(1, ceil(candles_in_gap / config.kline_batch_size))

    def _find_candle_gaps(self) -> list:
        """
        Single QuestDB query: LAG() window function across all (symbol, interval) partitions.
        Returns: [(symbol, interval, gap_start_ms, gap_end_ms), ...] sorted by recency (newest first).
        QuestDB note: TIMESTAMP columns returned as ISO 8601 strings for WAL tables.

        Threshold: gap_ms > iv_ms  (1x, not 2x).
        1x detects single-candle gaps. 2x was previously used to guard against DST
        false positives, which is not applicable here  -  all timestamps are UTC epoch ms.

        No LIMIT in detection: the full gap list is returned so gaps_found in /health
        reflects the true count. Repair is metered separately via PAGE_BUDGET.
        """
        sql = (
            "SELECT symbol, interval, gap_start_ts, gap_end_ts "
            "FROM ( "
            "  SELECT "
            "    symbol, interval, "
            "    LAG(timestamp) OVER (PARTITION BY symbol, interval ORDER BY timestamp) AS gap_start_ts, "
            "    timestamp AS gap_end_ts, "
            "    timestamp - LAG(timestamp) OVER (PARTITION BY symbol, interval ORDER BY timestamp) AS gap_ms, "
            f"   {self._CASE_EXPR} AS iv_ms "
            "  FROM candles "
            "  WHERE timestamp > dateadd('d', -10, now()) "
            ") WHERE gap_ms > iv_ms AND gap_start_ts IS NOT NULL "
            "ORDER BY gap_end_ts DESC "
            "LIMIT 2000"
        )
        result = _questdb_exec(sql)
        if not result:
            return []

        gaps = []
        for row in result.get("dataset", []):
            try:
                symbol, interval, gap_start_raw, gap_end_raw = row
                gap_start_ms = self._to_ms_static(gap_start_raw)
                gap_end_ms   = self._to_ms_static(gap_end_raw)
                if gap_start_ms and gap_end_ms and gap_end_ms > gap_start_ms:
                    gaps.append((symbol, interval, gap_start_ms, gap_end_ms))
            except Exception as e:
                logger.debug(f"[Librarian] Gap row parse error: {e} | row={row}")
        return gaps

    @staticmethod
    def _to_ms_static(val) -> int:
        if val is None:
            return 0
        if isinstance(val, str):
            try:
                dt = datetime.fromisoformat(val.replace('Z', '+00:00'))
                return int(dt.timestamp() * 1000)
            except Exception:
                return 0
        v = int(val)
        return v // 1000 if v > _QDB_TS_US_THRESHOLD else v

    def _find_oi_gaps(self) -> list:
        sql = (
            "SELECT symbol, interval, gap_start_ts, gap_end_ts "
            "FROM ( "
            "  SELECT symbol, interval, "
            "    LAG(timestamp) OVER (PARTITION BY symbol, interval ORDER BY timestamp) AS gap_start_ts, "
            "    timestamp AS gap_end_ts, "
            "    timestamp - LAG(timestamp) OVER (PARTITION BY symbol, interval ORDER BY timestamp) AS gap_ms, "
            f"   {self._OI_CASE_EXPR} AS iv_ms "
            "  FROM open_interest "
            "  WHERE timestamp > dateadd('d', -10, now()) "
            ") WHERE gap_ms > iv_ms AND gap_start_ts IS NOT NULL "
            "ORDER BY gap_end_ts DESC LIMIT 500"
        )
        result = _questdb_exec(sql)
        if not result:
            return []
        gaps = []
        for row in result.get("dataset", []):
            try:
                symbol, interval, gap_start_raw, gap_end_raw = row
                gap_start_ms = self._to_ms_static(gap_start_raw)
                gap_end_ms   = self._to_ms_static(gap_end_raw)
                if gap_start_ms and gap_end_ms and gap_end_ms > gap_start_ms:
                    gaps.append((symbol, interval, gap_start_ms, gap_end_ms))
            except Exception as e:
                logger.debug(f"[Librarian] OI gap row parse error: {e}")
        return gaps

    def _find_lsr_gaps(self) -> list:
        sql = (
            "SELECT symbol, interval, gap_start_ts, gap_end_ts "
            "FROM ( "
            "  SELECT symbol, interval, "
            "    LAG(timestamp) OVER (PARTITION BY symbol, interval ORDER BY timestamp) AS gap_start_ts, "
            "    timestamp AS gap_end_ts, "
            "    timestamp - LAG(timestamp) OVER (PARTITION BY symbol, interval ORDER BY timestamp) AS gap_ms, "
            f"   {self._OI_CASE_EXPR} AS iv_ms "
            "  FROM long_short_ratio "
            "  WHERE timestamp > dateadd('d', -10, now()) "
            ") WHERE gap_ms > iv_ms AND gap_start_ts IS NOT NULL "
            "ORDER BY gap_end_ts DESC LIMIT 500"
        )
        result = _questdb_exec(sql)
        if not result:
            return []
        gaps = []
        for row in result.get("dataset", []):
            try:
                symbol, interval, gap_start_raw, gap_end_raw = row
                gap_start_ms = self._to_ms_static(gap_start_raw)
                gap_end_ms   = self._to_ms_static(gap_end_raw)
                if gap_start_ms and gap_end_ms and gap_end_ms > gap_start_ms:
                    gaps.append((symbol, interval, gap_start_ms, gap_end_ms))
            except Exception as e:
                logger.debug(f"[Librarian] LSR gap row parse error: {e}")
        return gaps

    async def _repair_oi_gap(self, sem: asyncio.Semaphore, symbol: str, interval: str, gap_start_ms: int, gap_end_ms: int) -> bool:
        async with sem:
            try:
                await asyncio.to_thread(
                    self.rest_fetcher._fetch_open_interest,
                    symbol, interval, self.producer, gap_start_ms, gap_end_ms
                )
                return True
            except Exception as e:
                logger.error(f"[Librarian] OI repair failed {symbol}/{interval}: {e}")
                return False

    async def _repair_lsr_gap(self, sem: asyncio.Semaphore, symbol: str, interval: str, gap_start_ms: int, gap_end_ms: int) -> bool:
        async with sem:
            try:
                await asyncio.to_thread(
                    self.rest_fetcher._fetch_long_short_ratio,
                    symbol, interval, self.producer, gap_start_ms, gap_end_ms
                )
                return True
            except Exception as e:
                logger.error(f"[Librarian] LSR repair failed {symbol}/{interval}: {e}")
                return False


# ===== WebSocket Layer: pybit-backed BybitWsFeed + BybitWsManager =====
#
# Architecture:
#   BybitWsFeed   -  one pybit WebSocket connection for up to max_symbols_per_ws symbols.
#                  pybit handles: reconnect, resubscription, OPCODE_PING/PONG, custom app-level
#                  ping (text-frame), and infinite retry.  Our callback layer adds:
#                    • metrics.ws_messages / kafka_enqueued / dropped_messages
#                    • msgspec fast-parse → _ws_raw_queue → drain_to_kafka
#                    • disconnect_ts recording for Librarian stale-tail triggers
#
#   BybitWsManager  -  thread that owns N BybitWsFeed instances (one per symbol group),
#                    health-monitors them, and creates new ones when symbols are added.
#                    All mutable state is protected by a single RLock.


class BybitWsFeed:
    """
    One pybit WebSocket connection (≤ max_symbols_per_ws symbols).
    pybit owns: connect, reconnect, resubscription after reconnect, ping/pong chain.
    We own: message parsing, queue injection, metrics.
    """

    def __init__(self, raw_queue: deque, metrics_ref: "Metrics"):
        self.raw_queue    = raw_queue
        self.metrics      = metrics_ref
        self.symbols: list = []
        self.disconnect_ts: Optional[int] = None   # ms epoch of last drop; Librarian uses this
        self._lock        = threading.Lock()
        self._ws: Optional[_PybitWebSocket] = None
        self._closed      = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self):
        """
        Create the pybit WS and block until the socket is established.
        pybit's __init__ calls _connect() which loops until connected;
        this is intentionally synchronous so subscribe() can run immediately after.
        Called from a dedicated daemon thread by _drain_queue  -  never on the manager loop.
        """
        self._ws = _PybitWebSocket(
            channel_type     = "linear",
            testnet          = config.testnet,
            ping_interval    = config.ws_ping_interval,
            ping_timeout     = config.ws_ping_timeout,
            retries          = 0,            # 0 → infinite reconnect inside pybit
            restart_on_error = True,
        )
        self._connected_at = time.monotonic()   # for warmup guard in is_alive()
        logger.info(f"[WsFeed {id(self)}] pybit WS connected")

    def subscribe(self, symbols: list, streams: set = None):
        """Subscribe to configured streams for the given symbols.

        streams: set of stream names to subscribe. None means all streams.
                 Valid values: 'kline', 'orderbook', 'trades', 'liquidations'
        """
        if self._closed or self._ws is None:
            return
        with self._lock:
            self.symbols = list(symbols)

        ws = self._ws
        cb = self._on_message
        _active = streams or {'kline', 'orderbook', 'trades', 'liquidations'}

        for symbol in symbols:
            if 'kline' in _active:
                for interval in config.stream_intervals["kline"]:
                    try:
                        ws.kline_stream(interval=interval, symbol=symbol, callback=cb)
                    except Exception as e:
                        logger.error(f"[WsFeed {id(self)}] kline subscribe failed {symbol}/{interval}: {e}")
            if 'orderbook' in _active:
                try:
                    ws.orderbook_stream(depth=config.orderbook_depth, symbol=symbol, callback=cb)
                except Exception as e:
                    logger.error(f"[WsFeed {id(self)}] orderbook subscribe failed {symbol}: {e}")
            if 'trades' in _active:
                try:
                    ws.trade_stream(symbol=symbol, callback=cb)
                except Exception as e:
                    logger.error(f"[WsFeed {id(self)}] trades subscribe failed {symbol}: {e}")
            if 'liquidations' in _active:
                try:
                    ws.all_liquidation_stream(symbol=symbol, callback=cb)
                except Exception as e:
                    logger.error(f"[WsFeed {id(self)}] liquidations subscribe failed {symbol}: {e}")

        logger.info(f"[WsFeed {id(self)}] Subscribed {len(symbols)} symbols")

    def close(self):
        """Gracefully shut down the pybit connection."""
        self._closed = True
        try:
            if self._ws is not None:
                self._ws.exit()
        except Exception:
            pass

    def is_alive(self) -> bool:
        """
        True when the underlying WS socket is connected.
        A 3-second warmup grace period avoids false-dead detection immediately
        after BybitWsFeed.start() returns but before pybit's socket thread
        has fully established the connection state.
        """
        if self._ws is None or self._closed:
            return False
        # Warmup guard: treat feed as alive during the first 3 s after connect
        warmup = getattr(self, "_connected_at", None)
        if warmup is not None and (time.monotonic() - warmup) < 3.0:
            return True
        try:
            return self._ws.is_connected()
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Internal: message callback (runs on pybit's receive thread)
    # ------------------------------------------------------------------

    def _on_message(self, message: dict):
        """
        Called by pybit for every non-ping market-data message.
        pybit has already:
          • JSON-decoded the frame
          • filtered out op==pong and subscription-ack messages
          • merged orderbook deltas into a full snapshot
        Our job: parse fields → normalise → append to raw_queue.
        """
        self.metrics.inc("ws_messages")

        topic = message.get("topic")
        if not topic:
            return

        try:
            parts      = topic.split(".")
            model_key  = parts[0]
            symbol     = parts[-1]
            ts         = message.get("ts")
            data_list  = message.get("data", [])

            # pybit's orderbook handler wraps the merged snapshot directly in "data";
            # for other streams data_list is a list of items.
            if isinstance(data_list, dict):
                data_list = [data_list]

            if not data_list:
                return

            # ── fast path: msgspec ──────────────────────────────────────
            if _MSGSPEC_AVAILABLE:
                for raw_item in data_list:
                    try:
                        if model_key == "kline":
                            interval = parts[1] if len(parts) > 2 else "1"
                            s = _WsKline(
                                symbol    = symbol,
                                interval  = interval,
                                timestamp = int(raw_item.get("start", raw_item.get("timestamp", 0))),
                                open      = float(raw_item.get("open", 0)),
                                high      = float(raw_item.get("high", 0)),
                                low       = float(raw_item.get("low", 0)),
                                close     = float(raw_item.get("close", 0)),
                                volume    = float(raw_item.get("volume", 0)),
                                turnover  = float(raw_item.get("turnover", 0)),
                                confirm   = bool(raw_item.get("confirm", False)),
                            )
                            if not s.confirm:
                                continue   # skip forming candles  -  only write closed bars

                        elif model_key == "publicTrade":
                            s = _WsTrade(
                                symbol    = symbol,
                                trade_id  = str(raw_item.get("i", raw_item.get("trade_id", ""))),
                                timestamp = int(raw_item.get("T", raw_item.get("timestamp", 0))),
                                side      = raw_item.get("S", raw_item.get("side", "")),
                                price     = float(raw_item.get("p", raw_item.get("price", 0))),
                                size      = float(raw_item.get("v", raw_item.get("size", 0))),
                            )

                        elif model_key == "orderbook":
                            # pybit already merged delta → full snapshot in message["data"]
                            s = _WsOrderbook(
                                symbol    = symbol,
                                timestamp = int(ts or 0),
                                bids      = raw_item.get("b", raw_item.get("bids", []))[:config.orderbook_depth],
                                asks      = raw_item.get("a", raw_item.get("asks", []))[:config.orderbook_depth],
                            )

                        elif model_key in ("liquidation", "allLiquidation"):
                            s = _WsLiquidation(
                                symbol    = symbol,
                                timestamp = int(raw_item.get("updatedTime", raw_item.get("timestamp", 0))),
                                side      = raw_item.get("side", ""),
                                price     = float(raw_item.get("price", 0)),
                                size      = float(raw_item.get("qty", raw_item.get("size", 0))),
                            )
                        else:
                            continue   # unknown topic prefix

                        payload_dict  = _struct_to_dict(s)
                        message_out   = {"topic": topic, "source": "ws",
                                         "timestamp": ts, "data": [payload_dict]}

                        self.raw_queue.append(message_out)
                        ws_messages_recv_total.inc()
                        self.metrics.inc("kafka_enqueued")

                    except Exception as e:
                        logger.warning(f"[WsFeed] msgspec parse failed {topic}: {e}")

            # ── Pydantic fallback ───────────────────────────────────────
            else:
                MODEL_MAP = {
                    "kline":       StreamSchemas.Kline,
                    "publicTrade": StreamSchemas.Trade,
                    "orderbook":   StreamSchemas.Orderbook,
                    "liquidation": StreamSchemas.Liquidation,
                    "allLiquidation": StreamSchemas.Liquidation,
                }
                PayloadModel = MODEL_MAP.get(model_key)
                if not PayloadModel:
                    return
                for data_item in data_list:
                    try:
                        data_item["symbol"] = symbol
                        if model_key == "kline":
                            data_item["interval"] = parts[1] if len(parts) > 2 else "1"
                            if not data_item.get("confirm", False):
                                continue
                        validated  = PayloadModel(**data_item)
                        msg_out    = {"topic": topic, "source": "ws",
                                      "timestamp": ts, "data": [validated.model_dump()]}
                        self.raw_queue.append(msg_out)
                        ws_messages_recv_total.inc()
                        self.metrics.inc("kafka_enqueued")
                    except ValidationError as e:
                        logger.warning(f"[WsFeed] Pydantic parse failed {topic}: {e}")

        except Exception as e:
            logger.error(f"[WsFeed] Fatal message handling error ({topic}): {e}")


# ===== Multi-connection manager for 550+ symbols =====

class BybitWsManager(threading.Thread):
    """
    Owns N BybitWsFeed instances.
    • Splits symbols into groups of max_symbols_per_ws per connection.
    • Monitors health every HEALTH_CHECK_INTERVAL seconds; replaces dead feeds.
    • Thread-safe: all mutations under self._rlock (RLock  -  reentrant).
    """

    HEALTH_CHECK_INTERVAL = 10   # seconds between health sweeps

    def __init__(self, stop_event):
        """
        Parameters
        ----------
        stop_event : asyncio.Event or threading.Event
            The main application stop signal.  Because asyncio.Event cannot be
            awaited/waited from a non-async thread, we always create our own
            internal threading.Event (_stop_thread) and watch it in run().
            We still hold a reference to stop_event so callers can stop us via
            the asyncio path by calling stop_from_async().
        """
        super().__init__(name="WS-Manager", daemon=True)
        self.stop_event  = stop_event          # kept for external callers / logging
        self._stop_thread = threading.Event()  # the REAL stop flag used inside run()
        self._rlock      = threading.RLock()
        self._feeds: List[BybitWsFeed] = []
        self._symbol_queue: queue.Queue = queue.Queue()
        self._all_symbols: set  = set()

    # ------------------------------------------------------------------
    # Public API (thread-safe)
    # ------------------------------------------------------------------

    def add_symbols(self, symbols: list):
        """Enqueue new symbols for subscription. Idempotent."""
        with self._rlock:
            for sym in symbols:
                if sym not in self._all_symbols:
                    self._all_symbols.add(sym)
                    self._symbol_queue.put(sym)
        logger.info(f"[WsManager] Queued {len(symbols)} symbols ({self._symbol_queue.qsize()} pending)")

    @property
    def active_feed_count(self) -> int:
        with self._rlock:
            return len(self._feeds)

    def close_all(self):
        """Gracefully close all connections."""
        with self._rlock:
            for feed in self._feeds:
                try:
                    feed.close()
                except Exception:
                    pass
            self._feeds.clear()

    def stop_from_async(self):
        """Called from an asyncio context (e.g. loop.call_soon_threadsafe) to shut down the thread."""
        self._stop_thread.set()

    # ------------------------------------------------------------------
    # Thread main loop
    # ------------------------------------------------------------------

    def run(self):
        logger.info("[WsManager] Started")
        while not self._stop_thread.is_set():
            try:
                self._drain_queue()       # create new feeds for queued symbols
                self._health_sweep()      # replace dead feeds
            except Exception as e:
                logger.error(f"[WsManager] Loop error: {e}", exc_info=True)
            self._stop_thread.wait(timeout=self.HEALTH_CHECK_INTERVAL)
        logger.info("[WsManager] Stopped")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _drain_queue(self):
        """
        Pop up to max_symbols_per_ws symbols from the queue and spin up a new
        BybitWsFeed in a daemon thread  -  so the pybit _connect() blocking call
        never stalls this manager loop.  Respects max_concurrent_ws cap.
        """
        with self._rlock:
            if self._symbol_queue.empty():
                return
            # Count live feeds + feeds still connecting (sentinel: _connected_at not set yet)
            active = len(self._feeds)
            if active >= config.max_concurrent_ws:
                logger.warning(
                    f"[WsManager] Connection cap reached ({config.max_concurrent_ws}). "
                    f"{self._symbol_queue.qsize()} symbols still queued."
                )
                return

        # Collect one batch of symbols (outside lock to avoid blocking)
        batch: List[str] = []
        while len(batch) < config.max_symbols_per_ws:
            try:
                batch.append(self._symbol_queue.get_nowait())
            except queue.Empty:
                break

        if not batch:
            return

        # Create feed object now (cheap); add to _feeds immediately so
        # active_feed_count reflects it before the socket handshake completes.
        feed = BybitWsFeed(raw_queue=_ws_raw_queue, metrics_ref=metrics)
        with self._rlock:
            self._feeds.append(feed)

        def _connect_and_subscribe():
            """Runs in a daemon thread; blocks until pybit connect, then subscribes."""
            try:
                feed.start()            # blocks until pybit _connect() establishes socket
                feed.subscribe(batch)
                logger.info(
                    f"[WsManager] Feed {id(feed)} ready → {len(batch)} symbols "
                    f"| Total feeds: {self.active_feed_count}"
                )
            except Exception as e:
                logger.error(f"[WsManager] Feed {id(feed)} failed to connect: {e}")
                # Mark as closed so health_sweep removes it and re-queues symbols
                feed._closed = True

        t = threading.Thread(target=_connect_and_subscribe, daemon=True,
                             name=f"WsFeed-Connect-{id(feed)}")
        t.start()
        logger.info(f"[WsManager] Connecting feed {id(feed)} for {len(batch)} symbols (async)")

    def _health_sweep(self):
        """
        Detect dead feeds, close them cleanly, and re-queue their symbols.
        pybit auto-reconnects internally, but if is_alive() returns False
        after the warmup window it means pybit gave up  -  we replace the feed.
        NOTE: _health_sweep does NOT fall back to REST fetching.
              REST backfill is handled separately by the Librarian's _find_stale_tails
              logic which detects missing live data and triggers a repair fetch.
        """
        dead: List[BybitWsFeed] = []
        with self._rlock:
            for feed in self._feeds:
                warmup = getattr(feed, "_connected_at", None)
                if warmup is None and not feed._closed:
                    continue   # still connecting normally, give it time
                if not feed.is_alive():
                    dead.append(feed)
            for feed in dead:
                self._feeds.remove(feed)

        with self._rlock:
            ws_active_gauge.set(len([f for f in self._feeds if f.is_alive()]))

        for feed in dead:
            syms = feed.symbols
            metrics.inc("ws_disconnects")
            feed.disconnect_ts = int(time.time() * 1000)
            logger.warning(
                f"[WsManager] Feed {id(feed)} is DEAD. "
                f"pybit attempted reconnect but failed after warmup window. "
                f"Re-queuing {len(syms)} symbols → new feed will be created in next sweep. "
                f"Live data for these symbols is INTERRUPTED  -  "
                f"Librarian stale-tail scan will detect and backfill any gaps via REST."
            )
            try:
                feed.close()
            except Exception:
                pass
            # Re-queue symbols; they'll get a new feed on next _drain_queue
            for sym in syms:
                self._symbol_queue.put(sym)


# Alias so main() requires no change (it still calls WebSocketManager)
WebSocketManager = BybitWsManager



@dataclass
class RestJob:
    job_type: str    # "funding" | "open_interest" | "long_short"
    symbol: str
    interval: Optional[str]
    start_ts: int
    end_ts: int

def start_rest_scheduler(rest_fetcher: "RestDataFetcher", loop: asyncio.AbstractEventLoop):
    """
    Starts a daemon thread that triggers periodic REST jobs aligned to wall-clock intervals.
    Uses asyncio.run_coroutine_threadsafe() to safely enqueue jobs into the asyncio.Queue
    from outside the event loop thread  -  the previous code called asyncio.run() on a sync
    function and used put_nowait() from a thread, both of which are incorrect.
    """
    LOOKBACK_MS = 60 * 60 * 1000  # 1-hour lookback per scheduled trigger

    def should_trigger(now: datetime, interval: str) -> bool:
        """True if current UTC time falls on the interval boundary (with 30s tolerance)."""
        s = now.second
        m = now.minute
        h = now.hour
        if interval == "5min":   return m % 5 == 0 and s < 30
        if interval == "15min":  return m % 15 == 0 and s < 30
        if interval == "30min":  return m % 30 == 0 and s < 30
        if interval == "1h":     return m == 0 and s < 30
        if interval == "4h":     return h % 4 == 0 and m == 0 and s < 30
        if interval == "1d":     return h == 0 and m == 0 and s < 30
        return False

    def _enqueue(job: RestJob):
        """Thread-safe enqueue into an asyncio.Queue from outside the event loop."""
        try:
            asyncio.run_coroutine_threadsafe(rest_fetcher.queue.put(job), loop)
        except Exception as e:
            logger.warning(f"[Scheduler] Failed to enqueue {job.job_type}/{job.symbol}: {e}")

    def scheduler_loop():
        logger.info("REST scheduler started")
        last_triggered: Dict[str, int] = {}  # key → last minute/hour that fired

        while True:
            try:
                now = datetime.now(timezone.utc)
                now_ms = int(now.timestamp() * 1000)
                symbols = getattr(config, "symbols", [])

                if not symbols:
                    time.sleep(10)
                    continue

                # ---- Long / Short Ratio ----
                for interval in config.stream_intervals["long_short_ratio"]:
                    key = f"lsr_{interval}"
                    if should_trigger(now, interval) and last_triggered.get(key) != now.minute:
                        last_triggered[key] = now.minute
                        s = now_ms - LOOKBACK_MS
                        for sym in symbols:
                            _enqueue(RestJob(job_type="long_short", symbol=sym,
                                             interval=interval, start_ts=s, end_ts=now_ms))

                # ---- Open Interest ----
                for interval in config.stream_intervals["open_interest"]:
                    key = f"oi_{interval}"
                    if should_trigger(now, interval) and last_triggered.get(key) != now.minute:
                        last_triggered[key] = now.minute
                        s = now_ms - LOOKBACK_MS
                        for sym in symbols:
                            _enqueue(RestJob(job_type="open_interest", symbol=sym,
                                             interval=interval, start_ts=s, end_ts=now_ms))

                # ---- Funding Rate (every 8 h) ----
                key = "funding"
                if now.minute == 0 and now.second < 30 and now.hour % 8 == 0 \
                        and last_triggered.get(key) != now.hour:
                    last_triggered[key] = now.hour
                    s = now_ms - LOOKBACK_MS
                    for sym in symbols:
                        _enqueue(RestJob(job_type="funding", symbol=sym,
                                         interval=None, start_ts=s, end_ts=now_ms))

            except Exception as e:
                logger.error(f"[Scheduler] Loop error: {e}", exc_info=True)

            time.sleep(30)  # poll every 30 s (sub-minute resolution for 5-min intervals)

    thread = Thread(target=scheduler_loop, daemon=True, name="REST-Scheduler")
    thread.start()
    return thread

async def discover_and_filter_symbols() -> List[str]:
    """
    Fetches all tradeable symbols from the API in a thread-safe manner and filters them.
    """
    logger.info("Discovering USDT perpetual symbols via API…")
    discovered: List[str] = []

    def blocking_fetch():
        """This function runs in a separate thread.

        Uses HTTP() with DEFAULT pybit retry behavior (max_retries=3).
        Rationale: max_retries=0 is ONLY appropriate on the main rest_client, where
        we intercept 10006 rate-limit retCodes before pybit consumes the reset header.
        This is a one-time startup call  -  letting pybit retry 3× internally absorbs
        the transient network blips that occur when all connections are established at
        boot. Using max_retries=0 here causes FailedRequestError on the very first
        TCP hiccup, crashing the process before any data is collected.
        """
        http_client = HTTP()   # default max_retries=3  -  intentional, NOT max_retries=0

        cursor: Optional[str] = None
        while True:
            api_limiter.wait()
            params = {"category": "linear", "limit": 1000}
            if cursor:
                params["cursor"] = cursor

            try:
                resp = http_client.get_instruments_info(**params)
            except Exception as e:
                # pybit exhausted its internal retries (3×)  -  truly unrecoverable at startup.
                # Log clearly and return; the empty discovered list will trigger sys.exit() in main().
                logger.error(f"Symbol discovery unrecoverable after pybit retries: {e}")
                return

            if resp.get("retCode") != 0:
                logger.error(f"Symbol discovery API error: {resp.get('retMsg')}")
                break

            result = resp.get("result", {})
            symbol_list = result.get("list", [])
            logger.debug(f"Fetched {len(symbol_list)} instruments in this page…")

            for item in symbol_list:
                symbol = item.get("symbol", "")
                status = item.get("status", "")
                if symbol.endswith("USDT") and status == "Trading":
                    discovered.append(symbol)

            cursor = result.get("nextPageCursor")
            if not cursor:
                break

    # Run the thread-safe function in the background
    await asyncio.to_thread(blocking_fetch)
    
    logger.info(f"Discovered {len(discovered)} total tradeable USDT symbols.")

    # --- Manual Filtering Logic (remains the same) ---
    if not config.use_manual_symbols:
        return discovered
    
    manual_symbols = [
        # Blue-chip Cryptos (High Liquidity)
        "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT",
        # Mantle ecosystem token  -  analyzed natively by the AI copilot
        "MNTUSDT",
        # "DOGEUSDT", "ADAUSDT", "AVAXUSDT", "DOTUSDT", "TRXUSDT",
                
        # # Large-cap Alts
        # "POLUSDT", "LTCUSDT", "LINKUSDT", "ATOMUSDT", "UNIUSDT",
        # "XLMUSDT", "ETCUSDT", "FILUSDT", "ALGOUSDT", "NEARUSDT",
        # "VETUSDT", "ICPUSDT", "EGLDUSDT", "XTZUSDT", "AAVEUSDT",
                
        # # High Volume/Volatility
        # "1000PEPEUSDT", "WIFUSDT", "1000BONKUSDT", "1000FLOKIUSDT", "MEMEUSDT",
        # "ORDIUSDT", "JUPUSDT", "PYTHUSDT", "JTOUSDT", "BOMEUSDT",
        # "ONDOUSDT", "WLDUSDT", "TIAUSDT", "ARBUSDT", "OPUSDT",
                
        # # Emerging Leaders
        # "HYPERUSDT", "MNTUSDT", "SEIUSDT", "SUIUSDT", "APTUSDT",
        # "INJUSDT", "STRKUSDT", "DYMUSDT", "JASMYUSDT", "BCHUSDT",
        # "LDOUSDT", "ENAUSDT", "WUSDT", "STXUSDT", "PENDLEUSDT",
        # "ARUSDT", "GRASSUSDT", "GRTUSDT", "RUNEUSDT", "APEUSDT",
                
        # # New Listings & High Momentum
        # "TAOUSDT", "ZROUSDT", "PORTALUSDT",
        # "SAGAUSDT", "PENGUUSDT", "ZETAUSDT",
        # "ALTUSDT", "MAVUSDT", "ACEUSDT", "IDUSDT",
        # "FARTCOINUSDT", "MAGICUSDT", "GALAUSDT", "AXSUSDT", "IMXUSDT"
        # # Removed (delisted/not tradeable): OMNIUSDT, ZEUSUSDT, MYROUSDT, NFPUSDT, NTRNUSDT
    ]
    
    logger.info(f"Filtering against manual list of {len(manual_symbols)} symbols…")
    filtered = [s for s in manual_symbols if s in discovered]
    dropped = set(manual_symbols) - set(filtered)
    if dropped:
        logger.warning(f"{len(dropped)} manual symbols are not currently tradeable: {sorted(list(dropped))}")

    logger.info(f"Returning {len(filtered)} manually-filtered symbols.")
    return filtered

# === Phase 1: Dead-Letter Logger ===
# Any message that fails producer.send() is written here instead of silently dropped.
# Rotate manually or via logrotate; file named with startup timestamp.
_dead_letter_log: Optional[logging.Logger] = None

def _get_dead_letter_log() -> logging.Logger:
    global _dead_letter_log
    if _dead_letter_log is None:
        os.makedirs("logs", exist_ok=True)
        _dead_letter_log = logging.getLogger("dead_letter")
        _dead_letter_log.setLevel(logging.ERROR)
        _dead_letter_log.propagate = False  # never bubble up to root logger
        fh = logging.FileHandler(
            f"logs/dead_letter_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log",
            encoding="utf-8"
        )
        fh.setFormatter(logging.Formatter("%(asctime)s | %(message)s"))
        _dead_letter_log.addHandler(fh)
    return _dead_letter_log


# === Phase 1: Health Server (port 8001) ===
from http.server import HTTPServer, BaseHTTPRequestHandler

# Rolling rows/sec tracker  -  updated by _consumer_worker and read by health endpoint
_health_start_time: float = time.time()
_health_prev_records: int = 0
_health_prev_ts: float = time.time()
_health_rows_per_sec: float = 0.0
_health_lock = threading.Lock()

def _update_rows_per_sec(current_records: int) -> None:
    """Call periodically (e.g., from monitoring loop) to compute rolling rows/sec."""
    global _health_prev_records, _health_prev_ts, _health_rows_per_sec
    with _health_lock:
        now = time.time()
        elapsed = now - _health_prev_ts
        if elapsed >= 5.0:  # recompute every 5s
            delta = current_records - _health_prev_records
            _health_rows_per_sec = round(delta / elapsed, 1) if elapsed > 0 else 0.0
            _health_prev_records = current_records
            _health_prev_ts = now

class _HealthHandler(BaseHTTPRequestHandler):
    """Minimal JSON health endpoint. Does NOT hold any app locks."""
    def do_GET(self):
        if self.path != "/health":
            self.send_response(404); self.end_headers(); return

        db_stats = {}
        total_records = 0
        if _db_manager is not None:
            with _db_manager.stats_lock:
                db_stats = dict(_db_manager.stats)
                total_records = sum(v for k, v in db_stats.items() if k.endswith('_records'))

        # Trigger rows/sec update
        _update_rows_per_sec(total_records)

        # Rate limiter diagnostics
        now_t = time.time()
        throttle_remaining = max(0.0, api_limiter._throttle_until - now_t)
        observed_rps = round(api_limiter.get_current_rps(), 1)

        uptime_s = int(now_t - _health_start_time)

        # Backfill queue depth (if rest_fetcher is available globally)
        backfill_queue = 0
        rest_fetcher_ref = globals().get("_rest_fetcher")
        if rest_fetcher_ref is not None:
            try:
                backfill_queue = rest_fetcher_ref.queue.qsize()
            except Exception:
                pass

        # ── WS Manager stats helper (reads _ws_manager global) ──────────
        def _ws_stats() -> dict:
            mgr = _ws_manager
            if mgr is None:
                return {"status": "not_started", "feeds_active": 0,
                        "symbols_subscribed": 0, "symbols_pending": 0,
                        "total_disconnects": 0}
            with mgr._rlock:
                feeds = list(mgr._feeds)
            alive_feeds      = [f for f in feeds if f.is_alive()]
            connecting_feeds = [f for f in feeds if not getattr(f, "_connected_at", None)]
            symbols_live     = sum(len(f.symbols) for f in alive_feeds)
            symbols_pending  = mgr._symbol_queue.qsize()
            ws_ok = len(alive_feeds) > 0
            return {
                "status":             "active" if ws_ok else ("connecting" if connecting_feeds else "degraded"),
                "feeds_active":       len(alive_feeds),
                "feeds_connecting":   len(connecting_feeds),
                "feeds_total":        len(feeds),
                "symbols_subscribed": symbols_live,
                "symbols_pending_reconnect": symbols_pending,
                "total_disconnects":  metrics.ws_disconnects,
                "ws_messages_recv":   metrics.ws_messages,
                "ws_messages_dropped":metrics.dropped_messages,
                "raw_queue_depth":    len(_ws_raw_queue),
            }

        payload = json.dumps({
            "status":           "ok",
            "uptime_seconds":   uptime_s,
            # ── Websocket ────────────────────────────────────────────────
            "websocket":        _ws_stats(),

            # ── Kafka ────────────────────────────────────────────────────
            "kafka_enqueued":   metrics.kafka_enqueued,

            "kafka_sent":       metrics.kafka_sent,
            "kafka_errors":     metrics.kafka_errors,
            # ── QuestDB ──────────────────────────────────────────────────
            "questdb_written":  db_stats,
            "rows_per_sec":     _health_rows_per_sec,
            "rest_records_sent": rest_fetcher_ref.rest_records_sent if rest_fetcher_ref else 0,
            # ── Rate Limiter ─────────────────────────────────────────────
            "rate_limiter": {
                "observed_rps":       observed_rps,
                "configured_rps":     api_limiter.base_rps,
                "throttle_active":    throttle_remaining > 0,
                "throttle_remaining_ms": round(throttle_remaining * 1000),
                "token_bucket_level": round(api_limiter.tokens, 2),
            },
            # ── Backfill ─────────────────────────────────────────────────
            "backfill_queue_depth": backfill_queue,
            # ── Librarian ────────────────────────────────────────────────
            "librarian": {
                "gaps_found":    _librarian.gaps_found    if _librarian else 0,
                "gaps_repaired": _librarian.gaps_repaired if _librarian else 0,
                "scan_count":    _librarian.scan_count    if _librarian else 0,
            },
        }, indent=2).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, fmt, *args):  # suppress access log noise
        pass

def start_health_server(port: int = 8001) -> HTTPServer:
    server = HTTPServer(("0.0.0.0", port), _HealthHandler)
    t = threading.Thread(target=server.serve_forever, daemon=True, name="Health-Server")
    t.start()
    logger.info(f"✅ Health server listening on http://0.0.0.0:{port}/health")
    return server


# === Health Snapshot Logger ===

class _HealthSnapshotLogger:
    """
    Background thread that periodically snapshots the health payload to a JSONL file.

    Design notes:
    - One JSON object per line (JSONL format). Each line includes a `snapshot_ts`
      wall-clock ISO timestamp so you can correlate observations without parsing
      the nested `uptime_seconds` field.
    - A new file is created per process run (timestamped filename), consistent with
      the dead-letter log naming convention. Runs never overwrite each other.
    - Interval is configurable. Rule of thumb:
        * DB < 10M rows   → 15–30s is fine
        * DB 10M–100M rows → 30–60s (QuestDB LAG scan takes longer)
        * DB > 100M rows  → 60–120s to avoid contention with Librarian scans
    - The thread reads the same module-level globals as _HealthHandler  -  no extra
      locking needed beyond what already exists in _update_rows_per_sec().
    - Daemon thread: never blocks clean shutdown.
    """

    def __init__(self, interval_s: float, log_dir: str = "logs"):
        self.interval_s = interval_s
        os.makedirs(log_dir, exist_ok=True)
        fname = f"health_snapshots_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jsonl"
        self._path = os.path.join(log_dir, fname)
        self._stop  = threading.Event()
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="Health-Snapshot-Logger"
        )
        logger.info(
            f"✅ Health snapshot logger: writing to {self._path} "
            f"every {interval_s:.0f}s"
        )

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _build_payload(self) -> dict:
        """Reconstruct the same payload as _HealthHandler.do_GET()  -  no HTTP round-trip."""
        db_stats = {}
        total_records = 0
        if _db_manager is not None:
            with _db_manager.stats_lock:
                db_stats = dict(_db_manager.stats)
                total_records = sum(v for k, v in db_stats.items() if k.endswith('_records'))

        _update_rows_per_sec(total_records)

        now_t = time.time()
        throttle_remaining = max(0.0, api_limiter._throttle_until - now_t)

        rest_fetcher_ref = globals().get("_rest_fetcher")
        backfill_queue = 0
        rest_sent = 0
        if rest_fetcher_ref is not None:
            try:
                backfill_queue = rest_fetcher_ref.queue.qsize()
                rest_sent      = rest_fetcher_ref.rest_records_sent
            except Exception:
                pass

        def _ws_stats() -> dict:
            mgr = _ws_manager
            if mgr is None:
                return {"status": "not_started", "feeds_active": 0,
                        "symbols_subscribed": 0, "symbols_pending": 0,
                        "total_disconnects": 0}
            with mgr._rlock:
                feeds = list(mgr._feeds)
            alive_feeds      = [f for f in feeds if f.is_alive()]
            connecting_feeds = [f for f in feeds if not getattr(f, "_connected_at", None)]
            symbols_live     = sum(len(f.symbols) for f in alive_feeds)
            symbols_pending  = mgr._symbol_queue.qsize()
            ws_ok = len(alive_feeds) > 0
            return {
                "status":             "active" if ws_ok else ("connecting" if connecting_feeds else "degraded"),
                "feeds_active":       len(alive_feeds),
                "feeds_connecting":   len(connecting_feeds),
                "feeds_total":        len(feeds),
                "symbols_subscribed": symbols_live,
                "symbols_pending_reconnect": symbols_pending,
                "total_disconnects":  metrics.ws_disconnects,
                "ws_messages_recv":   metrics.ws_messages,
                "ws_messages_dropped":metrics.dropped_messages,
                "raw_queue_depth":    len(_ws_raw_queue),
            }

        return {
            "snapshot_ts":      datetime.now(timezone.utc).isoformat(),
            "status":           "ok",
            "uptime_seconds":   int(now_t - _health_start_time),
            "websocket":        _ws_stats(),

            "kafka_enqueued":   metrics.kafka_enqueued,
            "kafka_sent":       metrics.kafka_sent,
            "kafka_errors":     metrics.kafka_errors,
            "questdb_written":  db_stats,
            "rows_per_sec":     _health_rows_per_sec,
            "rest_records_sent": rest_sent,
            "rate_limiter": {
                "observed_rps":          round(api_limiter.get_current_rps(), 1),
                "configured_rps":        api_limiter.base_rps,
                "throttle_active":       throttle_remaining > 0,
                "throttle_remaining_ms": round(throttle_remaining * 1000),
                "token_bucket_level":    round(api_limiter.tokens, 2),
            },
            "backfill_queue_depth": backfill_queue,
            "librarian": {
                "gaps_found":    _librarian.gaps_found    if _librarian else 0,
                "gaps_repaired": _librarian.gaps_repaired if _librarian else 0,
                "scan_count":    _librarian.scan_count    if _librarian else 0,
            },
        }

    def _run(self) -> None:
        # Stagger first write by one full interval so startup noise is skipped.
        self._stop.wait(timeout=self.interval_s)
        while not self._stop.is_set():
            try:
                payload = self._build_payload()
                line    = json.dumps(payload, separators=(',', ':')) + "\n"
                with open(self._path, "a", encoding="utf-8") as fh:
                    fh.write(line)
            except Exception as e:
                logger.warning(f"[HealthSnapshot] Write failed: {e}")
            self._stop.wait(timeout=self.interval_s)


def start_health_snapshot_logger(
    interval_s: float = 30.0,
    log_dir: str = "logs",
) -> _HealthSnapshotLogger:
    """
    Start the periodic health snapshot logger.

    Recommended interval:
        DB < 10M rows   → 15–30s
        DB 10M–100M rows → 30–60s
        DB > 100M rows  → 60–120s

    Returns the logger instance so the caller can call .stop() on shutdown.
    """
    snap = _HealthSnapshotLogger(interval_s=interval_s, log_dir=log_dir)
    snap.start()
    return snap


# === Drain Coroutine: WS raw queue → Kafka ===
async def drain_to_kafka(producer: KafkaProducer, stop_event: asyncio.Event):
    """
    Asyncio task that continuously drains _ws_raw_queue and forwards batches to Kafka.
    Runs exclusively in the event loop  -  WS threads only append(); this task only popleft().
    Batch size and poll interval are tuned for <1ms latency under burst traffic.
    """
    POLL_INTERVAL = 0.001   # 1 ms idle poll
    BATCH_MAX     = 500     # max messages drained per iteration

    logger.info("drain_to_kafka task started")
    while not stop_event.is_set():
        if not _ws_raw_queue:
            await asyncio.sleep(POLL_INTERVAL)
            continue

        # Drain up to BATCH_MAX items in one pass
        batch = []
        try:
            while _ws_raw_queue and len(batch) < BATCH_MAX:
                batch.append(_ws_raw_queue.popleft())
        except IndexError:
            pass  # deque emptied by concurrent popleft (harmless)

        kafka_drain_batch_size.set(len(batch))  # Prometheus: batch size visibility

        for msg in batch:
            try:
                # Extract symbol from topic (e.g. 'kline.1.BTCUSDT' → 'BTCUSDT')
                # so WS messages get the same partition key as REST messages.
                # Without a key, Kafka uses round-robin, scattering one symbol
                # across multiple partitions and breaking consumer offset locality.
                _ws_sym = msg.get("topic", "").split(".")[-1]
                _ws_key = _ws_sym.encode() if _ws_sym else None
                producer.send(KAFKA_TOPIC, key=_ws_key, value=msg)
                metrics.inc("kafka_sent")
            except Exception as e:
                metrics.inc("kafka_errors")
                # Write to dead-letter log  -  never silently drop
                try:
                    _get_dead_letter_log().error(
                        f"topic={msg.get('topic','?')} | error={e} | payload={str(msg)[:400]}"
                    )
                except Exception:
                    pass
                logger.error(f"[drain_to_kafka] Producer send failed: {e}")

        if batch:
            await asyncio.sleep(0)  # yield to event loop after each batch

    logger.info("drain_to_kafka task stopped")


# === Main Entry Point ===

async def main():
    # === STAGE 1: INITIAL SETUP ===
    global _db_manager, _librarian
    db_manager = OptimizedDatabaseManager()
    _db_manager = db_manager          # expose globally for health endpoint
    _librarian  = None                 # set after Stage 5 once backfill completes

    # Pre-create all tables with WAL + DEDUP UPSERT KEYS BEFORE any consumer or ILP write.
    # This is the primary defense against rebalance-induced and reconnect-induced duplicates.
    # Idempotent: safe to call on every startup (CREATE TABLE IF NOT EXISTS).
    create_questdb_tables()

    start_http_server(9100)           # Prometheus metrics (moved from 8000, FastAPI uses 8000)
    start_health_server(8001)         # JSON health endpoint
    start_health_snapshot_logger(     # periodic health snapshots → logs/health_snapshots_*.jsonl
        interval_s=30.0,              #   rule of thumb: 30s while DB < 100M rows; raise to 60-120s beyond that
        log_dir="stat_logs",
    )
    stop_event = asyncio.Event()

    try:
        producer = KafkaProducer(
            bootstrap_servers=config.kafka_bootstrap,
            # No api_version override  -  auto-negotiate with Redpanda via ApiVersions handshake.
            # Setting (2,5,0) skips the handshake → Redpanda TCP-resets → socket disconnected flood.
            value_serializer=lambda v: json.dumps(v).encode('utf-8'),
            key_serializer=lambda k: k if isinstance(k, bytes) else k.encode('utf-8') if k else None,
            # Phase 6 performance fix:
            # enable_idempotence=True forces max_in_flight=1 AND adds PID sequence tracking.
            # QuestDB DEDUP handles duplicates, so we don't need producer-level idempotency.
            # IMPORTANT: keep max_in_flight=1  -  kafka-python's ProducerBatch lacks __lt__,
            # which causes heapq.heappush() to crash the I/O thread when >1 batch is in flight.
            # (kafka-python bug: sender.py line 204, TypeError '<' not supported)
            acks='all',                     # wait for leader + ISR ack
            retries=10,
            max_in_flight_requests_per_connection=1,  # must stay 1 (kafka-python heapq bug)
            compression_type='zstd',        # zstd: ~65% ratio at ~350 MB/s vs gzip ~30 MB/s
            linger_ms=5,                    # 5ms batch window
            batch_size=262144,              # 256 KB batches
            max_request_size=10485760,      # 10MB
            max_block_ms=5000,
            request_timeout_ms=15000,
        )
        logger.info("✅ Kafka Producer connected successfully.")
    except Exception as e:
        logger.critical(f"❌ Could not connect to Kafka/Redpanda: {e}")
        sys.exit(1)

    # Phase 5: Ensure topic has KAFKA_PARTITIONS partitions before any consumer joins.
    # If topic has 1 partition (default), all 4 consumers join but 3 get no work.
    # This call is idempotent and non-blocking on success.
    ensure_kafka_partitions(config.kafka_bootstrap)

    # === STAGE 2: SYMBOL DISCOVERY ===
    # Perform all initial blocking operations before starting async tasks
    http_client = HTTP()
    api = BybitV5(http_client)
    instrument_meta_manager = InstrumentMetadataManager(api)
    
    # Run blocking network I/O in a separate thread
    await asyncio.to_thread(instrument_meta_manager.load)

    # --- Symbol Discovery Logic (copied from your original main) ---
    logger.info("Discovering symbols…")
    symbols = await discover_and_filter_symbols()
    if not symbols:
        logger.critical("Symbol list is empty! No data will be fetched. Exiting.")
        sys.exit(1)

    # Assign the final list to the global config
    config.symbols = symbols
    logger.info(f"Using a final list of {len(symbols)} symbols.")


    # === STAGE 3: INITIALIZE MANAGERS & START TASKS ===
    # Now that we have symbols, initialize all managers
    # HTTP() with default parameters:
    #   max_retries=3    -  pybit retries up to 3 times on 10006/10002/etc (reads reset header)
    #   force_retry=False  -  TCP/SSL errors raise immediately; our fetcher except blocks handle them
    #   retry_delay=3    -  pybit sleeps 3s between its internal retries on non-network errors
    # pybit handles 10006 perfectly: it reads X-Bapi-Limit-Reset-Timestamp and sleeps exactly.
    # We do NOT need max_retries=0. With max_retries=0 the while loop in _submit_request()
    # NEVER runs  -  FailedRequestError is raised before any HTTP request is sent.
    rest_client = HTTP(
        api_key=config.api_key,
        api_secret=config.api_secret,
        testnet=config.testnet,
    )
    global _rest_fetcher
    rest_fetcher = RestDataFetcher(rest_client, config.rest_worker_threads, instrument_meta_manager, producer)
    _rest_fetcher = rest_fetcher
    ws_manager = WebSocketManager(stop_event)
    global _ws_manager   # expose to health endpoint + snapshot logger

    # Phase 3: N parallel consumer coroutines each with a dedicated ILP socket.
    # Same group_id → Kafka distributes partitions across them automatically.
    # run_parallel_consumers is a coroutine that supervises all workers via asyncio.gather.
    consumer_task = asyncio.create_task(
        db_manager.run_parallel_consumers(stop_event),
        name="Parallel-Consumers"
    )

    # Start db_manager writer resources if needed (we assume ILP writer is ready in __init__)
    # If OptimizedDatabaseManager has an async start/health check, call it; otherwise skip.
    if hasattr(db_manager, "start"):
        # keep non-blocking: start may be sync or async
        maybe_start = db_manager.start()
        if asyncio.iscoroutine(maybe_start):
            await maybe_start

    await rest_fetcher.start()

    # Capture the running event loop so the scheduler thread can enqueue jobs safely.
    loop = asyncio.get_running_loop()

    # === STAGE 4: START REAL-TIME COLLECTION (before backfill so live data flows immediately) ===
    # Tables are created with WAL DEDUP, so concurrent live WS writes and backfill REST writes
    # are safe - QuestDB deduplicates by UPSERT KEYS, no ordering requirement.
    logger.info("Starting WebSocket manager and drain task...")
    ws_manager.add_symbols(symbols)
    ws_manager.start()
    _ws_manager = ws_manager
    drain_task = asyncio.create_task(drain_to_kafka(producer, stop_event), name="WS-Drain")

    # REST scheduler for periodic OI/LSR/funding refreshes
    rest_scheduler_thread = start_rest_scheduler(rest_fetcher, loop)

    # === STAGE 5: RUN HISTORICAL BACKFILL (concurrent with live WS collection) ===
    logger.info("Starting historical data backfill (runs concurrently with live WS)...")
    await rest_fetcher.enqueue_historical(symbols)
    await rest_fetcher.queue.join()
    await asyncio.sleep(0.5)
    logger.info("✅ Historical backfill complete.")

    # Apply DEDUP UPSERT KEYS after backfill to ensure all tables exist.
    # Idempotent - safe on every restart; QuestDB ignores if already enabled.
    await asyncio.to_thread(ensure_table_dedup)

    # === STAGE 6: START LIBRARIAN (after backfill - 90s internal cooldown before first scan) ===
    _librarian = Librarian(rest_fetcher, producer)
    librarian_task = asyncio.create_task(_librarian.run(stop_event), name="Librarian")
    logger.info("[Librarian] Task created - first scan in 90s")

    # === STAGE 6: GRACEFUL SHUTDOWN & MONITORING LOOP ===
    def shutdown_handler(signum, frame):
        logger.info("Shutdown signal received. Initiating graceful shutdown...")
        loop.call_soon_threadsafe(stop_event.set)
        ws_manager.stop_from_async()   # signal the threading.Event used by WS-Manager thread

    signal.signal(signal.SIGINT, shutdown_handler)
    signal.signal(signal.SIGTERM, shutdown_handler)

    try:
        while not stop_event.is_set():
            # Use db_manager.get_stats if present, otherwise use lightweight fallback
            if hasattr(db_manager, "get_stats") and callable(db_manager.get_stats):
                stats = await asyncio.to_thread(db_manager.get_stats)
            else:
                # fallback to aggregated counters in manager
                stats = {}
                stats['write_queue_size'] = "N/A"
                if hasattr(db_manager, "stats"):
                    with getattr(db_manager, "stats_lock", threading.Lock()):
                        stats['written_records'] = sum(v for k, v in db_manager.stats.items() if k.endswith('_records'))

            # Enrich STATS with live WS breakdown
            with ws_manager._rlock:
                feeds_snap = list(ws_manager._feeds)
            alive  = sum(1 for f in feeds_snap if f.is_alive())
            connecting = sum(1 for f in feeds_snap if not getattr(f, "_connected_at", None))
            syms_live  = sum(len(f.symbols) for f in feeds_snap if f.is_alive())
            logger.info(
                f"STATS | WS: {alive} active / {connecting} connecting / {len(feeds_snap)} total feeds "
                f"| {syms_live} symbols live "
                f"| pending: {ws_manager._symbol_queue.qsize()} "
                f"| DB Queue: {stats.get('write_queue_size', 'N/A')} "
                f"| Written: {stats.get('written_records', 'N/A')}"
            )

            await asyncio.sleep(config.status_report_interval)

    finally:
        logger.info("Shutting down all components...")

        # Stop accepting new WS connections and close sockets
        try:
            await asyncio.to_thread(ws_manager.close_all)
        except Exception as e:
            logger.warning("Error closing WS manager: %s", e)

        # Close Kafka producer (do in executor to avoid blocking)
        if producer:
            try:
                await asyncio.to_thread(producer.flush)
                await asyncio.to_thread(producer.close)
            except Exception as e:
                logger.warning("Error closing Kafka producer: %s", e)

        # Stop rest fetcher workers gracefully
        try:
            await rest_fetcher.stop()
        except Exception as e:
            logger.warning("Error stopping RestDataFetcher: %s", e)

        # Cancel the drain coroutine and Librarian
        for task_name, task_obj in [("drain_task", drain_task), ("librarian_task", librarian_task)]:
            try:
                task_obj.cancel()
                await asyncio.gather(task_obj, return_exceptions=True)
            except Exception as e:
                logger.warning(f"Error cancelling {task_name}: %s", e)

        # Stop scheduler thread (it should be a daemon; join with timeout)
        try:
            if rest_scheduler_thread and isinstance(rest_scheduler_thread, threading.Thread):
                rest_scheduler_thread.join(timeout=5)
        except Exception as e:
            logger.warning("Error joining REST scheduler thread: %s", e)

        # Signal consumer to stop and wait for its task
        try:
            # stop_event was set earlier
            await asyncio.wait_for(consumer_task, timeout=5)
        except asyncio.TimeoutError:
            logger.warning("Kafka consumer did not exit in time; cancelling...")
            consumer_task.cancel()
            await asyncio.gather(consumer_task, return_exceptions=True)

        # Each _consumer_worker closes its own ILP socket on exit.
        # OptimizedDatabaseManager has no shared socket to close here.
        logger.info("DB manager cleanup complete (per-worker sockets closed on task exit).")
        logger.info("Shutdown complete. Exiting.")

if __name__ == "__main__":
    asyncio.run(main())
