# Celery app is NOT imported here on purpose.
# Celery 5.6.3 eagerly pings the broker (Redis) when the app object is
# created. That causes manage.py runserver to hang when Redis is not
# running locally.  The worker is invoked as:
#   celery -A stockapp worker ...
# which imports stockapp.celery directly, so the app is still discovered
# correctly for task routing.  @shared_task in core/tasks.py works
# independently of this module.
