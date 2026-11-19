from django.urls import path
from .views import InspectionReplacementView, PreIssueInspectionView

urlpatterns = [
    path("employee/reservations/<uuid:pk>/inspections/", PreIssueInspectionView.as_view(), name="pre-issue-inspections"),
    path("employee/reservations/<uuid:pk>/replace/", InspectionReplacementView.as_view(), name="inspection-replacement"),
]
