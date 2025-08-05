# UCL Research - Operational Learning Summary APIs

## Overview

This module contains unified API views for generating operational learning summaries and insights from IFRC humanitarian operations data. All views use modern async HTTP requests via httpx and include enhanced error handling, caching, and performance monitoring.

## Recent Improvements (Knowledge Transfer Session)

### Code Organization & Clean Architecture
- **Consolidated Caching**: Unified all caching mechanisms to use `BaseAITask` methods instead of multiple caching approaches
- **Task-Based Architecture**: Moved all business logic from views to dedicated task classes (`PreviousCrisesTask`, `DrefSummaryTask`, `RRCapacityTask`)
- **Enhanced Base Class**: Merged Azure OpenAI functionality into `BaseAITask` for better naming and inheritance structure
- **API Client Integration**: All HTTP requests moved to `IFRCAPIClient` for consistent async operations
- **Validation Consolidation**: All HTTP request validation consolidated in `BaseUCLView` with reusable validation methods

### File Organization
- **Resource Location**: Moved `rr_parsed_excel.json` to `ucl_research/` folder for better organization
- **Separation of Concerns**: Views now contain minimal logic and primarily handle HTTP request/response while task classes contain all business logic and AI processing

### Code Quality
- **Reduced Complexity**: Eliminated redundant classes and consolidated common functionality
- **Improved Maintainability**: Clear separation between HTTP handling (views) and business logic (tasks)
- **Consistent Patterns**: All endpoints now follow the same architectural patterns

## Architecture

```
per/ucl_research/
├── __init__.py                    # Module exports and public API
├── ops_learning_summary4.py       # Consolidated AI summary tasks & Azure OpenAI integration
├── ifrc_client.py                 # Unified async HTTP client
├── ucl_views.py                   # 4 unified API views (minimal logic, calls task classes)
├── rapid_response_parser.py       # RR capacity questions parser with Excel generation
├── blob_upload.py                 # Azure Blob Storage utility
├── serializers.py                 # DRF response serializers
├── rr_parsed_excel.json          # RR capacity questions template data
└── README.md                      # This documentation
```

## API Endpoints Overview

### Flow 1.1: IFRCEventListView - Previous Crises Insights
**Endpoint:** `GET /api/v1/ucl/previous-crises-insights/?country={country_id}&disaster_type={disaster_type_id}`

### Flow 1.2: RRCapacityQuestionsView - Rapid Response Excel File Generation
**Endpoint:** `GET /api/v1/ucl/rapid-response-capacity-questions/?country={country_id}&disaster_type={disaster_type_id}`

### Flow 2.1: PerDrefLLMSummaryView - DREF Economic Sectors
**Endpoint:** `GET /api/v1/ucl/dref-summary/?id={event_id}`

### Flow 2.2: PerDrefSituationalOverviewView - DREF Situational Overview
**Endpoint:** `GET /api/v1/ucl/dref-situational-overview/?id={event_id}`

---

# Technical Flow Documentation

## Flow 1.1: IFRCEventListView - Previous Crises Insights

**Purpose:** Enrich humanitarian operations with AI-synthesized operational learning insights from previous similar crises in specific countries and disaster types.

**Technical Architecture:** Multi-tier data fetching with intelligent fallback, AI synthesis engine, and Redis caching layer.

### Detailed Technical Request Flow

#### Phase 1: Input Validation & Parameter Processing
```python
# Request validation pipeline
country_param = request.query_params.get('country')     # Required: Country ID (int)
disaster_type_param = request.query_params.get('disaster_type')  # Required: Disaster Type ID (int)

# Type coercion and validation
try:
    country_id = int(country_param)
    disaster_type_id = int(disaster_type_param) 
except (ValueError, TypeError):
    return Response(400, "Parameters must be valid integers")
```

#### Phase 2: Intelligent Cache Layer
```python
# Redis cache key generation
cache_key = f"ifrc_events_summary:{country_id}:{disaster_type_id}"
cached_result = cache.get(cache_key)  # TTL: 3600 seconds

if cached_result:
    return Response({"ai_structured_summary": cached_result}, 200)
```

#### Phase 3: Multi-Tier Operational Learning Fetch Strategy
**Technical Implementation:** Hierarchical data fetching with deduplication

##### Stage 3.1: Primary Fetch (High Precision)
```python
# Primary API call: Country + Disaster Type specificity
async with IFRCAPIClient() as client:
    primary_learning = await client.get_ops_learning(
        country_id=country_id,
        disaster_type_id=disaster_type_id,
        is_validated="true",
        limit=6,
        appeal__event_details__dtype=disaster_type_id  # Nested filtering
    )
    
# Source attribution for transparency
primary_labeled = [
    {**learning, "source_note": "This insight was built off similar disasters from the same country."}
    for learning in primary_learning
]
```

##### Stage 3.2: Fallback Fetch (High Recall)
```python
# Conditional secondary fetch if insufficient primary data
if len(primary_learning) < 6:
    # Country-only fallback for broader insights
    all_country_learning = await client.get_ops_learning(
        country_id=country_id,
        disaster_type_id=None,  # Remove disaster type constraint
        is_validated="true",
        limit=20  # Larger pool for deduplication
    )
    
    # Deduplication by learning ID
    primary_ids = {p['id'] for p in primary_learning}
    secondary_learning = [l for l in all_country_learning if l['id'] not in primary_ids]
    
    secondary_labeled = [
        {**learning, "source_note": "This insight was built off other disasters from the same country."}
        for learning in secondary_learning
    ]
else:
    secondary_labeled = []
```

