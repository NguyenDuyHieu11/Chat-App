"""
Presence Redis primitives (shared by heartbeat consumer + reaper).

Design:
- Online users are tracked in a Redis ZSET.
  - member: AppUser.id (string)
  - score: expiry_ts (unix seconds; now + heartbeat window)

Sharding:
- Start with 1 shard: key `online_users`
- Future: key `online_users:{shard_id}` with user_id % num_shards routing
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from django.conf import settings


@dataclass(frozen=True)
class PresenceConfig:
    base_key: str = getattr(settings, "PRESENCE_ONLINE_USERS_KEY", "online_users")
    num_shards: int = int(getattr(settings, "PRESENCE_NUM_SHARDS", 1))
    heartbeat_window_seconds: int = int(
        getattr(settings, "PRESENCE_HEARTBEAT_WINDOW_SECONDS", 30)
    )
    state_key_prefix: str = getattr(settings, "PRESENCE_STATE_KEY_PREFIX", "presence:state")
    state_ttl_seconds: int = int(getattr(settings, "PRESENCE_STATE_TTL_SECONDS", 86400))
    heartbeat_min_interval_seconds: int = int(
        getattr(settings, "PRESENCE_HEARTBEAT_MIN_INTERVAL_SECONDS", 5)
    )
    max_subscriptions_per_socket: int = int(
        getattr(settings, "PRESENCE_MAX_SUBSCRIPTIONS_PER_SOCKET", 500)
    )


def now_ts_seconds() -> int:
    return int(time.time())


def compute_shard_id(app_user_id: int, num_shards: int) -> int:
    n = max(1, int(num_shards)) # either 1 or num_shards because it prepares for later sharding.
    return int(app_user_id) % n


def online_users_key(cfg: PresenceConfig, shard_id: int | None = None) -> str:
    if cfg.num_shards <= 1:
        return cfg.base_key
    if shard_id is None:
        raise ValueError("shard_id is required when num_shards > 1")
    return f"{cfg.base_key}:{int(shard_id)}"


def online_users_key_for_user(cfg: PresenceConfig, app_user_id: int) -> str:
    if cfg.num_shards <= 1:
        return cfg.base_key
    sid = compute_shard_id(app_user_id, cfg.num_shards)
    return online_users_key(cfg, sid)


def expiry_ts(cfg: PresenceConfig, now_ts: int | None = None) -> int:
    n = now_ts if now_ts is not None else now_ts_seconds()
    return int(n) + int(cfg.heartbeat_window_seconds)


def presence_state_key(cfg: PresenceConfig, app_user_id: int) -> str:
    return f"{cfg.state_key_prefix}:{int(app_user_id)}"


def effective_status_single(conn, cfg: PresenceConfig, app_user_id: int, now_ts: int | None = None) -> tuple[str, int]:
    """
    Compute effective presence status for a single user.

    Rule:
    - If ZSET expiry missing/expired -> offline
    - Else -> semantic status from state hash (away/online); default online

    Returns: (status, timestamp)
    """
    now_val = now_ts if now_ts is not None else now_ts_seconds()
    key = online_users_key_for_user(cfg, app_user_id)
    score = conn.zscore(key, str(app_user_id))
    if score is None or float(score) < float(now_val):
        return "offline", now_val

    state = conn.hgetall(presence_state_key(cfg, app_user_id))
    status = None
    updated_ts = None
    if state:
        if b"status" in state:
            status = state[b"status"].decode("utf-8")
        if b"updated_ts" in state:
            try:
                updated_ts = int(state[b"updated_ts"].decode("utf-8"))
            except Exception:
                updated_ts = None

    return status or "online", updated_ts or now_val


LUA_CONFIRM_OFFLINE = r"""
-- Atomic check-and-delete for offline detection.
-- KEYS[1] = zset key (online_users or online_users:{shard})
-- ARGV[1] = member (AppUser.id as string)
-- ARGV[2] = current_time (unix seconds)
--
-- Returns:
--   1 if removed (confirmed offline)
--   0 otherwise (abort)

local key = KEYS[1]
local member = ARGV[1]
local now = tonumber(ARGV[2])

local score = redis.call("ZSCORE", key, member)
if not score then
  return 0
end

score = tonumber(score)
if score < now then
  redis.call("ZREM", key, member)
  return 1
end

return 0
"""


