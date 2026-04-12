from django.contrib import admin

from .models import DirectConversation, Message


class MessageInline(admin.TabularInline):
    model = Message
    extra = 0
    readonly_fields = ("sender", "body", "created_at")
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(DirectConversation)
class DirectConversationAdmin(admin.ModelAdmin):
    list_display = ("id", "participant_a", "participant_b", "updated_at")
    list_filter = ("updated_at",)
    search_fields = (
        "participant_a__username",
        "participant_b__username",
    )
    inlines = [MessageInline]


@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    list_display = ("id", "conversation", "sender", "created_at")
    list_filter = ("created_at",)
    search_fields = ("body", "sender__username")
    readonly_fields = ("created_at",)
