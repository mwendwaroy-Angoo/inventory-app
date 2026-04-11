from django.conf import settings
from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from .models import UserProfile


class LanguagePreferenceTests(TestCase):
	@classmethod
	def setUpTestData(cls):
		cls.user = User.objects.create_user(
			username='language-owner',
			password='pass123456',
		)
		cls.profile = UserProfile.objects.create(
			user=cls.user,
			role='owner',
			preferred_language='en',
		)

	def setUp(self):
		self.client.force_login(self.user)

	def test_change_language_updates_profile_and_cookie(self):
		response = self.client.post(
			reverse('change_language'),
			{'language': 'kam', 'next': reverse('home')},
		)

		self.profile.refresh_from_db()

		self.assertEqual(self.profile.preferred_language, 'kam')
		self.assertEqual(response.cookies[settings.LANGUAGE_COOKIE_NAME].value, 'kam')

	def test_middleware_syncs_cookie_for_all_configured_languages(self):
		for language_code, _ in settings.LANGUAGES:
			with self.subTest(language_code=language_code):
				self.profile.preferred_language = language_code
				self.profile.save(update_fields=['preferred_language'])

				stale_language = 'sw' if language_code != 'sw' else 'en'
				self.client.cookies[settings.LANGUAGE_COOKIE_NAME] = stale_language

				response = self.client.get(reverse('role_redirect'))

				self.assertEqual(response.cookies[settings.LANGUAGE_COOKIE_NAME].value, language_code)

	def test_login_page_uses_language_cookie_for_anonymous_requests(self):
		self.client.logout()
		self.client.cookies[settings.LANGUAGE_COOKIE_NAME] = 'kam'

		response = self.client.get(reverse('login'))

		self.assertContains(response, 'Ĩngĩa akaunti yaku')
		self.assertContains(response, 'Andĩkĩthya Biashara')
		self.assertNotContains(response, 'Ingia kwenye akaunti yako')
