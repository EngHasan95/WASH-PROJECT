import io
import zipfile
import zlib
from xml.etree import ElementTree

from django import forms
from django.utils import timezone

from .models import ReportTemplate
from .template_engine import COMMON, DEFAULT_HEADER, FIELDS, LABEL, TOKEN

OPTIONS = {
    'orientation': [('portrait', 'A4 عمودي'), ('landscape', 'A4 أفقي')],
    'font_size': [('regular', 'عادي'), ('large', 'كبير')],
    'margin': [('normal', 'متوسطة'), ('wide', 'واسعة')],
    'accent': [('water', 'أزرق المياه'), ('ink', 'حبر رسمي'), ('olive', 'زيتوني'), ('sand', 'ذهبي هادئ')],
    'logo': [('center', 'وسط الترويسة'), ('side', 'جانب الترويسة'), ('hidden', 'دون شعار')],
    'alignment': [('right', 'محاذاة لليمين'), ('justify', 'ضبط النص')],
}


def import_docx(upload):
    limit = 2 * 1024 * 1024
    if not upload.name.lower().endswith('.docx') or upload.size > limit:
        raise forms.ValidationError('اختر ملف Word بصيغة DOCX بحجم لا يتجاوز 2 ميجابايت.')
    try:
        payload = upload.read(limit + 1)
        if len(payload) > limit:
            raise ValueError('oversize upload')
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            entries = archive.infolist()
            if len(entries) > 500 or sum(entry.file_size for entry in entries) > 10 * 1024 * 1024:
                raise ValueError('oversize')
            if any('vbaproject' in entry.filename.lower() or entry.flag_bits & 1 for entry in entries):
                raise ValueError('unsupported')
            if any(entry.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)
                   or (entry.file_size > 65536 and entry.file_size > max(entry.compress_size, 1) * 200)
                   for entry in entries):
                raise ValueError('unsupported compression')
            documents = [entry for entry in entries if entry.filename == 'word/document.xml']
            if len(documents) != 1 or documents[0].file_size > limit:
                raise ValueError('document size or duplicates')
            with archive.open(documents[0]) as stream:
                document = stream.read(limit + 1)
            # XML may be UTF-16/32, where ASCII declaration bytes contain NULs.
            declarations = document.replace(b'\x00', b'').upper()
            if len(document) > limit or b'<!DOCTYPE' in declarations or b'<!ENTITY' in declarations:
                raise ValueError('unsupported XML')
            root = ElementTree.fromstring(document)
            namespace = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
            text = '\n'.join(''.join(node.text or '' for node in paragraph.iter(namespace + 't'))
                for paragraph in root.iter(namespace + 'p')).strip()
            if not text or len(text) > 12000:
                raise ValueError('text length')
            return text
    except (ValueError, KeyError, zipfile.BadZipFile, ElementTree.ParseError, RuntimeError,
            zlib.error, NotImplementedError, EOFError, LookupError):
        raise forms.ValidationError('تعذر قراءة نص النموذج. استخدم DOCX عاديًا أو انسخ النص إلى المحرر.')


