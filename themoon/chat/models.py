from django.db import models
from feed.models import AppUser

# The Conversation model represents a chat between two or more users.
class Conversation(models.Model):
    participants = models.ManyToManyField(AppUser, related_name='conversations')
    created_datetime = models.DateTimeField(auto_now_add=True)

# The actuall messages in the conversation.
class Message(models.Model):
    Conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name="Message")
    Author = models.ForeignKey(AppUser, on_delete=models.CASCADE, related_name="TheOneSendingThisMessage")
    content = models.TextField()
    created_datetime = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Message from {self.Author.profile_name} to {self.Conversation.participants.all()}"