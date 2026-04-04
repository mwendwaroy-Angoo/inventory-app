release: python manage.py migrate && python manage.py fix_staff_profiles
web: gunicorn stockapp.wsgi:application --timeout 120 --log-file -