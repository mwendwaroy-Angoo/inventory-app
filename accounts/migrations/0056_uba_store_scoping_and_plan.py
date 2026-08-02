import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0055_alter_accountdeletionlog_reason'),
        ('core', '0141_store_uba_outlet_fields'),
    ]

    operations = [
        migrations.AddField(
            model_name='business',
            name='plan',
            field=models.CharField(
                choices=[('standard', 'Standard'), ('multi', 'Multi-Store'), ('pro', 'Pro')],
                default='standard', max_length=10,
                help_text='UBA pricing tier. Dormant hook only per the execution order — '
                          'nothing in the app currently reads or gates on this field.',
            ),
        ),
        migrations.AddField(
            model_name='userprofile',
            name='home_store',
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                related_name='home_staff', to='core.store',
            ),
        ),
        migrations.AddField(
            model_name='userprofile',
            name='stores',
            field=models.ManyToManyField(
                blank=True, related_name='assigned_staff', to='core.store',
                help_text='Stores this staff member may access. Empty means unrestricted '
                          '(see accessible_stores()) — an owner narrows this per staff member.',
            ),
        ),
    ]
