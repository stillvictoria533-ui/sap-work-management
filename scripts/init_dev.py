"""Create local config if absent. Does not create users or touch existing data."""
from pathlib import Path
import secrets

root = Path(__file__).resolve().parent.parent
path = root / '.env'
if path.exists():
    print('Existing .env preserved.')
else:
    path.write_text('DJANGO_DEBUG=true\nDJANGO_SECRET_KEY=' + secrets.token_urlsafe(64) + '\nDJANGO_ALLOWED_HOSTS=127.0.0.1,localhost\nCSRF_TRUSTED_ORIGINS=http://127.0.0.1:5173,http://localhost:5173\nDINGTALK_ENABLED=false\n')
    path.chmod(0o600)
    print('Local .env created; no credentials printed.')
