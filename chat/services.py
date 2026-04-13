from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ImproperlyConfigured
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from accounts.models import Profile
from .firebase import get_firestore_client, next_firestore_message_id
from .presence import is_user_online
from .models import DirectConversation, Message
from firebase_admin import firestore

User = get_user_model()
FIRESTORE_MESSAGES_COLLECTION = getattr(
    settings, "FIRESTORE_MESSAGES_COLLECTION", "messages"
)
FIRESTORE_MESSAGES_ENABLED = bool(
    getattr(settings, "FIRESTORE_MESSAGES_ENABLED", False)
)


def persist_chat_message(conversation_id: int, user, body: str) -> Message:
    conv = DirectConversation.objects.get(pk=conversation_id)
    if not conv.includes_user(user):
        raise PermissionError
    return Message.objects.create(conversation=conv, sender=user, body=body)


def _profile_for_user(user_id: int):
    return Profile.objects.filter(user_id=user_id).first()


def _sender_payload(sender) -> dict:
    profile = _profile_for_user(sender.id)
    return {
        "id": sender.id,
        "username": sender.username,
        "display_name": profile.display_name if profile else "",
        "is_online": is_user_online(sender.id),
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

    if not FIRESTORE_MESSAGES_ENABLED:
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
    if not FIRESTORE_MESSAGES_ENABLED:
        qs = (
            Message.objects.filter(conversation_id=conversation_id)
            .select_related("sender")
            .order_by("-created_at")
        )
        if before_id is not None:
            qs = qs.filter(pk__lt=before_id)
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
    if not FIRESTORE_MESSAGES_ENABLED:
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
    if not FIRESTORE_MESSAGES_ENABLED:
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
    if not FIRESTORE_MESSAGES_ENABLED:
        raise ImproperlyConfigured(
            "FIRESTORE_MESSAGES_ENABLED must be true to use Firestore message storage."
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