class TemplateForm(forms.Form):
    expected_revision = forms.IntegerField(min_value=0, widget=forms.HiddenInput, initial=0)
    client_id = forms.UUIDField(widget=forms.HiddenInput)
    name = forms.CharField(label='اسم النموذج', max_length=160)
    source = forms.ChoiceField(label='بيانات النموذج', choices=ReportTemplate.Source.choices)
    header = forms.CharField(label='الترويسة', max_length=1000, initial=DEFAULT_HEADER, widget=forms.Textarea(attrs={'rows': 4}))
    title = forms.CharField(label='عنوان التقرير', max_length=300)
    body = forms.CharField(label='نص النموذج', max_length=12000, required=False, widget=forms.Textarea(attrs={'rows': 12}))
    custom_fields = forms.CharField(label='حقول إضافية', required=False, max_length=2000, widget=forms.Textarea(attrs={'rows': 3}),
        help_text='اسم حقل واحد في كل سطر. تظهر هذه الحقول ليملأها الموظف عند إنشاء التقرير.')
    table_fields = forms.CharField(label='حقول جدول البيانات وترتيبها', required=False, max_length=3000, widget=forms.Textarea(attrs={'rows': 4}),
        help_text='اسم حقل واحد في كل سطر، من بيانات النظام أو الحقول الإضافية. اتركه فارغًا لإخفاء الجدول.')
    footer = forms.CharField(label='التذييل ومواقع الاعتماد', max_length=1000, required=False,
        initial='أعد التقرير: {{معد التقرير}}\nتوقيع المسؤول المختص: ........................\nالختم: ........................', widget=forms.Textarea(attrs={'rows': 3}))
    orientation = forms.ChoiceField(label='اتجاه الصفحة', choices=OPTIONS['orientation'])
    font_size = forms.ChoiceField(label='حجم النص', choices=OPTIONS['font_size'])
    margin = forms.ChoiceField(label='هوامش الصفحة', choices=OPTIONS['margin'])
    accent = forms.ChoiceField(label='لون التفاصيل', choices=OPTIONS['accent'])
    logo = forms.ChoiceField(label='موضع الشعار', choices=OPTIONS['logo'])
    alignment = forms.ChoiceField(label='محاذاة النص', choices=OPTIONS['alignment'])
    word_file = forms.FileField(label='استيراد نص من Word (اختياري)', required=False,
        widget=forms.ClearableFileInput(attrs={'accept': '.docx'}), help_text='يستورد النص فقط. اضبط الترويسة والجدول والشكل في المحرر ثم عاين النموذج.')

    def __init__(self, *args, source=None, **kwargs):
        super().__init__(*args, **kwargs)
        if source:
            self.fields['source'].initial = source
            self.fields['source'].disabled = True

    def clean(self):
        data = super().clean()
        if self.errors:
            return data
        if data.get('word_file'):
            try:
                data['body'] = import_docx(data['word_file'])
            except forms.ValidationError as error:
                self.add_error('word_file', error)
        if not data.get('body'):
            self.add_error('body', 'اكتب نص النموذج أو استورد النص من Word.')
        custom = [label.strip() for label in data['custom_fields'].splitlines() if label.strip()]
        known = (*COMMON, *FIELDS[data['source']])
        if len(custom) > 20 or len(set(custom)) != len(custom) or any(not LABEL.fullmatch(label) or label in known for label in custom):
            self.add_error('custom_fields', 'أضف حتى 20 اسم حقل مختلفًا، دون تكرار بيانات النظام أو استخدام أقواس المتغيرات.')
        table = [label.strip() for label in data['table_fields'].splitlines() if label.strip()]
        allowed = {*known, *custom}
        if len(table) > 30 or len(set(table)) != len(table) or any(label not in allowed for label in table):
            self.add_error('table_fields', 'اختر أسماء حقول معروفة وغير مكررة من القائمة أو الحقول الإضافية.')
        for field in ('header', 'title', 'body', 'footer'):
            text = data.get(field, '')
            unknown = [match.group(1).strip() for match in TOKEN.finditer(text) if match.group(1).strip() not in allowed]
            leftover = TOKEN.sub('', text)
            if unknown or '{{' in leftover or '}}' in leftover:
                self.add_error(field, 'استخدم حقلًا معروفًا بين {{ }}. الحقول غير المعروفة: ' + '، '.join(unknown))
        data['custom_fields'] = custom
        data['table_fields'] = table
        return data

    def spec(self):
        return {key: self.cleaned_data[key] for key in ('header', 'title', 'body', 'footer', 'custom_fields', 'table_fields', *OPTIONS)}


class GenerateReportForm(forms.Form):
    client_id = forms.UUIDField(widget=forms.HiddenInput)
    official_number = forms.CharField(label='رقم المستند', max_length=100, required=False)
    document_date = forms.DateField(label='تاريخ التقرير', initial=timezone.localdate, widget=forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'))
    recipient = forms.CharField(label='الجهة المخاطبة', max_length=200, required=False)

    def __init__(self, spec, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.custom_map = {}
        for index, label in enumerate(spec['custom_fields']):
            key = f'extra_{index}'
            self.custom_map[key] = label
            self.fields[key] = forms.CharField(label=label, max_length=2000, widget=forms.Textarea(attrs={'rows': 2}))

    def clean_document_date(self):
        date = self.cleaned_data['document_date']
        if date > timezone.localdate():
            raise forms.ValidationError('اختر تاريخًا غير مستقبلي.')
        return date
