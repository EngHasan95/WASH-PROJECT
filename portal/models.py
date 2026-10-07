import uuid
from decimal import Decimal

from django.contrib.auth.models import AbstractUser
from django.db import models

from .catalog import COMPLAINT_TYPES, NEIGHBORHOODS


class User(AbstractUser):
    class Role(models.TextChoices):
        CITIZEN = "citizen", "مستفيد"
        DIRECTOR = "director", "مدير الشؤون الفنية"
        TECHNICIAN = "technician", "فني"
        EMPLOYEE = "employee", "موظف"
        SYSTEM_MANAGER = "system_manager", "مسؤول النظام"
        SECRETARIAT = "secretariat", "السكرتارية"
        FOLLOWUP = "followup", "قسم المتابعة"
        FINANCE = "finance", "القسم المالي"

    role = models.CharField(max_length=24, choices=Role.choices, default=Role.CITIZEN)
    phone = models.CharField(max_length=20, blank=True)
    must_change_password = models.BooleanField(default=False)
    is_department_responsible = models.BooleanField(default=False)

    @property
    def display_name(self):
        return self.get_full_name() or self.username

    @property
    def is_employee(self):
        return self.role != self.Role.CITIZEN


class AccountEvent(models.Model):
    actor = models.ForeignKey(User, on_delete=models.PROTECT, related_name="account_actions")
    target = models.ForeignKey(User, on_delete=models.PROTECT, related_name="account_events")
    action = models.CharField(max_length=32, default="created")
    note = models.TextField(max_length=3000, blank=True)
    previous_role = models.CharField(max_length=24, blank=True)
    new_role = models.CharField(max_length=24, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class Draft(models.Model):
    """Foundation-stage drafts, not official complaints or violation proceedings."""
    class Kind(models.TextChoices):
        COMPLAINT = "complaint", "مسودة بلاغ"
        VIOLATION = "violation", "مسودة مخالفة ميدانية"

    client_id = models.UUIDField(default=uuid.uuid4)
    owner = models.ForeignKey(User, on_delete=models.PROTECT, related_name="drafts")
    kind = models.CharField(max_length=16, choices=Kind.choices)
    title = models.CharField(max_length=160)
    description = models.TextField(max_length=5000)
    received_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["owner", "client_id"], name="unique_owner_draft")]
        ordering = ["-received_at"]


class Complaint(models.Model):
    """A private report routed by type and handled by an accountable employee."""
    class Status(models.TextChoices):
        RECEIVED = "received", "تم الاستلام"
        ASSIGNED = "assigned", "أُسند إلى المختص"
        IN_PROGRESS = "in_progress", "قيد المعالجة"
        REVIEW = "review", "بانتظار مراجعة المدير"
        RETURNED = "returned", "أُعيد للمعالجة"
        CLOSED = "closed", "مغلق"

    class Department(models.TextChoices):
        TECHNICAL = "technical", "الشؤون الفنية"
        FINANCE = "finance", "القسم المالي"

    owner = models.ForeignKey(User, on_delete=models.PROTECT, related_name="complaints")
    client_id = models.UUIDField()
    payload_digest = models.CharField(max_length=64)
    reporter_name = models.CharField(max_length=150)
    phone = models.CharField(max_length=20)
    subscription_number = models.CharField(max_length=50, blank=True)
    meter_number = models.CharField(max_length=50, blank=True)
    complaint_type = models.CharField(max_length=32, choices=COMPLAINT_TYPES)
    other_type = models.CharField(max_length=150, blank=True)
    neighborhood = models.CharField(max_length=16, choices=NEIGHBORHOODS)
    other_neighborhood = models.CharField(max_length=150, blank=True)
    address = models.CharField(max_length=500)
    landmark = models.CharField(max_length=200)
    description = models.TextField(max_length=5000)
    latitude = models.DecimalField(max_digits=10, decimal_places=7, null=True, blank=True)
    longitude = models.DecimalField(max_digits=10, decimal_places=7, null=True, blank=True)
    status = models.CharField(max_length=24, choices=Status.choices, default=Status.RECEIVED)
    department = models.CharField(max_length=16, choices=Department.choices, default=Department.TECHNICAL)
    assignee = models.ForeignKey(User, null=True, blank=True, on_delete=models.PROTECT, related_name="assigned_complaints")
    received_at = models.DateTimeField(auto_now_add=True)

    @property
    def reference(self):
        return f"MRB-{self.pk:07d}"

    class Meta:
        constraints = [models.UniqueConstraint(fields=["owner", "client_id"], name="unique_owner_complaint")]
        ordering = ["-received_at"]


def complaint_photo_path(instance, filename):
    return f"complaints/{instance.complaint_id}/{uuid.uuid4().hex}.jpg"


class ComplaintPhoto(models.Model):
    complaint = models.ForeignKey(Complaint, on_delete=models.CASCADE, related_name="photos")
    image = models.FileField(upload_to=complaint_photo_path)
    position = models.PositiveSmallIntegerField()

    class Meta:
        ordering = ["position"]
        constraints = [models.UniqueConstraint(fields=["complaint", "position"], name="unique_complaint_photo_position")]