#### Phase 4: Data Synthesis & Structure Normalization
```python
# Combine multi-tier results with cap enforcement
combined_learning = (primary_labeled + secondary_labeled)[:6]

# Transform raw API data to structured learning entries
def create_learning_entry(learning_raw):
    return {
        'id': learning_raw.get('id'),
        'learning_text': learning_raw.get('learning_validated_en', learning_raw.get('learning_en')),
        'document_name': learning_raw.get('document_name'),
        'document_url': learning_raw.get('document_url'),
        'sector_validated': learning_raw.get('sector_validated'),
        'organization_validated': learning_raw.get('organization_validated'),
        'type_validated': learning_raw.get('type_validated'),
        'created_at': learning_raw.get('created_at'),
        'modified_at': learning_raw.get('modified_at'),
        'appeal_code': learning_raw.get('appeal_code'),
        'appeal_name': learning_raw.get('appeal', {}).get('name'),
        'event_id': learning_raw.get('appeal', {}).get('event_details', {}).get('id'),
        'source_note': learning_raw.get('source_note')  # Attribution preserved
    }

processed_learnings = [create_learning_entry(l) for l in combined_learning]
```

#### Phase 5: Azure OpenAI Synthesis Engine
```python
# AI-powered insight generation
ai_summary = self.azure_client.generate_summary([{
    "related_ops_learning": processed_learnings
}])

# AI processing pipeline:
# 1. Context analysis of 1-6 learning items
# 2. Pattern recognition across multiple disasters  
# 3. Actionable insight generation
# 4. Source traceability maintenance
# 5. Confidence scoring per insight
```

#### Phase 6: Cache Storage & Response Assembly
```python
# Store in Redis for future requests
cache.set(cache_key, ai_summary, timeout=3600)

# Final response structure
return Response({
    "ai_structured_summary": ai_summary  # List of structured insights
}, 200)
```

### Response Technical Structure
```json
{
    "ai_structured_summary": [
        {
            "id": 1,
            "title": "Early Warning Systems Effectiveness",
            "content": "Analysis of 3 flood operations in Bangladesh (2021-2023) shows early warning systems reduced casualty rates by 40% when implemented 48+ hours before peak flooding. Community-based warning networks proved most effective in rural areas.",
            "confidence_level": "high",
            "source_learnings": [
                {
                    "id": 1234,
                    "learning_text": "Early warning systems activated 48 hours before flooding significantly reduced casualties in Sylhet district",
                    "appeal_code": "MDRBD025",
                    "document_name": "Bangladesh Floods Evaluation 2023",
                    "source_note": "This insight was built off similar disasters from the same country"
                }
            ]
        }
    ]
}
```

---

## Flow 1.2: RRCapacityQuestionsView - Rapid Response Excel File Generation

**Purpose:** Generate AI-powered Excel reports pre-filled with contextual humanitarian capacity assessments using operational learning from similar historical responses.

**Technical Architecture:** Synchronous processing with async data pipeline, AI question processing, multi-sheet Excel generation, Azure Blob storage integration.

**Recent Fix:** Fixed coroutine JSON serialization error by converting `RapidResponseCapacityParser.process_rr_capacity_questions` from async to sync method using `asyncio.run()` for internal async operations.

### Detailed Technical Request Flow

#### Phase 1: Parameter Validation & Cache Check
```python
# Same validation as Flow 1.1
country_id, disaster_type_id = validate_country_disaster_params(request)

# Excel-specific cache key
cache_key = f"rr_capacity_excel:{country_id}:{disaster_type_id}"
cached_url = cache.get(cache_key)

if cached_url:
    return Response({"file_url": cached_url}, 200)
```

#### Phase 2: Questions Template Loading
```python
# Load pre-parsed capacity assessment framework
current_dir = os.path.dirname(os.path.abspath(__file__))
json_path = os.path.join(current_dir, 'rr_parsed_excel.json')
with open(json_path, 'r', encoding='utf-8') as f:
    questions_template = json.load(f)
    
# Template structure:
# {
#     "question_id": "RR_001",
#     "category": "Early Warning",
#     "question_text": "What early warning systems are available?",
#     "response_type": "text",
#     "ai_fillable": true,
#     "context_required": ["disaster_type", "country_context"]
# }
```

#### Phase 3: Two-Stage Operational Learning Data Acquisition
```python
# Using RapidResponseCapacityParser for dedicated processing
parser = RapidResponseCapacityParser()

# Stage 1: Precise matching (Country + Disaster Type) 
ops_learning_data, events_data = asyncio.run(
    parser._fetch_async_data(country_id, disaster_type_id)
)

# Internal two-stage approach:
# - Primary: Country + Disaster Type filtering (up to 10 results)
# - Secondary: Country-only fallback with disaster type validation
# - Deduplication by appeal_code to prevent duplicates
# - Source attribution for transparency
```

#### Phase 4: Event Context Extraction Pipeline
```python
async def fetch_events_from_ops_learning(client, ops_learning_data):
    events = []
    seen_event_ids = set()
    
    for learning in ops_learning_data:
        # Extract event ID from nested structure
        event_id = (learning.get('appeal', {})
                           .get('event_details', {})
                           .get('id'))
        
        if event_id and event_id not in seen_event_ids:
            seen_event_ids.add(event_id)
            
            # Async event detail fetch
            try:
                event = await client.get_event_detail(event_id)
                if event:
                    # Source attribution for traceability
                    event["source_note"] = f"Event from ops learning (Appeal: {learning.get('appeal', {}).get('code')}, Event ID: {event_id})"
                    event["appeal_source"] = learning.get('appeal', {}).get('code')
                    event["event_source_id"] = event_id
                    events.append(event)
            except Exception:
                continue  # Graceful degradation
    
    return events

events_data = await fetch_events_from_ops_learning(client, ops_learning_data)
```

#### Phase 5: AI-Powered Question Processing
```python
# Process questions and fill missing fields using RRCapacityTask
processed_questions = parser._process_questions(
    questions_data, events_data, ops_learning_data
)

# Internal processing for each question:
# - Check if "Notes on Response Capacity with sources" field is missing/null
# - Use RRCapacityTask.process_capacity_question() for AI generation
# - Update question with generated responses and source references
# - Handle processing failures gracefully with error messages
```

