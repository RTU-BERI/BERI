"""Scoped rollback/error handling for catalog Admin writes."""

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import IntegrityError, router, transaction
from django.http import HttpResponseRedirect
from django.urls import reverse


class CatalogWriteMixin:
    """Render a failed POST as an invalid form after Django's atomic rollback.

    The second rendering pass deliberately cannot save: its form clean raises
    the recorded error. No raw SQL/driver diagnostics are returned to the user.
    """

    actions = None

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        field = super().formfield_for_foreignkey(db_field, request, **kwargs)
        if (
            field is not None
            and db_field.remote_field.model._meta.app_label == "catalog"
        ):
            if db_field.remote_field.model._meta.model_name == "categoryattribute":
                field.queryset = field.queryset.select_related(
                    "category", "attribute_definition"
                )
                field.label_from_instance = lambda obj: (
                    f"{obj.category.name}: {obj.attribute_definition.name} (#{obj.pk})"
                )
            else:
                field.label_from_instance = lambda obj: f"{obj.name} (#{obj.pk})"
        return field

    def get_form(self, request, obj=None, **kwargs):
        form_class = super().get_form(request, obj, **kwargs)
        error = getattr(request, "_catalog_write_error", None)
        if error is None:
            return form_class

        class FailedWriteForm(form_class):
            def clean(self):
                super().clean()
                raise ValidationError(error)

        return FailedWriteForm

    def changeform_view(self, request, object_id=None, form_url="", extra_context=None):
        try:
            return super().changeform_view(request, object_id, form_url, extra_context)
        except (ValidationError, IntegrityError) as error:
            if request.method != "POST":
                raise
            request._catalog_write_error = (
                error.messages
                if isinstance(error, ValidationError)
                else [
                    "Данные конфликтуют с другой записью. Обновите форму и повторите ввод."
                ]
            )
        try:
            return super().changeform_view(request, object_id, form_url, extra_context)
        finally:
            del request._catalog_write_error

    def delete_view(self, request, object_id, extra_context=None):
        try:
            return super().delete_view(request, object_id, extra_context)
        except (ValidationError, IntegrityError):
            if request.method != "POST":
                raise
            self.message_user(
                request,
                "Удаление запрещено: связанные данные изменились или защищены историей.",
                level=messages.ERROR,
            )
            return HttpResponseRedirect(
                reverse(
                    f"admin:{self.opts.app_label}_{self.opts.model_name}_changelist",
                    current_app=self.admin_site.name,
                )
            )

    lock_object_on_post = True

    def get_object(self, request, object_id, from_field=None):
        obj = super().get_object(request, object_id, from_field)
        using = router.db_for_write(self.model)
        if (
            obj is not None
            and request.method == "POST"
            and self.lock_object_on_post
            and transaction.get_connection(using).in_atomic_block
        ):
            return (
                self.model.objects.using(using)
                .select_for_update()
                .filter(pk=obj.pk)
                .first()
            )
        return obj


class NoDeleteMixin:
    def has_delete_permission(self, request, obj=None):
        return False
