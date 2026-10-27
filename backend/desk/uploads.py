import io
import shutil
import zipfile
from pathlib import Path
from PIL import Image, UnidentifiedImageError
from django.conf import settings
from django.db.models import Sum
from rest_framework.exceptions import ValidationError
from .errors import AttachmentCapacityExceeded, AttachmentStorageLow
from .models import Attachment, UploadQuotaLock

CONTENT_TYPES = {'.pdf': 'application/pdf', '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.txt': 'text/plain', '.csv': 'text/csv', '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document', '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'}


def capacity_label(size):
    if size >= 1024**3:
        return f'{size / 1024**3:g} GiB'
    if size >= 1024**2:
        return f'{size / 1024**2:g} MiB'
    return f'{size} 字节'


def storage_free_bytes():
    path = Path(settings.MEDIA_ROOT)
    while not path.exists() and path.parent != path:
        path = path.parent
    try:
        return shutil.disk_usage(path).free
    except OSError:
        return None


def storage_summary():
    files = Attachment.objects.all()
    largest_ticket = files.values('event__ticket_id').annotate(total=Sum('size')).order_by('-total').values_list('total', flat=True).first() or 0
    largest_owner = files.values('event__ticket__owner_id').annotate(total=Sum('size')).order_by('-total').values_list('total', flat=True).first() or 0
    return {'count': files.count(), 'used_bytes': files.aggregate(total=Sum('size'))['total'] or 0,
            'largest_ticket_bytes': largest_ticket, 'largest_owner_bytes': largest_owner,
            'free_bytes': storage_free_bytes(),
            'limits': {'ticket_bytes': settings.ATTACHMENT_TICKET_LIMIT_BYTES, 'owner_bytes': settings.ATTACHMENT_OWNER_LIMIT_BYTES,
                       'global_bytes': settings.ATTACHMENT_GLOBAL_LIMIT_BYTES, 'min_free_bytes': settings.ATTACHMENT_MIN_FREE_BYTES}}


def check_attachment_capacity(owner_id, ticket_id, uploads):
    """Must run inside the ticket transaction before writing any attachment."""
    if not uploads:
        return
    UploadQuotaLock.objects.select_for_update().get_or_create(pk=1)
    incoming = sum(file.size for file, _name, _kind in uploads)
    all_files = Attachment.objects.all()
    groups = [
        ('工单', all_files.filter(event__ticket_id=ticket_id) if ticket_id else None, settings.ATTACHMENT_TICKET_LIMIT_BYTES),
        ('该提交人的所有工单', all_files.filter(event__ticket__owner_id=owner_id), settings.ATTACHMENT_OWNER_LIMIT_BYTES),
        ('平台', all_files, settings.ATTACHMENT_GLOBAL_LIMIT_BYTES),
    ]
    for label, queryset, limit in groups:
        used = queryset.aggregate(total=Sum('size'))['total'] or 0 if queryset is not None and limit else 0
        if limit and used + incoming > limit:
            raise AttachmentCapacityExceeded(f'{label}附件容量超过 {capacity_label(limit)}，请联系管理员处理。')
    free = storage_free_bytes()
    if free is None:
        raise AttachmentStorageLow('附件存储状态暂时不可用，请联系管理员。') from None
    if free - incoming < settings.ATTACHMENT_MIN_FREE_BYTES:
        raise AttachmentStorageLow('附件存储空间不足，请联系管理员处理；文字仍可正常提交。')

def validate_uploads(files):
    if len(files) > 5:
        raise ValidationError('每次最多上传 5 个附件。')
    result = []
    for f in files:
        name = Path(f.name.replace('\\', '/')).name
        suffix = Path(name).suffix.lower()
        if suffix not in CONTENT_TYPES or len(name) > 200 or any(ord(c) < 32 for c in name):
            raise ValidationError('附件格式或文件名不支持。支持 PNG、JPG、PDF、DOCX、XLSX、CSV、TXT。')
        if not 0 < f.size <= settings.MAX_UPLOAD_BYTES:
            raise ValidationError(f'{name} 为空或超过 20 MB。')
        head = f.read(1024)
        f.seek(0)
        valid = True
        try:
            if suffix in ['.png', '.jpg', '.jpeg']:
                with Image.open(f) as img:
                    valid = img.format == ('PNG' if suffix == '.png' else 'JPEG') and img.width * img.height <= 40_000_000
                    img.verify()
            elif suffix == '.pdf':
                valid = head.startswith(b'%PDF-')
            elif suffix in ['.docx', '.xlsx']:
                with zipfile.ZipFile(f) as archive:
                    infos = archive.infolist()
                    names = set(archive.namelist())
                    required = 'word/document.xml' if suffix == '.docx' else 'xl/workbook.xml'
                    valid = len(infos) <= 2000 and sum(x.file_size for x in infos) <= 100 * 1024 * 1024 and required in names and '[Content_Types].xml' in names and not any('vbaproject' in n.lower() for n in names)
            else:
                raw = f.read()
                raw.decode('utf-8-sig')
                valid = b'\x00' not in raw
        except (ValueError, OSError, UnicodeError, zipfile.BadZipFile, UnidentifiedImageError, Image.DecompressionBombError):
            valid = False
        finally:
            f.seek(0)
        if not valid:
            raise ValidationError(f'{name} 的内容与扩展名不符，或文件已损坏。文本附件请使用 UTF-8 编码。')
        result.append((f, name, CONTENT_TYPES[suffix]))
    return result