#### Phase 6: Multi-Sheet Excel Generation
```python
def create_rr_capacity_excel(processed_questions, country_id, disaster_type_id, events_data, ops_learning_data):
    from openpyxl import Workbook
    from openpyxl.styles import Font, Alignment, PatternFill
    
    wb = Workbook()
    
    # Main Sheet: RR Capacity Assessment with proper structure
    ws_main = wb.active
    ws_main.title = "RR Capacity Assessment"
    
    # Headers matching original Excel structure
    headers = [
        "Area", "Critical Questions", "Guiding/probing questions",
        "Notes on Response Capacity with sources", "Status",
        "Recommended actions for continuation of response", 
        "Examples of recommended actions", "References"
    ]
    
    # Area-based color coding and merged cells for same areas
    # Data population with AI-filled responses in "Notes on Response Capacity with sources"
    # Source attribution and references properly formatted
    
    # Sheet 2: Source Data Documentation
    ws_sources = wb.create_sheet("Source Data")
    ws_sources.append(["Data Type", "Count", "Details"])
    ws_sources.append(["Events", len(events_data), f"Historical events from country {country_id}"])
    ws_sources.append(["Ops Learning", len(ops_learning_data), f"Validated learning from disaster type {disaster_type_id}"])
    
    # Sheet 3: Processing Metadata
    ws_meta = wb.create_sheet("Processing Metadata")
    ws_meta.append(["Parameter", "Value"])
    ws_meta.append(["Country ID", country_id])
    ws_meta.append(["Disaster Type ID", disaster_type_id])
    ws_meta.append(["Processing Date", datetime.now().isoformat()])
    ws_meta.append(["AI Model", "Azure OpenAI GPT-4"])
    ws_meta.append(["Total Questions", len(processed_questions)])
    ws_meta.append(["AI-Filled Questions", sum(1 for q in processed_questions if q.get('ai_response'))])
    
    return wb
```

#### Phase 7: Azure Blob Storage & Caching
```python
# Generate unique filename
filename = f"rr_capacity_filled_{country_id}_{disaster_type_id}_{int(time.time())}.xlsx"

# Temporary file creation
with NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
    workbook.save(tmp.name)
    temp_path = tmp.name

# Upload to Azure Blob Storage
blob_url = upload_to_blob(temp_path, blob_name=filename)

# Cleanup temporary file
os.remove(temp_path)

# Cache URL for 1 hour
cache.set(cache_key, blob_url, timeout=3600)

return Response({"file_url": blob_url}, 200)
```

---

## Flow 2.1: PerDrefLLMSummaryView - DREF Economic Sectors

**Purpose:** Generate comprehensive DREF operation summaries with AI-powered economic sector analysis, financial planning insights, and intervention strategy recommendations.

**Technical Architecture:** Event-driven data pipeline, DREF data management system, sector-based AI analysis, structured JSON serialization.

### Detailed Technical Request Flow

#### Phase 1: Event ID Validation & Processing
```python
# Single parameter validation for event-based lookup
event_id_param = request.query_params.get("id")

if not event_id_param:
    return Response({"error": "Event ID is required"}, 400)

try:
    event_id = int(event_id_param)
except ValueError:
    return Response({"error": "Event ID must be a valid integer"}, 400)
```

#### Phase 2: Event Context Retrieval
```python
# Async event detail fetch
async with IFRCAPIClient() as client:
    event = await client.get_event_detail(event_id)
    
    if not event:
        return Response({"error": "Event not found"}, 404)
    
    # Field reports extraction for DREF linking
    field_reports = event.get("field_reports", [])
    
    if not field_reports:
        return Response({
            "error": "Field Reports not found",
            "event_id": event_id,
            "event_name": event.get("name")
        }, 404)
    
    field_report_ids = [fr['id'] for fr in field_reports]
```

#### Phase 3: DREF Data Management Pipeline
```python
# DREF filtering and retrieval
from per.dref_temp.dref_utils import dref_manager, DREFFilters

filters = DREFFilters(field_report_ids=field_report_ids)

# Multi-source DREF data attempt (hierarchical fallback)
dref_sources = ["basic", "op-update", "final-report"]
dref_data = None

for source in dref_sources:
    try:
        dref_data = dref_manager.get_data(source, filters)
        if dref_data:
            break
    except Exception as e:
        logger.warning(f"DREF source '{source}' unavailable: {e}")
        continue

if not dref_data:
    return Response({
        "error": "DREFs not found",
        "event_id": event_id,
        "event_name": event.get("name")
    }, 404)

# Get latest operational version
dref_data = dref_data[0]
latest_dref_version = dref_manager.get_latest_dref_version(dref_data)
```

#### Phase 4: DREF Economic Data Structuring
```python
# Comprehensive DREF dictionary creation with economic focus
dref_dict = {
    # Core identifiers
    'id': latest_dref_version.id,
    'title': latest_dref_version.title,
    
    # Economic & Financial Data
    'amount_requested': latest_dref_version.amount_requested,
    'total_targeted_population': latest_dref_version.total_targeted_population,
    'people_in_need': getattr(latest_dref_version, 'people_in_need', None),
    
    # Operational Context
    'operation_objective': getattr(latest_dref_version, 'operation_objective', None),
    'response_strategy': getattr(latest_dref_version, 'response_strategy', None),
    'operation_timeframe': getattr(latest_dref_version, 'operation_timeframe', None),
    
    # Geographic & Disaster Context
    'country_details': {
        'name': latest_dref_version.country_details.name if latest_dref_version.country_details else None,
        'iso': latest_dref_version.country_details.iso if latest_dref_version.country_details else None
    },
    'disaster_type_details': {
        'name': latest_dref_version.disaster_type_details.name if latest_dref_version.disaster_type_details else None
    },
    
    # Temporal Data
    'event_date': latest_dref_version.event_date,
    'end_date': getattr(latest_dref_version, 'end_date', None),
    
    # Sector-Specific Economic Data
    'planned_interventions': getattr(latest_dref_version, 'planned_interventions', []),
    'national_society_actions': getattr(latest_dref_version, 'national_society_actions', []),
    'needs_identified': getattr(latest_dref_version, 'needs_identified', []),
    
    # Capacity & Resources
    'human_resource': getattr(latest_dref_version, 'human_resource', None),
    'logistic_capacity_of_ns': getattr(latest_dref_version, 'logistic_capacity_of_ns', None),
    'pmer': getattr(latest_dref_version, 'pmer', None)
}
```

