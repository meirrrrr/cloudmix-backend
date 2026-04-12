from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from chat.presence import is_user_online
from .models import Profile as ProfileModel

User = get_user_model()


class RegisterSerializer(serializers.Serializer):
    username = serializers.CharField(max_length=150, trim_whitespace=True)
    password = serializers.CharField(write_only=True, min_length=8)
    display_name = serializers.CharField(max_length=150, trim_whitespace=True)

    def validate_username(self, value: str) -> str:
        if User.objects.filter(username__iexact=value).exists():
            raise serializers.ValidationError("A user with this username already exists.")
        return value

    def validate(self, attrs):
        validate_password(attrs["password"])
        return attrs

    def create(self, validated_data):
        user = User.objects.create_user(
            username=validated_data["username"],
            password=validated_data["password"],
        )
        ProfileModel.objects.create(
            user=user,
            display_name=validated_data["display_name"],
        )
        return user


class LoginSerializer(serializers.Serializer):
    username = serializers.CharField()
    password = serializers.CharField(write_only=True)


class MeSerializer(serializers.ModelSerializer):
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


class UserSearchSerializer(serializers.ModelSerializer):
    display_name = serializers.SerializerMethodField()
    last_seen_at = serializers.SerializerMethodField()
    is_online = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ("id", "username", "display_name", "is_online", "last_seen_at")

    @extend_schema_field(serializers.CharField())
    def get_display_name(self, obj) -> str:
        return obj.profile.display_name

    @extend_schema_field(serializers.BooleanField())
    def get_is_online(self, obj) -> bool:
        return is_user_online(obj.id)

    @extend_schema_field(serializers.DateTimeField(allow_null=True))
    def get_last_seen_at(self, obj):
        try:
            return obj.profile.last_seen_at
        except ProfileModel.DoesNotExist:
            return None
