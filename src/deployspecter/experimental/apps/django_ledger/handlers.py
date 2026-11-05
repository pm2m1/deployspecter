"""The Django ledger's request handlers: ordinary Django ORM code. ``lock`` is the build switch (select_for_update)."""

from __future__ import annotations

from typing import Any

from django.db import transaction
from django.db.models import F

from .models import Account, Entry


class InsufficientFunds(Exception):
    pass


def withdraw(*, alias: str, lock: bool, account_id: int, amount: int) -> dict[str, Any]:
    """Read the balance, refuse an overdraft from what was read, write balance - amount and an entry."""
    with transaction.atomic(using=alias):
        accounts = Account.objects.using(alias)
        if lock:
            accounts = accounts.select_for_update()
        acct = accounts.get(pk=account_id)
        if acct.balance < amount:
            return {"status": "insufficient"}
        acct.balance = acct.balance - amount  # computed in Python from what the read returned
        acct.save(update_fields=["balance"], using=alias)
        Entry.objects.using(alias).create(account_id=account_id, delta=-amount)
    return {"status": "ok"}


def deposit(*, alias: str, lock: bool, account_id: int, amount: int) -> dict[str, Any]:
    """An atomic UPDATE ... SET balance = balance + %s: correct in both builds."""
    with transaction.atomic(using=alias):
        if Account.objects.using(alias).filter(pk=account_id).update(balance=F("balance") + amount) != 1:
            raise LookupError(f"account {account_id}")
        Entry.objects.using(alias).create(account_id=account_id, delta=amount)
    return {"status": "ok"}


def transfer(*, alias: str, lock: bool, src: int, dst: int, amount: int) -> dict[str, Any]:
    """The outer transaction reads both accounts; the move runs in a nested atomic (a savepoint) that rolls back on
    an overdraft while the outer transaction still commits."""
    with transaction.atomic(using=alias):
        accounts_qs = Account.objects.using(alias).filter(pk__in=[src, dst]).order_by("pk")
        if lock:
            accounts_qs = accounts_qs.select_for_update()
        accounts = {a.pk: a for a in accounts_qs}
        try:
            with transaction.atomic(using=alias):
                if accounts[src].balance < amount:
                    raise InsufficientFunds(str(src))
                accounts[src].balance -= amount
                accounts[dst].balance += amount
                for a in (accounts[src], accounts[dst]):
                    a.save(update_fields=["balance"], using=alias)
                Entry.objects.using(alias).bulk_create([Entry(account_id=src, delta=-amount),
                                                        Entry(account_id=dst, delta=amount)])
        except InsufficientFunds:
            return {"status": "insufficient"}
    return {"status": "ok"}
