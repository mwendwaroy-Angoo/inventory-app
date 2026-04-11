release: python manage.py migrate && python manage.py fix_staff_profiles && python manage.py reset_superuser
web: gunicorn stockapp.wsgi:application --timeout 120 --log-file -