from django.db import models


class ChatMessage(models.Model):
    user = models.ForeignKey(
        "auth.User", on_delete=models.CASCADE, related_name="chat_messages"
    )
    role = models.CharField(max_length=20, choices=[("user", "User"), ("assistant", "Assistant")])
    content = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]
