from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ImproperlyConfigured
from django.db import close_old_connections
from django.utils import timezone
from django.utils.dateparse import parse_datetime
import json
import logging
import random
import threading
import time
from urllib import error as urllib_error
from urllib import request as urllib_request

from accounts.models import Profile
from .firebase import get_firestore_client, next_firestore_message_id
from .presence import is_user_online
from .models import DirectConversation, Message
from firebase_admin import firestore

User = get_user_model()
FIRESTORE_MESSAGES_COLLECTION = getattr(
    settings, "FIRESTORE_MESSAGES_COLLECTION", "messages"
)
USE_FIRESTORE_MESSAGES = bool(
    getattr(
        settings,
        "USE_FIRESTORE_MESSAGES",
        getattr(settings, "FIRESTORE_MESSAGES_ENABLED", False),
    )
)
AI_TYPING_MIN_DELAY_SECONDS = 1.0
AI_TYPING_MAX_DELAY_SECONDS = 5.0
logger = logging.getLogger(__name__)


def persist_chat_message(conversation_id: int, user, body: str) -> Message:
    conv = DirectConversation.objects.get(pk=conversation_id)
    if not conv.includes_user(user):
        raise PermissionError
    return Message.objects.create(conversation=conv, sender=user, body=body)


def get_ai_bot_user():
    username = getattr(settings, "CHAT_AI_BOT_USERNAME", "ai_assistant_bot")
    display_name = getattr(settings, "CHAT_AI_BOT_DISPLAY_NAME", "AI Assistant")
    bot_user, created = User.objects.get_or_create(username=username)
    if created:
        bot_user.set_unusable_password()
        bot_user.save(update_fields=["password"])
    profile = Profile.objects.filter(user=bot_user).first()
    if profile is None:
        Profile.objects.create(user=bot_user, display_name=display_name)
    elif profile.display_name != display_name:
        profile.display_name = display_name
        profile.save(update_fields=["display_name"])
    return bot_user


def ensure_ai_conversation_for_user(user):
    bot_user = get_ai_bot_user()
    if user.id == bot_user.id:
        return None
    lo, hi = (user, bot_user) if user.id < bot_user.id else (bot_user, user)
    conversation, _ = DirectConversation.objects.get_or_create(
        participant_a=lo,
        participant_b=hi,
    )
    return conversation


def is_ai_assistant_conversation(conversation: DirectConversation) -> bool:
    bot_user = get_ai_bot_user()
    return bot_user.id in (conversation.participant_a_id, conversation.participant_b_id)


def _profile_for_user(user_id: int):
    return Profile.objects.filter(user_id=user_id).first()


def is_ai_bot_user_id(user_id: int) -> bool:
    bot_user = get_ai_bot_user()
    return int(user_id) == int(bot_user.id)


def _sender_payload(sender) -> dict:
    profile = _profile_for_user(sender.id)
    is_bot = is_ai_bot_user_id(sender.id)
    return {
        "id": sender.id,
        "username": sender.username,
        "display_name": profile.display_name if profile else "",
        "is_online": True if is_bot else is_user_online(sender.id),
        "last_seen_at": profile.last_seen_at.isoformat()
        if profile and profile.last_seen_at
        else None,
    }


def _message_doc_to_payload(doc_data: dict, sender_map: dict[int, object]) -> dict:
    sender_id = int(doc_data["sender_id"])
    sender = sender_map.get(sender_id)
    if sender is None:
        sender = User.objects.filter(pk=sender_id).first()
    sender_payload = (
        _sender_payload(sender)
        if sender is not None
        else {
            "id": sender_id,
            "username": "",
            "display_name": "",
            "is_online": False,
            "last_seen_at": None,
        }
    )
    created_at_raw = doc_data.get("created_at")
    if isinstance(created_at_raw, str):
        created_at_parsed = parse_datetime(created_at_raw)
        created_at = (
            created_at_parsed.isoformat()
            if created_at_parsed is not None
            else created_at_raw
        )
    else:
        created_at = (
            created_at_raw.isoformat()
            if created_at_raw is not None
            else timezone.now().isoformat()
        )
    return {
        "id": int(doc_data["id"]),
        "sender": sender_payload,
        "body": doc_data["body"],
        "created_at": created_at,
    }


def message_to_payload(message: Message) -> dict:
    return {
        "id": message.id,
        "sender": _sender_payload(message.sender),
        "body": message.body,
        "created_at": message.created_at.isoformat(),
    }


def save_message(conversation_id: int, user, body: str) -> dict:
    conv = DirectConversation.objects.get(pk=conversation_id)
    if not conv.includes_user(user):
        raise PermissionError

    if not USE_FIRESTORE_MESSAGES:
        msg = Message.objects.create(conversation=conv, sender=user, body=body)
        return message_to_payload(msg)

    client = get_firestore_client()
    message_id = next_firestore_message_id(client)
    created_at = timezone.now()
    payload = {
        "id": message_id,
        "conversation_id": int(conversation_id),
        "sender_id": int(user.id),
        "body": body,
        "created_at": created_at,
    }
    client.collection(FIRESTORE_MESSAGES_COLLECTION).document(str(message_id)).set(payload)
    DirectConversation.objects.filter(pk=conversation_id).update(updated_at=created_at)
    return _message_doc_to_payload(payload, {user.id: user})


