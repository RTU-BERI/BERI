from django.urls import path
from .views import InspectionReplacementView, PreIssueInspectionView, RentalStartView

urlpatterns = [
    path("employee/reservations/<uuid:pk>/issue/", RentalStartView.as_view(), name="rental-start"),
    path("employee/reservations/<uuid:pk>/inspections/", PreIssueInspectionView.as_view(), name="pre-issue-inspections"),
    path("employee/reservations/<uuid:pk>/replace/", InspectionReplacementView.as_view(), name="inspection-replacement"),
]