#### Phase 5: AI-Powered Sector Analysis Engine
```python
# Advanced sector-based AI processing
summaries = DrefSummaryTask.generate_dref_summaries(dref_dict)

# AI processing pipeline includes:
# 1. Operational summary generation (3-line executive summary)
# 2. Sector data organization with fuzzy matching
# 3. Economic analysis per sector (needs, budget allocation, cost-effectiveness)
# 4. Intervention planning with financial projections
# 5. Risk assessment and mitigation strategies
```

#### Phase 6: Economic Metadata Extraction
```python
# Operational update tracking for economic context
op_update_number = 1
operational_updates = getattr(latest_dref_version, 'operational_update_details', [])

if operational_updates and isinstance(operational_updates, list):
    first_update = operational_updates[0]
    op_update_number = getattr(first_update, 'operational_update_number', 1)
```

#### Phase 7: Structured Response Assembly
```python
# Economic sector-focused response structure
sectors_data = summaries.get("sectors", [])

summary_data = {
    # Executive Summary (AI-generated)
    "operational_summary": summaries.get("operational_summary", ""),
    
    # Sector-based Economic Analysis
    "sectors": sectors_data,  # Each sector includes budget analysis, needs assessment, intervention planning
    
    # DREF Classification
    "dref_type": latest_dref_version.type_of_dref_display if hasattr(latest_dref_version, 'type_of_dref_display') else "",
    "dref_onset": latest_dref_version.type_of_onset_display if hasattr(latest_dref_version, 'type_of_onset_display') else "",
    
    # Economic & Operational Metadata
    "metadata": {
        "dref_id": latest_dref_version.id,
        "dref_title": latest_dref_version.title,
        "dref_appeal_code": latest_dref_version.appeal_code,
        "dref_date": latest_dref_version.event_date,
        "dref_created_at": latest_dref_version.created_at if hasattr(latest_dref_version, 'created_at') else None,
        "dref_budget_file": getattr(latest_dref_version, 'budget_file_preview', None),
        "dref_op_update_number": op_update_number,
        
        # Economic indicators
        "total_budget": latest_dref_version.amount_requested,
        "cost_per_beneficiary": (latest_dref_version.amount_requested / latest_dref_version.total_targeted_population) if latest_dref_version.total_targeted_population else None,
        "operational_duration_days": None  # Could be calculated from operation_timeframe
    }
}

# DRF serialization
serializer = PerDrefLLMSummarySerializer(summary_data)
return Response(serializer.data, 200)
```

### Advanced Response Structure with Economic Focus
```json
{
    "operational_summary": "The operation aims to provide emergency assistance to 15,000 flood-affected people in Bangladesh through multi-sector interventions. The strategy prioritizes rapid deployment of cash transfers, emergency shelter, and WASH services through National Society networks. The 6-month operation targets vulnerable communities across 5 districts with CHF 890,000 focusing on immediate life-saving needs and early recovery.",
    
    "sectors": [
        {
            "title": "multi_purpose_cash",
            "title_display": "Multi-Purpose Cash Grants",
            "needs_summary": "15,000 displaced families require immediate cash assistance for basic needs including food, temporary shelter materials, and essential household items following severe flooding.",
            "future_actions": [
                {
                    "indicators": [
                        {"title": "Households receiving cash grants", "people_targeted": 3000}
                    ],
                    "budget": 450000.00,
                    "people_targeted_total": 15000,
                    "intervention_summary": "Unconditional cash transfers of CHF 150 per household will enable families to prioritize their most urgent needs while supporting local market recovery in flood-affected areas."
                }
            ]
        },
        {
            "title": "shelter",
            "title_display": "Emergency Shelter and Settlements",
            "needs_summary": "Critical shelter needs identified for 2,500 families whose homes were completely destroyed, requiring emergency shelter materials and technical support.",
            "future_actions": [
                {
                    "indicators": [
                        {"title": "Emergency shelter kits distributed", "people_targeted": 2500}
                    ],
                    "budget": 275000.00,
                    "people_targeted_total": 12500,
                    "intervention_summary": "Distribution of emergency shelter kits including tarpaulins, ropes, and basic tools will provide immediate weather protection while families rebuild their homes."
                }
            ]
        }
    ],
    
    "dref_type": "Sudden Onset Emergency",
    "dref_onset": "Rapid",
    
    "metadata": {
        "dref_id": 12345,
        "dref_title": "Bangladesh: Floods - Emergency Response",
        "dref_appeal_code": "MDRBD025",
        "dref_date": "2024-08-15",
        "dref_op_update_number": 2,
        "total_budget": 890000.00,
        "cost_per_beneficiary": 59.33,
        "sectors_count": 4,
        "primary_sectors": ["multi_purpose_cash", "shelter", "wash", "health"]
    }
}
```

---

## Flow 2.2: PerDrefSituationalOverviewView - DREF Situational Overview

**Purpose:** Generate precise 5-line situational overview paragraphs that synthesize DREF operational context, strategic rationale, and situational changes for decision-maker briefings.

**Technical Architecture:** Event-DREF linkage system, operational update analysis, AI-powered narrative synthesis, metadata-rich response structure.

### Detailed Technical Request Flow

#### Phase 1-3: Event Validation & DREF Resolution 
```python
# Identical to Flow 2.1 for event validation and DREF data retrieval
# Phases 1-3 maintain same technical implementation for consistency
event_id = validate_event_id(request)
event = await client.get_event_detail(event_id)
field_report_ids = extract_field_report_ids(event)
dref_data = resolve_dref_data(field_report_ids)
```

