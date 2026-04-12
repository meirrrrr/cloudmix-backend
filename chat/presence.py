from django.core.cache import cache


def _presence_key(user_id: int) -> str:
    return f"chat:presence:user:{user_id}:connections"


def increment_user_connections(user_id: int) -> int:
    key = _presence_key(user_id)
    cache.add(key, 0, timeout=None)
    try:
        return int(cache.incr(key))
    except (ValueError, NotImplementedError, TypeError):
        count = int(cache.get(key, 0)) + 1
        cache.set(key, count, timeout=None)
        return count


def decrement_user_connections(user_id: int) -> int:
    key = _presence_key(user_id)
    cache.add(key, 0, timeout=None)
    try:
        count = int(cache.decr(key))
    except (ValueError, NotImplementedError, TypeError):
        count = int(cache.get(key, 0)) - 1

    if count < 0:
        count = 0

    cache.set(key, count, timeout=None)
    return count


def get_user_connections_count(user_id: int) -> int:
    return int(cache.get(_presence_key(user_id), 0) or 0)


def is_user_online(user_id: int) -> bool:
    return get_user_connections_count(user_id) > 0

