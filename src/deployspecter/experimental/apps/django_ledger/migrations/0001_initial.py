from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True
    dependencies: list = []
    operations = [
        migrations.CreateModel(
            name="Account",
            fields=[("id", models.IntegerField(primary_key=True, serialize=False)), ("owner", models.TextField()),
                    ("opening", models.BigIntegerField()), ("balance", models.BigIntegerField())],
            options={"db_table": "ledger_account"},
        ),
        migrations.CreateModel(
            name="Entry",
            fields=[("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                    ("delta", models.BigIntegerField()),
                    ("account", models.ForeignKey(on_delete=models.deletion.CASCADE, to="django_ledger.account"))],
            options={"db_table": "ledger_entry"},
        ),
    ]