#### Phase 4: Latest Operational Update Analysis
```python
# Get most recent operational version for situational context
latest_dref_version = dref_manager.get_latest_dref_version(dref_data)

# Situational data extraction with fallback hierarchy
def extract_situational_data(dref_version):
    return {
        # Event Situation Data (Lines 1-3 of overview)
        'event_description': (
            getattr(dref_version, 'event_description', '') or 
            getattr(dref_version, 'description', '') or 
            getattr(dref_version, 'summary', '') or
            "Event description not available"
        ),
        'event_scope': (
            getattr(dref_version, 'event_scope', '') or 
            getattr(dref_version, 'scope_and_scale', '') or
            "Event scope information not available"
        ),
        
        # Operational Rationale (Lines 4-5 of overview)
        'operation_objective': getattr(dref_version, 'operation_objective', ''),
        'response_strategy': getattr(dref_version, 'response_strategy', ''),
        
        # Context Metadata
        'title': getattr(dref_version, 'title', ''),
        'operational_update_number': getattr(dref_version, 'operational_update_number', 0),
        'date_of_approval': getattr(dref_version, 'date_of_approval', None),
        
        # Geographic & Disaster Context
        'country_details': {
            'name': dref_version.country_details.name if dref_version.country_details else None,
            'iso': dref_version.country_details.iso if dref_version.country_details else None
        },
        'disaster_type_details': {
            'name': dref_version.disaster_type_details.name if dref_version.disaster_type_details else None
        }
    }

latest_update_dict = extract_situational_data(latest_dref_version)
```

#### Phase 5: AI-Powered Situational Narrative Synthesis
```python
# Specialized 5-line overview generation
situational_overview = DrefSummaryTask.generate_situational_overview(latest_update_dict)

# AI prompt engineering for situational overview:
# - Lines 1-3: Event situation (disaster context, affected areas, impact scale)
# - Lines 4-5: Operational rationale (why needed, strategic approach)
# - Ensure coherent narrative flow across all 5 lines
# - Include specific data points (numbers, locations, timeframes)
# - Maintain professional humanitarian language

if not situational_overview:
    return Response({
        "error": "Failed to generate situational overview",
        "dref_id": dref_data.id,
        "event_id": event_id
    }, 500)
```

#### Phase 6: Event-Centric Metadata Assembly
```python
# Metadata structure focused on situational context
response_data = {
    "situational_overview": situational_overview,
    
    "metadata": {
        # Event-focused information (primary context)
        "event_id": event_id,
        "event_name": event.get("name"),
        "disaster_type": latest_update_dict['disaster_type_details']['name'],
        "country": latest_update_dict['country_details']['name'],
        "country_iso": latest_update_dict['country_details']['iso'],
        
        # Operational update context (situational changes tracking)
        "latest_update_number": latest_update_dict.get('operational_update_number'),
        "total_operational_updates": len(getattr(dref_data, 'operational_update_details', [])),
        "has_operational_updates": len(getattr(dref_data, 'operational_update_details', [])) > 0,
        
        # DREF reference information (minimal for overview context)
        "dref_id": dref_data.id,
        "dref_title": getattr(dref_data, 'title', None),
        "dref_appeal_code": getattr(dref_data, 'appeal_code', None),
        "dref_date": getattr(dref_data, 'date_of_approval', None),
        
        # Situational timeline
        "event_date": event.get('start_date'),
        "dref_approval_date": latest_update_dict.get('date_of_approval'),
        "days_since_event": calculate_days_between(event.get('start_date'), datetime.now()) if event.get('start_date') else None
    }
}

# Specialized serialization for situational overview
serializer = PerDrefSituationalOverviewSerializer(response_data)
return Response(serializer.data, 200)
```

### Technical Response Structure with Situational Focus
```json
{
    "situational_overview": "Severe flooding across 8 districts in northern Bangladesh has affected over 2.3 million people, with 450,000 displaced from their homes as of August 15, 2024. River levels remain above danger marks in Sylhet and Rangpur divisions, with continued rainfall forecasted for the next 72 hours, exacerbating the humanitarian crisis. Critical infrastructure including roads, bridges, and health facilities have sustained significant damage, severely limiting access to affected populations and essential services. The IFRC is implementing a rapid response operation to provide immediate life-saving assistance through the Bangladesh Red Crescent Society, focusing on emergency shelter, clean water, and medical support. This coordinated approach leverages pre-positioned emergency stocks and trained volunteers to reach the most vulnerable communities within the first 48 hours of operational approval.",
    
    "metadata": {
        "event_id": 6950,
        "event_name": "Bangladesh: Monsoon Floods - August 2024",
        "disaster_type": "Flood",
        "country": "Bangladesh",
        "country_iso": "BD",
        
        "latest_update_number": 2,
        "total_operational_updates": 3,
        "has_operational_updates": true,
        
        "dref_id": 12345,
        "dref_title": "Bangladesh: Floods - Emergency Response DREF",
        "dref_appeal_code": "MDRBD025",
        "dref_date": "2024-08-15",
        
        "event_date": "2024-08-12",
        "dref_approval_date": "2024-08-15",
        "days_since_event": 8,
        
        "situational_context": {
            "affected_population": 2300000,
            "displaced_population": 450000,
            "geographic_scope": "8 districts",
            "operational_status": "Active Response",
            "response_phase": "Emergency"
        }
    }
}
```

### Situational Overview Line Structure Analysis
```
Line 1: "Severe flooding across 8 districts in northern Bangladesh has affected over 2.3 million people, with 450,000 displaced from their homes as of August 15, 2024."
→ Geographic scope, population impact, displacement figures, temporal reference

Line 2: "River levels remain above danger marks in Sylhet and Rangpur divisions, with continued rainfall forecasted for the next 72 hours, exacerbating the humanitarian crisis."
→ Current hazard status, specific locations, forward-looking risk assessment

Line 3: "Critical infrastructure including roads, bridges, and health facilities have sustained significant damage, severely limiting access to affected populations and essential services."
→ Infrastructure impact, operational constraints, service delivery challenges

Line 4: "The IFRC is implementing a rapid response operation to provide immediate life-saving assistance through the Bangladesh Red Crescent Society, focusing on emergency shelter, clean water, and medical support."
→ Operational rationale, implementing partners, priority sectors

Line 5: "This coordinated approach leverages pre-positioned emergency stocks and trained volunteers to reach the most vulnerable communities within the first 48 hours of operational approval."
→ Strategic approach, operational assets, timeline, target beneficiaries
```

