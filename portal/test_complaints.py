import io
import json
import tempfile
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from PIL import Image
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import close_old_connections
from django.test import Client, TestCase, TransactionTestCase, override_settings

from .catalog import COMPLAINT_TYPES, NEIGHBORHOODS
from .models import Complaint, ComplaintPhoto, User


def image_file(color="blue", name="photo.png", dimensions=(50, 30), exif=None):
    output = io.BytesIO()
    Image.new("RGB", dimensions, color).save(output, format="PNG", exif=exif or b"")
    return SimpleUploadedFile(name, output.getvalue(), content_type="image/png")


class ComplaintIntakeTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = User.objects.create_user("intake-beneficiary", role="citizen", phone="777000000")
        cls.other = User.objects.create_user("intake-other", role="citizen")
        cls.director = User.objects.create_user("intake-director", role="director")

    def setUp(self):
        self.media = tempfile.TemporaryDirectory()
        self.override = override_settings(MEDIA_ROOT=self.media.name)
        self.override.enable()
        self.addCleanup(self.override.disable)
        self.addCleanup(self.media.cleanup)
        self.client.force_login(self.owner)

    def payload(self, **overrides):
        return {"owner_id": self.owner.pk, "client_id": str(uuid.uuid4()), "reporter_name": "مستفيد تجريبي",
                "phone": "777000000", "complaint_type": "leak", "neighborhood": "n11", "address": "عنوان تجريبي",
                "landmark": "مدرسة تجريبية", "description": "تسريب مياه تجريبي", **overrides}

    def submit(self, payload=None, photos=None, client=None):
        return (client or self.client).post("/api/complaints/sync/", {
            "payload": json.dumps(payload or self.payload()), "photos": photos or [],
        })

    def test_complete_intake_without_subscription_meter_location_or_photo(self):
        response = self.submit()
        self.assertEqual(response.status_code, 201)
        item = Complaint.objects.get()
        self.assertEqual(response.json()["reference"], item.reference)
        self.assertEqual(response.json()["status_label"], "تم الاستلام")
        self.assertEqual(item.owner, self.owner)
        self.assertEqual(item.subscription_number, "")
        self.assertIsNone(item.latitude)
        self.assertEqual(response.json()["photos"], [])

    def test_catalog_has_all_approved_types_and_neighborhoods(self):
        self.assertEqual(len(COMPLAINT_TYPES), 7)
        self.assertEqual(len(NEIGHBORHOODS), 37)
        for code, label in COMPLAINT_TYPES:
            with self.subTest(type=label):
                self.assertEqual(self.submit(self.payload(complaint_type=code, other_type="نوع تجريبي")).status_code, 201)
                item = Complaint.objects.latest("pk")
                self.assertEqual(item.department, "finance" if code == "high_bill" else "technical")
                self.assertEqual(item.events.first().action, "routed")
        for code, label in NEIGHBORHOODS:
            with self.subTest(neighborhood=label):
                self.assertEqual(self.submit(self.payload(neighborhood=code, other_neighborhood="حي تجريبي")).status_code, 201)
        self.assertIn(("n26", "حي الرضوان"), NEIGHBORHOODS)
        self.assertIn(("n36", "الجفينة – القطاع 10"), NEIGHBORHOODS)

    def test_other_requires_details_for_both_choices(self):
        for data in [self.payload(complaint_type="other"), self.payload(neighborhood="other")]:
            self.assertEqual(self.submit(data).status_code, 400)
        self.assertFalse(Complaint.objects.exists())

    def test_required_fields_and_invalid_catalog_are_checked_on_server(self):
        for field in ["reporter_name", "phone", "complaint_type", "neighborhood", "address", "landmark", "description"]:
            with self.subTest(field=field):
                self.assertEqual(self.submit(self.payload(**{field: ""})).status_code, 400)
        for overrides in [{"complaint_type": "unknown"}, {"neighborhood": "old-region-1"}, {"phone": "not-a-phone"}, {"description": "x" * 5001}]:
            self.assertEqual(self.submit(self.payload(**overrides)).status_code, 400)
        self.assertFalse(Complaint.objects.exists())

    def test_coordinates_are_optional_but_pair_and_bounds_are_required(self):
        valid = self.submit(self.payload(latitude="15.4839300", longitude="45.3220500"))
        self.assertEqual(valid.status_code, 201)
        self.assertEqual(str(Complaint.objects.get().latitude), "15.4839300")
        for overrides in [{"latitude": "15"}, {"longitude": "45"}, {"latitude": "91", "longitude": "45"},
                          {"latitude": "15", "longitude": "181"}, {"latitude": "NaN", "longitude": "45"}]:
            self.assertEqual(self.submit(self.payload(**overrides)).status_code, 400)
        self.assertEqual(Complaint.objects.count(), 1)

    def test_reference_status_and_owner_cannot_be_supplied_by_client(self):
        result = self.submit(self.payload(reference="FAKE-1", status="closed", owner=self.director.pk))
        self.assertEqual(result.status_code, 201)
        self.assertNotEqual(result.json()["reference"], "FAKE-1")
        self.assertEqual(Complaint.objects.get().status, "received")
        self.assertEqual(Complaint.objects.get().owner, self.owner)

    def test_owner_mismatch_never_saves(self):
        self.client.force_login(self.other)
        self.assertEqual(self.submit().status_code, 409)
        self.assertFalse(Complaint.objects.exists())

    def test_employees_cannot_enter_beneficiary_intake_api(self):
        for role in User.Role.values:
            if role == "citizen":
                continue
            worker = User.objects.create_user("intake-worker-" + role, role=role)
            self.client.force_login(worker)
            self.assertEqual(self.submit(self.payload(owner_id=worker.pk)).status_code, 403)
            self.assertEqual(self.client.get("/api/complaints/").status_code, 403)
        self.assertFalse(Complaint.objects.exists())

    def test_anonymous_apis_are_rejected(self):
        self.client.logout()
        self.assertEqual(self.submit().status_code, 401)
        self.assertEqual(self.client.get("/api/complaints/").status_code, 401)
        self.assertEqual(self.client.get("/api/complaints/photos/1/").status_code, 401)

    def test_csrf_is_enforced_for_multipart(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.owner)
        self.assertEqual(self.submit(client=client).status_code, 403)

    def test_same_uuid_retries_preserve_one_report_photo_and_reference(self):
        data = self.payload()
        first = self.submit(data, [image_file()])
        retry = self.submit(data, [image_file()])
        self.assertEqual((first.status_code, retry.status_code), (201, 200))
        self.assertEqual(first.json(), retry.json())
        self.assertEqual(Complaint.objects.count(), 1)
        self.assertEqual(ComplaintPhoto.objects.count(), 1)
        self.assertEqual(len(list(Path(self.media.name).rglob("*.jpg"))), 1)

    def test_retry_with_different_metadata_or_photo_cannot_overwrite(self):
        data = self.payload()
        self.submit(data, [image_file()])
        self.assertEqual(self.submit({**data, "description": "مختلف"}, [image_file()]).status_code, 409)
        self.assertEqual(self.submit(data, [image_file("red")]).status_code, 409)
        self.assertEqual(Complaint.objects.get().description, data["description"])
        self.assertEqual(ComplaintPhoto.objects.count(), 1)

    def test_same_client_uuid_belongs_to_each_owner_independently(self):
        data = self.payload()
        first = self.submit(data).json()
        self.client.force_login(self.other)
        second = self.submit({**data, "owner_id": self.other.pk}).json()
        self.assertNotEqual(first["reference"], second["reference"])
        self.assertEqual(Complaint.objects.count(), 2)

    def test_receipts_and_photos_are_owner_only_and_not_cacheable(self):
        submitted = self.submit(photos=[image_file()]).json()
        photo_url = submitted["photos"][0]["url"]
        response = self.client.get(photo_url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "image/jpeg")
        self.assertIn("no-store", response["Cache-Control"])
        self.assertTrue(b"".join(response.streaming_content))
        self.assertIn("no-store", self.client.get("/api/complaints/")["Cache-Control"])
        self.client.force_login(self.other)
        self.assertEqual(self.client.get("/api/complaints/").json()["complaints"], [])
        self.assertEqual(self.client.get(photo_url).status_code, 404)
        self.client.force_login(self.director)
        self.assertEqual(self.client.get(photo_url).status_code, 403)
        self.assertEqual(self.client.get("/media/" + ComplaintPhoto.objects.get().image.name).status_code, 404)

    def test_images_are_reencoded_resized_and_exif_removed(self):
        metadata = Image.Exif()
        metadata[270] = "Private camera metadata"
        self.assertEqual(self.submit(photos=[image_file(dimensions=(3000, 100), exif=metadata)]).status_code, 201)
        photo = ComplaintPhoto.objects.get()
        with photo.image.open("rb") as file, Image.open(file) as image:
            self.assertEqual(image.format, "JPEG")
            self.assertLessEqual(image.width, 2400)
            self.assertEqual(len(image.getexif()), 0)

    def test_invalid_disguised_or_oversize_images_do_not_create_records(self):
        for upload in [SimpleUploadedFile("bad.jpg", b"<svg onload='bad'/>", content_type="image/jpeg"),
                       SimpleUploadedFile("bad.png", b"not an image", content_type="image/png"),
                       SimpleUploadedFile("huge.jpg", b"x" * (5 * 1024 * 1024 + 1), content_type="image/jpeg")]:
            self.assertEqual(self.submit(photos=[upload]).status_code, 400)
        self.assertFalse(Complaint.objects.exists())
        self.assertFalse(list(Path(self.media.name).rglob("*.jpg")))

    def test_limit_three_photos_and_invalid_payload(self):
        self.assertEqual(self.submit(photos=[image_file(name=f"{i}.png") for i in range(4)]).status_code, 400)
        for payload in ["bad-json", "[]", "{}"]:
            self.assertEqual(self.client.post("/api/complaints/sync/", {"payload": payload}).status_code, 400)
        self.assertEqual(self.client.post("/api/complaints/sync/", "{}", content_type="application/json").status_code, 415)
        self.assertEqual(self.client.get("/api/complaints/sync/").status_code, 405)

    def test_legacy_foundation_drafts_remain_drafts(self):
        self.assertContains(self.client.get("/workspace/"), "المسودات الأولية السابقة")
        self.assertContains(self.client.get("/workspace/"), "تسجيل بلاغ جديد")
        self.client.force_login(self.director)
        self.assertNotContains(self.client.get("/workspace/"), 'id="complaint-form"')

    def test_receipt_expected_owner_detects_a_changed_session(self):
        self.submit()
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(f"/api/complaints/?owner_id={self.owner.pk}").status_code, 409)
        self.assertEqual(self.client.get(f"/api/complaints/?owner_id={self.other.pk}").json()["complaints"], [])

    def test_attachment_failure_rolls_back_report_and_removes_written_files(self):
        with patch.object(ComplaintPhoto, "save", side_effect=OSError("simulated storage failure")):
            with self.assertRaises(OSError):
                self.submit(photos=[image_file()])
        self.assertFalse(Complaint.objects.exists())
        self.assertFalse(ComplaintPhoto.objects.exists())
        self.assertFalse(list(Path(self.media.name).rglob("*.jpg")))


class ConcurrentComplaintTests(TransactionTestCase):
    def test_simultaneous_retries_create_one_receipt(self):
        user = User.objects.create_user("concurrent-intake", role="citizen")
        payload = {"owner_id": user.pk, "client_id": str(uuid.uuid4()), "reporter_name": "تجريبي", "phone": "777000000",
                   "complaint_type": "leak", "neighborhood": "n11", "address": "موقع تجريبي", "landmark": "معلم تجريبي", "description": "تسريب"}
        def send(_):
            close_old_connections()
            try:
                client = Client(); client.force_login(User.objects.get(pk=user.pk))
                response = client.post("/api/complaints/sync/", {"payload": json.dumps(payload)})
                return response.status_code, response.json()["reference"]
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(send, [1, 2]))
        self.assertEqual(sorted(status for status, _ in results), [200, 201])
        self.assertEqual(len({reference for _, reference in results}), 1)
        self.assertEqual(Complaint.objects.count(), 1)
