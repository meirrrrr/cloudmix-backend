from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("chat", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="directconversation",
            name="participant_a_last_read_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="directconversation",
            name="participant_b_last_read_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
