"""Settings for the D3 Django seam validation (results/django_seam_validation/PLAN.md).

Imports paperless-ngx's own settings unchanged, from its documented environment variables, then sets only:
the seam's ENGINE (``DS_SEAM_MODE=seam``; ``stock`` keeps Django's backend), and eager Celery with an in-memory broker.
The database password is read from a mounted file and never printed.
"""

import os
import secrets

_env = {
    "PAPERLESS_DBHOST": "127.0.0.1",
    "PAPERLESS_DBPORT": "5432",
    "PAPERLESS_DBNAME": "ds_mvp",
    "PAPERLESS_DBUSER": "ds_mvp",
    "PAPERLESS_CACHE_BACKEND": "django.core.cache.backends.locmem.LocMemCache",
    "PAPERLESS_CHANNELS_BACKEND": "channels.layers.InMemoryChannelLayer",
}
for _k, _v in _env.items():
    os.environ[_k] = _v
os.environ.pop("PAPERLESS_DB_POOLSIZE", None)
os.environ["PAPERLESS_SECRET_KEY"] = secrets.token_urlsafe(64)  # required; per process, nothing signed persists
with open("/run/ds_secret") as _f:
    os.environ["PAPERLESS_DBPASS"] = _f.read().strip()

from paperless.settings import *  # noqa: E402,F403
from paperless.settings import DATABASES  # noqa: E402

MODE = os.environ["DS_SEAM_MODE"]
if MODE == "seam":
    DATABASES["default"]["ENGINE"] = "deployspecter.experimental.django_backend"
elif MODE != "stock":
    raise SystemExit("DS_SEAM_MODE must be seam or stock")

CELERY_TASK_ALWAYS_EAGER = True
CELERY_BROKER_URL = "memory://"