def get_messages(
    conversation_id: int,
    *,
    limit: int = 50,
    before_id: int | None = None,
    before_created_at=None,
):
    if not USE_FIRESTORE_MESSAGES:
        qs = (
            Message.objects.filter(conversation_id=conversation_id)
            .select_related("sender")
            .order_by("-created_at")
        )
        if before_id is not None:
            qs = qs.filter(pk__lt=before_id)
        elif before_created_at is not None:
            qs = qs.filter(created_at__lt=before_created_at)
        chunk = list(qs[: limit + 1])
        has_more = len(chunk) > limit
        chunk = chunk[:limit]
        chunk.reverse()
        return [message_to_payload(item) for item in chunk], has_more

    client = get_firestore_client()
    collection_ref = client.collection(FIRESTORE_MESSAGES_COLLECTION)
    query = (
        collection_ref.where("conversation_id", "==", int(conversation_id))
        .order_by("created_at", direction=firestore.Query.DESCENDING)
    )
    if before_created_at is not None:
        query = query.where("created_at", "<", before_created_at)
    elif before_id is not None:
        cursor_doc = collection_ref.document(str(int(before_id))).get()
        if not cursor_doc.exists:
            return [], False
        cursor_data = cursor_doc.to_dict() or {}
        if int(cursor_data.get("conversation_id", -1)) != int(conversation_id):
            return [], False
        query = query.start_after(cursor_doc)
    docs = list(query.limit(limit + 1).stream())
    rows = [doc.to_dict() for doc in docs]
    has_more = len(rows) > limit
    rows = rows[:limit]
    rows.reverse()
    sender_ids = {int(row["sender_id"]) for row in rows}
    sender_map = {user.id: user for user in User.objects.filter(id__in=sender_ids)}
    return [_message_doc_to_payload(row, sender_map) for row in rows], has_more


def get_last_message_for_conversation(conversation_id: int):
    if not USE_FIRESTORE_MESSAGES:
        msg = (
            Message.objects.filter(conversation_id=conversation_id)
            .select_related("sender")
            .order_by("-created_at")
            .first()
        )
        return None if msg is None else message_to_payload(msg)

    client = get_firestore_client()
    docs = list(
        client.collection(FIRESTORE_MESSAGES_COLLECTION)
        .where("conversation_id", "==", int(conversation_id))
        .stream()
    )
    rows = [doc.to_dict() for doc in docs]
    if not rows:
        return None
    rows.sort(key=lambda item: int(item["id"]), reverse=True)
    row = rows[0]
    sender = User.objects.filter(pk=int(row["sender_id"])).first()
    sender_map = {sender.id: sender} if sender else {}
    return _message_doc_to_payload(row, sender_map)


def get_unread_count_for_conversation(
    conversation_id: int,
    *,
    user_id: int,
    last_read_at,
) -> int:
    if not USE_FIRESTORE_MESSAGES:
        qs = Message.objects.filter(conversation_id=conversation_id).exclude(
            sender_id=user_id
        )
        if last_read_at is not None:
            qs = qs.filter(created_at__gt=last_read_at)
        return qs.count()

    client = get_firestore_client()
    query = client.collection(FIRESTORE_MESSAGES_COLLECTION).where(
        "conversation_id", "==", int(conversation_id)
    )
    count = 0
    for doc in query.stream():
        row = doc.to_dict()
        if int(row["sender_id"]) == int(user_id):
            continue
        created_at = row.get("created_at")
        if isinstance(created_at, str):
            created_at = parse_datetime(created_at)
        if (
            last_read_at is not None
            and created_at is not None
            and created_at <= last_read_at
        ):
            continue
        count += 1
    return count


def ensure_firestore_ready() -> None:
    if not USE_FIRESTORE_MESSAGES:
        raise ImproperlyConfigured(
            "USE_FIRESTORE_MESSAGES must be true to use Firestore message storage."
        )
    get_firestore_client()


def broadcast_chat_message(conversation_id: int, payload: dict) -> None:
    layer = get_channel_layer()
    if layer is None:
        return
    async_to_sync(layer.group_send)(
        f"chat_{conversation_id}",
        {"type": "chat.message", "message": payload},
    )


def broadcast_typing_event(conversation_id: int, *, user_id: int, is_typing: bool) -> None:
    layer = get_channel_layer()
    if layer is None:
        return
    async_to_sync(layer.group_send)(
        f"chat_{conversation_id}",
        {
            "type": "chat.typing",
            "typing": {
                "type": "typing",
                "user_id": int(user_id),
                "is_typing": bool(is_typing),
            },
        },
    )