---

---

# Shared Services Documentation

## `ops_learning_summary4.py` - Consolidated AI Summary Tasks & Azure OpenAI Integration

**Purpose:** Centralized Azure OpenAI processing and operational learning summary generation for all UCL research endpoints. All unused Celery tasks and dead code have been removed for optimal maintainability.

### Technical Architecture
All Azure OpenAI functionality is consolidated in the appropriate task classes within `ops_learning_summary4.py`:

### Key Methods by Class

**`OpsLearningSummaryTask`**:
- **`generate_previous_crises_insights(learning_data)`**: AI-powered insights from operational learning data (moved from azure_service.py)

**`DrefSummaryTask`**:
- **`generate_dref_summaries(dref_dict)`**: Economic sector analysis for DREF operations
- **`generate_situational_overview(update_dict)`**: 5-line situational narratives

### AI Processing Pipeline
1. **Unified Base Class**: `BaseAITask` provides Azure OpenAI integration and caching for all AI-powered task classes
2. **Specialized Task Classes**: Each endpoint has dedicated task classes (PreviousCrisesTask, DrefSummaryTask, RRCapacityTask)
3. **Consolidated Caching**: Unified caching mechanisms via `BaseAITask.get_cached_result/set_cached_result`
4. **API Client Integration**: All HTTP requests use `IFRCAPIClient` for consistent async operations
5. **Performance Monitoring**: Built-in execution time tracking via `PerformanceMonitor`
6. **Error Handling**: Comprehensive fallback strategies and detailed logging

---

## `ifrc_client.py` - Unified Async HTTP Client

**Purpose:** Centralized async HTTP client for all IFRC API interactions, replacing multiple legacy clients.

### Technical Features
```python
class IFRCAPIClient:
    def __init__(self):
        self.base_url = "https://go-api.ifrc.org/api/v2"
        self.timeout = httpx.Timeout(10.0)
        self.limits = httpx.Limits(max_keepalive_connections=5)
```

### Core API Methods
- **`get_event_detail(event_id)`**: Fetches comprehensive event data with field reports
- **`get_ops_learning(country_id, disaster_type_id, **filters)`**: Retrieves operational learning with validation filters
- **`get_country_detail(country_id)`**: Gets country metadata and context
- **`get_disaster_type_detail(disaster_type_id)`**: Fetches disaster type classifications

### Connection Management
- **Async Context Manager**: `async with IFRCAPIClient() as client:`
- **Connection Pooling**: Automatic HTTP/2 connection reuse
- **Retry Strategy**: Exponential backoff for 429, 5xx errors
- **Timeout Handling**: Configurable per-request timeouts

### Performance Optimizations
- **Concurrent Requests**: Supports parallel API calls via asyncio.gather
- **Response Caching**: Integration with Django's Redis cache backend
- **Request Deduplication**: Prevents duplicate API calls within same request cycle

---

## `blob_upload.py` - Azure Blob Storage Integration

**Purpose:** Handles file uploads to Azure Blob Storage for Excel report generation and document management.

### Technical Implementation
```python
def upload_to_blob(file_path: str, blob_name: str) -> str:
    """
    Upload file to Azure Blob Storage
    Returns: Public blob URL for file access
    """
    connection_string = settings.AZURE_STORAGE_CONNECTION_STRING
    container_name = "ucl-research-reports"
    
    blob_service_client = BlobServiceClient.from_connection_string(connection_string)
    blob_client = blob_service_client.get_blob_client(
        container=container_name, 
        blob=blob_name
    )
```

### Storage Strategy
- **Container Organization**: Separate containers per data type (reports, temp-files, archives)
- **Naming Convention**: `{endpoint_type}_{params}_{timestamp}.{extension}`
- **Access Control**: Public read access for generated reports
- **Cleanup Strategy**: TTL-based automatic deletion for temporary files

### File Types Supported
- **Excel Reports**: `.xlsx` files from RapidResponseCapacityQuestionsView
- **JSON Exports**: Structured data exports for API responses
- **Log Files**: Error reports and performance metrics

---

## `serializers.py` - DRF Response Serialization

**Purpose:** Standardized response formatting for all UCL research endpoints using Django REST Framework serializers.

### Core Serializers

#### `PerDrefLLMSummarySerializer`
```python
class PerDrefLLMSummarySerializer(serializers.Serializer):
    operational_summary = serializers.CharField()
    sectors = SectorDataSerializer(many=True)
    dref_type = serializers.CharField()
    dref_onset = serializers.CharField()
    metadata = DrefMetadataSerializer()
```

#### `PerDrefSituationalOverviewSerializer`
```python
class PerDrefSituationalOverviewSerializer(serializers.Serializer):
    situational_overview = serializers.CharField(max_length=2000)
    metadata = SituationalMetadataSerializer()
```

### Validation Features
- **Field Validation**: Type checking and format validation
- **Data Sanitization**: XSS protection and input cleaning
- **Error Standardization**: Consistent error message formatting
- **Nested Serialization**: Support for complex JSON structures

### Response Standardization
- **Consistent Structure**: All endpoints follow same metadata patterns
- **Timezone Handling**: UTC standardization for all datetime fields
- **Null Value Handling**: Graceful handling of missing optional fields
- **Pagination Support**: Built-in support for large dataset responses

---

## `ucl_views.py` - Main API View Implementation

**Purpose:** Contains all 4 unified API endpoint classes with shared base functionality.

### Base Architecture
```python
class BaseUCLView(APIView):
    CACHE_TIMEOUT = 3600  # 1 hour default
    
    def __init__(self):
        self.ifrc_client = IFRCAPIClient()
        self.azure_client = AzureServiceClient()
```

### Shared Functionality
- **Parameter Validation**: Consistent validation for event_id, country_id, disaster_type_id
- **Cache Management**: Redis-based caching with intelligent key generation
- **Error Handling**: Standardized exception handling and user-friendly error responses
- **Performance Tracking**: Built-in execution time monitoring
- **Async Resource Management**: Proper cleanup of HTTP connections