class ComplaintEvent(models.Model):
    complaint = models.ForeignKey(Complaint, on_delete=models.CASCADE, related_name="events")
    actor = models.ForeignKey(User, null=True, on_delete=models.PROTECT, related_name="complaint_actions")
    action = models.CharField(max_length=24)
    previous_assignee = models.ForeignKey(User, null=True, blank=True, on_delete=models.PROTECT,
        related_name="previous_complaint_assignments")
    new_assignee = models.ForeignKey(User, null=True, blank=True, on_delete=models.PROTECT,
        related_name="new_complaint_assignments")
    note = models.TextField(max_length=3000, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "pk"]


class Violation(models.Model):
    class Kind(models.TextChoices):
        RANDOM_CONNECTION = "random_connection", "ربط عشوائي"
        ILLEGAL_SUBSCRIPTION = "illegal_subscription", "اشتراك مخالف"

    class Activity(models.TextChoices):
        RESIDENTIAL = "residential", "منزلي"
        COMMERCIAL = "commercial", "تجاري"
        GOVERNMENT = "government", "حكومي"

    class Status(models.TextChoices):
        SYSTEM_MANAGER = "system_manager", "لدى مسؤول النظام"
        SECRETARIAT = "secretariat", "لدى السكرتارية"
        FOLLOWUP = "followup", "لدى قسم المتابعة"
        RESULTS_RETURNED = "results_returned", "عادت النتائج للفنية والمالية"
        AWAITING_PAYMENT = "awaiting_payment", "بانتظار السداد"
        PAID = "paid", "أكدت المالية السداد"
        CLOSED = "closed", "مغلقة بعد التسوية"

    reporter = models.ForeignKey(User, on_delete=models.PROTECT, related_name="reported_violations")
    client_id = models.UUIDField()
    payload_digest = models.CharField(max_length=64)
    person_name = models.CharField(max_length=150)
    address = models.CharField(max_length=500)
    area = models.CharField(max_length=150)
    estimated_cubic_meters = models.DecimalField(max_digits=12, decimal_places=2)
    estimate_days = models.PositiveSmallIntegerField()
    activity = models.CharField(max_length=20, choices=Activity.choices)
    kind = models.CharField(max_length=24, choices=Kind.choices)
    description = models.TextField(max_length=3000, blank=True)
    status = models.CharField(max_length=24, choices=Status.choices, default=Status.SYSTEM_MANAGER)
    received_at = models.DateTimeField(auto_now_add=True)

    @property
    def reference(self):
        return f"V-MRB-{self.pk:07d}"

    class Meta:
        constraints = [models.UniqueConstraint(fields=["reporter", "client_id"], name="unique_reporter_violation")]
        ordering = ["-received_at"]


class ViolationEvent(models.Model):
    violation = models.ForeignKey(Violation, on_delete=models.CASCADE, related_name="events")
    actor = models.ForeignKey(User, null=True, on_delete=models.PROTECT, related_name="violation_actions")
    action = models.CharField(max_length=32)
    note = models.TextField(max_length=3000, blank=True)
    recipient = models.CharField(max_length=200, blank=True)
    document_reference = models.CharField(max_length=100, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "pk"]


class ViolationSettlement(models.Model):
    violation = models.OneToOneField(Violation, on_delete=models.CASCADE, related_name="settlement")
    policy_code = models.CharField(max_length=64)
    fixed_fee = models.DecimalField(max_digits=16, decimal_places=2)
    unit_rate = models.DecimalField(max_digits=10, decimal_places=2)
    approved_units = models.DecimalField(max_digits=12, decimal_places=2)
    unit_description = models.CharField(max_length=100)
    assessment_basis = models.TextField(max_length=3000)
    assessed_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name="violation_assessments")
    assessed_at = models.DateTimeField(auto_now_add=True)
    payment_amount = models.DecimalField(max_digits=16, decimal_places=2, null=True, blank=True)
    payment_receipt = models.CharField(max_length=100, blank=True)
    payment_date = models.DateField(null=True, blank=True)
    payment_confirmed_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.PROTECT, related_name="violation_payments")
    payment_confirmed_at = models.DateTimeField(null=True, blank=True)
    closure_note = models.TextField(max_length=3000, blank=True)
    closed_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.PROTECT, related_name="closed_violations")
    closed_at = models.DateTimeField(null=True, blank=True)

    @property
    def water_charge(self):
        return (self.approved_units * self.unit_rate).quantize(Decimal("0.01"))

    @property
    def total(self):
        return self.fixed_fee + self.water_charge


