from pathlib import Path

import firebase_admin
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from firebase_admin import credentials, firestore

_APP_NAME = "chat-backend"
_COUNTER_COLLECTION = "_meta"
_COUNTER_DOC = "counters"
_COUNTER_FIELD = "message_id"


def _credentials_path() -> Path:
    raw_value = (getattr(settings, "FIREBASE_CREDENTIALS", "") or "").strip()
    if not raw_value:
        raise ImproperlyConfigured(
            "FIREBASE_CREDENTIALS is required for Firestore message storage."
        )
    path = Path(raw_value)
    if not path.is_absolute():
        path = Path(settings.BASE_DIR) / path
    if not path.exists():
        raise ImproperlyConfigured(f"Firebase credentials file not found: {path}")
    return path


def get_firebase_app():
    existing = firebase_admin._apps.get(_APP_NAME)
    if existing:
        return existing
    cred = credentials.Certificate(str(_credentials_path()))
    options = {}
    project_id = (getattr(settings, "FIREBASE_PROJECT_ID", "") or "").strip()
    if project_id:
        options["projectId"] = project_id
    return firebase_admin.initialize_app(cred, options=options, name=_APP_NAME)


def get_firestore_client():
    app = get_firebase_app()
    return firestore.client(app=app)


def next_firestore_message_id(client) -> int:
    ref = client.collection(_COUNTER_COLLECTION).document(_COUNTER_DOC)
    tx = client.transaction()

    @firestore.transactional
    def _increment(transaction):
        snap = ref.get(transaction=transaction)
        current = 0
        if snap.exists:
            current = int((snap.to_dict() or {}).get(_COUNTER_FIELD, 0) or 0)
        new_value = current + 1
        transaction.set(ref, {_COUNTER_FIELD: new_value}, merge=True)
        return new_value

    return int(_increment(tx))

