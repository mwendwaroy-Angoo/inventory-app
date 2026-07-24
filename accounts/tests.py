from decimal import Decimal

from django.conf import settings
from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

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


class ToggleHakiTest(TestCase):
    """2026-07-25 live report: Roy could no longer see Haki anywhere in the app
    (staff or owner side). Business.haki_enabled defaults to True and no
    application code ever wrote to it — there was no owner-facing toggle to see
    or correct its state if it were ever False. Mirrors core.tests.
    ToggleKitchenIdempotencyTest's pattern for the equivalent has_kitchen toggle."""

    def setUp(self):
        self.biz = Business.objects.create(name='Toggle Haki Biz')
        self.owner = User.objects.create_user(username='togglehaki_owner', password='x')
        UserProfile.objects.create(user=self.owner, business=self.biz, role='owner')
        self.staff = User.objects.create_user(username='togglehaki_staff', password='x')
        UserProfile.objects.create(user=self.staff, business=self.biz, role='staff')
        self.client.force_login(self.owner)

    def test_defaults_to_enabled(self):
        self.assertTrue(self.biz.haki_enabled)

    def test_owner_can_disable_and_reenable(self):
        resp = self.client.post('/business/toggle-haki/', {'enable': '0'})
        self.assertTrue(resp.json()['ok'])
        self.biz.refresh_from_db()
        self.assertFalse(self.biz.haki_enabled)

        resp2 = self.client.post('/business/toggle-haki/', {'enable': '1'})
        self.assertTrue(resp2.json()['ok'])
        self.biz.refresh_from_db()
        self.assertTrue(self.biz.haki_enabled)

    def test_staff_cannot_toggle(self):
        self.client.force_login(self.staff)
        resp = self.client.post('/business/toggle-haki/', {'enable': '0'})
        self.assertEqual(resp.status_code, 403)

    def test_duplicate_token_is_idempotent(self):
        payload = {'enable': '0', 'idempotency_token': 'togglehaki-same-token'}
        r1 = self.client.post('/business/toggle-haki/', payload)
        r2 = self.client.post('/business/toggle-haki/', payload)
        self.assertTrue(r1.json()['ok'])
        self.assertTrue(r2.json().get('duplicate'))


