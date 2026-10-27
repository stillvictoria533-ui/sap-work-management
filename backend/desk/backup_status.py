"""Read only sanitized host backup health; no backup paths, data or secrets."""
import json
import math
from datetime import datetime, timezone as dt_timezone
from pathlib import Path

from django.conf import settings
from django.utils import timezone


def backup_summary(now=None):
    now = now or timezone.now()
    try:
        path = Path(settings.OPS_BACKUP_HEALTH_PATH)
        if path.is_symlink() or not path.is_file():
            raise ValueError('status unavailable')
        with path.open('rb') as stream:
            raw = stream.read(4097)
        if len(raw) > 4096:
            raise ValueError('status too large')
        data = json.loads(raw)
        if not isinstance(data, dict) or data.get('schema') != 'sapdesk-backup-health/v1':
            raise ValueError('invalid status')
        def as_time(value):
            if type(value) not in (int, float) or value <= 0 or value > now.timestamp()+300 or not math.isfinite(value):
                return None
            try:
                return datetime.fromtimestamp(value, tz=dt_timezone.utc)
            except (OverflowError, OSError, ValueError):
                return None
        generated = as_time(data.get('generated_at'))
        if not generated or (now-generated).total_seconds() > 180:
            raise ValueError('status stale')
        last_success = as_time(data.get('last_success_at'))
        last_drill = as_time(data.get('last_drill_at'))
        if type(data.get('running')) is not bool or (data.get('last_backup_ok') is not None and type(data.get('last_backup_ok')) is not bool):
            raise ValueError('invalid status')
        running = data['running']
        failed = data.get('last_backup_ok') is False
        stale = bool(last_success and (now-last_success).total_seconds() > 30*3600)
        status = 'running' if running else 'failed' if failed else 'stale' if stale else 'healthy' if last_success else 'unknown'
        drill_ok = data.get('last_drill_ok') if type(data.get('last_drill_ok')) is bool else None
        return {'status': status, 'last_success_at': last_success, 'last_drill_at': last_drill,
                'last_drill_ok': drill_ok, 'reported_at': generated}
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return {'status': 'unknown', 'last_success_at': None, 'last_drill_at': None,
                'last_drill_ok': None, 'reported_at': None}
