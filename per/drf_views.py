# Standard library imports
from datetime import datetime
from typing import Dict, List, Optional, Any
import requests
import pytz
import httpx

# Django imports
from django.conf import settings
from django.db import transaction
from django.db.models import Count, F, Prefetch, Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.utils.translation import get_language as django_get_language

# Django filters
from django_filters import rest_framework as filters
from django_filters.widgets import CSVWidget

# Django REST Framework imports
from rest_framework import mixins, permissions, response, status, views, viewsets
from rest_framework import status as drf_status
from rest_framework.authentication import TokenAuthentication
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.settings import api_settings
from rest_framework.views import APIView

# Third-party imports
from drf_spectacular.utils import extend_schema
from openpyxl import Workbook

# Local app imports
from api.models import Country, Event, Region
from api.serializers import IfrcEventSummarySerializer
from api.logger import logger

from deployments.models import SectorTag

from main.permissions import DenyGuestUserMutationPermission, DenyGuestUserPermission
from main.utils import SpreadSheetContentNegotiation

from per.dref_temp.dref_utils import dref_manager, DREFFilters
from per.event_api_client import EventAPIClient
from per.field_report_api_client import FieldReportAPIClient
from per.cache import OpslearningSummaryCacheHelper
from per.filter_set import (
    PerDocumentFilter,
    PerOverviewFilter,
    PerPrioritizationFilter,
    PerWorkPlanFilter,
)
from per.permissions import (
    OpsLearningPermission,
    PerDocumentUploadPermission,
    PerGeneralPermission,
    PerPermission,
)
from per.task import generate_summary
from per.utils import filter_per_queryset_by_user_access
from per.azure_service import AzureServiceClient

from .admin_classes import RegionRestrictedAdmin
from .custom_renderers import NarrowCSVRenderer
from .models import (
    AreaResponse,
    AssessmentType,
    FormAnswer,
    FormArea,
    FormComponent,
    FormComponentQuestionAndAnswer,
    FormComponentResponse,
    FormData,
    FormPrioritization,
    FormPrioritizationComponent,
    FormQuestion,
    FormQuestionGroup,
    NiceDocument,
    OpsLearning,
    OpsLearningCacheResponse,
    OpsLearningComponentCacheResponse,
    OpsLearningSectorCacheResponse,
    OrganizationTypes,
    Overview,
    PerAssessment,
    PerComponentRating,
    PerDocumentUpload,
    PerFile,
    PerWorkPlan,
)
from .serializers import (
    FormAnswerSerializer,
    FormAreaSerializer,
    FormComponentSerializer,
    FormPrioritizationSerializer,
    FormQuestionGroupSerializer,
    FormQuestionSerializer,
    LatestCountryOverviewSerializer,
    ListNiceDocSerializer,
    NiceDocumentSerializer,
    OpsLearningCSVSerializer,
    OpsLearningInSerializer,
    OpsLearningOrganizationTypeSerializer,
    OpsLearningSerializer,
    OpsLearningStatSerializer,
    OpsLearningSummarySerializer,
    PerAssessmentSerializer,
    PerDocumentUploadSerializer,
    PerFileInputSerializer,
    PerFileSerializer,
    PerFormDataSerializer,
    PerOptionsSerializer,
    PerOverviewSerializer,
    PerProcessSerializer,
    PerWorkPlanSerializer,
    PublicOpsLearningSerializer,
    PublicPerAssessmentSerializer,
    PublicPerCountrySerializer,
    PublicPerProcessSerializer,
    UserPerCountrySerializer,
)

class PERDocsFilter(filters.FilterSet):
    id = filters.NumberFilter(field_name="id", lookup_expr="exact")

    class Meta:
        model = NiceDocument
        fields = {
            "id": ("exact",),
        }


class PERDocsViewset(viewsets.ReadOnlyModelViewSet):
    """To collect PER Documents"""

    # Duplicate of FormDataViewset
    queryset = NiceDocument.objects.all()
    authentication_classes = (TokenAuthentication,)
    permission_classes = (IsAuthenticated,)
    get_request_user_regions = RegionRestrictedAdmin.get_request_user_regions
    get_filtered_queryset = RegionRestrictedAdmin.get_filtered_queryset
    filterset_class = PERDocsFilter

    def get_queryset(self):
        queryset = NiceDocument.objects.all()
        cond1 = Q()
        cond2 = Q()
        cond3 = Q()
        if "new" in self.request.query_params.keys():
            last_duedate = settings.PER_LAST_DUEDATE
            tmz = pytz.timezone("Europe/Zurich")
            if not last_duedate:
                last_duedate = tmz.localize(datetime(2000, 11, 15, 9, 59, 25, 0))
            cond1 = Q(created_at__gt=last_duedate)
        if "country" in self.request.query_params.keys():
            cid = self.request.query_params.get("country", None) or 0
            country = Country.objects.filter(pk=cid)
            if country:
                cond2 = Q(country_id=country[0].id)
        if "visible" in self.request.query_params.keys():
            cond3 = Q(visibility=1)
        queryset = NiceDocument.objects.filter(cond1 & cond2 & cond3)
        if queryset.exists():
            queryset = self.get_filtered_queryset(self.request, queryset, 4)
        return queryset

    def get_serializer_class(self):
        if self.action == "list":
            return ListNiceDocSerializer
        return NiceDocumentSerializer


class FormAreaFilter(filters.FilterSet):
    id = filters.NumberFilter(field_name="id", lookup_expr="exact")
    area_num = filters.NumberFilter(field_name="area_num", lookup_expr="exact")

    class Meta:
        model = FormArea
        fields = {"id": ("exact",), "area_num": ("exact",)}


class FormAreaViewset(viewsets.ReadOnlyModelViewSet):
    """PER Form Areas Viewset"""

    serializer_class = FormAreaSerializer
    queryset = FormArea.objects.all().order_by("area_num")
    filterset_class = FormAreaFilter


class FormComponentFilter(filters.FilterSet):
    area_id = filters.NumberFilter(field_name="area__id", lookup_expr="exact")
    exclude_subcomponents = filters.BooleanFilter(
        method="get_exclude_subcomponents",
    )

    class Meta:
        model = FormComponent
        fields = {"area": ("exact",)}

    def get_exclude_subcomponents(self, queryset, name, value):
        if value:
            return queryset.exclude(component_num=14, is_parent__isnull=True)
        return queryset


