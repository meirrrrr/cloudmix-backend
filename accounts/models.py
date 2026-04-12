from django.conf import settings
from django.db import models


class Profile(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="profile",
    )
    display_name = models.CharField(max_length=150, db_index=True)
    last_seen_at = models.DateTimeField(null=True, blank=True, db_index=True)

    def __str__(self) -> str:
        return self.display_name
