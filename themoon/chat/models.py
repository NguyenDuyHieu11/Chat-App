from django.db import models
from feed.models import AppUser


class Conversation(models.Model):
    """Represents a chat between two or more users."""
    participants = models.ManyToManyField(AppUser, related_name='conversations')
    created_datetime = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Conversation #{self.id}"


class Message(models.Model):
    """Actual messages in the conversation."""
    conversation = models.ForeignKey(
        Conversation, on_delete=models.CASCADE, related_name="messages"
    )
    author = models.ForeignKey(
        AppUser, on_delete=models.CASCADE, related_name="sent_messages"
    )
    content = models.TextField()
    created_datetime = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Message from {self.author.profile_name} in Conversation #{self.conversation.id}"
