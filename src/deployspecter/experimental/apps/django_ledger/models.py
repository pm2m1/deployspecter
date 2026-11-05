from django.db import models


class Account(models.Model):
    id = models.IntegerField(primary_key=True)
    owner = models.TextField()
    opening = models.BigIntegerField()
    balance = models.BigIntegerField()

    class Meta:
        app_label = "django_ledger"
        db_table = "ledger_account"


class Entry(models.Model):
    account = models.ForeignKey(Account, on_delete=models.CASCADE)
    delta = models.BigIntegerField()

    class Meta:
        app_label = "django_ledger"
        db_table = "ledger_entry"
