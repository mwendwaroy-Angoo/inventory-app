from django.db import migrations, models


class Migration(migrations.Migration):
    """
    SalaryPayment: remove unique_together constraint (allow multiple partial payments
    per period), add payment_type and staff_note fields.
    """

    dependencies = [
        ('core', '0100_write_off_request_salary_deduction'),
    ]

    operations = [
        migrations.AlterUniqueTogether(
            name='salarypayment',
            unique_together=set(),
        ),
        migrations.AddField(
            model_name='salarypayment',
            name='payment_type',
            field=models.CharField(
                choices=[('full', 'Full Payment'), ('partial', 'Partial Payment')],
                default='full',
                help_text="'full' = complete salary; 'partial' = instalment toward the period's salary.",
                max_length=10,
            ),
        ),
        migrations.AddField(
            model_name='salarypayment',
            name='staff_note',
            field=models.CharField(
                blank=True,
                help_text='Optional note shown to the staff member on their Kazi Yangu page.',
                max_length=500,
            ),
        ),
        migrations.AlterModelOptions(
            name='salarypayment',
            options={
                'ordering': ['-period', '-paid_at', 'staff'],
                'verbose_name': 'Salary Payment',
                'verbose_name_plural': 'Salary Payments',
            },
        ),
    ]
