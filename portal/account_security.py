"""Revoke employee sessions when account access is changed by the director."""
from django.contrib.sessions.models import Session
from django.utils import timezone


def revoke_sessions(user_id):
    # This application uses Django's database session backend. IDs are compared
    # after signature verification; no cookie values/passwords go into audit logs.
    expired_ids = []
    for session in Session.objects.filter(expire_date__gt=timezone.now()).iterator(chunk_size=200):
        if session.get_decoded().get("_auth_user_id") == str(user_id):
            expired_ids.append(session.session_key)
            if len(expired_ids) >= 200:
                Session.objects.filter(session_key__in=expired_ids).delete()
                expired_ids.clear()
    if expired_ids:
        Session.objects.filter(session_key__in=expired_ids).delete()
