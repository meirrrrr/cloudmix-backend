from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from django.utils import timezone

from accounts.models import Profile
from .models import DirectConversation, Message
from .presence import decrement_user_connections, increment_user_connections
from .serializers import MessageCreateSerializer
from .services import message_to_payload, persist_chat_message


class ChatConsumer(AsyncJsonWebsocketConsumer):
    async def connect(self):
        user = self.scope["user"]
        if not getattr(user, "is_authenticated", False):
            await self.close(code=4401)
            return

        try:
            self.conversation_id = int(self.scope["url_route"]["kwargs"]["conversation_id"])
        except (KeyError, ValueError, TypeError):
            await self.close(code=4400)
            return

        allowed = await self._user_allowed_in_conversation(user.id, self.conversation_id)
        if not allowed:
            await self.close(code=4403)
            return

        self.group_name = f"chat_{self.conversation_id}"
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()
        self.presence_user_id = user.id
        connections = await self._increment_presence_connections(user.id)
        if connections == 1:
            await self.channel_layer.group_send(
                self.group_name,
                {
                    "type": "chat.presence",
                    "presence": {
                        "type": "presence",
                        "user_id": user.id,
                        "is_online": True,
                        "last_seen_at": None,
                    },
                },
            )

    async def disconnect(self, code):
        presence_user_id = getattr(self, "presence_user_id", None)
        if presence_user_id is not None:
            remaining_connections = await self._decrement_presence_connections(
                presence_user_id
            )
            if remaining_connections == 0:
                last_seen_at = await self._update_last_seen(presence_user_id)
                if getattr(self, "group_name", None):
                    await self.channel_layer.group_send(
                        self.group_name,
                        {
                            "type": "chat.presence",
                            "presence": {
                                "type": "presence",
                                "user_id": presence_user_id,
                                "is_online": False,
                                "last_seen_at": last_seen_at,
                            },
                        },
                    )
        if getattr(self, "group_name", None):
            await self.channel_layer.group_discard(self.group_name, self.channel_name)

    async def receive_json(self, content, **kwargs):
        event_type = content.get("type")
        if event_type == "typing":
            is_typing = content.get("is_typing")
            if not isinstance(is_typing, bool):
                await self.send_json(
                    {
                        "type": "error",
                        "detail": 'Expected {"type": "typing", "is_typing": true|false}.',
                    }
                )
                return

            await self.channel_layer.group_send(
                self.group_name,
                {
                    "type": "chat.typing",
                    "typing": {
                        "type": "typing",
                        "user_id": self.scope["user"].id,
                        "is_typing": is_typing,
                    },
                },
            )
            return

        if event_type != "send":
            await self.send_json(
                {
                    "type": "error",
                    "detail": 'Expected {"type": "send", "body": "..."} or {"type": "typing", "is_typing": true|false}.',
                }
            )
            return

        ok, payload_or_errors = await self._validate_and_persist(
            self.scope["user"], self.conversation_id, content.get("body")
        )
        if not ok:
            await self.send_json({"type": "error", "errors": payload_or_errors})
            return

        await self.channel_layer.group_send(
            self.group_name,
            {"type": "chat.message", "message": payload_or_errors},
        )

    async def chat_message(self, event):
        await self.send_json({"type": "message", "message": event["message"]})

    async def chat_presence(self, event):
        await self.send_json(event["presence"])

    async def chat_typing(self, event):
        await self.send_json(event["typing"])

    @database_sync_to_async
    def _user_allowed_in_conversation(self, user_id: int, conversation_id: int) -> bool:
        try:
            conv = DirectConversation.objects.get(pk=conversation_id)
        except DirectConversation.DoesNotExist:
            return False
        return user_id in (conv.participant_a_id, conv.participant_b_id)

    @database_sync_to_async
    def _validate_and_persist(self, user, conversation_id: int, body_raw):
        ser = MessageCreateSerializer(data={"body": body_raw})
        if not ser.is_valid():
            return False, ser.errors
        try:
            msg = persist_chat_message(
                conversation_id, user, ser.validated_data["body"]
            )
        except DirectConversation.DoesNotExist:
            return False, {"detail": ["Conversation not found."]}
        except PermissionError:
            return False, {"detail": ["You are not a participant in this conversation."]}

        msg = Message.objects.select_related("sender__profile").get(pk=msg.pk)
        return True, message_to_payload(msg)

    @database_sync_to_async
    def _increment_presence_connections(self, user_id: int) -> int:
        return increment_user_connections(user_id)

    @database_sync_to_async
    def _decrement_presence_connections(self, user_id: int) -> int:
        return decrement_user_connections(user_id)

    @database_sync_to_async
    def _update_last_seen(self, user_id: int) -> str:
        now = timezone.now()
        Profile.objects.filter(user_id=user_id).update(last_seen_at=now)
        return now.isoformat()
