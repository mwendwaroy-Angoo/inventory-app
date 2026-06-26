from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0040_salary_payment_model'),
    ]

    operations = [
        migrations.AddField(
            model_name='business',
            name='credit_policy_enabled',
            field=models.BooleanField(
                default=True,
                help_text='Enforce the credit discipline gate at every issuance point.',
            ),
        ),
        migrations.AddField(
            model_name='business',
            name='debt_cycle',
            field=models.CharField(
                max_length=10,
                choices=[('rolling', 'Rolling'), ('monthly', 'Monthly')],
                default='rolling',
                help_text='Rolling = always-on window. Monthly = reset at month-end.',
            ),
        ),
        migrations.AddField(
            model_name='business',
            name='debt_cutoff_days_before_month_end',
            field=models.PositiveIntegerField(
                default=5,
                help_text='Monthly cycle only: block new credit in the last N days of the month.',
            ),
        ),
        migrations.AddField(
            model_name='business',
            name='block_if_overdue',
            field=models.BooleanField(
                default=True,
                help_text='Block new credit while the customer has any debt overdue past the window.',
            ),
        ),
        migrations.AddField(
            model_name='business',
            name='overdue_grace_days',
            field=models.PositiveIntegerField(
                default=0,
                help_text='Extra days beyond the credit window before a debt is treated as blocking.',
            ),
        ),
        migrations.AddField(
            model_name='business',
            name='late_repayment_strikes',
            field=models.PositiveIntegerField(
                default=3,
                help_text='Block after this many significantly-late repayments.',
            ),
        ),
        migrations.AddField(
            model_name='business',
            name='late_threshold_days',
            field=models.PositiveIntegerField(
                default=7,
                help_text='A repayment is "significantly late" if it lands this many days past the credit window.',
            ),
        ),
        migrations.AddField(
            model_name='business',
            name='defaulter_permanent',
            field=models.BooleanField(
                default=False,
                help_text='Permanently block customers whose debt was written off as bad debt.',
            ),
        ),
        migrations.AddField(
            model_name='business',
            name='cooldown_days',
            field=models.PositiveIntegerField(
                default=14,
                help_text='Clean days required after clearing all debt before credit resumes (for repeat late-payers).',
            ),
        ),
    ]
