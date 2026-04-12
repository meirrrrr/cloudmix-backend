from django.contrib.auth import get_user_model
from django.db.models import Case, Count, DateTimeField, F, Q, When
from django.shortcuts import get_object_or_404
from django.utils import timezone
from drf_spectacular.utils import (
    OpenApiParameter,
    extend_schema,
    extend_schema_view,
    inline_serializer,
)
from rest_framework import serializers, status
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import DirectConversation, Message
from .serializers import (
    ConversationSerializer,
    ConversationStartSerializer,
    MessageCreateSerializer,
    MessageSerializer,
)
from .services import broadcast_chat_message, message_to_payload, persist_chat_message

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
    return (
        base_qs.annotate(
            current_user_last_read_at=Case(
                When(participant_a=user, then=F("participant_a_last_read_at")),
                default=F("participant_b_last_read_at"),
                output_field=DateTimeField(),
            )
        )
        .annotate(
            unread_count=Count(
                "messages",
                filter=(
                    ~Q(messages__sender=user)
                    & (
                        Q(current_user_last_read_at__isnull=True)
                        | Q(messages__created_at__gt=F("current_user_last_read_at"))
                    )
                ),
            )
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


@extend_schema(tags=["Chat"], responses={200: ConversationSerializer(many=True)})
class ConversationListView(APIView):
    def get(self, request):
        qs = _conversation_qs_for_user(request.user)
        return Response(
            ConversationSerializer(qs, many=True, context={"request": request}).data
        )


@extend_schema(
    tags=["Chat"],
    request=ConversationStartSerializer,
    responses={
        200: ConversationSerializer,
        400: ErrorDetailSerializer,
        404: ErrorDetailSerializer,
    },
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


@extend_schema_view(
    get=extend_schema(
        tags=["Chat"],
        parameters=[
            OpenApiParameter(
                name="limit",
                type=int,
                location=OpenApiParameter.QUERY,
                required=False,
                description="Max messages to return (default 50, max 100).",
            ),
            OpenApiParameter(
                name="before",
                type=int,
                location=OpenApiParameter.QUERY,
                required=False,
                description="Only messages older than this message id (for paging upward).",
            ),
        ],
        responses={200: MessagesPageSerializer, 404: ErrorDetailSerializer},
    ),
    post=extend_schema(
        tags=["Chat"],
        request=MessageCreateSerializer,
        responses={
            201: MessageSerializer,
            400: ErrorDetailSerializer,
            404: ErrorDetailSerializer,
        },
    ),
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

        qs = conv.messages.select_related("sender__profile").order_by("-created_at")
        if before_id is not None:
            qs = qs.filter(pk__lt=before_id)

        chunk = list(qs[: limit + 1])
        has_more = len(chunk) > limit
        chunk = chunk[:limit]
        chunk.reverse()
        return Response(
            {
                "results": MessageSerializer(chunk, many=True).data,
                "has_more": has_more,
            }
        )

    def post(self, request, conversation_id: int):
        conv = _conversation_for_user_or_404(request.user, conversation_id)
        ser = MessageCreateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        msg = persist_chat_message(
            conv.pk, request.user, ser.validated_data["body"]
        )
        msg = Message.objects.select_related("sender__profile").get(pk=msg.pk)
        payload = message_to_payload(msg)
        broadcast_chat_message(conv.pk, payload)
        return Response(payload, status=status.HTTP_201_CREATED)


@extend_schema(
    tags=["Chat"],
    responses={204: None, 404: ErrorDetailSerializer},
)
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
