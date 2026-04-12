from django.contrib.auth import get_user_model
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from accounts.models import Profile as ProfileModel
from .presence import is_user_online
from .models import DirectConversation, Message

User = get_user_model()


class PeerUserSerializer(serializers.ModelSerializer):
    display_name = serializers.SerializerMethodField()
    last_seen_at = serializers.SerializerMethodField()
    is_online = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ("id", "username", "display_name", "is_online", "last_seen_at")

    @extend_schema_field(serializers.CharField())
    def get_display_name(self, obj) -> str:
        try:
            return obj.profile.display_name
        except ProfileModel.DoesNotExist:
            return ""

    @extend_schema_field(serializers.BooleanField())
    def get_is_online(self, obj) -> bool:
        return is_user_online(obj.id)

    @extend_schema_field(serializers.DateTimeField(allow_null=True))
    def get_last_seen_at(self, obj):
        try:
            return obj.profile.last_seen_at
        except ProfileModel.DoesNotExist:
            return None


class ConversationSerializer(serializers.ModelSerializer):
    peer = serializers.SerializerMethodField()
    last_message = serializers.SerializerMethodField()
    unread_count = serializers.SerializerMethodField()

    class Meta:
        model = DirectConversation
        fields = ("id", "updated_at", "peer", "last_message", "unread_count")

    @extend_schema_field(PeerUserSerializer())
    def get_peer(self, obj: DirectConversation):
        request = self.context["request"]
        other = obj.other_user(request.user)
        return PeerUserSerializer(other).data

    @extend_schema_field(serializers.JSONField(allow_null=True))
    def get_last_message(self, obj: DirectConversation):
        last_message = (
            obj.messages.select_related("sender__profile").order_by("-created_at").first()
        )
        if last_message is None:
            return None
        return MessageSerializer(last_message).data

    @extend_schema_field(serializers.IntegerField(min_value=0))
    def get_unread_count(self, obj: DirectConversation) -> int:
        return max(0, int(getattr(obj, "unread_count", 0) or 0))


class ConversationStartSerializer(serializers.Serializer):
    user_id = serializers.IntegerField(min_value=1)


class MessageSerializer(serializers.ModelSerializer):
    sender = PeerUserSerializer(read_only=True)

    class Meta:
        model = Message
        fields = ("id", "sender", "body", "created_at")


class MessageCreateSerializer(serializers.Serializer):
    body = serializers.CharField(max_length=5000, trim_whitespace=True)

    def validate_body(self, value: str) -> str:
        text = value.strip()
        if not text:
            raise serializers.ValidationError("Message cannot be empty.")
        return text
