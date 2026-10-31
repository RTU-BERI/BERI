"""Admin-only input rules; canonical models and public API stay unchanged."""

from django import forms
from django.core.exceptions import ValidationError

from .models import ProductAttributeValue


class ProductAttributeValueForm(forms.ModelForm):
    """Select the storage slot explicitly, preserving false/zero/empty text."""

    value_kind = forms.ChoiceField(
        label="Тип значения",
        choices=(("text", "Текст"), ("number", "Число"), ("boolean", "Да / Нет")),
        help_text="Выберите одно поле хранения. Это не словарь data_type.",
    )
    value_text = forms.CharField(required=False, strip=False, widget=forms.Textarea)
    value_boolean = forms.NullBooleanField(required=False)

    class Meta:
        model = ProductAttributeValue
        fields = (
            "product",
            "category_attribute",
            "value_kind",
            "value_text",
            "value_number",
            "value_boolean",
        )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            self.initial["value_kind"] = next(
                kind
                for kind in ("text", "number", "boolean")
                if getattr(self.instance, "value_" + kind) is not None
            )

    def clean(self):
        cleaned = super().clean()
        kind = cleaned.get("value_kind")
        if kind not in {"text", "number", "boolean"}:
            return cleaned
        for other in {"text", "number", "boolean"} - {kind}:
            field = "value_" + other
            value = cleaned.get(field)
            if value is not None and not (other == "text" and value == ""):
                raise ValidationError("Заполните только выбранное поле значения.")
            cleaned[field] = None
        selected = "value_" + kind
        if cleaned.get(selected) is None and selected not in self.errors:
            self.add_error(selected, "Укажите выбранное значение.")
        return cleaned