class ViolationDocument(models.Model):
    class Kind(models.TextChoices):
        MINUTES = "minutes", "محضر معاينة"
        LETTER = "letter", "مذكرة أو مراسلة"

    class Status(models.TextChoices):
        DRAFT = "draft", "مسودة للتحرير"
        ISSUED = "issued", "صادرة من النظام"

    violation = models.ForeignKey(Violation, on_delete=models.CASCADE, related_name="documents")
    client_id = models.UUIDField()
    kind = models.CharField(max_length=16, choices=Kind.choices)
    title = models.CharField(max_length=200)
    official_number = models.CharField(max_length=100, blank=True)
    document_date = models.DateField()
    recipient = models.CharField(max_length=200, blank=True)
    body = models.TextField(max_length=12000)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    revision = models.PositiveIntegerField(default=1)
    snapshot = models.JSONField(default=dict)
    created_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name="created_violation_documents")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    issued_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.PROTECT, related_name="issued_violation_documents")
    issued_at = models.DateTimeField(null=True, blank=True)

    @property
    def reference(self):
        return f"DOC-MRB-{self.pk:07d}"

    class Meta:
        ordering = ["-created_at", "-pk"]
        constraints = [models.UniqueConstraint(fields=["violation", "client_id"], name="unique_violation_document_request")]


class ReportTemplate(models.Model):
    class Source(models.TextChoices):
        VIOLATION = "violation", "ملف مخالفة"
        COMPLAINT = "complaint", "بلاغ مستفيد"
        SUMMARY = "summary", "تقرير إداري مجمع"

    client_id = models.UUIDField(default=uuid.uuid4)
    source = models.CharField(max_length=16, choices=Source.choices)
    revision = models.PositiveIntegerField(default=1)
    enabled = models.BooleanField(default=True)
    created_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name="report_templates")
    created_at = models.DateTimeField(auto_now_add=True)
    active_version = models.ForeignKey("ReportTemplateVersion", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        constraints = [models.UniqueConstraint(fields=["created_by", "client_id"], name="unique_template_create_request")]


class ReportTemplateVersion(models.Model):
    template = models.ForeignKey(ReportTemplate, on_delete=models.CASCADE, related_name="versions")
    client_id = models.UUIDField(default=uuid.uuid4)
    number = models.PositiveIntegerField()
    name = models.CharField(max_length=160)
    spec = models.JSONField(default=dict)
    created_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name="template_versions")
    created_at = models.DateTimeField(auto_now_add=True)
    approved_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.PROTECT, related_name="approved_template_versions")
    approved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-number", "-pk"]
        constraints = [models.UniqueConstraint(fields=["template", "number"], name="unique_report_template_version"),
            models.UniqueConstraint(fields=["template", "client_id"], name="unique_template_edit_request")]


class ReportTemplateEvent(models.Model):
    template = models.ForeignKey(ReportTemplate, on_delete=models.CASCADE, related_name="events")
    version = models.ForeignKey(ReportTemplateVersion, null=True, on_delete=models.CASCADE, related_name="events")
    actor = models.ForeignKey(User, on_delete=models.PROTECT, related_name="report_template_events")
    action = models.CharField(max_length=24)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "pk"]


class SavedReport(models.Model):
    template_version = models.ForeignKey(ReportTemplateVersion, on_delete=models.PROTECT, related_name="saved_reports")
    complaint = models.ForeignKey(Complaint, null=True, blank=True, on_delete=models.CASCADE, related_name="saved_reports")
    violation = models.ForeignKey(Violation, null=True, blank=True, on_delete=models.CASCADE, related_name="saved_reports")
    client_id = models.UUIDField()
    payload_digest = models.CharField(max_length=64)
    content = models.JSONField(default=dict)
    created_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name="saved_reports")
    created_at = models.DateTimeField(auto_now_add=True)

    @property
    def reference(self):
        return f"RPT-MRB-{self.pk:07d}"

    class Meta:
        ordering = ["-created_at", "-pk"]
        constraints = [
            models.UniqueConstraint(fields=["created_by", "client_id"], name="unique_saved_report_request"),
            models.CheckConstraint(condition=models.Q(complaint__isnull=True) | models.Q(violation__isnull=True), name="one_saved_report_record"),
        ]


class LoginThrottle(models.Model):
    key = models.CharField(max_length=64, unique=True)
    window_start = models.DateTimeField()
    attempts = models.PositiveIntegerField(default=0)
    blocked_until = models.DateTimeField(null=True, blank=True)


class ComplaintExecutionResult(models.Model):
    actor = models.ForeignKey(User, on_delete=models.PROTECT, related_name="execution_results")
    complaint = models.ForeignKey(Complaint, on_delete=models.CASCADE, related_name="execution_results")
    event = models.OneToOneField(ComplaintEvent, on_delete=models.PROTECT, related_name="execution_result")
    client_id = models.UUIDField()
    payload_digest = models.CharField(max_length=64)
    assignment_event_id = models.PositiveBigIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["actor", "client_id"], name="unique_actor_execution_result")]


def execution_photo_path(instance, filename):
    return f"execution-results/{instance.result.complaint_id}/{uuid.uuid4().hex}.jpg"


class ComplaintExecutionPhoto(models.Model):
    result = models.ForeignKey(ComplaintExecutionResult, on_delete=models.CASCADE, related_name="photos")
    image = models.FileField(upload_to=execution_photo_path)
    position = models.PositiveSmallIntegerField()

    class Meta:
        ordering = ["position"]
        constraints = [models.UniqueConstraint(fields=["result", "position"], name="unique_result_photo_position")]
