from asgiref.sync import async_to_sync
from channels.testing import WebsocketCommunicator
from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase, TransactionTestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from accounts.models import Profile
from chat.models import DirectConversation
from config.asgi import application

User = get_user_model()


class ChatApiTests(TestCase):
    def setUp(self):
        self.u1 = User.objects.create_user(username="alice", password="testpass12")
        self.u2 = User.objects.create_user(username="bob", password="testpass12")
        Profile.objects.create(user=self.u1, display_name="Alice A")
        Profile.objects.create(user=self.u2, display_name="Bob B")
        self.client = APIClient()
        self.client.force_authenticate(user=self.u1)

    def test_start_conversation_creates_pair(self):
        url = reverse("chat-conversations-start")
        r = self.client.post(url, {"user_id": self.u2.pk}, format="json")
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertEqual(DirectConversation.objects.count(), 1)
        r2 = self.client.post(url, {"user_id": self.u2.pk}, format="json")
        self.assertEqual(r2.status_code, status.HTTP_200_OK)
        self.assertEqual(DirectConversation.objects.count(), 1)

    def test_send_and_list_messages(self):
        self.client.post(
            reverse("chat-conversations-start"),
            {"user_id": self.u2.pk},
            format="json",
        )
        conv = DirectConversation.objects.get()
        msg_url = reverse("chat-conversation-messages", args=[conv.pk])
        r = self.client.post(msg_url, {"body": "hello"}, format="json")
        self.assertEqual(r.status_code, status.HTTP_201_CREATED)
        r2 = self.client.get(msg_url)
        self.assertEqual(r2.status_code, status.HTTP_200_OK)
        self.assertEqual(len(r2.data["results"]), 1)
        self.assertEqual(r2.data["results"][0]["body"], "hello")

    def test_conversation_list_includes_last_message(self):
        self.client.post(
            reverse("chat-conversations-start"),
            {"user_id": self.u2.pk},
            format="json",
        )
        conv = DirectConversation.objects.get()
        self.client.post(
            reverse("chat-conversation-messages", args=[conv.pk]),
            {"body": "latest hello"},
            format="json",
        )

        r = self.client.get(reverse("chat-conversations"))
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertEqual(len(r.data), 1)
        self.assertEqual(r.data[0]["last_message"]["body"], "latest hello")
        self.assertEqual(r.data[0]["unread_count"], 0)

    def test_unread_count_for_receiver_and_mark_read(self):
        self.client.post(
            reverse("chat-conversations-start"),
            {"user_id": self.u2.pk},
            format="json",
        )
        conv = DirectConversation.objects.get()
        msg_url = reverse("chat-conversation-messages", args=[conv.pk])
        read_url = reverse("chat-conversation-read", args=[conv.pk])

        self.client.post(msg_url, {"body": "first"}, format="json")
        self.client.post(msg_url, {"body": "second"}, format="json")

        # Sender should not see own messages as unread.
        sender_list = self.client.get(reverse("chat-conversations"))
        self.assertEqual(sender_list.status_code, status.HTTP_200_OK)
        self.assertEqual(sender_list.data[0]["unread_count"], 0)

        # Receiver should see unread messages from peer.
        self.client.force_authenticate(user=self.u2)
        receiver_list = self.client.get(reverse("chat-conversations"))
        self.assertEqual(receiver_list.status_code, status.HTTP_200_OK)
        self.assertEqual(receiver_list.data[0]["unread_count"], 2)

        # Mark as read should reset unread count.
        mark_read_response = self.client.post(read_url)
        self.assertEqual(mark_read_response.status_code, status.HTTP_204_NO_CONTENT)
        receiver_list_after_read = self.client.get(reverse("chat-conversations"))
        self.assertEqual(receiver_list_after_read.status_code, status.HTTP_200_OK)
        self.assertEqual(receiver_list_after_read.data[0]["unread_count"], 0)

    def test_other_user_cannot_post_to_foreign_conversation(self):
        lo, hi = (self.u1, self.u2) if self.u1.pk < self.u2.pk else (self.u2, self.u1)
        conv = DirectConversation.objects.create(participant_a=lo, participant_b=hi)
        eve = User.objects.create_user(username="eve", password="testpass12")
        Profile.objects.create(user=eve, display_name="Eve")
        self.client.force_authenticate(user=eve)
        url = reverse("chat-conversation-messages", args=[conv.pk])
        r = self.client.post(url, {"body": "hack"}, format="json")
        self.assertEqual(r.status_code, status.HTTP_404_NOT_FOUND)


