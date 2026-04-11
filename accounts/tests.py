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
        self.assertEqual(response.cookies[settings.DEVICE_LANGUAGE_COOKIE_NAME].value, '1')

    def test_authenticated_requests_do_not_force_device_cookie(self):
        self.profile.preferred_language = 'kam'
        self.profile.save(update_fields=['preferred_language'])

        response = self.client.get(reverse('role_redirect'))

        self.assertNotIn(settings.LANGUAGE_COOKIE_NAME, response.cookies)
        self.assertNotIn(settings.DEVICE_LANGUAGE_COOKIE_NAME, response.cookies)

    def test_public_pages_use_saved_device_language_cookie(self):
        self.client.logout()
        self.client.cookies[settings.LANGUAGE_COOKIE_NAME] = 'kam'
        self.client.cookies[settings.DEVICE_LANGUAGE_COOKIE_NAME] = '1'

        login_response = self.client.get(reverse('login'))
        home_response = self.client.get(reverse('home'))

        self.assertEqual(login_response.wsgi_request.LANGUAGE_CODE, 'kam')
        self.assertEqual(home_response.wsgi_request.LANGUAGE_CODE, 'kam')

    def test_logout_clears_language_cookie_for_unsaved_devices(self):
        self.profile.preferred_language = 'kam'
        self.profile.save(update_fields=['preferred_language'])
        self.client.cookies[settings.LANGUAGE_COOKIE_NAME] = 'kam'

        response = self.client.post(reverse('logout'))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.cookies[settings.LANGUAGE_COOKIE_NAME].value, '')
        self.assertEqual(response.cookies[settings.LANGUAGE_COOKIE_NAME]['max-age'], 0)

    def test_logout_preserves_language_cookie_for_saved_devices(self):
        self.profile.preferred_language = 'kam'
        self.profile.save(update_fields=['preferred_language'])
        self.client.cookies[settings.LANGUAGE_COOKIE_NAME] = 'kam'
        self.client.cookies[settings.DEVICE_LANGUAGE_COOKIE_NAME] = '1'

        response = self.client.post(reverse('logout'))

        self.assertEqual(response.status_code, 302)
        self.assertNotIn(settings.LANGUAGE_COOKIE_NAME, response.cookies)
        home_response = self.client.get(reverse('home'))
        self.assertEqual(home_response.wsgi_request.LANGUAGE_CODE, 'kam')
