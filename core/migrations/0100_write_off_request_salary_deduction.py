from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0047_alter_userprofile_role'),
        ('core', '0099_businessexpense_security_category'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        # WriteOffRequest
        migrations.CreateModel(
            name='WriteOffRequest',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                ('reason', models.CharField(max_length=500)),
                ('customer_name_cache', models.CharField(blank=True, max_length=100)),
                ('manager_verdict', models.CharField(blank=True, max_length=20)),
                ('manager_at', models.DateTimeField(blank=True, null=True)),
                ('status', models.CharField(
                    choices=[
                        ('pending',  'Inasubiri Idhini'),
                        ('approved', 'Imeidhinishwa'),
                        ('rejected', 'Imekataliwa'),
                    ],
                    default='pending',
                    max_length=20,
                )),
                ('reviewed_at', models.DateTimeField(blank=True, null=True)),
                ('haki_deduction_created', models.BooleanField(default=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('manager_by', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='write_off_manager_reviews',
                    to=settings.AUTH_USER_MODEL,
                )),
                ('requested_by', models.ForeignKey(
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='write_off_requests',
                    to=settings.AUTH_USER_MODEL,
                )),
                ('reviewed_by', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='write_off_reviews',
                    to=settings.AUTH_USER_MODEL,
                )),
                ('transaction', models.OneToOneField(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='write_off_request',
                    to='core.transaction',
                )),
            ],
            options={
                'verbose_name': 'Write-off Request',
                'verbose_name_plural': 'Write-off Requests',
                'ordering': ['-created_at'],
            },
        ),
        # SalaryDeduction
        migrations.CreateModel(
            name='SalaryDeduction',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                ('period', models.CharField(
                    help_text="Period in YYYY-MM format. Deduction counts against this period's salary.",
                    max_length=7,
                )),
                ('amount', models.DecimalField(decimal_places=2, max_digits=12)),
                ('reason', models.CharField(max_length=500)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('business', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='salary_deductions',
                    to='accounts.business',
                )),
                ('created_by', models.ForeignKey(
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='salary_deductions_created',
                    to=settings.AUTH_USER_MODEL,
                )),
                ('staff', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='salary_deductions',
                    to='accounts.userprofile',
                )),
                ('write_off', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='deductions',
                    to='core.writeoffrequest',
                )),
            ],
            options={
                'verbose_name': 'Salary Deduction',
                'verbose_name_plural': 'Salary Deductions',
                'ordering': ['-created_at'],
            },
        ),
    ]
