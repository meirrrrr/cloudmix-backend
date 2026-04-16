# Chat Project Run Guide

Requirements:

- Python 3.12+
- PostgreSQL running locally

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Create `backend/.env` (or update it) with at least:

```env
POSTGRES_DB=zimran_chat
POSTGRES_USER=postgres
POSTGRES_PASSWORD=postgres
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
USE_FIRESTORE_MESSAGES=true - true if use firestore for saving messages, if false use postgresql to store messages.
OPENAI_API_KEY=openaitoken
```

```
To use firestore, get firestore_credentials.json, and put on .env level. In .env place USE_FIRESTORE_MESSAGES=true to use firstore as message store.
```

Optional backend env:

- `OPENAI_API_KEY` if you use AI chat features
- `USE_FIRESTORE_MESSAGES=true` + Firebase vars only if you use Firestore storage

Make sure the database exists, then run migrations:

```bash
python manage.py migrate
```

Start backend:

```bash
python manage.py runserver 8000
```
