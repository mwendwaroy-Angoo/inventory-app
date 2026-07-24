from django.conf import settings
from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from .models import Business, UserProfile


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


class EditStaffUsernameTest(TestCase):
    """2026-07-24 live report: renaming a staff member's display name (first/last
    name) via edit_staff left their login username permanently unchanged — e.g.
    "Dush Master" renamed to "Jack Musau" everywhere in the app, but he still had
    to type "Dush" to log in, with the new password. edit_staff() only ever wrote
    first_name/last_name/email/phone/role, never User.username."""

    def setUp(self):
        self.owner = User.objects.create_user(username='editstaff_owner', password='x')
        self.biz = Business.objects.create(name='Edit Staff Biz')
        UserProfile.objects.create(user=self.owner, business=self.biz, role='owner')
        self.staff = User.objects.create_user(username='dush', password='x', first_name='Dush', last_name='Master')
        self.staff_profile = UserProfile.objects.create(
            user=self.staff, business=self.biz, role='staff', phone='0712345678',
        )
        self.client.force_login(self.owner)

    def test_changing_username_actually_updates_login_handle(self):
        resp = self.client.post(f'/business/staff/edit/{self.staff.id}/', {
            'username': 'jackmusau', 'first_name': 'Jack', 'last_name': 'Musau',
            'email': '', 'phone': '0712345678', 'role': 'staff',
        })
        self.assertEqual(resp.status_code, 302)
        self.staff.refresh_from_db()
        self.assertEqual(self.staff.username, 'jackmusau')
        self.assertEqual(self.staff.first_name, 'Jack')
        # The old username must no longer work; the new one must be the real login handle
        self.assertFalse(User.objects.filter(username='dush').exists())

    def test_duplicate_username_is_rejected_with_swahili_error(self):
        other = User.objects.create_user(username='taken_name', password='x')
        UserProfile.objects.create(user=other, business=self.biz, role='staff')
        resp = self.client.post(f'/business/staff/edit/{self.staff.id}/', {
            'username': 'taken_name', 'first_name': 'Dush', 'last_name': 'Master',
            'email': '', 'phone': '0712345678', 'role': 'staff',
        }, follow=True)
        self.staff.refresh_from_db()
        self.assertEqual(self.staff.username, 'dush', 'Username must not change on a rejected duplicate')
        msgs = [str(m) for m in resp.context['messages']]
        self.assertTrue(any('tayari linatumika' in m for m in msgs))

    def test_staff_is_notified_when_username_changes(self):
        from core.models import Notification
        self.client.post(f'/business/staff/edit/{self.staff.id}/', {
            'username': 'jackmusau', 'first_name': 'Jack', 'last_name': 'Musau',
            'email': '', 'phone': '0712345678', 'role': 'staff',
        })
        notif = Notification.objects.filter(user=self.staff, title__icontains='Kuingia').first()
        self.assertIsNotNone(notif)
        self.assertIn('dush', notif.message)
        self.assertIn('jackmusau', notif.message)

    def test_unchanged_username_does_not_require_uniqueness_recheck_against_self(self):
        resp = self.client.post(f'/business/staff/edit/{self.staff.id}/', {
            'username': 'dush', 'first_name': 'Dush', 'last_name': 'Renamed',
            'email': '', 'phone': '0712345678', 'role': 'staff',
        })
        self.assertEqual(resp.status_code, 302)
        self.staff.refresh_from_db()
        self.assertEqual(self.staff.username, 'dush')
        self.assertEqual(self.staff.last_name, 'Renamed')