class DeactivateStaffSoftDeleteTest(TestCase):
    """2026-07-25: delete_staff() used to hard-delete the User row, cascading
    through Shift.staff/SalaryPayment.staff/SalaryDeduction.staff and
    destroying exactly the history a "staff journey" report needs. Replaced
    with deactivate_staff(): the User row is never destroyed, only
    deactivated (is_active=False blocks login), so every historical record
    survives and stays queryable."""

    def setUp(self):
        from core.models import Shift, RecurringExpense
        self.Shift = Shift
        self.RecurringExpense = RecurringExpense

        self.biz = Business.objects.create(name='Deactivate Staff Biz')
        self.owner = User.objects.create_user(username='deact_owner', password='x')
        UserProfile.objects.create(user=self.owner, business=self.biz, role='owner')
        self.staff_user = User.objects.create_user(username='deact_staff', password='x')
        self.staff_profile = UserProfile.objects.create(
            user=self.staff_user, business=self.biz, role='staff',
        )
        self.shift = Shift.objects.create(business=self.biz, staff=self.staff_user, status='CLOSED')
        self.client.force_login(self.owner)

    def test_deactivate_does_not_delete_the_user_row(self):
        self.client.post(f'/business/staff/deactivate/{self.staff_user.id}/', {
            'departure_reason': 'resigned', 'departure_note': 'Alihama jiji',
        })
        self.assertTrue(User.objects.filter(id=self.staff_user.id).exists())
        self.staff_user.refresh_from_db()
        self.assertFalse(self.staff_user.is_active)

    def test_deactivate_preserves_shift_history(self):
        self.client.post(f'/business/staff/deactivate/{self.staff_user.id}/', {'departure_reason': 'resigned'})
        self.assertTrue(self.Shift.objects.filter(id=self.shift.id).exists())

    def test_deactivate_stamps_departure_metadata(self):
        self.client.post(f'/business/staff/deactivate/{self.staff_user.id}/', {
            'departure_reason': 'terminated', 'departure_note': 'Alichelewa mara nyingi',
        })
        self.staff_profile.refresh_from_db()
        self.assertEqual(self.staff_profile.departure_reason, 'terminated')
        self.assertEqual(self.staff_profile.departure_note, 'Alichelewa mara nyingi')
        self.assertIsNotNone(self.staff_profile.departed_at)
        self.assertEqual(self.staff_profile.departed_by_id, self.owner.id)

    def test_deactivated_staff_disappears_from_staff_list(self):
        self.client.post(f'/business/staff/deactivate/{self.staff_user.id}/', {'departure_reason': 'resigned'})
        # A fresh GET (not following the POST's redirect) so the one-time flash
        # success message — which legitimately names the deactivated staffer —
        # isn't still queued; only the roster table itself should be checked.
        self.client.get('/business/staff/')
        resp = self.client.get('/business/staff/')
        self.assertNotIn(b'@deact_staff', resp.content)

    def test_deactivated_staff_cannot_log_in(self):
        self.client.post(f'/business/staff/deactivate/{self.staff_user.id}/', {'departure_reason': 'resigned'})
        self.client.logout()
        logged_in = self.client.login(username='deact_staff', password='x')
        self.assertFalse(logged_in)

    def test_deactivate_pauses_active_recurring_salary(self):
        salary_rule = self.RecurringExpense.objects.create(
            business=self.biz, description='Mshahara', category='labor',
            amount=Decimal('10000'), period='MONTHLY',
            staff_profile=self.staff_profile, is_active=True,
        )
        self.client.post(f'/business/staff/deactivate/{self.staff_user.id}/', {'departure_reason': 'resigned'})
        salary_rule.refresh_from_db()
        self.assertFalse(salary_rule.is_active)

    def test_staff_cannot_deactivate_colleague(self):
        other_staff = User.objects.create_user(username='deact_other', password='x')
        UserProfile.objects.create(user=other_staff, business=self.biz, role='staff')
        self.client.force_login(other_staff)
        resp = self.client.post(f'/business/staff/deactivate/{self.staff_user.id}/', {'departure_reason': 'resigned'})
        self.assertEqual(resp.status_code, 302)  # redirected home, not authorized
        self.staff_user.refresh_from_db()
        self.assertTrue(self.staff_user.is_active)

    def test_already_deactivated_staff_returns_404(self):
        self.staff_user.is_active = False
        self.staff_user.save(update_fields=['is_active'])
        resp = self.client.post(f'/business/staff/deactivate/{self.staff_user.id}/', {'departure_reason': 'resigned'})
        self.assertEqual(resp.status_code, 404)


class ReactivateStaffTest(TestCase):
    def setUp(self):
        self.biz = Business.objects.create(name='Reactivate Staff Biz')
        self.owner = User.objects.create_user(username='react_owner', password='x')
        UserProfile.objects.create(user=self.owner, business=self.biz, role='owner')
        self.staff_user = User.objects.create_user(username='react_staff', password='x', is_active=False)
        self.staff_profile = UserProfile.objects.create(
            user=self.staff_user, business=self.biz, role='staff',
            departed_at=timezone.now(), departure_reason='resigned',
        )
        self.client.force_login(self.owner)

    def test_reactivate_restores_login_and_roster_visibility(self):
        resp = self.client.post(f'/business/staff/reactivate/{self.staff_user.id}/')
        self.assertEqual(resp.status_code, 302)
        self.staff_user.refresh_from_db()
        self.assertTrue(self.staff_user.is_active)
        list_resp = self.client.get('/business/staff/')
        self.assertIn(b'react_staff', list_resp.content)

    def test_reactivate_stamps_metadata_without_erasing_departure_history(self):
        self.client.post(f'/business/staff/reactivate/{self.staff_user.id}/')
        self.staff_profile.refresh_from_db()
        self.assertIsNotNone(self.staff_profile.reactivated_at)
        self.assertEqual(self.staff_profile.reactivated_by_id, self.owner.id)
        self.assertEqual(self.staff_profile.departure_reason, 'resigned')  # history preserved, not cleared

    def test_manager_cannot_reactivate(self):
        manager = User.objects.create_user(username='react_manager', password='x')
        UserProfile.objects.create(user=manager, business=self.biz, role='manager')
        self.client.force_login(manager)
        self.client.post(f'/business/staff/reactivate/{self.staff_user.id}/')
        self.staff_user.refresh_from_db()
        self.assertFalse(self.staff_user.is_active)

    def test_already_active_staff_returns_404_on_reactivate(self):
        self.staff_user.is_active = True
        self.staff_user.save(update_fields=['is_active'])
        resp = self.client.post(f'/business/staff/reactivate/{self.staff_user.id}/')
        self.assertEqual(resp.status_code, 404)