class ChatWebsocketTests(TransactionTestCase):
    def setUp(self):
        self.u1 = User.objects.create_user(username="ws_alice", password="testpass12")
        self.u2 = User.objects.create_user(username="ws_bob", password="testpass12")
        Profile.objects.create(user=self.u1, display_name="Alice W")
        Profile.objects.create(user=self.u2, display_name="Bob W")
        lo, hi = (self.u1, self.u2) if self.u1.pk < self.u2.pk else (self.u2, self.u1)
        self.conv = DirectConversation.objects.create(participant_a=lo, participant_b=hi)

    def _ws_headers(self, user):
        token = str(RefreshToken.for_user(user).access_token)
        name = settings.AUTH_ACCESS_COOKIE_NAME
        return [
            (b"origin", b"http://localhost:5173"),
            (b"cookie", f"{name}={token}".encode()),
        ]

    def test_websocket_rejects_unauthenticated(self):
        async def run():
            comm = WebsocketCommunicator(
                application, f"/ws/chat/{self.conv.pk}/"
            )
            connected, _ = await comm.connect()
            self.assertFalse(connected)

        async_to_sync(run)()

    def test_websocket_send_broadcasts_to_room(self):
        async def run():
            async def receive_until_message(comm):
                for _ in range(5):
                    event = await comm.receive_json_from()
                    if event.get("type") == "message":
                        return event
                self.fail("Did not receive message event in expected attempts.")

            comm_a = WebsocketCommunicator(
                application,
                f"/ws/chat/{self.conv.pk}/",
                headers=self._ws_headers(self.u1),
            )
            comm_b = WebsocketCommunicator(
                application,
                f"/ws/chat/{self.conv.pk}/",
                headers=self._ws_headers(self.u2),
            )
            self.assertTrue((await comm_a.connect())[0])
            self.assertTrue((await comm_b.connect())[0])
            await comm_a.send_json_to({"type": "send", "body": "hello ws"})
            msg_a = await receive_until_message(comm_a)
            msg_b = await receive_until_message(comm_b)
            self.assertEqual(msg_a["type"], "message")
            self.assertEqual(msg_b["type"], "message")
            self.assertEqual(msg_a["message"]["body"], "hello ws")
            self.assertEqual(msg_b["message"]["body"], "hello ws")
            await comm_a.disconnect()
            await comm_b.disconnect()

        async_to_sync(run)()

    def test_websocket_typing_broadcasts_to_room(self):
        async def run():
            async def receive_until_typing(comm):
                for _ in range(5):
                    event = await comm.receive_json_from()
                    if event.get("type") == "typing":
                        return event
                self.fail("Did not receive typing event in expected attempts.")

            comm_a = WebsocketCommunicator(
                application,
                f"/ws/chat/{self.conv.pk}/",
                headers=self._ws_headers(self.u1),
            )
            comm_b = WebsocketCommunicator(
                application,
                f"/ws/chat/{self.conv.pk}/",
                headers=self._ws_headers(self.u2),
            )
            self.assertTrue((await comm_a.connect())[0])
            self.assertTrue((await comm_b.connect())[0])

            await comm_a.send_json_to({"type": "typing", "is_typing": True})
            typing_a = await receive_until_typing(comm_a)
            typing_b = await receive_until_typing(comm_b)
            self.assertEqual(typing_a["type"], "typing")
            self.assertEqual(typing_b["type"], "typing")
            self.assertEqual(typing_a["user_id"], self.u1.id)
            self.assertEqual(typing_b["user_id"], self.u1.id)
            self.assertTrue(typing_a["is_typing"])
            self.assertTrue(typing_b["is_typing"])

            await comm_a.send_json_to({"type": "typing", "is_typing": False})
            stop_typing_a = await receive_until_typing(comm_a)
            stop_typing_b = await receive_until_typing(comm_b)
            self.assertFalse(stop_typing_a["is_typing"])
            self.assertFalse(stop_typing_b["is_typing"])
            await comm_a.disconnect()
            await comm_b.disconnect()

        async_to_sync(run)()
