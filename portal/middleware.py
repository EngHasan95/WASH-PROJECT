from django.http import JsonResponse
from django.shortcuts import redirect


class ResponsePolicyMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated and request.user.must_change_password:
            allowed = ("/accounts/password/", "/accounts/logout/", "/app-shell/", "/sw.js", "/health/")
            if request.path not in allowed and not request.path.startswith("/static/"):
                if request.path.startswith("/api/"):
                    return JsonResponse({"error": "password_change_required"}, status=403)
                return redirect("password_change")
        response = self.get_response(request)
        if request.path.startswith(("/workspace/", "/staff/", "/accounts/", "/api/", "/committee/")):
            response["Cache-Control"] = "no-store, private"
        response["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data: blob:; "
            "connect-src 'self'; font-src 'self'; object-src 'none'; base-uri 'self'; "
            "form-action 'self'; frame-ancestors 'none'"
        )
        response["Referrer-Policy"] = "same-origin"
        response["Permissions-Policy"] = "camera=(), microphone=(), geolocation=(self)"
        return response
