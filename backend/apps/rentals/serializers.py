"""Employee inspection inputs and explicitly whitelisted history output."""

from rest_framework import serializers
from .models import Inspection, InspectionProblem, Rental


class ProblemInputSerializer(serializers.Serializer):
    problem_type = serializers.ChoiceField(choices=InspectionProblem.Type.choices)
    description = serializers.CharField(trim_whitespace=False)
    product_component_id = serializers.IntegerField(min_value=1, max_value=2**63-1, allow_null=True, required=False)
    missing_quantity = serializers.IntegerField(min_value=1, max_value=2**31-1, allow_null=True, required=False)

    def to_internal_value(self, data):
        if isinstance(data, dict) and set(data) - set(self.fields):
            raise serializers.ValidationError({"non_field_errors": ["Неизвестные поля запроса."]})
        return super().to_internal_value(data)


class InspectionInputSerializer(serializers.Serializer):
    expected_assignment_id = serializers.IntegerField(min_value=1, max_value=2**63-1)
    condition_ok = serializers.BooleanField()
    completeness_ok = serializers.BooleanField()
    comment = serializers.CharField(required=False, allow_null=True, allow_blank=True, trim_whitespace=False)
    problems = ProblemInputSerializer(many=True, required=False)

    def to_internal_value(self, data):
        if isinstance(data, dict) and set(data) - set(self.fields):
            raise serializers.ValidationError({"non_field_errors": ["Неизвестные или серверные поля запроса."]})
        return super().to_internal_value(data)


class ReplacementInputSerializer(InspectionInputSerializer):
    reason = serializers.CharField(max_length=30, trim_whitespace=False)
    replacement_item_id = serializers.IntegerField(min_value=1, max_value=2**63-1, required=False, allow_null=True)
    decline_replacement = serializers.BooleanField(required=False, default=False)
    completion_reason = serializers.CharField(max_length=50, required=False, allow_null=True, trim_whitespace=False)


class InspectionProblemSerializer(serializers.ModelSerializer):
    class Meta:
        model = InspectionProblem
        fields = ("id", "problem_type", "description", "product_component_id", "missing_quantity")
        read_only_fields = fields


class InspectionSerializer(serializers.ModelSerializer):
    problems = InspectionProblemSerializer(many=True, read_only=True)

    class Meta:
        model = Inspection
        fields = ("id", "reservation_id", "product_item_id", "employee_id", "inspection_type",
                  "condition_ok", "completeness_ok", "comment", "inspected_at", "problems")
        read_only_fields = fields


# Принимает назначение, которое сотрудник видел при подготовке выдачи.
class RentalStartInputSerializer(serializers.Serializer):
    expected_assignment_id = serializers.IntegerField(min_value=1, max_value=2**63-1)

    # Не позволяет незаметно передать серверные поля вместо одного ID.
    def to_internal_value(self, data):
        if isinstance(data, dict) and set(data) - set(self.fields):
            raise serializers.ValidationError({"non_field_errors": ["Неизвестные или серверные поля запроса."]})
        return super().to_internal_value(data)


# Возвращает только договорённые данные аренды, без платёжных идентификаторов.
class RentalSerializer(serializers.ModelSerializer):
    # Задаёт явный неизменяемый контракт ответа.
    class Meta:
        model = Rental
        fields = ("id", "reservation_id", "product_item_id", "status", "started_at",
                  "started_by_employee_id", "tariff_id", "hourly_rate_applied")
        read_only_fields = fields
