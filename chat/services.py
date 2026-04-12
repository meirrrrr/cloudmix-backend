import json

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from rest_framework.renderers import JSONRenderer

from .models import DirectConversation, Message
from .serializers import MessageSerializer


def persist_chat_message(conversation_id: int, user, body: str) -> Message:
    conv = DirectConversation.objects.get(pk=conversation_id)
    if not conv.includes_user(user):
        raise PermissionError
    return Message.objects.create(conversation=conv, sender=user, body=body)


def message_to_payload(message: Message) -> dict:
    data = MessageSerializer(message).data
    return json.loads(JSONRenderer().render(data))


def broadcast_chat_message(conversation_id: int, payload: dict) -> None:
    layer = get_channel_layer()
    if layer is None:
        return
    async_to_sync(layer.group_send)(
        f"chat_{conversation_id}",
        {"type": "chat.message", "message": payload},
    )