### View-Specific Logic
Each view implements specific processing logic while inheriting base functionality:
- **PreviousCrisesInsightsView**: Multi-tier operational learning aggregation
- **RapidResponseCapacityQuestionsView**: Excel generation with AI-filled questions
- **DrefSummaryView**: Economic sector analysis with budget breakdowns
- **DrefSituationalOverviewView**: 5-line situational narrative generation

---

# DREF Temp Folder Integration

## Overview
The `per/dref_temp/` folder provides essential DREF data management utilities that are heavily used by the UCL research endpoints, particularly `DrefSummaryView` and `DrefSituationalOverviewView`.

## Key Components

### `dref_manager` - DREF Data Access Layer
```python
from per.dref_temp.dref_utils import dref_manager, DREFFilters

# Usage in UCL endpoints
filters = DREFFilters(field_report_ids=field_report_ids)
dref_data = dref_manager.get_data("basic", filters)
```

### Data Sources Integration
- **`basic`**: Current DREF operations and applications (primary source)
- **`op-update`**: Operations with updates/changes (fallback for latest versions)
- **`final-report`**: Completed operations with results (historical analysis)

### Filtering Capabilities
The UCL endpoints leverage advanced DREF filtering:
```python
# Event-based DREF resolution
field_report_ids = [fr['id'] for fr in event['field_reports']]
filters = DREFFilters(field_report_ids=field_report_ids)

# Multi-source fallback strategy
dref_sources = ["basic", "op-update", "final-report"]
for source in dref_sources:
    dref_data = dref_manager.get_data(source, filters)
    if dref_data:
        break
```

### Latest Version Resolution
```python
# Get most recent operational version
dref_data = dref_data[0]  # First result
latest_dref_version = dref_manager.get_latest_dref_version(dref_data)
```

### UCL Integration Points
1. **Event → Field Reports → DREF Linking**: Links events to DREF operations via field reports
2. **Operational Update Tracking**: Tracks DREF operation changes over time
3. **Version Management**: Ensures latest operational data is used for AI processing
4. **Data Validation**: Validates DREF data completeness before AI processing

---

# Technical Implementation Details

## Caching Architecture

### Redis Backend Configuration
```python
# settings.py integration
CACHES = {
    "default": {
        "BACKEND": "django_redis.cache.RedisCache",
        "LOCATION": env("CACHE_REDIS_URL"),
        "OPTIONS": {
            "CLIENT_CLASS": "django_redis.client.DefaultClient",
        },
    }
}
```

### Cache Key Strategy
- **Namespace Prefixing**: `ucl_*` prefixes prevent conflicts with other modules
- **Parameter-Based Keys**: Include all relevant parameters for uniqueness
- **TTL Management**: 1-hour default with configurable timeouts per endpoint

### Cache Key Examples
```python
# Previous Crises Insights
cache_key = f"ucl_previous_crises:{country_id}:{disaster_type_id}"

# DREF Summary  
cache_key = f"ucl_dref_summary:{event_id}"

# Situational Overview
cache_key = f"ucl_dref_situational:{event_id}"

# RR Capacity Questions
cache_key = f"ucl_rr_capacity:{country_id}:{disaster_type_id}"
```

## Synchronous Processing with Caching

### Architecture Decision
All endpoints use **synchronous processing with Redis caching** instead of Celery async tasks to provide immediate responses for frontend implementation:

```python
# Cache-first approach
cached_result = cache.get(cache_key)
if cached_result:
    return Response(cached_result, status=200)

# Process synchronously if not cached
result = asyncio.run(self._process_endpoint_logic(params, cache_key))
return result
```

### Benefits
- **Immediate Response**: No "processing" delays for frontend users
- **Cache Performance**: Subsequent requests served from Redis (sub-millisecond response)
- **Reliability**: No dependency on Celery worker availability
- **Debugging**: Easier to trace request flow without async complexity

## Error Handling Strategy

### Graceful Degradation
- **Multi-Source Fallbacks**: DREF data sources, operational learning tiers
- **Partial Results**: Return available data even if some sources fail
- **User-Friendly Messages**: Clear error responses with actionable guidance
- **Logging Context**: Comprehensive error context for debugging

### Exception Hierarchy
```python
try:
    # Primary processing logic
except ValidationError as e:
    return Response({"error": "Invalid parameters"}, 400)
except DataNotFoundError as e:
    return Response({"error": "Resource not found"}, 404) 
except AIProcessingError as e:
    return Response({"error": "AI processing failed"}, 500)
except Exception as e:
    logger.error(f"Unexpected error: {e}", exc_info=True)
    return Response({"error": "Internal server error"}, 500)
```

## Performance Monitoring

### Execution Tracking
```python
from per.ucl_research.ops_learning_summary4 import PerformanceMonitor

start_time = datetime.now()
# ... processing logic ...
end_time = datetime.now()
PerformanceMonitor.track_execution_time("endpoint_name", start_time, end_time)
```

### Metrics Storage
- **Redis-Based**: Daily performance aggregation in Redis
- **Cache Analytics**: Hit/miss ratios and response times
- **Error Tracking**: Exception rates and types per endpoint

## AI Integration Details

### Azure OpenAI Configuration
```python
# Environment variables required
AZURE_OPENAI_ENDPOINT = "https://your-resource.openai.azure.com/"
AZURE_OPENAI_KEY = "your-api-key"
AZURE_OPENAI_DEPLOYMENT_NAME = "your-gpt-deployment"
```

### Prompt Engineering
- **Endpoint-Specific Prompts**: Tailored prompts for each use case
- **Context Injection**: Relevant operational data included in prompts
- **Output Structure**: Defined JSON schemas for consistent responses
- **Fallback Handling**: Default responses when AI processing fails

---

# Usage Examples

## API Endpoint Usage

