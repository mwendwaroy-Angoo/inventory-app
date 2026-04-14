import io
import traceback
from django.utils import timezone
from django.core.management import call_command

from .models import ImportJob


def run_import_job(job_id):
    """Background runner for queued import jobs.

    This updates the ImportJob status and captures stdout/stderr into the
    job.result_text field.
    """
    try:
        job = ImportJob.objects.get(id=job_id)
    except ImportJob.DoesNotExist:
        return

    job.status = 'running'
    job.started_at = timezone.now()
    job.save(update_fields=['status', 'started_at'])

    out = io.StringIO()
    try:
        if job.job_type == 'products':
            kwargs = {}
            if job.commit:
                kwargs['commit'] = True
            if job.store:
                kwargs['store_id'] = job.store.id
            call_command('import_products', job.file_path, stdout=out, **kwargs)
        else:
            kwargs = {}
            if job.commit:
                kwargs['commit'] = True
            call_command('import_taxonomy', job.file_path, stdout=out, **kwargs)

        job.result_text = out.getvalue()
        job.status = 'completed'
    except Exception as e:
        tb = traceback.format_exc()
        job.result_text = f"Exception: {e}\n\n{tb}\n\nPartial output:\n" + out.getvalue()
        job.status = 'failed'
    finally:
        job.finished_at = timezone.now()
        job.save(update_fields=['result_text', 'status', 'finished_at'])
