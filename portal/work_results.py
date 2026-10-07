"""Account-bound, idempotent execution results and private normalized photographs."""
import hashlib
import json
import uuid
from functools import wraps

from django.core.files.base import ContentFile
from django.db import transaction
from django.http import FileResponse, JsonResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from .complaints import MAX_PHOTOS, normalize_photo
from .models import Complaint, ComplaintExecutionResult, ComplaintExecutionPhoto, User
from .workflow import visible_reports


def assignment_epoch(complaint):
    return complaint.events.filter(action__in=("assign", "reassign", "claim", "start")).order_by("-pk").values_list("pk", flat=True).first() or 0


def task_card(user, complaint):
    """Only the minimum task data deliberately prepared for that employee's device."""
    from .workflow import permitted_actions
    if "submit" not in permitted_actions(user, complaint):
        return None
    return {"owner_id": user.pk, "owner_role": user.role, "complaint_id": complaint.pk,
            "reference": complaint.reference, "title": complaint.get_complaint_type_display(),
            "address": complaint.address, "assignment_event_id": assignment_epoch(complaint)}


def employee_api(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return JsonResponse({"error": "login_required"}, status=401)
        if not request.user.is_active or request.user.role not in (User.Role.TECHNICIAN, User.Role.FINANCE):
            return JsonResponse({"error": "role_forbidden"}, status=403)
        return view(request, *args, **kwargs)
    return wrapped


def receipt(result):
    return {"saved": True, "client_id": str(result.client_id), "complaint_id": result.complaint_id,
            "reference": result.complaint.reference, "result_id": result.pk,
            "status": Complaint.Status.REVIEW,
            "photos": [{"id": photo.pk, "url": reverse("work_result_photo", args=[photo.pk])} for photo in result.photos.all()]}


@employee_api
@require_POST
def sync_result(request):
    if request.content_type != "multipart/form-data":
        return JsonResponse({"error": "multipart_required"}, status=415)
    try:
        payload = json.loads(request.POST.get("payload", ""))
        if not isinstance(payload, dict) or set(payload) != {"owner_id", "client_id", "complaint_id", "assignment_event_id", "note"}:
            raise ValueError()
        client_id = uuid.UUID(payload["client_id"])
        owner_id = payload["owner_id"]
        complaint_id = payload["complaint_id"]
        epoch = payload["assignment_event_id"]
        note = payload["note"]
        if (type(owner_id) is not int or not 0 < owner_id < 2 ** 63 or
                type(complaint_id) is not int or not 0 < complaint_id < 2 ** 63 or
                type(epoch) is not int or not 0 <= epoch < 2 ** 63 or not isinstance(note, str) or not note.strip() or len(note) > 3000):
            raise ValueError()
        note = note.strip()
    except (ValueError, TypeError, KeyError, UnicodeDecodeError, AttributeError):
        return JsonResponse({"error": "invalid_payload", "message": "دوّن نتيجة صحيحة لا تتجاوز 3000 حرف."}, status=400)
    if owner_id != request.user.pk:
        return JsonResponse({"error": "account_changed"}, status=409)
    uploads = request.FILES.getlist("photos")
    if len(uploads) > MAX_PHOTOS or any(key != "photos" for key in request.FILES):
        return JsonResponse({"error": "invalid_photos", "message": "أرفق حتى 3 صور فقط."}, status=400)
    try:
        photos = [normalize_photo(upload) for upload in uploads]
    except ValueError as error:
        return JsonResponse({"error": "invalid_photos", "message": str(error)}, status=400)
    digest = hashlib.sha256(json.dumps({"complaint_id": complaint_id, "assignment_event_id": epoch,
        "note": note, "photos": [photo[0] for photo in photos]}, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    stored_files = []
    try:
        with transaction.atomic():
            # Serialize by actor before checking the idempotency key, including different tasks.
            actor = User.objects.select_for_update().get(pk=request.user.pk)
            if not actor.is_active or actor.role != request.user.role:
                return JsonResponse({"error": "account_changed"}, status=409)
            existing = ComplaintExecutionResult.objects.select_related("complaint").filter(actor=request.user, client_id=client_id).first()
            if existing:
                if existing.payload_digest != digest:
                    return JsonResponse({"error": "id_conflict", "message": "تغير محتوى النتيجة بعد إرسالها؛ راجع السجل."}, status=409)
                return JsonResponse(receipt(existing))
            complaint = get_object_or_404(Complaint.objects.select_for_update(), pk=complaint_id)
            from .workflow import permitted_actions, apply_action
            if "submit" not in permitted_actions(actor, complaint) or epoch != assignment_epoch(complaint):
                return JsonResponse({"error": "task_changed", "message": "تغير إسناد المهمة أو حالتها. النتيجة وصورها محفوظة على الجهاز للمراجعة مع المسؤول."}, status=409)
            event = apply_action(actor, complaint, "submit", note=note)
            result = ComplaintExecutionResult.objects.create(complaint=complaint, actor=request.user,
                event=event, client_id=client_id, payload_digest=digest, assignment_event_id=epoch)
            for position, (_, image_bytes) in enumerate(photos):
                photo = ComplaintExecutionPhoto(result=result, position=position)
                photo.image.save("execution.jpg", ContentFile(image_bytes), save=False)
                stored_files.append((photo.image.storage, photo.image.name))
                photo.save()
    except Exception:
        for storage, name in stored_files:
            storage.delete(name)
        raise
    return JsonResponse(receipt(result), status=201)


@require_GET
def photo(request, photo_id):
    if not request.user.is_authenticated:
        return JsonResponse({"error": "login_required"}, status=401)
    image = get_object_or_404(ComplaintExecutionPhoto, pk=photo_id,
                             result__complaint__in=visible_reports(request.user))
    try:
        file = image.image.open("rb")
    except FileNotFoundError:
        return JsonResponse({"error": "photo_unavailable"}, status=404)
    response = FileResponse(file, content_type="image/jpeg", filename="execution-photo.jpg")
    response["Cache-Control"] = "no-store, private"
    return response
