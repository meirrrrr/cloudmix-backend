import json
from pathlib import Path

from django.core.management.base import BaseCommand

from chat.models import Message


class Command(BaseCommand):
    help = "Export PostgreSQL chat messages to a JSON file."

    def add_arguments(self, parser):
        parser.add_argument(
            "--output",
            default="messages_export.json",
            help="Output JSON file path (default: messages_export.json).",
        )

    def handle(self, *args, **options):
        output = Path(options["output"]).expanduser()
        rows = []
        queryset = Message.objects.select_related("sender").order_by("id")
        for msg in queryset.iterator():
            rows.append(
                {
                    "id": int(msg.id),
                    "conversation_id": int(msg.conversation_id),
                    "sender_id": int(msg.sender_id),
                    "body": msg.body,
                    "created_at": msg.created_at.isoformat(),
                }
            )
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(rows, ensure_ascii=True, indent=2), encoding="utf-8")
        self.stdout.write(
            self.style.SUCCESS(f"Exported {len(rows)} messages to {output}")
        )

