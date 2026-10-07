"""Private complaint intake and image normalization, independent of later routing."""
import hashlib
import io
import json

from PIL import Image, ImageOps, UnidentifiedImageError
from django.core.files.base import ContentFile
from django.db import transaction
from django.http import FileResponse, JsonResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from .forms import ComplaintForm
from .models import Complaint, ComplaintPhoto, User

MAX_PHOTOS = 3
MAX_PHOTO_BYTES = 5 * 1024 * 1024
MAX_PHOTO_PIXELS = 20_000_000


def beneficiary_api(view):
    from functools import wraps
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return JsonResponse({"error": "login_required"}, status=401)
        if request.user.role != User.Role.CITIZEN:
            return JsonResponse({"error": "role_forbidden"}, status=403)
        return view(request, *args, **kwargs)
    return wrapped


def normalize_photo(upload):
    if upload.size > MAX_PHOTO_BYTES:
        raise ValueError("الحد الأقصى للصورة 5 ميجابايت.")
    raw = upload.read(MAX_PHOTO_BYTES + 1)
    if len(raw) > MAX_PHOTO_BYTES:
        raise ValueError("الحد الأقصى للصورة 5 ميجابايت.")
    try:
        with Image.open(io.BytesIO(raw)) as image:
            if image.format not in {"JPEG", "PNG", "WEBP"}:
                raise ValueError("تقبل صور JPEG وPNG وWebP فقط.")
            if image.width * image.height > MAX_PHOTO_PIXELS:
                raise ValueError("الصورة كبيرة جدًا؛ اختر صورة بدقة أقل.")
            image.verify()
        with Image.open(io.BytesIO(raw)) as image:
            clean = ImageOps.exif_transpose(image).convert("RGB")
            clean.thumbnail((2400, 2400))
            output = io.BytesIO()
            # A new JPEG discards EXIF/location metadata and any original trailing bytes.
            clean.save(output, format="JPEG", quality=85)
    except (UnidentifiedImageError, OSError, SyntaxError, Image.DecompressionBombError) as error:
        raise ValueError("تعذر قراءة الصورة. اختر ملف صورة صالحًا.") from error
    return hashlib.sha256(raw).hexdigest(), output.getvalue()


def receipt(complaint):
    return {
        "client_id": str(complaint.client_id), "reference": complaint.reference,
        "detail_url": reverse("complaint_detail", args=[complaint.pk]),
        "status": complaint.status, "status_label": complaint.get_status_display(),
        "department": complaint.get_department_display(),
        "complaint_type": complaint.get_complaint_type_display(), "other_type": complaint.other_type,
        "neighborhood": complaint.get_neighborhood_display(), "other_neighborhood": complaint.other_neighborhood,
        "received_at": complaint.received_at.isoformat(),
        "photos": [{"id": photo.pk, "url": reverse("complaint_photo", args=[photo.pk])} for photo in complaint.photos.all()],
    }


@beneficiary_api
@require_POST
def sync_complaint(request):
    if request.content_type != "multipart/form-data":
        return JsonResponse({"error": "multipart_required"}, status=415)
    try:
        payload = json.loads(request.POST.get("payload", ""))
    except (ValueError, UnicodeDecodeError):
        return JsonResponse({"error": "invalid_payload"}, status=400)
    if not isinstance(payload, dict):
        return JsonResponse({"error": "invalid_payload"}, status=400)
    form = ComplaintForm(payload)
    if not form.is_valid():
        return JsonResponse({"error": "invalid_payload", "fields": form.errors.get_json_data()}, status=400)
    data = form.cleaned_data.copy()
    if data.pop("owner_id") != request.user.pk:
        return JsonResponse({"error": "account_changed"}, status=409)
    client_id = data.pop("client_id")
    uploads = request.FILES.getlist("photos")
    if len(uploads) > MAX_PHOTOS or any(key != "photos" for key in request.FILES):
        return JsonResponse({"error": "invalid_photos", "message": "أرفق حتى 3 صور فقط."}, status=400)
    try:
        photos = [normalize_photo(upload) for upload in uploads]
    except ValueError as error:
        return JsonResponse({"error": "invalid_photos", "message": str(error)}, status=400)
    digest = hashlib.sha256(json.dumps({"data": data, "photos": [item[0] for item in photos]},
                                      default=str, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    stored_files = []
    try:
        with transaction.atomic():
            complaint, created = Complaint.objects.get_or_create(
                owner=request.user, client_id=client_id, defaults={**data, "payload_digest": digest,
                    "department": Complaint.Department.FINANCE if data["complaint_type"] == "high_bill" else Complaint.Department.TECHNICAL},
            )
            if not created and complaint.payload_digest != digest:
                return JsonResponse({"error": "id_conflict"}, status=409)
            if created:
                from .models import ComplaintEvent
                ComplaintEvent.objects.create(complaint=complaint, actor=None, action="routed",
                                              note=complaint.get_department_display())
                for position, (_, image_bytes) in enumerate(photos):
                    photo = ComplaintPhoto(complaint=complaint, position=position)
                    photo.image.save("photo.jpg", ContentFile(image_bytes), save=False)
                    stored_files.append((photo.image.storage, photo.image.name))
                    photo.save()
    except Exception:
        for storage, name in stored_files:
            storage.delete(name)
        raise
    return JsonResponse({"saved": True, **receipt(complaint)}, status=201 if created else 200)


@beneficiary_api
@require_GET
def my_complaints(request):
    # The workspace shows the latest receipts; the full register has pagination.
    if request.GET.get("owner_id") is not None and request.GET["owner_id"] != str(request.user.pk):
        return JsonResponse({"error": "account_changed"}, status=409)
    complaints = request.user.complaints.prefetch_related("photos")[:20]
    return JsonResponse({"complaints": [receipt(item) for item in complaints]})


@beneficiary_api
@require_GET
def complaint_photo(request, photo_id):
    photo = get_object_or_404(ComplaintPhoto, pk=photo_id, complaint__owner=request.user)
    try:
        image = photo.image.open("rb")
    except FileNotFoundError:
        return JsonResponse({"error": "photo_unavailable"}, status=404)
    response = FileResponse(image, content_type="image/jpeg", filename="complaint-photo.jpg")
    response["Cache-Control"] = "no-store, private"
    return response