### 1. Previous Crises Insights
```bash
# Get AI-powered insights from similar disasters
GET /api/v2/ifrc-events/?country=194&disaster_type=12

# Response: Structured insights with source attribution
{
  "ai_structured_summary": [
    {
      "id": 1,
      "title": "Early Warning Systems Effectiveness",
      "content": "Analysis shows 40% casualty reduction with 48+ hour warnings",
      "confidence_level": "high",
      "source_learnings": [...]
    }
  ]
}
```

### 2. Rapid Response Capacity Questions
```bash
# Generate AI-filled Excel assessment forms
GET /api/v2/rr-capacity-questions/?country=194&disaster_type=12

# Response: Azure Blob Storage URL
{
  "file_url": "https://storage.blob.core.windows.net/.../rr_capacity_filled_194_12.xlsx"
}
```

### 3. DREF Economic Sector Summary
```bash
# Get comprehensive DREF operation analysis
GET /api/v2/per-dref-summary/?id=6955

# Response: Detailed sector-based breakdown
{
  "operational_summary": "The operation aims to provide emergency assistance...",
  "sectors": [
    {
      "title_display": "Multi-Purpose Cash Grants",
      "budget": 450000.00,
      "people_targeted_total": 15000,
      "future_actions": [...]
    }
  ],
  "metadata": {
    "total_budget": 890000.00,
    "cost_per_beneficiary": 59.33
  }
}
```

### 4. DREF Situational Overview
```bash
# Get 5-line situational summary
GET /api/v2/per-dref-situational-overview/?id=6955

# Response: Narrative overview with metadata
{
  "situational_overview": "Severe flooding across 8 districts...",
  "metadata": {
    "event_id": 6955,
    "disaster_type": "Flood",
    "days_since_event": 8
  }
}
```

## Direct Python Usage

### Async HTTP Client
```python
from per.ucl_research.ifrc_client import IFRCAPIClient

async def get_event_data():
    async with IFRCAPIClient() as client:
        # Get event with field reports
        event = await client.get_event_detail(6955)
        
        # Get operational learning data
        learning = await client.get_ops_learning(
            country_id=194, 
            disaster_type_id=12,
            is_validated="true",
            limit=10
        )
        
        return event, learning
```

### DREF Data Management
```python
from per.dref_temp.dref_utils import dref_manager, DREFFilters

# Find DREF by field report IDs
field_report_ids = [17010, 17005, 17003]
filters = DREFFilters(field_report_ids=field_report_ids)
dref_operations = dref_manager.get_data('basic', filters)

# Get latest operational version
if dref_operations:
    latest_version = dref_manager.get_latest_dref_version(dref_operations[0])
```

### Azure AI Integration
```python
from per.ucl_research.ops_learning_summary4 import OpsLearningSummaryTask, DrefSummaryTask

# Generate AI summaries for different endpoint types
previous_crises_insights = OpsLearningSummaryTask.generate_previous_crises_insights(learning_data)
dref_summaries = DrefSummaryTask.generate_dref_summaries(dref_dict)
situational_overview = DrefSummaryTask.generate_situational_overview(update_dict)
```

### Cache Management
```python
from django.core.cache import cache

# Check cache before processing
cache_key = f"ucl_dref_summary:{event_id}"
cached_result = cache.get(cache_key)

if not cached_result:
    # Process and cache result
    result = process_dref_summary(event_id)
    cache.set(cache_key, result, timeout=3600)
```

## Django URLconf Integration
```python
# main/urls.py or per/urls.py
from per.ucl_research.ucl_views import (
    PreviousCrisesInsightsView,
    RapidResponseCapacityQuestionsView, 
    DrefSummaryView,
    DrefSituationalOverviewView
)

urlpatterns = [
    path('api/v2/ifrc-events/', PreviousCrisesInsightsView.as_view(), name='ifrc-events'),
    path('api/v2/rr-capacity-questions/', RapidResponseCapacityQuestionsView.as_view(), name='rr-capacity'),
    path('api/v2/per-dref-summary/', DrefSummaryView.as_view(), name='dref-summary'),
    path('api/v2/per-dref-situational-overview/', DrefSituationalOverviewView.as_view(), name='dref-situational'),
]
```

## Testing Examples

### Unit Testing
```python
from django.test import TestCase
from unittest.mock import patch, AsyncMock
from per.ucl_research.ucl_views import DrefSummaryView

class TestDrefSummaryView(TestCase):
    @patch('per.ucl_research.ucl_views.IFRCAPIClient')
    async def test_dref_summary_success(self, mock_client):
        # Mock API responses
        mock_client.return_value.__aenter__.return_value.get_event_detail = AsyncMock(
            return_value={"id": 6955, "field_reports": [{"id": 17010}]}
        )
        
        # Test view logic
        view = DrefSummaryView()
        response = await view._process_dref_summary(6955, "test_cache_key")
        
        self.assertEqual(response.status_code, 200)
```

### Performance Testing
```python
import time
from per.ucl_research.ops_learning_summary4 import PerformanceMonitor

# Track endpoint performance
start_time = time.time()
# ... endpoint processing ...
end_time = time.time()

PerformanceMonitor.track_execution_time(
    "dref_summary", 
    start_time, 
    end_time
)
```

---

# Production Deployment Considerations

## Environment Configuration
```bash
# Required environment variables
AZURE_OPENAI_ENDPOINT=https://your-openai.openai.azure.com/
AZURE_OPENAI_KEY=your-api-key
AZURE_OPENAI_DEPLOYMENT_NAME=gpt-4
AZURE_STORAGE_CONNECTION_STRING=DefaultEndpointsProtocol=https...
CACHE_REDIS_URL=redis://localhost:6379/1
```

## Monitoring & Alerting
- **Cache Hit Rates**: Monitor Redis cache performance
- **AI Response Times**: Track Azure OpenAI API latency
- **Error Rates**: Alert on endpoint failure thresholds
- **Resource Usage**: Monitor memory and CPU usage patterns

## Scaling Considerations
- **Redis Clustering**: Scale cache layer for high load
- **Connection Pooling**: Optimize HTTP connection reuse
- **Rate Limiting**: Implement Azure OpenAI rate limit handling
- **CDN Integration**: Cache static Excel files via CDN