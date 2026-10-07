import tempfile
import uuid
from datetime import datetime, timezone

from django.core.files.base import ContentFile
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import Complaint, ComplaintPhoto, User


class ComplaintRegisterTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = User.objects.create_user("register-owner", role="citizen")
        cls.other = User.objects.create_user("register-other", role="citizen")
        cls.director = User.objects.create_user("register-director", role="director")
        cls.mine = cls.record(cls.owner, reporter_name="مستفيد تجريبي ألف", phone="777111111")
        cls.hidden = cls.record(cls.other, reporter_name="بيانات مستفيد آخر", phone="777222222", complaint_type="other", other_type="نوع خاص")

    @staticmethod
    def record(owner, **kwargs):
        return Complaint.objects.create(owner=owner, client_id=uuid.uuid4(), payload_digest="a" * 64,
            **{"reporter_name": "اسم تجريبي", "phone": "777000000", "complaint_type": "leak", "neighborhood": "n11",
               "address": "عنوان تجريبي", "landmark": "معلم تجريبي", "description": "وصف تجريبي", **kwargs})

    def setUp(self):
        self.client.force_login(self.owner)

    def test_beneficiary_list_detail_and_direct_reference_are_owner_only(self):
        response = self.client.get(reverse("complaint_register"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.mine.reference)
        self.assertNotContains(response, self.hidden.reference)
        self.assertEqual(response.context["page"].paginator.count, 1)
        self.assertEqual(self.client.get(reverse("complaint_detail", args=[self.hidden.pk])).status_code, 404)
        result = self.client.get(reverse("complaint_register"), {"q": self.hidden.reference, "owner_id": self.other.pk})
        self.assertEqual(result.context["page"].paginator.count, 0)
        detail = self.client.get(reverse("complaint_detail", args=[self.mine.pk]))
        self.assertContains(detail, "عنوان تجريبي")
        self.assertContains(detail, "لم يُذكر")
        self.assertEqual(detail["Cache-Control"], "no-store, private")

    def test_director_can_read_full_register_and_details_only_via_staff_routes(self):
        self.client.force_login(self.director)
        response = self.client.get(reverse("staff_complaints"))
        self.assertEqual(response.context["page"].paginator.count, 2)
        self.assertContains(response, self.hidden.reference)
        detail = self.client.get(reverse("staff_complaint_detail", args=[self.hidden.pk]))
        self.assertContains(detail, "بيانات مستفيد آخر")
        self.assertEqual(detail["Cache-Control"], "no-store, private")
        self.assertEqual(self.client.get(reverse("complaint_register")).status_code, 403)
        self.assertEqual(self.client.get(reverse("complaint_detail", args=[self.mine.pk])).status_code, 403)

    def test_every_non_director_role_is_denied_internal_register_detail_and_photos(self):
        for role, _ in User.Role.choices:
            if role == User.Role.DIRECTOR:
                continue
            with self.subTest(role=role):
                user = User.objects.create_user("blocked-" + role, role=role)
                self.client.force_login(user)
                for route, args in (("staff_complaints", []), ("staff_complaint_detail", [self.mine.pk]), ("staff_complaint_photo", [999])):
                    self.assertEqual(self.client.get(reverse(route, args=args)).status_code, 403)
                if role != User.Role.CITIZEN:
                    self.assertEqual(self.client.get(reverse("complaint_register")).status_code, 403)

    def test_anonymous_register_and_detail_require_login_and_photo_requires_session(self):
        self.client.logout()
        for route, args in (("complaint_register", []), ("staff_complaints", []), ("complaint_detail", [self.mine.pk]), ("staff_complaint_detail", [self.mine.pk])):
            response = self.client.get(reverse(route, args=args))
            self.assertEqual(response.status_code, 302)
            self.assertTrue(response.url.startswith("/accounts/login/"))
        self.assertEqual(self.client.get(reverse("staff_complaint_photo", args=[999])).status_code, 401)

    def test_search_by_reference_arabic_digits_name_phone_and_filter_combination(self):
        arabic_reference = self.mine.reference.translate(str.maketrans("0123456789", "٠١٢٣٤٥٦٧٨٩"))
        for query in (self.mine.reference.lower(), arabic_reference, "تجريبي ألف", "777111111"):
            with self.subTest(query=query):
                response = self.client.get(reverse("complaint_register"), {"q": query, "complaint_type": "leak", "neighborhood": "n11", "status": "received"})
                self.assertEqual(list(response.context["page"]), [self.mine])
        self.assertEqual(self.client.get(reverse("complaint_register"), {"complaint_type": "other"}).context["page"].paginator.count, 0)
        self.client.force_login(self.director)
        self.assertEqual(self.client.get(reverse("staff_complaints"), {"q": "777222222", "complaint_type": "other"}).context["page"].paginator.count, 1)

    def test_invalid_filters_do_not_silently_expand_results(self):
        for params in ({"complaint_type": "invented"}, {"status": "invented"}, {"date_from": "invalid"}, {"date_from": "2026-03-02", "date_to": "2026-03-01"}, {"date_to": "9999-12-31"}, {"q": "a" * 151}):
            with self.subTest(params=params):
                response = self.client.get(reverse("complaint_register"), params)
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.context["filter_form"].errors)
                self.assertEqual(response.context["page"].paginator.count, 0)

    def test_date_filter_uses_inclusive_mareb_calendar_day(self):
        before = self.record(self.owner)
        after = self.record(self.owner)
        Complaint.objects.filter(pk=before.pk).update(received_at=datetime(2026, 3, 1, 20, 59, 59, tzinfo=timezone.utc))
        Complaint.objects.filter(pk=self.mine.pk).update(received_at=datetime(2026, 3, 1, 21, tzinfo=timezone.utc))
        Complaint.objects.filter(pk=after.pk).update(received_at=datetime(2026, 3, 2, 21, tzinfo=timezone.utc))
        response = self.client.get(reverse("complaint_register"), {"date_from": "2026-03-02", "date_to": "2026-03-02"})
        self.assertEqual([item.pk for item in response.context["page"]], [self.mine.pk])

    def test_pagination_is_stable_and_retains_filters(self):
        ids = [self.record(self.owner).pk for _ in range(24)] + [self.mine.pk]
        Complaint.objects.filter(pk__in=ids).update(received_at=datetime(2026, 3, 1, tzinfo=timezone.utc))
        first = self.client.get(reverse("complaint_register"), {"complaint_type": "leak"})
        second = self.client.get(reverse("complaint_register"), {"complaint_type": "leak", "page": 2})
        self.assertEqual(first.context["page"].paginator.count, 25)
        self.assertEqual([item.pk for item in first.context["page"]], sorted(ids, reverse=True)[:20])
        self.assertEqual([item.pk for item in second.context["page"]], sorted(ids, reverse=True)[20:])
        self.assertContains(first, "complaint_type=leak&amp;page=2")
        self.assertEqual(self.client.get(reverse("complaint_register"), {"page": "bad"}).context["page"].number, 1)

    def test_html_escapes_reporter_description_and_address(self):
        Complaint.objects.filter(pk=self.mine.pk).update(description='<script>alert("secret")</script>', address='<img src=x onerror="attack()">')
        response = self.client.get(reverse("complaint_detail", args=[self.mine.pk]))
        self.assertContains(response, "&lt;script&gt;")
        self.assertNotContains(response, '<script>alert("secret")</script>')
        self.assertNotContains(response, '<img src=x onerror="attack()">')

    def test_director_photo_is_private_and_not_accessible_to_beneficiary(self):
        with tempfile.TemporaryDirectory() as media, override_settings(MEDIA_ROOT=media):
            photo = ComplaintPhoto.objects.create(complaint=self.mine, position=0, image=ContentFile(b"test-private-image", name="photo.jpg"))
            staff_url = reverse("staff_complaint_photo", args=[photo.pk])
            self.assertEqual(self.client.get(staff_url).status_code, 403)
            self.client.force_login(self.director)
            response = self.client.get(staff_url)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(b"".join(response.streaming_content), b"test-private-image")
            self.assertEqual(response["Cache-Control"], "no-store, private")
            self.assertContains(self.client.get(reverse("staff_complaint_detail", args=[self.mine.pk])), staff_url)
            self.assertEqual(self.client.get(reverse("complaint_photo", args=[photo.pk])).status_code, 403)
            photo.image.delete(save=False)
            self.assertEqual(self.client.get(staff_url).status_code, 404)

    def test_new_register_views_are_read_only(self):
        self.assertEqual(self.client.post(reverse("complaint_register")).status_code, 405)
        self.assertEqual(self.client.post(reverse("complaint_detail", args=[self.mine.pk])).status_code, 405)
        self.client.force_login(self.director)
        self.assertEqual(self.client.post(reverse("staff_complaints")).status_code, 405)
        self.assertEqual(self.client.post(reverse("staff_complaint_detail", args=[self.mine.pk])).status_code, 405)
