release: python manage.py migrate && python manage.py fix_staff_profiles
web: gunicorn stockapp.wsgi:application --log-file -