from django.urls import path

from . import views

urlpatterns = [
    path("conversations/", views.ConversationListView.as_view(), name="chat-conversations"),
    path(
        "conversations/start/",
        views.ConversationStartView.as_view(),
        name="chat-conversations-start",
    ),
    path(
        "conversations/<int:conversation_id>/",
        views.ConversationDetailView.as_view(),
        name="chat-conversation-detail",
    ),
    path(
        "conversations/<int:conversation_id>/messages/",
        views.ConversationMessagesView.as_view(),
        name="chat-conversation-messages",
    ),
    path(
        "conversations/<int:conversation_id>/read/",
        views.ConversationReadView.as_view(),
        name="chat-conversation-read",
    ),
]