def maybe_generate_ai_reply(conversation_id: int, sender, body: str) -> None:
    try:
        conversation = DirectConversation.objects.get(pk=conversation_id)
    except DirectConversation.DoesNotExist:
        return

    if not is_ai_assistant_conversation(conversation):
        return

    bot_user = get_ai_bot_user()
    if sender.id == bot_user.id:
        return

    threading.Thread(
        target=_generate_ai_reply_worker,
        args=(conversation_id, body),
        daemon=True,
    ).start()


def _generate_ai_reply_worker(conversation_id: int, user_message: str) -> None:
    close_old_connections()
    bot_user = get_ai_bot_user()
    broadcast_typing_event(conversation_id, user_id=bot_user.id, is_typing=True)
    try:
        delay_seconds = random.uniform(
            AI_TYPING_MIN_DELAY_SECONDS,
            AI_TYPING_MAX_DELAY_SECONDS,
        )
        time.sleep(delay_seconds)
        reply_text = _build_ai_reply(user_message, conversation_id=conversation_id, bot_user_id=bot_user.id)
        payload = save_message(conversation_id, bot_user, reply_text)
        broadcast_chat_message(conversation_id, payload)
    except Exception:
        # Keep chat stable even if AI response generation fails, but log root cause.
        logger.exception("AI reply worker failed for conversation_id=%s", conversation_id)
    finally:
        broadcast_typing_event(conversation_id, user_id=bot_user.id, is_typing=False)
        close_old_connections()


def _build_ai_reply(user_message: str, *, conversation_id: int, bot_user_id: int) -> str:
    openai_reply = _generate_openai_reply(
        user_message,
        conversation_id=conversation_id,
        bot_user_id=bot_user_id,
    )
    if openai_reply:
        return openai_reply
    return _build_fallback_ai_reply(user_message, conversation_id=conversation_id, bot_user_id=bot_user_id)


def _generate_openai_reply(user_message: str, *, conversation_id: int, bot_user_id: int) -> str | None:
    api_key = getattr(settings, "OPENAI_API_KEY", "").strip()
    model = getattr(settings, "OPENAI_MODEL", "gpt-4.1-mini").strip() or "gpt-4.1-mini"
    base_url = getattr(settings, "OPENAI_BASE_URL", "https://api.openai.com/v1").strip().rstrip("/")
    timeout_seconds = float(getattr(settings, "OPENAI_TIMEOUT_SECONDS", 20))

    if not api_key:
        return None

    messages, _ = get_messages(conversation_id, limit=12)
    chat_messages = [
        {
            "role": "assistant" if int(item["sender"]["id"]) == int(bot_user_id) else "user",
            "content": item["body"],
        }
        for item in messages
        if item.get("body")
    ]
    if not chat_messages:
        chat_messages = [{"role": "user", "content": user_message}]

    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a concise and helpful AI assistant inside a Telegram-like chat app. "
                    "Respond directly, be practical, and keep answers short unless the user asks for details."
                ),
            },
            *chat_messages,
        ],
        "temperature": 0.7,
    }

    request = urllib_request.Request(
        url=f"{base_url}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    try:
        with urllib_request.urlopen(request, timeout=timeout_seconds) as response:
            raw_body = response.read().decode("utf-8")
            response_json = json.loads(raw_body)
            choices = response_json.get("choices") or []
            if not choices:
                return None
            message = choices[0].get("message") or {}
            content = message.get("content")
            if isinstance(content, str):
                cleaned = content.strip()
                return cleaned or None
            return None
    except urllib_error.HTTPError as exc:
        response_body = ""
        try:
            response_body = exc.read().decode("utf-8")
        except Exception:
            response_body = "<unable to read error body>"
        logger.warning(
            "OpenAI request failed with status=%s reason=%s body=%s",
            exc.code,
            exc.reason,
            response_body,
        )
        return None
    except (urllib_error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        logger.warning("OpenAI request failed: %s", exc)
        return None


def _build_fallback_ai_reply(user_message: str, *, conversation_id: int, bot_user_id: int) -> str:
    text = user_message.strip()
    text_lower = text.lower()

    if any(greeting in text_lower for greeting in ("hello", "hi", "hey")):
        return "Hi! I am your AI assistant. Tell me what you want to work on, and I will help you step by step."
    if "help" in text_lower:
        return "Sure - I can help. Share your goal, constraints, and timeline, and I will propose a concrete plan."
    if text.endswith("?"):
        return f"Good question. My short answer is: focus on the core requirement first, then iterate safely. About '{text}', I can break it down into practical steps if you want."

    messages, _ = get_messages(conversation_id, limit=8)
    recent_user_points = [
        msg["body"].strip()
        for msg in messages
        if int(msg["sender"]["id"]) != int(bot_user_id) and msg["body"].strip()
    ]
    recent_hint = recent_user_points[-2] if len(recent_user_points) >= 2 else None
    if recent_hint:
        return (
            f"I got it. Building on your earlier point '{recent_hint}', the next best step is to implement a small version first, verify it, and then expand."
        )

    return f"Understood. For '{text}', I recommend starting with a minimal working version, validating behavior, and then refining UX and edge cases."
