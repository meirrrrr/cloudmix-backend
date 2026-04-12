from django.conf import settings
from django.db import models
from django.db.models import F, Q
from django.utils import timezone


class DirectConversation(models.Model):
    """
    One row per unordered pair of distinct users. participant_a_id < participant_b_id.
    """

    participant_a = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="dm_as_participant_a",
    )
    participant_b = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="dm_as_participant_b",
    )
    participant_a_last_read_at = models.DateTimeField(null=True, blank=True)
    participant_b_last_read_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["participant_a", "participant_b"],
                name="chat_directconversation_unique_pair",
            ),
            models.CheckConstraint(
                condition=Q(participant_a_id__lt=F("participant_b_id")),
                name="chat_directconversation_ordered_participants",
            ),
        ]
        indexes = [
            models.Index(fields=["participant_a", "-updated_at"]),
            models.Index(fields=["participant_b", "-updated_at"]),
        ]

    def other_user(self, user):
        if user.pk == self.participant_a_id:
            return self.participant_b
        if user.pk == self.participant_b_id:
            return self.participant_a
        raise ValueError("user is not a participant")

    def includes_user(self, user) -> bool:
        return user.pk in (self.participant_a_id, self.participant_b_id)


class Message(models.Model):
    conversation = models.ForeignKey(
        DirectConversation,
        on_delete=models.CASCADE,
        related_name="messages",
    )
    sender = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="sent_chat_messages",
    )
    body = models.TextField(max_length=5000)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["created_at"]
        indexes = [
            models.Index(fields=["conversation", "-created_at"]),
        ]

    def save(self, *args, **kwargs):
        is_new = self.pk is None
        super().save(*args, **kwargs)
        if is_new:
            DirectConversation.objects.filter(pk=self.conversation_id).update(
                updated_at=timezone.now()
            )