class FormComponentViewset(viewsets.ReadOnlyModelViewSet):
    """PER Form Components Viewset"""

    serializer_class = FormComponentSerializer
    filterset_class = FormComponentFilter

    def get_queryset(self):
        return FormComponent.objects.all().order_by("area__area_num", "component_num", "component_letter").select_related("area")


class FormQuestionFilter(filters.FilterSet):
    area_id = filters.NumberFilter(field_name="component__area__id", lookup_expr="exact")

    class Meta:
        model = FormQuestion
        fields = {"component": ("exact",)}


class FormQuestionViewset(viewsets.ReadOnlyModelViewSet):
    """PER Form Questions Viewset"""

    serializer_class = FormQuestionSerializer
    filterset_class = FormQuestionFilter
    ordering_fields = "__all__"

    def get_queryset(self):
        return (
            FormQuestion.objects.all()
            .order_by("component__component_num", "question_num", "question")
            .select_related("component", "component__area")
            .prefetch_related("answers")
        )


class FormQuestionGroupViewset(viewsets.ReadOnlyModelViewSet):
    """PER From Question Group ViewSet"""

    serializer_class = FormQuestionGroupSerializer

    def get_queryset(self):
        return FormQuestionGroup.objects.select_related("component")


class FormAnswerViewset(viewsets.ReadOnlyModelViewSet):
    """PER Form Answers Viewset"""

    serializer_class = FormAnswerSerializer
    queryset = FormAnswer.objects.all()
    ordering_fields = "__all__"


class CountryPublicPerStatsViewset(mixins.ListModelMixin, viewsets.GenericViewSet):
    serializer_class = LatestCountryOverviewSerializer
    filterset_class = PerOverviewFilter

    def get_queryset(self):
        return Overview.objects.select_related("country", "type_of_assessment").order_by("-created_at")


class CountryPerStatsViewset(mixins.ListModelMixin, viewsets.GenericViewSet):
    serializer_class = LatestCountryOverviewSerializer
    filterset_class = PerOverviewFilter
    permission_classes = [IsAuthenticated, DenyGuestUserPermission]

    def get_queryset(self):
        return Overview.objects.select_related("country", "type_of_assessment").order_by("-created_at")


class PerOverviewViewSet(viewsets.ModelViewSet):
    serializer_class = PerOverviewSerializer
    permission_classes = [IsAuthenticated, PerPermission, DenyGuestUserPermission]
    filterset_class = PerOverviewFilter
    ordering_fields = "__all__"
    get_request_user_regions = RegionRestrictedAdmin.get_request_user_regions
    get_filtered_queryset = RegionRestrictedAdmin.get_filtered_queryset

    def get_queryset(self):
        queryset = Overview.objects.select_related("country", "user")
        return self.get_filtered_queryset(self.request, queryset, dispatch=0)

class PerDrefStatusView(APIView):
    def get(self, request):
        event_id = request.query_params.get("id", None)

        if not event_id:
            return Response({"error": "Event ID is required"}, status=drf_status.HTTP_400_BAD_REQUEST)
        
        try:
            # Convert to int for validation
            event_id = int(event_id)
        except ValueError:
            return Response({"error": "Event ID must be a valid integer"}, status=drf_status.HTTP_400_BAD_REQUEST)
        
        try:
            # Step 1: Check if the event exists using EventAPIClient
            event_client = EventAPIClient()
            event = event_client.get_event_detail(event_id)
            if not event:
                return Response({"error": "Event not found"}, status=drf_status.HTTP_404_NOT_FOUND)
            
            field_reports = event.get("field_reports", [])
            
            if len(field_reports) == 0:
                return Response({
                    "error": "Field Reports not found",
                    "event_id": event_id,
                    "event_name": event.get("name")
                }, status=drf_status.HTTP_404_NOT_FOUND)
            
            print(f"Found {len(field_reports)} field reports for event {event_id}")
            
            # Step 3: Extract field report IDs from the results array
            field_report_ids = [fr['id'] for fr in field_reports]
            print(f"Field report IDs: {field_report_ids}")
            
            # Step 4: Filter DREFs using the list of field report IDs
            filters = DREFFilters(field_report_ids=field_report_ids)
            matching_drefs = dref_manager.get_data("basic", filters)
            
            # Alternative approach using helper method:
            # matching_drefs = dref_manager.get_drefs_by_field_report_ids("basic", field_report_ids)
            
            print(f"Matching DREF records found: {len(matching_drefs)}")
            print(f"Event ID: {event_id}, Event Name: {event.get('name')}")

            if len(matching_drefs) == 0:
                return Response({
                    "error": "No DREF found for the given event ID",
                    "event_id": event_id,
                    "event_name": event.get("name"),
                    "field_reports_count": len(field_reports),
                    "field_report_ids": field_report_ids
                }, status=drf_status.HTTP_404_NOT_FOUND)
            
            # Step 5: Extract DREF information
            dref = matching_drefs[0]
            type_of_dref_display = dref.type_of_dref_display
            type_of_onset_display = dref.type_of_onset_display

            print(f"Type of DREF: {type_of_dref_display}, Type of Onset: {type_of_onset_display}")

            # Step 6: Return comprehensive response
            response_data = {
                "dref_id": matching_drefs[0].id,
                "dref_count": len(matching_drefs),
                "type_of_dref_display": type_of_dref_display,
                "type_of_onset_display": type_of_onset_display
            }
            
            # If multiple DREFs found, include info about all of them
            # if len(matching_drefs) > 1:
            #     response_data["all_drefs"] = [
            #         {
            #             "dref_id": d.id,
            #             "title": d.title,
            #             "appeal_code": d.appeal_code,
            #             "type_of_dref_display": d.type_of_dref_display,
            #             "field_report": getattr(d, 'field_report', None)
            #         }
            #         for d in matching_drefs
            #     ]

            return Response(response_data, status=drf_status.HTTP_200_OK)
            
        except requests.RequestException as e:
            return Response({"error": f"API request failed: {str(e)}"}, status=drf_status.HTTP_500_INTERNAL_SERVER_ERROR)
        except Exception as e:
            print(f"Unexpected error in PerDrefStatusView: {str(e)}")
            return Response({"error": f"Internal server error: {str(e)}"}, status=drf_status.HTTP_500_INTERNAL_SERVER_ERROR)
        
