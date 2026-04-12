from django.conf import settings
from django.contrib.auth import authenticate
from django.contrib.auth import get_user_model
from django.db.models import Q
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from drf_spectacular.utils import OpenApiParameter, extend_schema, inline_serializer
from rest_framework import serializers, status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.serializers import TokenRefreshSerializer
from rest_framework_simplejwt.tokens import RefreshToken

from .cookies import attach_auth_cookies, clear_auth_cookies
from .serializers import LoginSerializer, MeSerializer, RegisterSerializer, UserSearchSerializer

User = get_user_model()

ErrorDetailSerializer = inline_serializer(
    "ErrorDetail",
    fields={"detail": serializers.CharField()},
)
RefreshOkSerializer = inline_serializer(
    "RefreshOk",
    fields={"detail": serializers.CharField()},
)
LogoutOkSerializer = inline_serializer(
    "LogoutOk",
    fields={"detail": serializers.CharField()},
)


def _tokens_for_user(user):
    refresh = RefreshToken.for_user(user)
    return str(refresh.access_token), str(refresh)


@extend_schema(tags=["Auth"], request=RegisterSerializer, responses={201: MeSerializer})
@method_decorator(csrf_exempt, name="dispatch")
class RegisterView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        ser = RegisterSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        user = ser.save()
        access, refresh = _tokens_for_user(user)
        resp = Response(MeSerializer(user).data, status=status.HTTP_201_CREATED)
        attach_auth_cookies(resp, access, refresh)
        return resp


@extend_schema(
    tags=["Auth"],
    request=LoginSerializer,
    responses={200: MeSerializer, 401: ErrorDetailSerializer},
)
@method_decorator(csrf_exempt, name="dispatch")
class LoginView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        ser = LoginSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        user = authenticate(
            request,
            username=ser.validated_data["username"],
            password=ser.validated_data["password"],
        )
        if user is None:
            return Response(
                {"detail": "Invalid username or password."},
                status=status.HTTP_401_UNAUTHORIZED,
            )
        access, refresh = _tokens_for_user(user)
        resp = Response(MeSerializer(user).data, status=status.HTTP_200_OK)
        attach_auth_cookies(resp, access, refresh)
        return resp


@extend_schema(
    tags=["Auth"],
    request=None,
    responses={200: RefreshOkSerializer, 401: ErrorDetailSerializer},
    description="Reads refresh token from HttpOnly cookie; sets new access (and refresh) cookies.",
)
@method_decorator(csrf_exempt, name="dispatch")
class TokenRefreshView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        raw = request.COOKIES.get(settings.AUTH_REFRESH_COOKIE_NAME)
        if not raw:
            return Response(
                {"detail": "Refresh cookie missing."},
                status=status.HTTP_401_UNAUTHORIZED,
            )
        ser = TokenRefreshSerializer(data={"refresh": raw})
        if not ser.is_valid():
            return Response(
                {"detail": "Invalid or expired refresh token."},
                status=status.HTTP_401_UNAUTHORIZED,
            )
        access = ser.validated_data["access"]
        resp = Response({"detail": "ok"}, status=status.HTTP_200_OK)
        refresh_out = ser.validated_data.get("refresh", raw)
        attach_auth_cookies(resp, access, refresh_out)
        return resp


@extend_schema(
    tags=["Auth"],
    request=None,
    responses={200: LogoutOkSerializer},
    description=(
        "Clears auth cookies. If refresh token blacklisting is enabled and a refresh "
        "cookie is present, the refresh token is blacklisted."
    ),
)
@method_decorator(csrf_exempt, name="dispatch")
class LogoutView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        raw_refresh = request.COOKIES.get(settings.AUTH_REFRESH_COOKIE_NAME)
        if raw_refresh:
            try:
                RefreshToken(raw_refresh).blacklist()
            except Exception:
                # Missing blacklist app, invalid token, or already blacklisted:
                # logout should still clear cookies and succeed.
                pass

        resp = Response({"detail": "Logged out."}, status=status.HTTP_200_OK)
        clear_auth_cookies(resp)
        return resp


@extend_schema(tags=["Users"], responses={200: MeSerializer})
class MeView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(MeSerializer(request.user).data)


@extend_schema(
    tags=["Users"],
    parameters=[
        OpenApiParameter(
            name="name",
            type=str,
            location=OpenApiParameter.QUERY,
            required=True,
            description="Substring match on display name or username.",
        ),
    ],
    responses={
        200: UserSearchSerializer(many=True),
        400: ErrorDetailSerializer,
    },
)
class UserSearchView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        name = (request.query_params.get("name") or "").strip()
        if not name:
            return Response(
                {"detail": "Query parameter 'name' is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        qs = (
            User.objects.filter(profile__isnull=False)
            .filter(
                Q(profile__display_name__icontains=name)
                | Q(username__icontains=name)
            )
            .select_related("profile")
            .exclude(pk=request.user.pk)
            .order_by("username")[:50]
        )
        return Response(UserSearchSerializer(qs, many=True).data)
