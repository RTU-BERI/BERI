from django import forms
from django.contrib.auth.forms import (
    BaseUserCreationForm,
    ReadOnlyPasswordHashField,
)

from .models import Account, Employee


class AccountCreationForm(BaseUserCreationForm):
    class Meta:
        model = Account
        fields = ("phone", "account_type", "status", "is_active")


class AccountChangeForm(forms.ModelForm):
    password = ReadOnlyPasswordHashField()

    class Meta:
        model = Account
        fields = "__all__"


class EmployeeForm(forms.ModelForm):
    class Meta:
        model = Employee
        fields = ("account", "pickup_point")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["account"].queryset = Account.objects.filter(
            account_type=Account.AccountType.EMPLOYEE,
        )
