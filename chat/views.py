from django.contrib.auth import get_user_model
from django.db.models import Case, DateTimeField, F, IntegerField, Q, When
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from drf_spectacular.utils import inline_serializer
from rest_framework import serializers, status
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import DirectConversation
from .serializers import (
    ConversationSerializer,
    ConversationStartSerializer,
    MessageCreateSerializer,
    MessageSerializer,
)
from .services import (
    broadcast_chat_message,
    ensure_ai_conversation_for_user,
    get_ai_bot_user,
    get_messages,
    maybe_generate_ai_reply,
    save_message,
)

User = get_user_model()

ErrorDetailSerializer = inline_serializer(
    "ChatErrorDetail",
    fields={"detail": serializers.CharField()},
)

MessagesPageSerializer = inline_serializer(
    "MessagesPage",
    fields={
        "results": MessageSerializer(many=True),
        "has_more": serializers.BooleanField(),
        "next_before": serializers.IntegerField(allow_null=True),
        "next_before_created_at": serializers.CharField(allow_null=True),
    },
)


def _conversation_qs_for_user(user):
    base_qs = (
        DirectConversation.objects.filter(
            Q(participant_a=user) | Q(participant_b=user)
        )
        .select_related(
            "participant_a__profile",
            "participant_b__profile",
        )
        .order_by("-updated_at")
    )
    return base_qs.annotate(
        current_user_last_read_at=Case(
            When(participant_a=user, then=F("participant_a_last_read_at")),
            default=F("participant_b_last_read_at"),
            output_field=DateTimeField(),
        )
    )


def _get_or_create_dm(request_user, peer: User):
    if peer.pk == request_user.pk:
        return None, "self"
    lo, hi = (
        (request_user, peer) if request_user.pk < peer.pk else (peer, request_user)
    )
    conv, _ = DirectConversation.objects.get_or_create(
        participant_a=lo,
        participant_b=hi,
    )
    return conv, None


def _conversation_for_user_or_404(user, pk: int) -> DirectConversation:
    return get_object_or_404(_conversation_qs_for_user(user), pk=pk)


class ConversationListView(APIView):
    def get(self, request):
        ensure_ai_conversation_for_user(request.user)
        ai_bot_user = get_ai_bot_user()
        qs = _conversation_qs_for_user(request.user).annotate(
            ai_sort_rank=Case(
                When(participant_a_id=ai_bot_user.id, then=0),
                When(participant_b_id=ai_bot_user.id, then=0),
                default=1,
                output_field=IntegerField(),
            )
        )
        return Response(
            ConversationSerializer(
                qs.order_by("ai_sort_rank", "-updated_at"),
                many=True,
                context={"request": request},
            ).data
        )


class ConversationDetailView(APIView):
    def get(self, request, conversation_id: int):
        conv = _conversation_for_user_or_404(request.user, conversation_id)
        return Response(
            ConversationSerializer(conv, context={"request": request}).data
        )


class ConversationStartView(APIView):
    def post(self, request):
        ser = ConversationStartSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        peer_id = ser.validated_data["user_id"]
        peer = get_object_or_404(
            User.objects.filter(profile__isnull=False).select_related("profile"),
            pk=peer_id,
        )
        conv, err = _get_or_create_dm(request.user, peer)
        if err == "self":
            return Response(
                {"detail": "Cannot start a conversation with yourself."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        conv = (
            DirectConversation.objects.select_related(
                "participant_a__profile",
                "participant_b__profile",
            ).get(pk=conv.pk)
        )
        return Response(
            ConversationSerializer(conv, context={"request": request}).data,
            status=status.HTTP_200_OK,
        )



class ConversationMessagesView(APIView):
    def get(self, request, conversation_id: int):
        conv = _conversation_for_user_or_404(request.user, conversation_id)
        try:
            limit = int(request.query_params.get("limit") or 50)
        except (TypeError, ValueError):
            limit = 50
        limit = max(1, min(limit, 100))
        before_raw = request.query_params.get("before")
        before_id = None
        if before_raw is not None and before_raw != "":
            try:
                before_id = int(before_raw)
            except (TypeError, ValueError):
                return Response(
                    {"detail": "Invalid 'before' parameter."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        before_created_at_raw = request.query_params.get("before_created_at")
        before_created_at = None
        if (
            before_created_at_raw is not None
            and before_created_at_raw != ""
        ):
            before_created_at = parse_datetime(before_created_at_raw)
            if before_created_at is None:
                return Response(
                    {"detail": "Invalid 'before_created_at' parameter."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if timezone.is_naive(before_created_at):
                before_created_at = timezone.make_aware(
                    before_created_at,
                    timezone.get_current_timezone(),
                )

        chunk, has_more = get_messages(
            conv.pk,
            limit=limit,
            before_id=before_id,
            before_created_at=before_created_at,
        )
        next_before = None
        next_before_created_at = None
        if has_more and chunk:
            next_before = chunk[0]["id"]
            next_before_created_at = chunk[0]["created_at"]
        return Response(
            {
                "results": chunk,
                "has_more": has_more,
                "next_before": next_before,
                "next_before_created_at": next_before_created_at,
            }
        )

    def post(self, request, conversation_id: int):
        conv = _conversation_for_user_or_404(request.user, conversation_id)
        ser = MessageCreateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        body = ser.validated_data["body"]
        payload = save_message(conv.pk, request.user, body)
        broadcast_chat_message(conv.pk, payload)
        maybe_generate_ai_reply(conv.pk, request.user, body)
        return Response(payload, status=status.HTTP_201_CREATED)


class ConversationReadView(APIView):
    def post(self, request, conversation_id: int):
        conv = _conversation_for_user_or_404(request.user, conversation_id)
        now = timezone.now()
        if conv.participant_a_id == request.user.id:
            DirectConversation.objects.filter(pk=conv.pk).update(
                participant_a_last_read_at=now
            )
        else:
            DirectConversation.objects.filter(pk=conv.pk).update(
                participant_b_last_read_at=now
            )
        return Response(status=status.HTTP_204_NO_CONTENT)
