from django.shortcuts import render, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse

from django_redis import get_redis_connection

from .models import Conversation
from feed.models import Follower

from chat.presence_redis import PresenceConfig, effective_status_single, now_ts_seconds

@login_required
def chat_room(request, conversation_id):
    """Simple chat room view."""
    conversation = get_object_or_404(Conversation, id=conversation_id)
    
    # Optional: Check if user is a participant
    # For now, we'll let the WebSocket consumer handle authorization
    
    return render(request, 'chat/room.html', {
        'conversation_id': conversation_id,
    })


@login_required
def presence_leaderboard(request):
    """
    Return mutual followers + their effective presence status.

    This is intended for the homepage "online friends" / leaderboard list.

    Output shape:
    {
      "now": <unix_seconds>,
      "count": <n>,
      "results": [
        {"user_id": <AppUser.id>, "profile_name": "...", "status": "online|away|offline", "timestamp": <unix_seconds>}
      ]
    }
    """
    me = request.user.app_user

    # Optional limit to avoid heavy payloads.
    try:
        limit = int(request.GET.get("limit", "50"))
    except ValueError:
        limit = 50
    limit = max(1, min(limit, 500))

    # Mutual followers: A follows B and B follows A.
    following_ids = set(
        Follower.objects.filter(following_user_id=me.id).values_list("followed_user_id", flat=True)
    )
    follower_ids = set(
        Follower.objects.filter(followed_user_id=me.id).values_list("following_user_id", flat=True)
    )
    mutual_ids = list(following_ids & follower_ids)

    # Fetch basic profile info (stable ordering).
    friends = list(
        me.__class__.objects.filter(id__in=mutual_ids).only("id", "profile_name").order_by("profile_name")[:limit]
    )

    cfg = PresenceConfig()
    conn = get_redis_connection("default")
    now_ts = now_ts_seconds()

    results = []
    for friend in friends:
        status, ts = effective_status_single(conn, cfg, friend.id, now_ts=now_ts)
        results.append(
            {
                "user_id": friend.id,
                "profile_name": friend.profile_name,
                "status": status,
                "timestamp": ts,
            }
        )

    # Sort online first, then away, then offline (stable within group by profile_name).
    priority = {"online": 0, "away": 1, "offline": 2}
    results.sort(key=lambda r: (priority.get(r["status"], 99), r["profile_name"]))

    return JsonResponse(
        {
            "now": now_ts,
            "count": len(results),
            "results": results,
        }
    )
