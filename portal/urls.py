from django.contrib.auth.views import LoginView, LogoutView
from django.urls import path

from . import views
from . import complaints
from . import registers
from . import workflow
from . import violations
from . import reports
from . import settlements
from . import documents
from . import report_templates
from . import committee
from . import work_results
from .forms import LoginForm
from .auth_views import ThrottledLoginView

urlpatterns = [
    path("committee/", committee.guide, name="committee_guide"),
    path("committee/enter/", committee.enter, name="committee_enter"),
    path("", views.home, name="home"),
    path("accounts/login/", ThrottledLoginView.as_view(authentication_form=LoginForm, template_name="registration/login.html"), name="login"),
    path("accounts/logout/", LogoutView.as_view(), name="logout"),
    path("accounts/register/", views.register, name="register"),
    path("accounts/password/", views.ChangePasswordView.as_view(), name="password_change"),
    path("workspace/", views.workspace, name="workspace"),
    path("workspace/complaints/", registers.beneficiary_register, name="complaint_register"),
    path("workspace/complaints/<int:complaint_id>/", registers.beneficiary_detail, name="complaint_detail"),
    path("staff/", views.staff_workspace, name="staff_workspace"),
    path("staff/employees/", views.employees, name="employees"),
    path("staff/employees/<int:employee_id>/edit/", views.employee_manage, name="employee_manage"),
    path("staff/complaints/", registers.director_register, name="staff_complaints"),
    path("staff/complaints/<int:complaint_id>/", registers.director_detail, name="staff_complaint_detail"),
    path("staff/tasks/", workflow.queue, name="work_queue"),
    path("staff/tasks/<int:complaint_id>/", workflow.detail, name="work_detail"),
    path("staff/tasks/<int:complaint_id>/action/", workflow.act, name="work_action"),
    path("staff/violations/", violations.register, name="violation_register"),
    path("staff/violations/new/", violations.new, name="violation_new"),
    path("staff/violations/<int:violation_id>/", violations.detail, name="violation_detail"),
    path("staff/violations/<int:violation_id>/action/", violations.handoff, name="violation_handoff"),
    path("staff/violations/<int:violation_id>/documents/new/", documents.compose, name="violation_document_new"),
    path("staff/documents/<int:document_id>/", documents.detail, name="violation_document"),
    path("staff/documents/<int:document_id>/edit/", documents.edit, name="violation_document_edit"),
    path("staff/documents/<int:document_id>/issue/", documents.issue, name="violation_document_issue"),
    path("staff/documents/<int:document_id>/print/", documents.printable, name="violation_document_print"),
    path("staff/violations/<int:violation_id>/settlement/<str:action>/", settlements.act, name="violation_settlement"),
    path("api/staff/violations/fee-memo/", settlements.memo, name="violation_fee_memo"),
    path("staff/reports/", reports.director_report, name="director_report"),
    path("staff/report-templates/", report_templates.library, name="report_template_library"),
    path("staff/report-templates/new/", report_templates.create, name="report_template_new"),
    path("staff/report-templates/<int:template_id>/", report_templates.detail, name="report_template_detail"),
    path("staff/report-templates/<int:template_id>/retire/", report_templates.retire, name="report_template_retire"),
    path("staff/report-templates/versions/<int:version_id>/edit/", report_templates.edit, name="report_template_edit"),
    path("staff/report-templates/versions/<int:version_id>/approve/", report_templates.approve, name="report_template_approve"),
    path("staff/report-templates/versions/<int:version_id>/preview/", report_templates.preview, name="report_template_preview"),
    path("staff/generated-reports/<str:source>/<int:record_id>/", report_templates.choose, name="report_template_choose"),
    path("staff/generated-reports/versions/<int:version_id>/<int:record_id>/new/", report_templates.generate, name="report_template_generate"),
    path("staff/generated-reports/<int:report_id>/print/", report_templates.saved_print, name="saved_report_print"),
    path("api/violations/sync/", violations.sync, name="violation_sync"),
    path("api/staff/tasks/photos/<int:photo_id>/", workflow.photo, name="work_photo"),
    path("api/work-results/sync/", work_results.sync_result, name="work_result_sync"),
    path("api/staff/tasks/execution-photos/<int:photo_id>/", work_results.photo, name="work_result_photo"),
    path("api/staff/complaints/photos/<int:photo_id>/", registers.director_photo, name="staff_complaint_photo"),
    path("app-shell/", views.app_shell, name="app_shell"),
    path("sw.js", views.service_worker, name="service_worker"),
    path("api/session/", views.session_info, name="session_info"),
    path("api/drafts/sync/", views.sync_draft, name="sync_draft"),
    path("api/drafts/", views.my_drafts, name="my_drafts"),
    path("api/complaints/sync/", complaints.sync_complaint, name="sync_complaint"),
    path("api/complaints/", complaints.my_complaints, name="my_complaints"),
    path("api/complaints/photos/<int:photo_id>/", complaints.complaint_photo, name="complaint_photo"),
    path("health/", views.health, name="health"),
]