class DepartedStaffListTest(TestCase):
    def setUp(self):
        self.biz = Business.objects.create(name='Departed List Biz')
        self.owner = User.objects.create_user(username='dlist_owner', password='x')
        UserProfile.objects.create(user=self.owner, business=self.biz, role='owner')
        active_user = User.objects.create_user(username='dlist_active', password='x')
        UserProfile.objects.create(user=active_user, business=self.biz, role='staff')
        departed_user = User.objects.create_user(username='dlist_departed', password='x', is_active=False)
        UserProfile.objects.create(
            user=departed_user, business=self.biz, role='staff',
            departed_at=timezone.now(), departure_reason='resigned',
        )
        self.client.force_login(self.owner)

    def test_only_departed_staff_appear(self):
        resp = self.client.get('/business/staff/departed/')
        self.assertIn(b'dlist_departed', resp.content)
        self.assertNotIn(b'dlist_active', resp.content)


class StaffNameChangeLogTest(TestCase):
    """edit_staff() silently overwrote first_name/last_name/username with no
    trace. Now logs every actual change to StaffNameChangeLog."""

    def setUp(self):
        from .models import StaffNameChangeLog
        self.StaffNameChangeLog = StaffNameChangeLog
        self.biz = Business.objects.create(name='Name Change Log Biz')
        self.owner = User.objects.create_user(username='namelog_owner', password='x')
        UserProfile.objects.create(user=self.owner, business=self.biz, role='owner')
        self.staff = User.objects.create_user(
            username='oldname', password='x', first_name='Old', last_name='Name',
        )
        UserProfile.objects.create(user=self.staff, business=self.biz, role='staff', phone='0700000000')
        self.client.force_login(self.owner)

    def test_rename_creates_a_log_entry(self):
        self.client.post(f'/business/staff/edit/{self.staff.id}/', {
            'username': 'newname', 'first_name': 'New', 'last_name': 'Name',
            'email': '', 'phone': '0700000000', 'role': 'staff',
        })
        log = self.StaffNameChangeLog.objects.filter(staff=self.staff).first()
        self.assertIsNotNone(log)
        self.assertEqual(log.old_username, 'oldname')
        self.assertEqual(log.new_username, 'newname')
        self.assertEqual(log.old_display_name, 'Old Name')
        self.assertEqual(log.new_display_name, 'New Name')
        self.assertEqual(log.changed_by_id, self.owner.id)

    def test_unrelated_field_change_does_not_log(self):
        self.client.post(f'/business/staff/edit/{self.staff.id}/', {
            'username': 'oldname', 'first_name': 'Old', 'last_name': 'Name',
            'email': '', 'phone': '0711111111', 'role': 'staff',
        })
        self.assertFalse(self.StaffNameChangeLog.objects.filter(staff=self.staff).exists())


class DeactivatedStaffMiddlewareTest(TestCase):
    """A staffer deactivated mid-session must be logged out on their very
    next request, not just blocked at their next login attempt — Django's
    AuthenticationForm only checks is_active at login."""

    def setUp(self):
        self.biz = Business.objects.create(name='Middleware Deactivate Biz')
        self.staff_user = User.objects.create_user(username='mw_staff', password='x')
        UserProfile.objects.create(user=self.staff_user, business=self.biz, role='staff')

    def test_deactivated_mid_session_is_logged_out_on_next_request(self):
        self.client.force_login(self.staff_user)
        # Confirm the session works before deactivation
        resp = self.client.get('/')
        self.assertEqual(resp.status_code, 200)

        self.staff_user.is_active = False
        self.staff_user.save(update_fields=['is_active'])

        resp2 = self.client.get('/', follow=True)
        # Session should no longer be authenticated — middleware must have logged them out
        self.assertFalse(resp2.wsgi_request.user.is_authenticated)
