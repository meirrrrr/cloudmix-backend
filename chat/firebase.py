import json
from pathlib import Path

import firebase_admin
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from firebase_admin import credentials, firestore

_APP_NAME = "chat-backend"
_COUNTER_COLLECTION = "_meta"
_COUNTER_DOC = "counters"
_COUNTER_FIELD = "message_id"


def _firebase_credentials():
    """
    FIREBASE_CREDENTIALS may be:
    - A filesystem path (relative to BASE_DIR or absolute), for local dev.
    - Inline service-account JSON (string starting with '{'), e.g. on Render.
    """
    raw_value = (getattr(settings, "FIREBASE_CREDENTIALS", "") or "").strip()
    if not raw_value:
        raise ImproperlyConfigured(
            "FIREBASE_CREDENTIALS is required for Firestore message storage."
        )
    if raw_value.startswith("{"):
        try:
            data = json.loads(raw_value)
        except json.JSONDecodeError as e:
            raise ImproperlyConfigured(
                "FIREBASE_CREDENTIALS must be valid JSON when used as inline credentials."
            ) from e
        if not isinstance(data, dict):
            raise ImproperlyConfigured(
                "FIREBASE_CREDENTIALS JSON must be an object (service account key)."
            )
        return credentials.Certificate(data)
    path = Path(raw_value)
    if not path.is_absolute():
        path = Path(settings.BASE_DIR) / path
    if not path.exists():
        raise ImproperlyConfigured(f"Firebase credentials file not found: {path}")
    return credentials.Certificate(str(path))


def get_firebase_app():
    existing = firebase_admin._apps.get(_APP_NAME)
    if existing:
        return existing
    cred = _firebase_credentials()
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

