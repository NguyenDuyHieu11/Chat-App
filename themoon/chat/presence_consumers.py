import json
import logging

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer
from django_redis import get_redis_connection

from feed.models import AppUser, Follower

from chat.presence_redis import (
    PresenceConfig,
    expiry_ts,
    now_ts_seconds,
    online_users_key_for_user,
    presence_state_key,
)

logger = logging.getLogger(__name__)


class PresenceConsumer(AsyncWebsocketConsumer):
    """
    Presence WebSocket consumer.

    Security model (Option B):
    - Client requests subscription to a target user's presence group.
    - Server authorizes that subscription using mutual-follow relationship
      in `feed.models.Follower` (AppUser -> AppUser).

    Group naming (AppUser IDs):
    - Presence group for a user: `user_{app_user_id}_status`
    """

    async def connect(self):
        self.user = self.scope.get("user")
        if not self.user or not self.user.is_authenticated:
            await self.close(code=4001)
            return

        self.app_user = await self._get_app_user()
        if not self.app_user:
            await self.close(code=4002)
            return

        self.app_user_id = int(self.app_user.id)
        self.subscribed_groups: set[str] = set()

        # Join own group (useful for multi-device sync and self status updates).
        own_group = self._status_group(self.app_user_id)
        await self.channel_layer.group_add(own_group, self.channel_name)
        self.subscribed_groups.add(own_group)

        await self.accept()
        await self.send(
            text_data=json.dumps(
                {"type": "presence.connected", "user_id": self.app_user_id}
            )
        )

    async def disconnect(self, code):
        for group in list(self.subscribed_groups):
            try:
                await self.channel_layer.group_discard(group, self.channel_name)
            except Exception:
                # Best-effort cleanup.
                pass
        self.subscribed_groups.clear()

    async def receive(self, text_data=None, bytes_data=None):
        if not text_data:
            return

        try:
            data = json.loads(text_data)
        except json.JSONDecodeError:
            await self.send(
                text_data=json.dumps({"type": "error", "message": "Invalid JSON"})
            )
            return

        msg_type = data.get("type")

        if msg_type == "presence.heartbeat":
            await self._handle_heartbeat()
            return

        if msg_type == "presence.away":
            await self._broadcast_status("away")
            return

        if msg_type == "presence.active":
            await self._broadcast_status("online")
            return

        if msg_type == "presence.subscribe":
            await self._handle_subscribe(data)
            return

        if msg_type == "presence.unsubscribe":
            await self._handle_unsubscribe(data)
            return

        await self.send(
            text_data=json.dumps({"type": "error", "message": "Unknown message type"})
        )

    async def _handle_heartbeat(self):
        """
        Heartbeat flow (Reaper pattern):
        - Update Redis ZSET member score to expiry_ts (now + window)
        - If user was missing or expired, broadcast "online" to their status group
        """
        cfg = PresenceConfig()
        now_ts = now_ts_seconds()
        key = online_users_key_for_user(cfg, self.app_user_id)
        member = str(self.app_user_id)
        new_expiry = expiry_ts(cfg, now_ts)

        conn = get_redis_connection("default")

        # Rate limit (best-effort) to reduce Redis write amplification.
        # We intentionally do not ACK heartbeats; client can keep sending.
        state_key = presence_state_key(cfg, self.app_user_id)
        try:
            last = conn.hget(state_key, "last_heartbeat_ts")
            if last is not None:
                last_ts = int(last.decode("utf-8") if isinstance(last, (bytes, bytearray)) else last)
                if (now_ts - last_ts) < cfg.heartbeat_min_interval_seconds:
                    logger.debug(
                        "Presence heartbeat dropped by rate limit",
                        extra={"app_user_id": self.app_user_id},
                    )
                    return
        except Exception:
            # If parsing fails, proceed (better to keep user online than drop).
            pass

        # Consider "online" only if existing expiry is still >= now.
        # We keep semantic status (online/away) in a separate Redis hash so that:
        # - away does not get overridden by heartbeats
        # - new subscribers can fetch a snapshot
        current = conn.zscore(key, member)
        was_online = current is not None and float(current) >= float(now_ts)

        pipe = conn.pipeline()
        pipe.zadd(key, {member: new_expiry})
        # Always update last heartbeat timestamp for observability/debugging.
        pipe.hset(state_key, mapping={"last_heartbeat_ts": now_ts})
        pipe.expire(state_key, cfg.state_ttl_seconds)
        pipe.execute()

        if not was_online:
            # If the user was previously offline, make them semantically online again.
            # (Away is a client-idle state for an active session; offline->online resets it.)
            conn.hset(
                state_key,
                mapping={"status": "online", "updated_ts": now_ts, "last_seen_ts": now_ts},
            )
            conn.expire(state_key, cfg.state_ttl_seconds)
            await self.channel_layer.group_send(
                self._status_group(self.app_user_id),
                {
                    "type": "presence.status.changed",
                    "user_id": self.app_user_id,
                    "status": "online",
                    "timestamp": now_ts,
                },
            )

    async def _broadcast_status(self, status: str):
        """
        Broadcast a semantic status change (away/online) for the current AppUser.

        Note:
        - Offline is derived by the Reaper (no heartbeat); we do not set offline here.
        - We intentionally do not mutate the ZSET expiry here; heartbeat owns liveness.
        """
        cfg = PresenceConfig()
        now_ts = now_ts_seconds()

        # Persist semantic status (online/away) so late subscribers can snapshot.
        conn = get_redis_connection("default")
        state_key = presence_state_key(cfg, self.app_user_id)
        conn.hset(state_key, mapping={"status": status, "updated_ts": now_ts})
        conn.expire(state_key, cfg.state_ttl_seconds)

        await self.channel_layer.group_send(
            self._status_group(self.app_user_id),
            {
                "type": "presence.status.changed",
                "user_id": self.app_user_id,
                "status": status,
                "timestamp": now_ts,
            },
        )

    async def _send_snapshot(self, target_id: int):
        """
        Send the current effective presence status for `target_id` to this socket.

        Rules:
        - If ZSET expiry < now OR missing => offline (wins)
        - Else: use semantic status from state hash if present (away/online)
        """
        cfg = PresenceConfig()
        conn = get_redis_connection("default")
        now_ts = now_ts_seconds()

        key = online_users_key_for_user(cfg, target_id)
        member = str(target_id)
        score = conn.zscore(key, member)
        is_online = score is not None and float(score) >= float(now_ts)

        effective_status = "offline"
        ts = now_ts

        if is_online:
            state = conn.hgetall(presence_state_key(cfg, target_id))
            # hgetall returns bytes->bytes
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
            effective_status = status or "online"
            ts = updated_ts or now_ts

        await self.send(
            text_data=json.dumps(
                {
                    "type": "presence.status",
                    "user_id": target_id,
                    "status": effective_status,
                    "timestamp": ts,
                    "snapshot": True,
                }
            )
        )

    async def _handle_subscribe(self, data: dict):
        target_id = data.get("target_user_id")
        if target_id is None:
            await self.send(
                text_data=json.dumps(
                    {"type": "error", "message": "target_user_id required"}
                )
            )
            return

        try:
            target_id = int(target_id)
        except (TypeError, ValueError):
            await self.send(
                text_data=json.dumps(
                    {"type": "error", "message": "target_user_id must be int"}
                )
            )
            return

        if target_id != self.app_user_id:
            exists = await self._app_user_exists(target_id)
            if not exists:
                await self.send(
                    text_data=json.dumps(
                        {
                            "type": "presence.subscribe.denied",
                            "target_user_id": target_id,
                            "reason": "user_not_found",
                        }
                    )
                )
                return

            allowed = await self._is_mutual_follower(self.app_user_id, target_id)
            if not allowed:
                await self.send(
                    text_data=json.dumps(
                        {
                            "type": "presence.subscribe.denied",
                            "target_user_id": target_id,
                            "reason": "not_mutual_followers",
                        }
                    )
                )
                return

        # Backpressure: cap per-socket subscriptions (prevents memory abuse).
        cfg = PresenceConfig()
        # subscribed_groups includes own group; allow +N additional.
        if len(self.subscribed_groups) >= (cfg.max_subscriptions_per_socket + 1):
            await self.send(
                text_data=json.dumps(
                    {
                        "type": "presence.subscribe.denied",
                        "target_user_id": target_id,
                        "reason": "too_many_subscriptions",
                    }
                )
            )
            return

        group = self._status_group(target_id)
        if group not in self.subscribed_groups:
            await self.channel_layer.group_add(group, self.channel_name)
            self.subscribed_groups.add(group)

        await self.send(
            text_data=json.dumps({"type": "presence.subscribe.ok", "target_user_id": target_id})
        )

        # Immediately send a snapshot so UI renders status without waiting for next event.
        await self._send_snapshot(target_id)

    async def _handle_unsubscribe(self, data: dict):
        target_id = data.get("target_user_id")
        if target_id is None:
            await self.send(
                text_data=json.dumps(
                    {"type": "error", "message": "target_user_id required"}
                )
            )
            return

        try:
            target_id = int(target_id)
        except (TypeError, ValueError):
            await self.send(
                text_data=json.dumps(
                    {"type": "error", "message": "target_user_id must be int"}
                )
            )
            return

        group = self._status_group(target_id)
        if group in self.subscribed_groups and group != self._status_group(self.app_user_id):
            await self.channel_layer.group_discard(group, self.channel_name)
            self.subscribed_groups.discard(group)

        await self.send(
            text_data=json.dumps(
                {"type": "presence.unsubscribe.ok", "target_user_id": target_id}
            )
        )

    def _status_group(self, app_user_id: int) -> str:
        return f"user_{app_user_id}_status"

    async def presence_status_changed(self, event):
        """
        Broadcast handler.
        Publishers should send events with type "presence.status.changed" and fields:
        - user_id (AppUser.id)
        - status (online|away|offline|typing...)
        - timestamp
        """
        await self.send(
            text_data=json.dumps(
                {
                    "type": "presence.status",
                    "user_id": event.get("user_id"),
                    "status": event.get("status"),
                    "timestamp": event.get("timestamp"),
                }
            )
        )

    @database_sync_to_async
    def _get_app_user(self):
        try:
            return self.user.app_user
        except AppUser.DoesNotExist:
            return None

    @database_sync_to_async
    def _app_user_exists(self, app_user_id: int) -> bool:
        return AppUser.objects.filter(id=app_user_id).exists()

    @database_sync_to_async
    def _is_mutual_follower(self, me_app_user_id: int, target_app_user_id: int) -> bool:
        # Mutual means BOTH directed edges exist.
        a_follows_b = Follower.objects.filter(
            following_user_id=me_app_user_id, followed_user_id=target_app_user_id
        ).exists()
        if not a_follows_b:
            return False
        b_follows_a = Follower.objects.filter(
            following_user_id=target_app_user_id, followed_user_id=me_app_user_id
        ).exists()
        return b_follows_a


