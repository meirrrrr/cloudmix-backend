from django.contrib.auth import authenticate
from django.contrib.auth import get_user_model
from django.db.models import Q
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from drf_spectacular.utils import inline_serializer
from rest_framework import serializers, status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.serializers import TokenRefreshSerializer
from rest_framework_simplejwt.tokens import RefreshToken

from .serializers import LoginSerializer, MeSerializer, RegisterSerializer, UserSearchSerializer

User = get_user_model()

ErrorDetailSerializer = inline_serializer(
    "AccountsErrorDetail",
    fields={"detail": serializers.CharField()},
)


def _tokens_for_user(user):
    refresh = RefreshToken.for_user(user)
    return str(refresh.access_token), str(refresh)


def _auth_response(user, *, status_code):
    access, refresh = _tokens_for_user(user)
    body = MeSerializer(user).data
    body["access"] = access
    body["refresh"] = refresh
    return Response(body, status=status_code)


@method_decorator(csrf_exempt, name="dispatch")
class RegisterView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request):
        ser = RegisterSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        user = ser.save()
        return _auth_response(user, status_code=status.HTTP_201_CREATED)


@method_decorator(csrf_exempt, name="dispatch")
class LoginView(APIView):
    authentication_classes = []
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
        return _auth_response(user, status_code=status.HTTP_200_OK)


@method_decorator(csrf_exempt, name="dispatch")
class TokenRefreshView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request):
        raw = request.data.get("refresh")
        if not raw:
            return Response(
                {"detail": "Refresh token missing."},
                status=status.HTTP_401_UNAUTHORIZED,
            )
        ser = TokenRefreshSerializer(data={"refresh": raw})
        if not ser.is_valid():
            return Response(
                {"detail": "Invalid or expired refresh token."},
                status=status.HTTP_401_UNAUTHORIZED,
            )
        access = ser.validated_data["access"]
        refresh_out = ser.validated_data.get("refresh", raw)
        return Response({"access": access, "refresh": refresh_out}, status=status.HTTP_200_OK)


@method_decorator(csrf_exempt, name="dispatch")
class LogoutView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request):
        raw_refresh = request.data.get("refresh")
        if raw_refresh:
            try:
                RefreshToken(raw_refresh).blacklist()
            except Exception:
                # Missing blacklist app, invalid token, or already blacklisted.
                pass

        return Response({"detail": "Logged out."}, status=status.HTTP_200_OK)


class MeView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(MeSerializer(request.user).data)


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
