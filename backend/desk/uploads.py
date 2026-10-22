import io
import zipfile
from pathlib import Path
from PIL import Image, UnidentifiedImageError
from django.conf import settings
from rest_framework.exceptions import ValidationError

CONTENT_TYPES = {'.pdf': 'application/pdf', '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.txt': 'text/plain', '.csv': 'text/csv', '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document', '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'}

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
