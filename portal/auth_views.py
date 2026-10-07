"""Bounded authentication attempts, shared by workers without trusting forwarded IPs."""
import hashlib
import hmac
import ipaddress
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.views import LoginView
from django.db import transaction
from django.shortcuts import render
from django.utils import timezone
from django.forms.utils import ErrorDict

from .models import LoginThrottle


def client_address(request):
    # REMOTE_ADDR is provided by the server. Never let untrusted X-Forwarded-For
    # headers choose a fresh throttle bucket. Proxy deployments must configure
    # the server's trusted-proxy handling instead of parsing headers here.
    try:
        return str(ipaddress.ip_address(request.META.get("REMOTE_ADDR", "")))
    except ValueError:
        return "unknown"


def throttle_key(scope, address, username=""):
    message = f"{scope}\0{address}\0{username.strip().casefold()}".encode()
    return hmac.new(settings.SECRET_KEY.encode(), message, hashlib.sha256).hexdigest()


def reserve_attempt(request, kind):
    """Return seconds remaining if blocked; count attempts before authentication."""
    now = timezone.now()
    address = client_address(request)
    if kind == "login":
        buckets = [("login-address", "", 60, 600), ("login-user-address", request.POST.get("username", "")[:150], 5, 300)]
    else:
        buckets = [("registration-address", "", 20, 600)]
    delay = 0
    with transaction.atomic():
        # Fixed order and unique keys coordinate simultaneous requests/processes.
        for scope, username, maximum, seconds in buckets:
            if delay:
                break  # A blocked address must not create unlimited username buckets.
            key = throttle_key(scope, address, username)
            LoginThrottle.objects.get_or_create(key=key, defaults={"window_start": now})
            bucket = LoginThrottle.objects.select_for_update().get(key=key)
            if bucket.window_start + timedelta(seconds=seconds) <= now:
                bucket.window_start, bucket.attempts, bucket.blocked_until = now, 0, None
            if bucket.blocked_until and bucket.blocked_until > now:
                delay = max(delay, int((bucket.blocked_until - now).total_seconds()) + 1)
            else:
                bucket.attempts += 1
                if bucket.attempts > maximum:
                    bucket.blocked_until = bucket.window_start + timedelta(seconds=seconds)
                    delay = max(delay, int((bucket.blocked_until - now).total_seconds()) + 1)
            bucket.save(update_fields=["window_start", "attempts", "blocked_until"])
    # Expired buckets contain hashed identifiers only; bound database growth.
    LoginThrottle.objects.filter(window_start__lt=now - timedelta(days=1)).delete()
    return delay


def block_response(request, form, template, delay):
    # Unbound form prevents password authentication/registration while blocked.
    form._errors = ErrorDict()
    form.cleaned_data = {}
    form.add_error(None, "تكررت المحاولات. انتظر بضع دقائق ثم أعد المحاولة.")
    response = render(request, template, {"form": form}, status=429)
    response["Retry-After"] = str(delay)
    return response


class ThrottledLoginView(LoginView):
    def post(self, request, *args, **kwargs):
        delay = reserve_attempt(request, "login")
        if delay:
            return block_response(request, self.get_form_class()(request=request), self.template_name, delay)
        return super().post(request, *args, **kwargs)

    def form_valid(self, form):
        response = super().form_valid(form)
        LoginThrottle.objects.filter(key=throttle_key("login-user-address", client_address(self.request), self.request.POST.get("username", "")[:150])).delete()
        return response
