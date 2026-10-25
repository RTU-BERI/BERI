"""Admin forms for the existing phone-based Account and Employee models."""

from django import forms
from django.contrib.auth.forms import BaseUserCreationForm, UserChangeForm
from django.core.exceptions import ValidationError

from .models import Account, Employee
from .validators import normalize_phone, validate_status


class AccountIdentityFields:
    def clean_phone(self):
        return normalize_phone(self.cleaned_data["phone"])


class AccountCreationForm(AccountIdentityFields, BaseUserCreationForm):
    # Validate after removing approved separators, before the storage length.
    phone = forms.CharField()
    status = forms.CharField(max_length=20, strip=False, validators=[validate_status])

    class Meta:
        model = Account
        fields = ("phone", "account_type", "status", "is_active")


class AccountChangeForm(AccountIdentityFields, UserChangeForm):
    phone = forms.CharField()
    status = forms.CharField(max_length=20, strip=False, validators=[validate_status])

    class Meta:
        model = Account
        fields = "__all__"

    def clean(self):
        cleaned = super().clean()
        if self.instance.pk:
            current_type = Account.objects.filter(pk=self.instance.pk).values_list(
                "account_type", flat=True
            ).first()
            proposed = cleaned.get("account_type", current_type)
            # Also reject a crafted POST of a field removed as readonly.
            supplied = self.data.get("account_type", proposed)
            if (
                (proposed != current_type or supplied != current_type)
                and Employee.objects.filter(account_id=self.instance.pk).exists()
            ):
                raise ValidationError(
                    "An account linked to an Employee cannot change account type."
                )
        return cleaned


class EmployeeForm(forms.ModelForm):
    class Meta:
        model = Employee
        fields = ("account", "pickup_point")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if "account" in self.fields:
            self.fields["account"].queryset = Account.objects.filter(
                account_type=Account.AccountType.EMPLOYEE
            )
        if self.instance.pk:
            for field in ("account", "pickup_point"):
                if field in self.fields:
                    self.fields[field].disabled = True

    def clean(self):
        cleaned = super().clean()
        if self.instance.pk:
            current = Employee.objects.filter(pk=self.instance.pk).values(
                "account_id", "pickup_point_id"
            ).first()
            if current:
                for field in ("account", "pickup_point"):
                    supplied = self.data.get(field)
                    if supplied is not None and str(supplied) != str(current[f"{field}_id"]):
                        raise ValidationError(
                            "Employee account and pickup point cannot be changed here."
                        )
        return cleaned
