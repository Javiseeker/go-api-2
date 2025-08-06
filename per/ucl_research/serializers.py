"""
Serializers for UCL Research Views
==================================

DRF serializers for the 4 UCL research API endpoints.
Extracted from per.serializers for standalone functionality.
"""

from rest_framework import serializers


class PerDrefLLMSummaryIndicatorSerializer(serializers.Serializer):
    """Serializer for DREF indicators"""
    title = serializers.CharField(required=False, allow_blank=True)
    people_targeted = serializers.IntegerField(required=False, allow_null=True)


class PerDrefLLMSummaryFutureActionSerializer(serializers.Serializer):
    """Serializer for future actions in DREF summaries"""
    indicators = PerDrefLLMSummaryIndicatorSerializer(many=True, required=False)
    budget = serializers.DecimalField(max_digits=15, decimal_places=2, required=False, allow_null=True)
    people_targeted_total = serializers.IntegerField(required=False, allow_null=True)
    intervention_summary = serializers.CharField(required=False, allow_blank=True)


class PerDrefLLMSummarySectorSerializer(serializers.Serializer):
    """Serializer for sector-based summaries"""
    title = serializers.CharField(required=False, allow_blank=True)
    title_display = serializers.CharField(required=False, allow_blank=True)
    needs_summary = serializers.CharField(required=False, allow_blank=True)
    future_actions = PerDrefLLMSummaryFutureActionSerializer(many=True, required=False)


class PerDrefLLMSummaryMetadataSerializer(serializers.Serializer):
    """Serializer for DREF metadata"""
    dref_id = serializers.IntegerField(required=False, allow_null=True)
    dref_title = serializers.CharField(required=False, allow_blank=True)
    dref_appeal_code = serializers.CharField(required=False, allow_blank=True)
    dref_date = serializers.DateField(required=False, allow_null=True)
    dref_created_at = serializers.DateTimeField(required=False, allow_null=True)
    dref_budget_file = serializers.CharField(required=False, allow_blank=True)
    dref_op_update_number = serializers.IntegerField(required=False, allow_null=True)


class PerDrefLLMSummarySerializer(serializers.Serializer):
    """
    DTO for PerDrefLLMSummaryView response matching sector_object.json structure.
    
    operational_summary: Short operational objectives and strategy summary
    sectors: List of sector-based summaries with needs, actions, and future plans
    dref_type: Type of DREF operation
    dref_onset: Type of disaster onset
    metadata: Additional information about the DREF
    """
    operational_summary = serializers.CharField(required=False, allow_blank=True)
    sectors = PerDrefLLMSummarySectorSerializer(many=True, required=False)
    dref_type = serializers.CharField(required=False, allow_blank=True)
    dref_onset = serializers.CharField(required=False, allow_blank=True)
    metadata = PerDrefLLMSummaryMetadataSerializer(required=False)


class PerDrefSituationalOverviewMetadataSerializer(serializers.Serializer):
    """Serializer for DREF situational overview metadata - focuses on event and operational context"""
    # Event-focused information (primary for situational overview)
    event_id = serializers.IntegerField(required=False, allow_null=True)
    event_name = serializers.CharField(required=False, allow_blank=True)
    disaster_type = serializers.CharField(required=False, allow_blank=True)
    country = serializers.CharField(required=False, allow_blank=True)
    
    # Operational update context (key for understanding situation changes)
    latest_update_number = serializers.IntegerField(required=False, allow_null=True)
    total_operational_updates = serializers.IntegerField(required=False, allow_null=True)
    
    # Basic DREF information (minimal, for reference)
    dref_id = serializers.IntegerField(required=False, allow_null=True)
    dref_title = serializers.CharField(required=False, allow_blank=True)
    dref_appeal_code = serializers.CharField(required=False, allow_blank=True)
    dref_date = serializers.DateField(required=False, allow_null=True)


class PerDrefSituationalOverviewSerializer(serializers.Serializer):
    """
    DTO for PerDrefSituationalOverviewView response.
    
    situational_overview: 5-line paragraph summarizing the event situation
    metadata: Event and DREF context information
    """
    situational_overview = serializers.CharField(required=False, allow_blank=True)
    metadata = PerDrefSituationalOverviewMetadataSerializer(required=False)


class IFRCEventLearningSerializer(serializers.Serializer):
    """Serializer for operational learning entries in IFRC events"""
    id = serializers.IntegerField()
    learning_text = serializers.CharField()
    document_name = serializers.CharField(required=False, allow_blank=True)
    document_url = serializers.URLField(required=False, allow_blank=True)
    sector_validated = serializers.CharField(required=False, allow_blank=True)
    organization_validated = serializers.CharField(required=False, allow_blank=True)
    type_validated = serializers.CharField(required=False, allow_blank=True)
    created_at = serializers.DateTimeField(required=False, allow_null=True)
    modified_at = serializers.DateTimeField(required=False, allow_null=True)
    appeal_code = serializers.CharField(required=False, allow_blank=True)
    appeal_name = serializers.CharField(required=False, allow_blank=True)
    event_id = serializers.IntegerField(required=False, allow_null=True)
    source_note = serializers.CharField(required=False, allow_blank=True)


class IFRCEventSummarySerializer(serializers.Serializer):
    """Serializer for IFRC event AI structured summary"""
    ai_structured_summary = serializers.ListField(
        child=serializers.DictField(),
        required=False
    )
    fallback_note = serializers.CharField(required=False, allow_blank=True)


class RRCapacityQuestionsResponseSerializer(serializers.Serializer):
    """Serializer for RR Capacity Questions response"""
    file_url = serializers.URLField()
    cached = serializers.BooleanField(default=False)