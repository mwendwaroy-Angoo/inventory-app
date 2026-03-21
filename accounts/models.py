
from django.db import models
from django.contrib.auth.models import User

class Business(models.Model):
	name = models.CharField(max_length=255, unique=True)
	created_at = models.DateTimeField(auto_now_add=True)

	def __str__(self):
		return self.name

class UserProfile(models.Model):
	user = models.OneToOneField(User, on_delete=models.CASCADE)
	business = models.ForeignKey(Business, on_delete=models.CASCADE, related_name='users')

	def __str__(self):
		return f"{self.user.username} ({self.business.name})"
