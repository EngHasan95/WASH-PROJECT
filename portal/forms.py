from django import forms
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm

from .models import User
from .catalog import COMPLAINT_TYPES, NEIGHBORHOODS


class LoginForm(AuthenticationForm):
    username = forms.CharField(label="اسم المستخدم", widget=forms.TextInput(attrs={"autocomplete": "username", "autocapitalize": "none"}))
    password = forms.CharField(label="كلمة المرور", strip=False, widget=forms.PasswordInput(attrs={"autocomplete": "current-password"}))


class CitizenRegistrationForm(UserCreationForm):
    first_name = forms.CharField(label="الاسم الكامل", max_length=150)
    phone = forms.RegexField(label="رقم الهاتف", regex=r"^\+?[0-9]{7,15}$", error_messages={"invalid": "أدخل رقم هاتف صحيحًا بالأرقام الإنجليزية."})

    class Meta:
        model = User
        fields = ("first_name", "phone", "username")
        labels = {"username": "اسم المستخدم"}
        help_texts = {"username": "اختر اسمًا تستخدمه للدخول ومتابعة مسوداتك."}

    def save(self, commit=True):
        user = super().save(commit=False)
        user.role = User.Role.CITIZEN
        user.is_staff = False
        user.is_superuser = False
        if commit:
            user.save()
        return user


class EmployeeCreationForm(UserCreationForm):
    first_name = forms.CharField(label="اسم الموظف", max_length=150)
    role = forms.ChoiceField(label="الدور الوظيفي", choices=[choice for choice in User.Role.choices if choice[0] != User.Role.CITIZEN])
    is_department_responsible = forms.BooleanField(label="مسؤول إسناد بلاغات القسم المالي", required=False)

    def clean(self):
        data = super().clean()
        if data.get("is_department_responsible") and data.get("role") != User.Role.FINANCE:
            self.add_error("is_department_responsible", "مسؤول الإسناد المالي يجب أن يكون من القسم المالي.")
        return data

    class Meta:
        model = User
        fields = ("first_name", "username", "phone", "role", "is_department_responsible")
        labels = {"username": "اسم المستخدم", "phone": "رقم الهاتف (اختياري)"}
        help_texts = {"username": "حساب مستقل لهذا الموظف."}

    def save(self, commit=True):
        user = super().save(commit=False)
        user.must_change_password = True
        user.is_staff = False
        user.is_superuser = False
        if commit:
            user.save()
        return user


class DraftForm(forms.Form):
    owner_id = forms.IntegerField(min_value=1)
    client_id = forms.UUIDField()
    kind = forms.ChoiceField(choices=[("complaint", "مسودة بلاغ"), ("violation", "مسودة مخالفة")])
    title = forms.CharField(max_length=160)
    description = forms.CharField(max_length=5000)


class ComplaintForm(forms.Form):
    owner_id = forms.IntegerField(min_value=1)
    client_id = forms.UUIDField()
    reporter_name = forms.CharField(label="اسم مقدم البلاغ", max_length=150)
    phone = forms.RegexField(label="رقم الهاتف", regex=r"^\+?[0-9]{7,15}$")
    subscription_number = forms.CharField(max_length=50, required=False)
    meter_number = forms.CharField(max_length=50, required=False)
    complaint_type = forms.ChoiceField(choices=COMPLAINT_TYPES)
    other_type = forms.CharField(max_length=150, required=False)
    neighborhood = forms.ChoiceField(choices=NEIGHBORHOODS)
    other_neighborhood = forms.CharField(max_length=150, required=False)
    address = forms.CharField(max_length=500)
    landmark = forms.CharField(max_length=200)
    description = forms.CharField(max_length=5000)
    latitude = forms.DecimalField(max_digits=10, decimal_places=7, min_value=-90, max_value=90, required=False)
    longitude = forms.DecimalField(max_digits=10, decimal_places=7, min_value=-180, max_value=180, required=False)

    def clean(self):
        data = super().clean()
        for choice, other in (("complaint_type", "other_type"), ("neighborhood", "other_neighborhood")):
            if data.get(choice) == "other" and not data.get(other):
                self.add_error(other, "اكتب التفاصيل عند اختيار أخرى.")
            elif data.get(choice) != "other":
                data[other] = ""
        if (data.get("latitude") is None) != (data.get("longitude") is None):
            self.add_error("latitude", "أرفق إحداثيي الموقع معًا أو اتركهما فارغين.")
        return data


class EmployeeManagementForm(forms.ModelForm):
    role = forms.ChoiceField(label="الدور الوظيفي", choices=[choice for choice in User.Role.choices if choice[0] != User.Role.CITIZEN])
    is_active = forms.BooleanField(label="الحساب فعال", required=False)
    is_department_responsible = forms.BooleanField(label="مسؤول إسناد بلاغات القسم المالي", required=False)
    reason = forms.CharField(label="سبب التعديل", max_length=2000, widget=forms.Textarea(attrs={"rows": 3}))
    password1 = forms.CharField(label="كلمة مرور مؤقتة جديدة (اختياري)", required=False, strip=False, widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}))
    password2 = forms.CharField(label="تأكيد كلمة المرور الجديدة", required=False, strip=False, widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}))

    class Meta:
        model = User
        fields = ("first_name", "phone", "role", "is_active", "is_department_responsible")
        labels = {"first_name": "اسم الموظف", "phone": "رقم الهاتف"}

    def clean(self):
        from django.contrib.auth.password_validation import validate_password
        from django.core.exceptions import ValidationError
        data = super().clean()
        if data.get("is_department_responsible") and data.get("role") != User.Role.FINANCE:
            self.add_error("is_department_responsible", "مسؤول الإسناد المالي يجب أن يكون من القسم المالي.")
        password = data.get("password1")
        if password or data.get("password2"):
            if password != data.get("password2"):
                self.add_error("password2", "كلمتا المرور غير متطابقتين.")
            elif password:
                try:
                    validate_password(password, self.instance)
                except ValidationError as error:
                    self.add_error("password1", error)
        return data
