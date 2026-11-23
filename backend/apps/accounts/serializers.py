import logging

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.contrib.auth.validators import ASCIIUsernameValidator
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers
from rest_framework.exceptions import AuthenticationFailed
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from rest_framework_simplejwt.settings import api_settings
from rest_framework_simplejwt.tokens import RefreshToken

from apps.tenants.models import Tenant

from .throttles import LoginUsernameThrottle

logger = logging.getLogger(__name__)

NO_ACCOUNT = "No active account found with the given credentials"


def _audit(*args, **kwargs):
    """An audit failure must never break the auth response."""
    try:
        from apps.audit import record

        record(*args, **kwargs)
    except Exception:
        logger.exception("audit write failed for %s", args[1] if len(args) > 1 else "?")


def can_sign_in(user) -> bool:
    return bool(
        user is not None
        and user.is_active
        and user.tenant_id
        and user.tenant.status == Tenant.Status.ACTIVE
    )


class TokenObtainSerializer(TokenObtainPairSerializer):
    """Login `{username, password}` -> `{access, refresh}` with tenant_id and role claims."""

    @classmethod
    def get_token(cls, user):
        token = super().get_token(user)
        token["tenant_id"] = user.tenant_id
        token["role"] = user.role
        return token

    def _audit_failure(self, username):
        from . import failguard

        request = self.context.get("request")
        if request is not None and not failguard.should_audit_failure(request, "auth.login_failed"):
            return
        # People paste passwords into the username box: keep the text only when it names an
        # existing user, otherwise record that it was unknown.
        known = get_user_model().objects.filter(username__iexact=username).exists()
        data = {"username": username} if known else {"username_known": False}
        _audit("anonymous", "auth.login_failed", ("User", ""), data=data)

    def validate(self, attrs):
        username = str(attrs.get(self.username_field, ""))[:150]
        try:
            data = super().validate(attrs)  # wrong password / inactive -> AuthenticationFailed
            if not can_sign_in(self.user):  # no tenant (incl. superusers) or tenant not ACTIVE
                raise AuthenticationFailed(NO_ACCOUNT, "no_active_account")
        except AuthenticationFailed:
            LoginUsernameThrottle.record_failure(username)
            self._audit_failure(username)
            raise AuthenticationFailed(NO_ACCOUNT, "no_active_account") from None
        _audit(self.user, "auth.login", self.user, data={"username": self.user.get_username()})
        return {"access": data["access"], "refresh": data["refresh"]}


class TokenRefreshSerializer(serializers.Serializer):
    """`{refresh}` -> `{access}`; claims are re-read from the DB. No rotation, no blacklist."""

    refresh = serializers.CharField(write_only=True)
    access = serializers.CharField(read_only=True)

    def validate(self, attrs):
        try:
            token = RefreshToken(attrs["refresh"])  # signature, expiry, token_type == refresh
            user_id = token[api_settings.USER_ID_CLAIM]
        except (TokenError, KeyError) as exc:
            raise InvalidToken(str(exc.args[0]) if exc.args else "Invalid token") from None
        user = get_user_model().objects.select_related("tenant").filter(pk=user_id).first()
        if not can_sign_in(user):
            raise AuthenticationFailed(NO_ACCOUNT, "no_active_account")
        return {"access": str(TokenObtainSerializer.get_token(user).access_token)}


class TenantSummarySerializer(serializers.Serializer):
    id = serializers.CharField()
    name = serializers.CharField()


class UserSerializer(serializers.ModelSerializer):
    """List/create for `/users`. Only these fields exist, so `tenant`, `tenant_id`, `is_staff`,
    `is_superuser` in a request body are simply ignored; the tenant is set by the view."""

    id = serializers.CharField(source="api_id", read_only=True)
    username = serializers.CharField(max_length=150)
    email = serializers.EmailField(required=False, allow_blank=True, default="")
    name = serializers.CharField(max_length=200, required=False, allow_blank=True, default="")
    # The 4 user roles only: SYSTEM (or anything else) is a 400.
    role = serializers.ChoiceField(choices=[r.value for r in get_user_model().Role])
    password = serializers.CharField(write_only=True, trim_whitespace=False, max_length=128)

    class Meta:
        model = get_user_model()
        fields = ["id", "username", "email", "name", "role", "is_active", "password"]
        read_only_fields = ["is_active"]

    def validate_username(self, value):
        User = get_user_model()
        value = User.normalize_username(value)  # NFKC: fullwidth lookalikes become ASCII
        try:
            ASCIIUsernameValidator()(value)  # letters, digits and . @ + - _ only
        except DjangoValidationError as exc:
            raise serializers.ValidationError(list(exc.messages)) from None
        if User.objects.filter(username__iexact=value).exists():
            raise serializers.ValidationError("This username is not available.")
        return value

    def validate(self, attrs):
        candidate = get_user_model()(
            username=attrs["username"], email=attrs.get("email", ""), name=attrs.get("name", "")
        )
        try:
            validate_password(attrs["password"], candidate)
        except DjangoValidationError as exc:
            raise serializers.ValidationError({"password": list(exc.messages)}) from None
        return attrs

    def create(self, validated_data):
        password = validated_data.pop("password")
        user = get_user_model()(**validated_data, is_staff=False, is_superuser=False)
        user.set_password(password)
        user.save()
        return user


class MeSerializer(serializers.Serializer):
    id = serializers.CharField(source="api_id")
    email = serializers.EmailField()
    name = serializers.CharField()
    role = serializers.CharField()
    tenant = TenantSummarySerializer()
