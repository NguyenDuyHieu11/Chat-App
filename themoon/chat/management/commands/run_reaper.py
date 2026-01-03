import time

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.core.management.base import BaseCommand
from django_redis import get_redis_connection

from chat.presence_redis import (
    LUA_CONFIRM_OFFLINE,
    PresenceConfig,
    now_ts_seconds,
    online_users_key,
    presence_state_key,
)


class Command(BaseCommand):
    help = "Run Redis ZSET polling reaper (offline detection) and broadcast status changes via Django Channels."

    def add_arguments(self, parser):
        parser.add_argument(
            "--redis-alias",
            default="default",
            help="django-redis cache alias to use (default uses Redis DB 1 in this project).",
        )
        parser.add_argument(
            "--poll-interval",
            type=float,
            default=1.0,
            help="Seconds to sleep between polls.",
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=500,
            help="Max number of expired users to process per poll.",
        )
        parser.add_argument(
            "--shard-id",
            type=int,
            default=0,
            help="Shard index to reap (only used when PRESENCE_NUM_SHARDS > 1).",
        )
        parser.add_argument(
            "--all-shards",
            action="store_true",
            help="If set and PRESENCE_NUM_SHARDS > 1, sweep all shards in this process.",
        )

    def handle(self, *args, **options):
        redis_alias = options["redis_alias"]
        poll_interval = float(options["poll_interval"])
        batch_size = int(options["batch_size"])
        shard_id = int(options["shard_id"])
        all_shards = bool(options["all_shards"])

        cfg = PresenceConfig()
        if cfg.num_shards > 1 and (shard_id < 0 or shard_id >= cfg.num_shards):
            raise ValueError("--shard-id must be within [0, PRESENCE_NUM_SHARDS)")

        if all_shards and cfg.num_shards <= 1:
            all_shards = False

        conn = get_redis_connection(redis_alias)
        confirm_offline = conn.register_script(LUA_CONFIRM_OFFLINE)
        channel_layer = get_channel_layer()

        self.stdout.write(
            self.style.SUCCESS(
                f"Presence reaper started: base_key={cfg.base_key} num_shards={cfg.num_shards} "
                f"mode={'all_shards' if all_shards else 'single_shard'} poll_interval={poll_interval}s batch_size={batch_size}"
            )
        )

        def process_key(key: str):
            now_ts = now_ts_seconds()
            candidates = conn.zrangebyscore(
                key,
                min="-inf",
                max=now_ts,
                start=0,
                num=batch_size,
            )

            if not candidates:
                return

            for raw_member in candidates:
                member = (
                    raw_member.decode("utf-8")
                    if isinstance(raw_member, (bytes, bytearray))
                    else str(raw_member)
                )

                try:
                    confirmed = confirm_offline(keys=[key], args=[member, now_ts])
                except Exception:
                    confirmed = 0

                if confirmed == 1:
                    # Persist semantic state for snapshots.
                    state_key = presence_state_key(cfg, int(member) if member.isdigit() else member)
                    try:
                        conn.hset(
                            state_key,
                            mapping={"status": "offline", "updated_ts": now_ts, "last_seen_ts": now_ts},
                        )
                        conn.expire(state_key, cfg.state_ttl_seconds)
                    except Exception:
                        pass

                    group = f"user_{member}_status"
                    async_to_sync(channel_layer.group_send)(
                        group,
                        {
                            "type": "presence.status.changed",
                            "user_id": int(member) if member.isdigit() else member,
                            "status": "offline",
                            "timestamp": now_ts,
                        },
                    )

        while True:
            if all_shards:
                for sid in range(cfg.num_shards):
                    process_key(online_users_key(cfg, sid))
            else:
                key = online_users_key(cfg, shard_id) if cfg.num_shards > 1 else online_users_key(cfg)
                process_key(key)

            time.sleep(poll_interval)


