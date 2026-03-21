

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

# Signal for automatic UserProfile creation (must be after model definitions)
from django.db.models.signals import post_save
from django.dispatch import receiver

@receiver(post_save, sender=User)
def create_user_profile(sender, instance, created, **kwargs):
	if created:
		business = Business.objects.first()
		if business:
			UserProfile.objects.create(user=instance, business=business)

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