# Objective 2
    # Object return two summaries, operational stratgies and overall objectives + all budgeting in DREF
    # which can be shown in the frontend
    # Two DREF summaries are returned
    # Summary 1 - Data for two properties:
    #   1. Overall objective of the operation 
    #   2. Operation strategy rationale
    # Summary 2 - Budgeting for DREF

class PerDrefLLMSummaryView(APIView):
    # Create DTO for Summary 1 and Summary 2
    # Creating a use method to obtain data from DREF dump
    # Use Mustafa API key to prompt LLM summary using method

    def get(self, request):

        # event_id = request.query_params.get("id", None)
        # if not event_id:
        #     return Response({"error": "Event ID is required"}, status=drf_status.HTTP_400_BAD_REQUEST)
        return Response({200: "DREF LLM Summary View is not implemented yet"})

class ExportPerView(views.APIView):
    permission_classes = [permissions.IsAuthenticated, DenyGuestUserPermission]

    content_negotiation_class = SpreadSheetContentNegotiation

    def get(self, request, pk, format=None):
        per = get_object_or_404(Overview, pk=pk)
        per_queryset = Overview.objects.filter(id=per.id)
        # if per.extracted_at is None or per.updated_at > per.extracted_at:
        wb = Workbook()
        ws = wb.active
        ws.title = "Overview"
        # Overview Columns
        ws.row_dimensions[1].height = 70
        overview_columns = [
            "National Society",
            "Date of creation (register)",
            "Date of last update (of the whole process)",
            "Date of Orientation",
            "Orientation document uploaded? (Yes/No)",
            "Date of Current PER Assessment",
            "Type of Assessment",
            "Branches involved",
            "Method",
            "Epidemic Considerations",
            "Urban Considerations",
            "Climate and env considerations",
            "PER process cycle",
            "Work-plan development date planned",
            "Work-plan revision date planned",
            "NS FP name",
            "NS FP email",
            "NS FP phone number",
            "NS Second FP name",
            "NS Second FP email",
            "NS Second FP phone number",
            "Partner FP name",
            "Partner FP email",
            "Partner FP phone number",
            "Partner FP organization",
            "PER facilitator name",
            "PER facilitator email",
            "PER facilitator phone number",
            "PER facilitator other contact",
        ]
        row_num = 1

        # Assign the titles for each cell of the header
        for col_num, column_title in enumerate(overview_columns, 1):
            cell = ws.cell(row=row_num, column=col_num)
            cell.value = column_title
        for per in per_queryset:
            row_num += 1

            overview_rows = [
                per.country.name,
                per.created_at.date(),
                per.updated_at.date(),
                per.date_of_orientation,
                "Yes" if len(per.orientation_documents.all()) else "No",
                per.date_of_assessment,
                per.type_of_assessment.name,
                per.branches_involved,
                per.get_assessment_method_display(),
                per.assess_preparedness_of_country,
                per.assess_urban_aspect_of_country,
                per.assess_climate_environment_of_country,
                per.assessment_number,
                per.workplan_development_date,
                per.workplan_revision_date,
                per.ns_focal_point_name,
                per.ns_focal_point_email,
                per.ns_focal_point_phone,
                per.ns_second_focal_point_name,
                per.ns_second_focal_point_email,
                per.ns_second_focal_point_phone,
                per.partner_focal_point_name,
                per.partner_focal_point_email,
                per.partner_focal_point_phone,
                per.partner_focal_point_organization,
                per.facilitator_name,
                per.facilitator_email,
                per.facilitator_phone,
                per.facilitator_contact,
            ]

            for col_num, cell_value in enumerate(overview_rows, 1):
                cell = ws.cell(row=row_num, column=col_num)
                cell.value = cell_value

        # Assessment
        ws_assessment = wb.create_sheet("Assessment")
        ws_assessment.row_dimensions[1].height = 70
        assessment_columns = [
            "Component number",
            "Component letter",
            "Component description",
            "Benchmark number",
            "Benchmark descprition",
            "Benchmark answer (Yes/No/Partially)",
            "Benchmark notes",
            "Consideration notes epidemic",
            "Consideration notes urban",
            "Consideration notes climate",
            "Component rating",
            "Component notes",
        ]
        assessment_num = 1
        for col_num, column_title in enumerate(assessment_columns, 1):
            cell = ws_assessment.cell(row=assessment_num, column=col_num)
            cell.value = column_title

        assessment_rows = []
        assessment_queryset = (
            PerAssessment.objects.filter(overview=per.id)
            .order_by("area_responses__component_response__component__component_num")
            .prefetch_related(
                Prefetch(
                    "area_responses",
                    queryset=AreaResponse.objects.filter(perassessment__overview=per.id).prefetch_related(
                        Prefetch(
                            "component_response",
                            queryset=FormComponentResponse.objects.filter(arearesponse__perassessment__overview=per.id)
                            .exclude(component_id=14)
                            .prefetch_related(
                                Prefetch(
                                    "question_responses",
                                    queryset=FormComponentQuestionAndAnswer.objects.filter(
                                        formcomponentresponse__arearesponse__perassessment__overview=per.id
                                    ),
                                )
                            ),
                        )
                    ),
                )
            )
        )
        if assessent := assessment_queryset.first():
            for area_response in assessent.area_responses.all():
                for co in area_response.component_response.all():
                    question_answer = co.question_responses.all()
                    for question in question_answer:
                        assessment_inner = [
                            co.component.component_num,
                            co.component.component_letter,
                            co.component.description_en,
                            (
                                str(question.question.component.component_num) + "." + str(question.question.question_num)
                                if question.question
                                else None
                            ),
                            question.question.question if question.question else None,
                            question.answer.text if question.answer else None,
                            question.notes,
                            co.epi_considerations,
                            co.urban_considerations,
                            co.climate_environmental_considerations,
                            co.rating.title if co.rating else None,
                            co.notes,
                        ]
                        assessment_rows.append(assessment_inner)

        for row_num, row_data in enumerate(assessment_rows, 2):
            for col_num, cell_value in enumerate(row_data, 1):
                cell = ws_assessment.cell(row=row_num, column=col_num)
                cell.value = cell_value

        # Prioritization
        ws_prioritization = wb.create_sheet("Prioritization")
        ws_prioritization.row_dimensions[1].height = 70
        prioritization_columns = [
            "Prioritized component number",
            "Prioritized component letter",
            "Prioritized component description",
            "Justification",
        ]
        prioritization_rows = []
        prioritization_num = 1
        for col_num, column_title in enumerate(prioritization_columns, 1):
            cell = ws_prioritization.cell(row=prioritization_num, column=col_num)
            cell.value = column_title

        prioritization_queryset = (
            FormPrioritizationComponent.objects.filter(
                formprioritization__overview=per.id,
            )
            .order_by("component__component_num")
            .exclude(component_id=14)
        )
        for prioritization in prioritization_queryset:
            prioritization_inner = [
                prioritization.component.component_num,
                prioritization.component.component_letter,
                prioritization.component.description,
                prioritization.justification_text,
            ]
            prioritization_rows.append(prioritization_inner)
        for row_num, row_data in enumerate(prioritization_rows, 2):
            for col_num, cell_value in enumerate(row_data, 1):
                cell = ws_prioritization.cell(row=row_num, column=col_num)
                cell.value = cell_value
        # Workplan
        ws_workplan = wb.create_sheet("Workplan")
        ws_workplan.row_dimensions[1].height = 70
        workplan_columns = [
            "Actions",
            "Number of component related",
            "Letter of component related",
            "Description of component related",
            "Due date",
            "Supported by",
            "Supporting National Society",
            "Status",
        ]
        workplan_rows = []
        workplan_num = 1
        for col_num, column_title in enumerate(workplan_columns, 1):
            cell = ws_workplan.cell(row=workplan_num, column=col_num)
            cell.value = column_title

        workplan_queryset = PerWorkPlan.objects.filter(overview=per.id)
        if workplan_queryset.exists():
            for workplan in workplan_queryset.first().prioritized_action_responses.all():
                workplan_inner = [
                    workplan.actions,
                    workplan.component.component_num,
                    workplan.component.component_letter,
                    workplan.component.description_en,
                    workplan.due_date,
                    workplan.get_supported_by_organization_type_display(),
                    workplan.supported_by.name if workplan.supported_by else None,
                    workplan.get_status_display(),
                ]
                workplan_rows.append(workplan_inner)
        if workplan_queryset.exists():
            for workplan in workplan_queryset.first().additional_action_responses.all():
                workplan_inner = [
                    workplan.actions,
                    None,
                    None,
                    None,
                    workplan.due_date,
                    workplan.get_supported_by_organization_type_display(),
                    workplan.supported_by.name if workplan.supported_by else None,
                    workplan.get_status_display(),
                ]
                workplan_rows.append(workplan_inner)
        for row_num, row_data in enumerate(workplan_rows, 2):
            for col_num, cell_value in enumerate(row_data, 1):
                cell = ws_workplan.cell(row=row_num, column=col_num)
                cell.value = cell_value

        response = HttpResponse(
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        response["Content-Disposition"] = "attachment; filename=export.xlsx"
        wb.save(response)
        return response


class NewPerWorkPlanViewSet(viewsets.ModelViewSet):
    permission_classes = (IsAuthenticated, PerGeneralPermission, DenyGuestUserPermission)
    queryset = PerWorkPlan.objects.all()
    serializer_class = PerWorkPlanSerializer
    filterset_class = PerWorkPlanFilter
    ordering_fields = "__all__"


class PerFormDataViewSet(viewsets.ModelViewSet):
    serializer_class = PerFormDataSerializer
    queryset = FormData.objects.all()
    ordering_fields = "__all__"


class FormPrioritizationViewSet(viewsets.ModelViewSet):
    serializer_class = FormPrioritizationSerializer
    queryset = FormPrioritization.objects.all()
    filterset_class = PerPrioritizationFilter
    permission_classes = (IsAuthenticated, PerGeneralPermission, DenyGuestUserPermission)
    ordering_fields = "__all__"


class PublicFormPrioritizationViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = FormPrioritizationSerializer
    queryset = FormPrioritization.objects.all()
    ordering_fields = "__all__"


class PerOptionsView(views.APIView):
    permission_classes = [permissions.IsAuthenticated, DenyGuestUserPermission]
    ordering_fields = "__all__"

    @extend_schema(request=None, responses=PerOptionsSerializer)
    def get(self, request, version=None):
        return response.Response(
            PerOptionsSerializer(
                dict(
                    componentratings=PerComponentRating.objects.all().order_by("value"),
                    answers=FormAnswer.objects.all(),
                    overviewassessmenttypes=AssessmentType.objects.all(),
                )
            ).data
        )


class PerProcessStatusViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = PerProcessSerializer
    filterset_class = PerOverviewFilter
    permission_classes = [permissions.IsAuthenticated, DenyGuestUserPermission]
    ordering_fields = "__all__"
    get_request_user_regions = RegionRestrictedAdmin.get_request_user_regions
    get_filtered_queryset = RegionRestrictedAdmin.get_filtered_queryset

    def get_queryset(self):
        queryset = Overview.objects.order_by("country", "-assessment_number", "-date_of_assessment")
        return self.get_filtered_queryset(self.request, queryset, dispatch=0)


class PublicPerProcessStatusViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = PublicPerProcessSerializer
    filterset_class = PerOverviewFilter
    ordering_fields = "__all__"

    def get_queryset(self):
        return Overview.objects.order_by("country", "-assessment_number", "-date_of_assessment")


class FormAssessmentViewSet(viewsets.ModelViewSet):
    serializer_class = PerAssessmentSerializer
    permission_classes = [permissions.IsAuthenticated, PerGeneralPermission, DenyGuestUserPermission]
    ordering_fields = "__all__"

    def get_queryset(self):
        return PerAssessment.objects.select_related("overview")


class PublicFormAssessmentViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = PublicPerAssessmentSerializer
    ordering_fields = "__all__"

    def get_queryset(self):
        return PerAssessment.objects.select_related("overview")


class PerFileViewSet(mixins.ListModelMixin, mixins.CreateModelMixin, viewsets.GenericViewSet):
    permission_classes = [permissions.IsAuthenticated, DenyGuestUserPermission]
    serializer_class = PerFileSerializer

    def get_queryset(self):
        if self.request is None:
            return PerFile.objects.none()
        return PerFile.objects.filter(created_by=self.request.user)

    @extend_schema(request=PerFileInputSerializer, responses=PerFileSerializer(many=True))
    @action(
        detail=False,
        url_path="multiple",
        methods=["POST"],
        permission_classes=[permissions.IsAuthenticated, DenyGuestUserPermission],
    )
    def multiple_file(self, request, pk=None, version=None):
        # converts querydict to original dict
        files = [files[0] for files in dict((request.data).lists()).values()]
        data = [{"file": file} for file in files]
        file_serializer = PerFileSerializer(data=data, context={"request": request}, many=True)
        if file_serializer.is_valid():
            file_serializer.save()
            return response.Response(file_serializer.data, status=drf_status.HTTP_201_CREATED)
        return response.Response(file_serializer.errors, status=drf_status.HTTP_400_BAD_REQUEST)


class PerCountryViewSet(viewsets.ReadOnlyModelViewSet):
    def get_serializer_class(self):
        user = self.request.user
        if not user.is_authenticated:
            return PublicPerCountrySerializer
        else:
            return UserPerCountrySerializer

    def get_queryset(self):
        country_id = self.request.GET.get("country_id", None)
        if country_id:
            return (
                Overview.objects.select_related("country", "type_of_assessment")
                .filter(country_id=country_id)
                .order_by("-created_at")[:1]
            )
        return Overview.objects.none()


class PerAggregatedViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = PerProcessSerializer
    filterset_class = PerOverviewFilter
    permission_classes = [permissions.IsAuthenticated, DenyGuestUserPermission]
    ordering_fields = ["assessment_number", "phase", "date_of_assessment"]
    get_request_user_regions = RegionRestrictedAdmin.get_request_user_regions
    get_filtered_queryset = RegionRestrictedAdmin.get_filtered_queryset

    def get_queryset(self):
        queryset = Overview.objects.filter(
            id__in=Overview.objects.order_by("country_id", "-assessment_number").distinct("country_id").values("id")
        )
        return self.get_filtered_queryset(self.request, queryset, dispatch=0)


class OpsLearningFilter(filters.FilterSet):
    type_validated = filters.NumberFilter(field_name="type_validated", lookup_expr="exact")
    appeal_document_id = filters.NumberFilter(field_name="appeal_document_id", lookup_expr="exact")
    organization_validated__in = filters.ModelMultipleChoiceFilter(
        label="validated_organizations",
        field_name="organization_validated",
        help_text="Organization GO identifiers, comma separated",
        widget=CSVWidget,
        queryset=OrganizationTypes.objects.all(),
    )
    sector_validated__in = filters.ModelMultipleChoiceFilter(
        label="validated_sectors",
        field_name="sector_validated",
        help_text="Sector identifiers, comma separated",
        widget=CSVWidget,
        queryset=SectorTag.objects.all(),
    )
    per_component_validated__in = filters.ModelMultipleChoiceFilter(
        label="validated_per_components",
        field_name="per_component_validated",
        help_text="PER Component identifiers, comma separated",
        widget=CSVWidget,
        queryset=FormComponent.objects.all(),
    )
    insight_id = filters.NumberFilter(
        label="Base Insight id for used extracts",
        method="get_cache_response",
    )
    insight_sector_id = filters.NumberFilter(label="Sector insight id for used extracts", method="get_cache_response_sector")
    insight_component_id = filters.NumberFilter(
        label="Component insight id for used extracts",
        method="get_cache_response_component",
    )
    # NOTE: overriding the fields for the typing issue
    sector_validated = filters.NumberFilter(field_name="sector_validated", lookup_expr="exact")
    per_component_validated = filters.NumberFilter(field_name="per_component_validated", lookup_expr="exact")
    # NOTE: this field is used in summary generation
    search_extracts = filters.CharFilter(method="get_filter_search_extracts")

    class Meta:
        model = OpsLearning
        fields = {
            "id": ("exact", "in"),
            "created_at": ("exact", "gt", "gte", "lt", "lte"),
            "modified_at": ("exact", "gt", "gte", "lt", "lte"),
            "is_validated": ("exact",),
            "learning": ("exact", "icontains"),
            "learning_validated": ("exact", "icontains"),
            "type_validated": ("exact", "in"),
            "organization_validated": ("exact",),
            "organization_validated__title": ("exact", "in"),
            "appeal_code": ("exact", "in"),
            "appeal_code__code": ("exact", "icontains", "in"),
            "appeal_code__num_beneficiaries": ("exact", "gt", "gte", "lt", "lte"),
            "appeal_code__start_date": ("exact", "gt", "gte", "lt", "lte"),
            "appeal_code__dtype": ("exact", "in"),
            "appeal_code__atype": ("exact", "in"),
            "appeal_code__country": ("exact", "in"),
            "appeal_code__country__name": ("exact", "in"),
            "appeal_code__country__iso": ("exact", "in"),
            "appeal_code__country__iso3": ("exact", "in"),
            "appeal_code__region": ("exact", "in"),
        }

    def get_cache_response(self, queryset, name, value):
        if value and (ops_learning_cache_response := OpsLearningCacheResponse.objects.filter(id=value).first()):
            return queryset.filter(id__in=ops_learning_cache_response.used_ops_learning.all())
        return queryset

    def get_cache_response_sector(self, queryset, name, value):
        if value and (ops_learning_sector_cache_response := OpsLearningSectorCacheResponse.objects.filter(id=value).first()):
            return queryset.filter(id__in=ops_learning_sector_cache_response.used_ops_learning.all())
        return queryset

    def get_cache_response_component(self, queryset, name, value):
        if value and (
            ops_learning_component_cache_response := OpsLearningComponentCacheResponse.objects.filter(id=value).first()
        ):
            return queryset.filter(id__in=ops_learning_component_cache_response.used_ops_learning.all())
        return queryset

    def get_filter_search_extracts(self, queryset, name, value):
        return queryset.filter(
            Q(learning__icontains=value)
            | Q(learning_validated__icontains=value)
            | Q(appeal_code__name__icontains=value)
            | Q(appeal_code__code__icontains=value)
        )


class OpsLearningViewset(viewsets.ModelViewSet):
    """
    A simple ViewSet for viewing and editing OpsLearning records.
    """

    queryset = OpsLearning.objects.all()
    permission_classes = [DenyGuestUserMutationPermission, OpsLearningPermission]
    filterset_class = OpsLearningFilter
    search_fields = (
        "learning",
        "learning_validated",
        "appeal_code__code",
        "appeal_code__name",
        "appeal_code__name_en",
        "appeal_code__name_es",
        "appeal_code__name_fr",
        "appeal_code__name_ar",
    )

    def get_renderers(self):
        serializer = self.get_serializer()
        if isinstance(serializer, OpsLearningCSVSerializer):
            return [NarrowCSVRenderer()]
        return [renderer() for renderer in tuple(api_settings.DEFAULT_RENDERER_CLASSES)]

    def get_queryset(self):
        qs = super().get_queryset()
        if OpsLearning.is_user_admin(self.request.user):
            return qs.select_related(
                "appeal_code",
            ).prefetch_related(
                "sector",
                "sector_validated",
                "organization",
                "organization_validated",
                "per_component",
                "per_component_validated",
                "appeal_code__event__countries_for_preview",
            )
        return (
            qs.filter(is_validated=True)
            .select_related(
                "appeal_code",
            )
            .prefetch_related(
                "sector",
                "sector_validated",
                "organization",
                "organization_validated",
                "per_component",
                "per_component_validated",
                "appeal_code__event__countries_for_preview",
            )
        )

    def get_serializer_class(self):
        if self.request.method == "GET":
            request_format = self.request.GET.get("format", "json")
            if request_format == "csv":
                return OpsLearningCSVSerializer
            elif OpsLearning.is_user_admin(self.request.user):
                return OpsLearningSerializer
            return PublicOpsLearningSerializer
        return OpsLearningInSerializer

    def get_renderer_context(self):
        context = super().get_renderer_context()
        # Force the order from the serializer. Otherwise redundant literal list

        original = [
            "id",
            "appeal_code.code",
            "appeal_code.name",
            "appeal_code.atype",
            "learning",
            "finding",
            "sector",
            "per_component",
            "organization",
            "appeal_code.country",
            "appeal_code.country_name",
            "appeal_code.region",
            "appeal_code.region_name",
            "appeal_code.dtype",
            "appeal_code.start_date",
            "appeal_code.num_beneficiaries",
            "modified_at",
        ]
        displayed = [
            "id",
            "appeal_code",
            "appeal_name",
            "appeal_type",
            "learning",
            "finding",
            "sector",
            "component",
            "organization",
            "country_id",
            "country_name",
            "region_id",
            "region_name",
            "dtype_name",
            "appeal_year",
            "appeal_num_beneficiaries",
            "modified_at",
        ]

        context["header"] = original
        context["labels"] = {i: i for i in context["header"]}
        # We can change the column titles (called "labels"):
        for i, label in enumerate(displayed):
            context["labels"][original[i]] = label
        context["bom"] = True

        return context

    @extend_schema(
        request=None,
        filters=False,
        responses=OpsLearningOrganizationTypeSerializer(many=True),
    )
    @action(
        detail=False,
        methods=["GET"],
        permission_classes=[DenyGuestUserMutationPermission, OpsLearningPermission],
        serializer_class=OpsLearningOrganizationTypeSerializer,
        url_path="organization-type",
    )
    def organization(self, request):
        """
        Get the Organization Types
        """
        queryset = OrganizationTypes.objects.exclude(is_deprecated=True)
        serializer = OpsLearningOrganizationTypeSerializer(queryset, many=True)
        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = OpsLearningOrganizationTypeSerializer(page, many=True)
            return self.get_paginated_response(serializer.data)
        return Response(serializer.data)

    @extend_schema(
        request=None,
        filters=True,
        responses=OpsLearningSummarySerializer,
    )
    @action(
        detail=False,
        methods=["GET"],
        permission_classes=[DenyGuestUserMutationPermission, OpsLearningPermission],
        url_path="summary",
    )
    def summary(self, request):
        """
        Get the Ops Learning Summary based on the filters
        """
        ops_learning_summary_instance, filter_data = OpslearningSummaryCacheHelper.get_or_create(request, [self.filterset_class])
        if ops_learning_summary_instance.status == OpsLearningCacheResponse.Status.SUCCESS:
            return response.Response(OpsLearningSummarySerializer(ops_learning_summary_instance).data)

        requested_lang = django_get_language()
        transaction.on_commit(
            lambda: generate_summary.delay(
                ops_learning_summary_id=ops_learning_summary_instance.id,
                filter_data=filter_data,
                translation_lazy=requested_lang == "en",
            )
        )
        return response.Response(OpsLearningSummarySerializer(ops_learning_summary_instance).data)

    @extend_schema(
        request=None,
        filters=True,
        responses=OpsLearningStatSerializer,
    )
    @action(
        detail=False,
        methods=["GET"],
        permission_classes=[DenyGuestUserMutationPermission, OpsLearningPermission],
        url_path="stats",
    )
    def stats(self, request):
        """
        Get the Ops Learning stats based on the filters
        """
        queryset = self.filter_queryset(self.get_queryset()).filter(is_validated=True)
        ops_data = queryset.aggregate(
            operations_included=Count("appeal_code", distinct=True),
            learning_extracts=Count("id", distinct=True),
            sector_covered=Count("sector_validated", distinct=True),
            source_used=Count("appeal_document_id", distinct=True),
        )

        learning_by_sector_qs = (
            SectorTag.objects.filter(validated_sectors__in=queryset, title__isnull=False)
            .annotate(sector_id=F("id"), count=Count("validated_sectors", distinct=True))
            .values("sector_id", "title", "count")
        )

        # NOTE: Queryset is unbounded, we may need to add some start_date filter.
        sources_overtime_qs = (
            queryset.filter(appeal_document_id__isnull=False)
            .annotate(
                atype=F("appeal_code__atype"),
                date=F("appeal_code__start_date"),
                count=Count("appeal_document_id", distinct=True),
            )
            .values("atype", "date", "count")
        )

        learning_by_region_qs = (
            Region.objects.filter(appeal__opslearning__in=queryset)
            .annotate(
                region_id=F("id"),
                region_name=F("label"),
                count=Count("appeal__opslearning", distinct=True),
            )
            .values("region_id", "region_name", "count")
        )

        learning_by_country_qs = (
            Country.objects.filter(appeal__opslearning__in=queryset)
            .annotate(
                country_id=F("id"),
                country_name=F("name"),
                count=Count("appeal__opslearning", distinct=True),
            )
            .values("country_id", "country_name", "count")
        )

        data = {
            "operations_included": ops_data["operations_included"],
            "learning_extracts": ops_data["learning_extracts"],
            "sectors_covered": ops_data["sector_covered"],
            "sources_used": ops_data["source_used"],
            "learning_by_region": learning_by_region_qs,
            "learning_by_sector": learning_by_sector_qs,
            "sources_overtime": sources_overtime_qs,
            "learning_by_country": learning_by_country_qs,
        }
        return response.Response(OpsLearningStatSerializer(data).data)


class PerDocumentUploadViewSet(viewsets.ModelViewSet):
    queryset = PerDocumentUpload.objects.all()
    serializer_class = PerDocumentUploadSerializer
    filterset_class = PerDocumentFilter
    permission_classes = [permissions.IsAuthenticated, PerDocumentUploadPermission, DenyGuestUserPermission]

    def get_queryset(self):
        queryset = super().get_queryset()
        user = self.request.user
        return filter_per_queryset_by_user_access(user, queryset)

# -------------------------------------------------------------------
# implementation of flow - 1 
# -------------------------------------------------------------------


class IFRCEventListView(views.APIView):
    """API view for fetching and enriching IFRC event data with operational learning insights."""
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.azure_client = AzureServiceClient()
    
    def get(self, request) -> Response:
        """Handle GET requests for IFRC event data."""
        # Validate request parameters
        validation_response = self._validate_request_params(request)
        if validation_response:
            return validation_response
        
        country_id = int(request.query_params.get('country'))
        disaster_type_id = int(request.query_params.get('disaster_type'))
        
        # Fetch data from external APIs
        events_result = self._fetch_events(country_id, disaster_type_id)
        if self._is_error_response(events_result):
            return self._create_error_response(events_result)
        
        ops_learning_result = self._fetch_ops_learning(country_id, disaster_type_id)
        if self._is_error_response(ops_learning_result):
            return self._create_error_response(ops_learning_result)
        
        # Process and structure the data
        structured_data = self._join_events_and_learning(
            events_result.get('results', []),
            ops_learning_result.get('results', [])
        )
        
        # Generate AI summary if Azure client is available
        ai_summary = self._generate_ai_summary(structured_data)
        
        return Response({
            'ai_structured_summary': ai_summary
        }, status=drf_status.HTTP_200_OK)
    
    def _validate_request_params(self, request) -> Optional[Response]:
        """Validate required query parameters."""
        country = request.query_params.get('country')
        disaster_type = request.query_params.get('disaster_type')
        
        if not country or not disaster_type:
            return Response(
                {'detail': 'Both "country" and "disaster_type" query parameters are required.'},
                status=drf_status.HTTP_400_BAD_REQUEST
            )
        
        try:
            int(country)
            int(disaster_type)
        except ValueError:
            return Response(
                {'detail': '"country" and "disaster_type" must be integer IDs.'},
                status=drf_status.HTTP_400_BAD_REQUEST
            )
        
        return None
    
    def _is_error_response(self, result: Dict[str, Any]) -> bool:
        """Check if API response contains an error."""
        return 'error' in result
    
    def _create_error_response(self, error_result: Dict[str, Any]) -> Response:
        """Create error response from API result."""
        return Response(
            error_result, 
            status=error_result.get('status', drf_status.HTTP_502_BAD_GATEWAY)
        )
    
    def _fetch_events(self, country_id: int, disaster_type_id: int) -> Dict[str, Any]:
        """Fetch events from IFRC API."""
        api_url = 'https://goadmin.ifrc.org/api/v2/event/'
        # Try different parameter names for country filtering
        params = {'countries__in': country_id, 'dtype': disaster_type_id, 'limit': 5}
        
        return self._make_api_request(api_url, params, 'events')
    
    def _fetch_ops_learning(self, country_id: int, disaster_type_id: int) -> Dict[str, Any]:
        """Fetch operational learning data from IFRC API and filter by country + dtype."""
        api_url = 'https://goadmin.ifrc.org/api/v2/ops-learning/'
        params = {
            'is_validated': 'true',
            'limit': 100
        }

        ops_learning_data = self._make_api_request(api_url, params, 'ops learning')

        # 🔍 Filter locally
        filtered = [
            item for item in ops_learning_data.get('results', [])
            if item.get('appeal', {}).get('country') == country_id
            and item.get('appeal', {}).get('event_details', {}).get('dtype') == disaster_type_id
        ]

        print(f"=== DEBUG: Filtered ops learning count: {len(filtered)} ===")

        return {'results': filtered}
    
    def _make_api_request(self, url: str, params: Dict[str, Any], data_type: str) -> Dict[str, Any]:
        """Make HTTP request to external API with error handling."""
        try:
            print(f"=== DEBUG: Making {data_type} request to {url} with params: {params} ===")
            with httpx.Client(timeout=10.0) as client:
                response = client.get(url, params=params)
                response.raise_for_status()
            
            results = response.json().get('results', [])
            print(f"=== DEBUG: {data_type} request returned {len(results)} results ===")
            
            # Debug: Print country info for each event
            if results and data_type == 'events':
                for i, event in enumerate(results):
                    countries = event.get('countries', [])
                    primary_country = countries[0] if countries else {}
                    print(f"=== DEBUG: Event {i+1}: Country: {primary_country.get('name')} (ID: {primary_country.get('id')}) ===")
                    print(f"=== DEBUG: Event {i+1} details: {event.get('name')} - Countries count: {len(countries)} ===")
            
            return {'results': results}
        except (httpx.RequestError, httpx.HTTPStatusError, ValueError) as exc:
            print(f"=== DEBUG: Error in {data_type} request: {exc} ===")
            return {
                'error': True,
                'detail': f'Error fetching {data_type}: {exc}',
                'status': drf_status.HTTP_502_BAD_GATEWAY
            }
    
    def _join_events_and_learning(self, events: List[Dict], ops_learning: List[Dict]) -> List[Dict]:
        """Combine events with operational learning data."""
        # Index learning data by appeal code
        learning_by_appeal = self._index_learning_by_appeal(ops_learning)
        
        # Process events and link with learning data
        structured_data = []
        for event in events:
            event_data = self._create_event_structure(event)
            self._link_appeals_and_learning(event_data, event, learning_by_appeal)
            structured_data.append(event_data)
        
        # Add orphaned learning entries
        self._add_orphaned_learning(structured_data, learning_by_appeal)
        
        return structured_data
    
    def _index_learning_by_appeal(self, ops_learning: List[Dict]) -> Dict[str, List[Dict]]:
        """Create index of learning entries by appeal code."""
        learning_by_appeal = {}
        
        for learning in ops_learning:
            appeal_code = learning.get('appeal_code')
            if not appeal_code:
                continue
            
            learning_entry = self._create_learning_entry(learning)
            learning_by_appeal.setdefault(appeal_code, []).append(learning_entry)
        
        return learning_by_appeal
    
    def _create_learning_entry(self, learning: Dict) -> Dict:
        """Create structured learning entry."""
        return {
            'id': learning.get('id'),
            'learning_text': learning.get('learning_validated_en', learning.get('learning_en')),
            'document_name': learning.get('document_name'),
            'document_url': learning.get('document_url'),
            'sector_validated': learning.get('sector_validated'),
            'organization_validated': learning.get('organization_validated'),
            'type_validated': learning.get('type_validated'),
            'created_at': learning.get('created_at'),
            'modified_at': learning.get('modified_at')
        }
    
    def _create_event_structure(self, event: Dict) -> Dict:
        """Create structured event data."""
        # Handle countries array structure
        countries = event.get('countries', [])
        primary_country = countries[0] if countries else {}
        
        return {
            'event_id': event.get('id'),
            'event_name': event.get('name'),
            'event_slug': event.get('slug'),
            'summary': self._create_event_summary(event),
            'description': event.get('summary', ''),
            'disaster_type': {
                'id': event.get('dtype'), 
                'name': event.get('dtype_name')
            },
            'country': {
                'id': primary_country.get('id'), 
                'name': primary_country.get('name')
            },
            'start_date': event.get('start_date'),
            'num_affected': event.get('num_affected'),
            'ifrc_severity_level': event.get('ifrc_severity_level'),
            'appeals': [],
            'related_ops_learning': []
        }
    
    def _create_event_summary(self, event: Dict) -> str:
        """Create formatted event summary."""
        name = event.get('name', 'Unknown Event')
        country = event.get('country_name', 'Unknown Country')
        date = event.get('start_date', 'an unknown date')
        return f"{name} in {country} on {date}"
    
    def _link_appeals_and_learning(self, event_data: Dict, event: Dict, learning_by_appeal: Dict):
        """Link appeals and learning data to event."""
        appeals = event.get('appeals', [])
        
        for appeal in appeals:
            appeal_code = appeal.get('code')
            appeal_data = self._create_appeal_structure(appeal, learning_by_appeal.get(appeal_code, []))
            event_data['appeals'].append(appeal_data)
            
            # Add learning entries to event
            if appeal_code in learning_by_appeal:
                event_data['related_ops_learning'].extend(learning_by_appeal[appeal_code])
        
        # Remove duplicate learning entries
        event_data['related_ops_learning'] = self._deduplicate_learning(
            event_data['related_ops_learning']
        )
    
    def _create_appeal_structure(self, appeal: Dict, ops_learning: List[Dict]) -> Dict:
        """Create structured appeal data."""
        return {
            'appeal_id': appeal.get('id'),
            'appeal_code': appeal.get('code'),
            'appeal_name': appeal.get('name'),
            'appeal_type': appeal.get('atype'),
            'start_date': appeal.get('start_date'),
            'end_date': appeal.get('end_date'),
            'amount_requested': appeal.get('amount_requested'),
            'amount_funded': appeal.get('amount_funded'),
            'ops_learning': ops_learning
        }
    
    def _deduplicate_learning(self, learning_entries: List[Dict]) -> List[Dict]:
        """Remove duplicate learning entries based on ID."""
        seen_ids = set()
        unique_learning = []
        
        for entry in learning_entries:
            entry_id = entry.get('id')
            if entry_id not in seen_ids:
                seen_ids.add(entry_id)
                unique_learning.append(entry)
        
        return unique_learning
    
    def _add_orphaned_learning(self, structured_data: List[Dict], learning_by_appeal: Dict):
        """Add learning entries that couldn't be linked to specific events."""
        # Find all linked appeal codes
        linked_codes = {
            appeal['appeal_code'] 
            for event in structured_data 
            for appeal in event['appeals']
        }
        
        # Collect orphaned learning entries
        orphaned_learning = []
        for appeal_code, learning_entries in learning_by_appeal.items():
            if appeal_code not in linked_codes:
                for learning in learning_entries:
                    learning['appeal_code'] = appeal_code
                    orphaned_learning.append(learning)
        
        # Add orphaned entries as separate event
        if orphaned_learning:
            orphaned_event = self._create_orphaned_event_structure(orphaned_learning)
            structured_data.append(orphaned_event)
    
    def _create_orphaned_event_structure(self, orphaned_learning: List[Dict]) -> Dict:
        """Create structure for orphaned learning entries."""
        return {
            'event_id': None,
            'event_name': 'Unlinked Ops Learning',
            'event_slug': None,
            'summary': 'Ops learning entries that could not be linked to specific events',
            'description': '',
            'disaster_type': None,
            'country': None,
            'start_date': None,
            'num_affected': None,
            'ifrc_severity_level': None,
            'appeals': [],
            'related_ops_learning': orphaned_learning
        }
    
    def _generate_ai_summary(self, structured_data: List[Dict]) -> Optional[str]:
        """Generate AI-powered summary using Azure OpenAI."""
        print(f"=== DEBUG: Azure client configured: {self.azure_client.openai_client is not None} ===")
        
        if not self.azure_client.openai_client:
            print("=== DEBUG: Azure OpenAI not configured - skipping enrichment ===")
            print("=== DEBUG: Environment variables needed: AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_KEY, AZURE_OPENAI_DEPLOYMENT_NAME ===")
            return None
        
        print("=== DEBUG: Processing data with Azure OpenAI... ===")
        
        # Combine all text content
        all_summaries = [event.get('summary', '') for event in structured_data]
        all_descriptions = [event.get('description', '') for event in structured_data]
        all_learnings = []
        all_learning_texts = []
        
        for event in structured_data:
            learning_entries = event.get('related_ops_learning', [])
            all_learnings.extend(learning_entries)
            
            # Extract learning text content
            for learning in learning_entries:
                learning_text = learning.get('learning_text', '')
                if learning_text:
                    all_learning_texts.append(learning_text)
        
        combined_summary = "\n".join(all_summaries)
        combined_description = "\n".join(all_descriptions)
        combined_learning_text = "\n".join(all_learning_texts)
        
        return self.azure_client.get_structured_summary(
            combined_summary, combined_description, all_learnings
        )