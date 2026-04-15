import json
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils.dateparse import parse_datetime

from chat.firebase import get_firestore_client


class Command(BaseCommand):
    help = "Migrate exported messages JSON into Firestore using batched writes."

    def add_arguments(self, parser):
        parser.add_argument(
            "--input",
            default="messages_export.json",
            help="Input JSON file path (default: messages_export.json).",
        )
        parser.add_argument(
            "--batch-size",
            default=500,
            type=int,
            help="Batch size for Firestore writes (max 500).",
        )

    def handle(self, *args, **options):
        use_firestore = bool(
            getattr(
                settings,
                "USE_FIRESTORE_MESSAGES",
                getattr(settings, "FIRESTORE_MESSAGES_ENABLED", False),
            )
        )
        if not use_firestore:
            raise CommandError("Set USE_FIRESTORE_MESSAGES=true before migrating.")

        input_path = Path(options["input"]).expanduser()
        if not input_path.exists():
            raise CommandError(f"Input file not found: {input_path}")
        try:
            payload = json.loads(input_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise CommandError(f"Invalid JSON in {input_path}: {exc}") from exc
        if not isinstance(payload, list):
            raise CommandError("Input JSON must be an array of message records.")

        batch_size = int(options["batch_size"])
        if batch_size < 1 or batch_size > 500:
            raise CommandError("--batch-size must be between 1 and 500.")

        collection_name = getattr(settings, "FIRESTORE_MESSAGES_COLLECTION", "messages")
        client = get_firestore_client()
        total = 0
        max_message_id = 0

        for start in range(0, len(payload), batch_size):
            chunk = payload[start : start + batch_size]
            batch = client.batch()
            for item in chunk:
                msg_id = int(item["id"])
                created_at = parse_datetime(item["created_at"])
                doc = {
                    "id": msg_id,
                    "conversation_id": int(item["conversation_id"]),
                    "sender_id": int(item["sender_id"]),
                    "body": str(item["body"]),
                    "created_at": created_at if created_at is not None else item["created_at"],
                }
                ref = client.collection(collection_name).document(str(msg_id))
                batch.set(ref, doc, merge=True)
                max_message_id = max(max_message_id, msg_id)
                total += 1
            batch.commit()

        meta_ref = client.collection("_meta").document("counters")
        meta_ref.set({"message_id": max_message_id}, merge=True)
        self.stdout.write(
            self.style.SUCCESS(
                f"Migrated {total} messages to Firestore collection '{collection_name}'."
            )
        )

