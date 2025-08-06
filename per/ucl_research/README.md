# UCL Research - Operational Learning Summary APIs

## Overview

This module provides 4 unified API endpoints for generating AI-powered operational learning summaries and insights from IFRC humanitarian operations data. All endpoints use async HTTP requests, Redis caching, and Azure OpenAI integration.

## Quick Start

### API Endpoints

| Endpoint | Purpose | Parameters |
|----------|---------|------------|
| `GET /api/v2/previous-crises-insights/` | AI insights from similar disasters | `country={id}&disaster_type={id}` |
| `GET /api/v2/rr-capacity-questions/` | Excel assessment with AI-filled responses | `country={id}&disaster_type={id}` |
| `GET /api/v2/dref-summary/` | DREF economic sector analysis | `id={event_id}` |
| `GET /api/v2/dref-situational-overview/` | 5-line situational summary | `id={event_id}` |

### Example Usage

```bash
# Get AI insights from similar disasters in Bangladesh (floods)
GET /api/v2/previous-crises-insights/?country=194&disaster_type=12

# Generate Excel capacity assessment
GET /api/v2/rr-capacity-questions/?country=194&disaster_type=12

# Get DREF economic analysis
GET /api/v2/dref-summary/?id=6955

# Get situational overview
GET /api/v2/dref-situational-overview/?id=6955
```

## Architecture

```
per/ucl_research/
├── ucl_views.py                   # 4 API endpoints (minimal HTTP logic)
├── ops_learning_summary4.py       # AI task classes & Azure OpenAI integration
├── ifrc_client.py                 # Unified async HTTP client
├── rapid_response_parser.py       # Excel generation for RR capacity
├── serializers.py                 # DRF response serializers
├── blob_upload.py                 # Azure Blob Storage utility
├── rr_parsed_excel.json          # RR capacity questions template
└── README.md                      # This documentation
```

### Design Principles

- **Views → Task Classes**: Views handle HTTP logic, task classes handle business logic
- **Unified Caching**: All endpoints use Redis via `BaseAITask.get_cached_result/set_cached_result`
- **Async API Calls**: All external requests use `IFRCAPIClient` with connection pooling

## Endpoint Details

### 1. Previous Crises Insights (`PreviousCrisesInsightsView`)

**Purpose**: Generate AI-synthesized insights from operational learning data of similar disasters to inform current humanitarian responses.

**Technical Architecture**: Multi-tier data fetching with intelligent fallback, AI synthesis engine, and Redis caching layer.

#### Detailed Request Flow

**Phase 1: Parameter Validation & Cache Check**
```python
# Required parameters
country_id = int(request.query_params.get('country'))      # Country ID (e.g., 194 for Bangladesh)
disaster_type_id = int(request.query_params.get('disaster_type'))  # Disaster Type ID (e.g., 12 for Flood)

# Cache key generation and lookup
cache_key = f"ucl_previous_crises:{country_id}:{disaster_type_id}"
cached_result = BaseAITask.get_cached_result(cache_key)
if cached_result:
    return Response({"ai_structured_summary": cached_result}, 200)
```

**Phase 2: Multi-Tier Operational Learning Fetch Strategy**

*Stage 2.1: Primary Fetch (High Precision)*
```python
# Primary API call: Country + Disaster Type specificity
async with IFRCAPIClient() as client:
    primary_learning = await client.get_ops_learning(
        country_id=country_id,
        disaster_type_id=disaster_type_id,
        is_validated="true",
        limit=6
    )
    
# Source attribution for transparency
primary_labeled = [
    {**learning, "source_note": "This insight was built off similar disasters from the same country."}
    for learning in primary_learning
]
```

*Stage 2.2: Fallback Fetch (High Recall)*
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
```

**Phase 3: Data Synthesis & Structure Normalization**
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

**Phase 4: Azure OpenAI Synthesis Engine**
```python
# AI-powered insight generation using PreviousCrisesTask
ai_summary = self.previous_crises_task.generate_ai_summary([{
    "related_ops_learning": processed_learnings
}])

# AI processing pipeline includes:
# 1. Context analysis of 1-6 learning items
# 2. Pattern recognition across multiple disasters  
# 3. Actionable insight generation
# 4. Source traceability maintenance
```

**Phase 5: Response Assembly**
```python
response_data = {
    "ai_structured_summary": ai_summary
}

# Cache result for future requests (1-hour TTL)
BaseAITask.set_cached_result(cache_key, response_data)
```

**Response Structure**:
```json
{
  "ai_structured_summary": [
    {
      "id": 1,
      "title": "Early Warning Systems Effectiveness",
      "content": "Analysis of 3 flood operations in Bangladesh (2021-2023) shows early warning systems reduced casualty rates by 40% when implemented 48+ hours before peak flooding. Community-based warning networks proved most effective in rural areas.",
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

### 2. Rapid Response Capacity Questions (`RapidResponseCapacityQuestionsView`)

**Purpose**: Generate AI-powered Excel reports pre-filled with contextual humanitarian capacity assessments using operational learning from similar historical responses.

**Technical Architecture**: Synchronous processing with async data pipeline, AI question processing, multi-sheet Excel generation, Azure Blob storage integration.

#### Detailed Request Flow

**Phase 1: Parameter Validation & Cache Check**
```python
# Same validation as Previous Crises Insights
country_id, disaster_type_id = validate_country_disaster_params(request)

# Excel-specific cache key
cache_key = f"ucl_rr_capacity:{country_id}:{disaster_type_id}"
cached_url = BaseAITask.get_cached_result(cache_key)
if cached_url:
    return Response({"file_url": cached_url}, 200)
```

**Phase 2: Questions Template Loading**
```python
# Load pre-parsed capacity assessment framework
current_dir = os.path.dirname(os.path.abspath(__file__))
json_path = os.path.join(current_dir, 'rr_parsed_excel.json')
with open(json_path, 'r', encoding='utf-8') as f:
    questions_template = json.load(f)
    
# Template structure includes:
# - Area-based categorization (Policy/Strategy, Analysis/Planning, Operational Capacity, etc.)
# - Critical questions and guiding/probing questions
# - Response capacity fields for AI completion
# - Status tracking and recommended actions
# - Reference fields for source attribution
```

**Phase 3: Two-Stage Operational Learning Data Acquisition**
```python
# Stage 1: Precise matching (Country + Disaster Type)
async def _fetch_rr_ops_learning_data(client, country_id, disaster_type_id, target_count=10):
    # Primary batch with both filters
    primary_batch = await client.get_ops_learning(
        country_id=country_id,
        disaster_type_id=disaster_type_id,
        max_results=10
    )
    
    primary_labeled = [
        {**l, "source_note": "This insight was built off similar disasters from the same country."}
        for l in primary_batch
    ]
    
    # Track appeal codes to avoid duplicates
    seen_appeal_codes = set()
    deduplicated_results = []
    
    # Add primary results and track their appeal codes
    for learning in primary_labeled:
        appeal_info = learning.get('appeal', {})
        appeal_code = appeal_info.get('code') if isinstance(appeal_info, dict) else str(appeal_info)
        
        if appeal_code and appeal_code not in seen_appeal_codes:
            seen_appeal_codes.add(appeal_code)
            deduplicated_results.append(learning)
    
    # Stage 2: If we need more results, fetch country-only data
    if len(deduplicated_results) < 10:
        remaining_needed = 10 - len(deduplicated_results)
        secondary_batch = await client.get_ops_learning(
            country_id=country_id,
            disaster_type_id=None,
            max_results=remaining_needed * 4
        )
        
        # Filter secondary batch to only include the specific disaster type
        filtered_secondary = [
            learning for learning in secondary_batch
            if learning.get('appeal', {}).get('dtype', {}).get('id') == disaster_type_id
        ]
        
        # Add secondary results, avoiding duplicates
        for learning in filtered_secondary:
            if len(deduplicated_results) >= 10:
                break
            appeal_code = learning.get('appeal', {}).get('code')
            if appeal_code and appeal_code not in seen_appeal_codes:
                seen_appeal_codes.add(appeal_code)
                deduplicated_results.append(learning)
    
    return deduplicated_results[:10]
```

**Phase 4: Event Context Extraction Pipeline**
```python
async def _fetch_events_from_ops_learning(client, ops_learning_data):
    events = []
    seen_event_ids = set()
    
    for learning in ops_learning_data:
        # Extract event ID from nested structure
        event_id = (learning.get('appeal', {})
                           .get('event_details', {})
                           .get('id'))
        appeal_code = learning.get('appeal', {}).get('code')
        
        if event_id and event_id not in seen_event_ids:
            seen_event_ids.add(event_id)
            
            # Async event detail fetch
            try:
                event = await client.get_event_detail(event_id)
                if event:
                    # Source attribution for traceability
                    event["source_note"] = f"Event from ops learning (Appeal: {appeal_code}, Event ID: {event_id})"
                    event["appeal_source"] = appeal_code
                    event["event_source_id"] = event_id
                    events.append(event)
            except Exception:
                continue  # Graceful degradation
    
    return events
```

**Phase 5: AI-Powered Question Processing**
```python
def _process_questions(questions_data, events_data, ops_learning_data):
    processed_questions = []
    
    for question in questions_data:
        processed_question = question.copy()
        
        # Check if "Notes on Response Capacity with sources" field is missing/null
        notes_field_new = "Notes on Response Capacity with sources"
        notes_field_old = "Notes on Response include the source"
        
        notes_value = question.get(notes_field_new) or question.get(notes_field_old)
        needs_processing = (
            not notes_value or 
            str(notes_value or "").lower() in ['nan', 'null', 'none', '']
        )
        
        if needs_processing:
            try:
                # Use RRCapacityTask to fill missing fields
                generated_responses = self.response_service.process_capacity_question(
                    question, events_data, ops_learning_data
                )
                
                # Update question with generated responses and source references
                for field_name, response in generated_responses.items():
                    if response:
                        if field_name == "Notes on Response Capacity with sources":
                            # Update both field names for compatibility
                            processed_question["Notes on Response Capacity with sources"] = response
                            processed_question["Notes on Response include the source"] = response
            except Exception as e:
                # If processing fails, add error note
                error_message = f"Processing failed: {str(e)}"
                processed_question["Notes on Response Capacity with sources"] = error_message
        
        processed_questions.append(processed_question)
    
    return processed_questions
```

**Phase 6: Multi-Sheet Excel Generation**
```python
def _create_rr_capacity_excel(processed_questions, country_id, disaster_type_id, events_data, ops_learning_data):
    wb = Workbook()
    ws = wb.active
    ws.title = "RR Capacity Assessment"
    
    # Headers matching original Excel structure
    headers = [
        "Area", "Critical Questions", "Guiding/probing questions",
        "Notes on Response Capacity with sources", "Status",
        "Recommended actions for continuation of response", 
        "Examples of recommended actions", "References"
    ]
    
    # Main title row with styling
    ws.merge_cells('A1:H1')
    title_cell = ws['A1']
    title_cell.value = "Rapid Response Capacity Check"
    title_cell.font = Font(bold=True, size=20, color="FFFFFF")
    title_cell.fill = PatternFill(start_color="000000", end_color="000000", fill_type="solid")
    
    # Country and Date identifiers
    country_cell = ws['A2']
    country_cell.value = "Country:"
    country_value_cell = ws['B2']
    country_value_cell.value = get_country_name(country_id)
    
    date_cell = ws['D2']
    date_cell.value = "Date:"
    date_value_cell = ws['E2']
    date_value_cell.value = datetime.now().strftime("%d %B %Y")
    
    # Area-based color coding and merged cells
    current_area = None
    area_start = 4
    row_idx = 4
    
    for question in processed_questions:
        area = question.get("Area", "")
        
        # Area merging logic for same areas
        if area and area != current_area:
            if current_area:
                # Merge previous area block
                ws.merge_cells(start_row=area_start, start_column=1, end_row=row_idx-1, end_column=1)
                # Apply area-specific color coding
                area_color = get_area_color(current_area)
                area_cell = ws.cell(area_start, 1)
                area_cell.fill = PatternFill(start_color=area_color, end_color=area_color, fill_type="solid")
            
            area_start = row_idx
            current_area = area
        
        # Populate data cells with AI-filled responses
        for col_idx, header in enumerate(headers, 1):
            value = question.get(header, "")
            
            # Handle NaN values and format lists
            if str(value).lower() == 'nan':
                value = ""
            elif isinstance(value, list):
                if header == "References" and value:
                    # Special formatting for references
                    reference_strings = [
                        f"{item.get('text', '')} ({item.get('url', '')})" if isinstance(item, dict) and item.get('url')
                        else str(item) for item in value
                    ]
                    value = '\n'.join(reference_strings)
                else:
                    value = '\n'.join([str(item) for item in value]) if value else ""
            
            # Clean markdown formatting from AI responses
            if value and isinstance(value, str) and "**" in value:
                import re
                value = re.sub(r'\*\*([^*]+)\*\*', r'\1', value)  # Remove bold
                value = re.sub(r'\*([^*]+)\*', r'\1', value)      # Remove italic
                value = value.replace('**', '')
            
            cell = ws.cell(row=row_idx, column=col_idx, value=value)
            cell.alignment = Alignment(wrap_text=True, vertical="top")
            cell.font = Font(size=11, color="000000")
        
        ws.row_dimensions[row_idx].height = 60  # Row height for readability
        row_idx += 1
    
    # Create Sources sheet with detailed source attribution
    _add_sources_sheet(wb, events_data, ops_learning_data)
    
    return wb

def get_area_color(area):
    """Get appropriate pastel color for each area"""
    area_colors = {
        "Policy, Strategy and Standards": "E6E6FA",  # Light lavender
        "Analysis and Planning": "FFFACD",           # Light yellow
        "Operational Capacity": "E6F3FF",           # Light blue
        "Coordination": "E6FFE6",                   # Light green
        "Operations Support": "FFE6F0"              # Light pink
    }
    return area_colors.get(area.split('\n')[0].strip(), "FFFFFF")
```

**Phase 7: Azure Blob Storage & Caching**
```python
# Generate unique filename
filename = f"rr_capacity_filled_{country_id}_{disaster_type_id}.xlsx"

# Temporary file creation and upload
with NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
    workbook.save(tmp.name)
    temp_path = tmp.name

# Upload to Azure Blob Storage
blob_url = upload_to_blob(temp_path, blob_name=filename)
os.remove(temp_path)  # Cleanup

response_data = {"file_url": blob_url}

# Cache URL for 1 hour
BaseAITask.set_cached_result(cache_key, response_data)
```

**Response Structure**:
```json
{
  "file_url": "https://storage.blob.core.windows.net/ucl-research-reports/rr_capacity_filled_194_12.xlsx"
}
```

---

### 3. DREF Summary (`DrefSummaryView`)

**Purpose**: Generate comprehensive DREF operation summaries with AI-powered economic sector analysis, financial planning insights, and intervention strategy recommendations.

**Technical Architecture**: Event-driven data pipeline, DREF data management system, sector-based AI analysis, structured JSON serialization.

#### Detailed Request Flow

**Phase 1: Event ID Validation & Processing**
```python
# Single parameter validation for event-based lookup
event_id_param = request.query_params.get("id")

if not event_id_param:
    return Response({"error": "Event ID is required"}, 400)

try:
    event_id = int(event_id_param)
except ValueError:
    return Response({"error": "Event ID must be a valid integer"}, 400)

# Cache check for DREF summary
cache_key = f"ucl_dref_summary:{event_id}"
cached_result = BaseAITask.get_cached_result(cache_key)
if cached_result:
    return Response(cached_result, 200)
```

**Phase 2: Event Context Retrieval**
```python
# Async event detail fetch with comprehensive error handling
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

**Phase 3: DREF Data Management Pipeline**
```python
# Multi-source DREF data resolution with hierarchical fallback
from per.dref_temp.dref_utils import dref_manager, DREFFilters

filters = DREFFilters(field_report_ids=field_report_ids)

# Attempt multiple DREF sources in priority order
dref_sources = ["basic", "op-update", "final-report"]
dref_data = None

for source in dref_sources:
    try:
        dref_data = dref_manager.get_data(source, filters)
        if dref_data:
            logger.info(f"DREF data found in source: {source}")
            break
    except Exception as e:
        logger.warning(f"DREF source '{source}' unavailable: {e}")
        continue

if not dref_data:
    return Response({
        "error": "DREFs not found",
        "event_id": event_id,
        "event_name": event.get("name"),
        "field_reports_count": len(field_reports),
        "field_report_ids": field_report_ids
    }, 404)

# Get latest operational version for most current data
dref_data = dref_data[0]
latest_dref_version = dref_manager.get_latest_dref_version(dref_data)
```

**Phase 4: DREF Economic Data Structuring**
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

**Phase 5: AI-Powered Sector Analysis Engine**
```python
# Advanced sector-based AI processing using DrefSummaryTask
dref_summary_task = DrefSummaryTask()
summaries = dref_summary_task.generate_dref_summaries(dref_dict)

# AI processing pipeline includes:
# 1. Operational summary generation (3-line executive summary)
# 2. Sector data organization with fuzzy matching
# 3. Economic analysis per sector (needs, budget allocation, cost-effectiveness)
# 4. Intervention planning with financial projections
# 5. Risk assessment and mitigation strategies
# 6. People-targeted calculations and beneficiary analysis
```

**Phase 6: Economic Metadata Extraction**
```python
# Operational update tracking for economic context
op_update_number = 1  # Default value
operational_updates = getattr(latest_dref_version, 'operational_update_details', [])

if operational_updates and isinstance(operational_updates, list):
    first_update = operational_updates[0]
    op_update_number = getattr(first_update, 'operational_update_number', 1)

# Calculate economic indicators
cost_per_beneficiary = (
    latest_dref_version.amount_requested / latest_dref_version.total_targeted_population
    if latest_dref_version.total_targeted_population else None
)
```

**Phase 7: Response Assembly**
```python
sectors_data = summaries.get("sectors", [])

# Economic sector-focused response structure
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
        "cost_per_beneficiary": cost_per_beneficiary,
        "sectors_count": len(sectors_data),
        "people_targeted": latest_dref_version.total_targeted_population
    }
}

# Cache result for future requests
BaseAITask.set_cached_result(cache_key, summary_data)

# DRF serialization with economic focus
serializer = PerDrefLLMSummarySerializer(summary_data)
return Response(serializer.data, 200)
```

**Response Structure**:
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
    "people_targeted": 15000
  }
}
```

---

### 4. DREF Situational Overview (`DrefSituationalOverviewView`)

**Purpose**: Generate precise 5-line situational overview paragraphs that synthesize DREF operational context, strategic rationale, and situational changes for decision-maker briefings.

**Technical Architecture**: Event-DREF linkage system, operational update analysis, AI-powered narrative synthesis, metadata-rich response structure.

#### Detailed Request Flow

**Phase 1-3: Event Validation & DREF Resolution**
```python
# Identical to DREF Summary for event validation and DREF data retrieval
# Phases 1-3 maintain same technical implementation for consistency
event_id = validate_event_id(request)

# Cache check for situational overview
cache_key = f"ucl_dref_situational:{event_id}"
cached_result = BaseAITask.get_cached_result(cache_key)
if cached_result:
    return Response(cached_result, 200)

# Event → Field Reports → DREF resolution (same as DREF Summary)
async with IFRCAPIClient() as client:
    event = await client.get_event_detail(event_id)
    field_report_ids = [fr['id'] for fr in event.get("field_reports", [])]
    
filters = DREFFilters(field_report_ids=field_report_ids)
dref_data = dref_manager.get_data("basic", filters)
latest_dref_version = dref_manager.get_latest_dref_version(dref_data[0])
```

**Phase 4: Latest Operational Update Analysis**
```python
# Situational data extraction with comprehensive fallback hierarchy
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

**Phase 5: AI-Powered Situational Narrative Synthesis**
```python
# Specialized 5-line overview generation using DrefSummaryTask
dref_summary_task = DrefSummaryTask()
situational_overview = dref_summary_task.generate_situational_overview(latest_update_dict)

# AI prompt engineering for situational overview:
# - Lines 1-3: Event situation (disaster context, affected areas, impact scale)
# - Lines 4-5: Operational rationale (why needed, strategic approach)
# - Ensure coherent narrative flow across all 5 lines
# - Include specific data points (numbers, locations, timeframes)
# - Maintain professional humanitarian language
# - Focus on situational changes and operational context

if not situational_overview:
    return Response({
        "error": "Failed to generate situational overview",
        "dref_id": dref_data.id,
        "event_id": event_id
    }, 500)
```

**Phase 6: Event-Centric Metadata Assembly**
```python

# Calculate temporal context
def calculate_days_between(start_date, end_date):
    if start_date and end_date:
        return (end_date.date() - start_date.date()).days if hasattr(start_date, 'date') else None
    return None

days_since_event = calculate_days_between(
    event.get('start_date'), 
    datetime.now()
) if event.get('start_date') else None

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
        "days_since_event": days_since_event,
        
        # Situational context indicators
        "situational_context": {
            "response_phase": "Emergency" if days_since_event and days_since_event < 30 else "Recovery",
            "operational_status": "Active Response" if latest_update_dict.get('operational_update_number', 0) > 0 else "Initial Response",
            "update_frequency": "Regular" if len(getattr(dref_data, 'operational_update_details', [])) > 2 else "Limited"
        }
    }
}

# Cache result for future requests
BaseAITask.set_cached_result(cache_key, response_data)

# Specialized serialization for situational overview
serializer = PerDrefSituationalOverviewSerializer(response_data)
return Response(serializer.data, 200)
```

**Situational Overview Line Structure Analysis**:
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

**Response Structure**:
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
      "response_phase": "Emergency",
      "operational_status": "Active Response",
      "update_frequency": "Regular"
    }
  }
}
```

## Technical Implementation

### Caching Strategy

All endpoints use Redis caching with 1-hour TTL:

```python
# Cache-first approach
cache_key = f"ucl_{endpoint}:{params}"
cached_result = BaseAITask.get_cached_result(cache_key)
if cached_result:
    return Response(cached_result, 200)

# Process and cache if not found
result = process_endpoint_logic(params)
BaseAITask.set_cached_result(cache_key, result)
```

### Error Handling

- **Graceful Degradation**: Multi-source fallbacks, partial results when possible
- **User-Friendly Errors**: Clear error messages with actionable guidance
- **Comprehensive Logging**: Full error context for debugging

### AI Integration

**Azure OpenAI Configuration**:
```python
# Environment variables required
AZURE_OPENAI_ENDPOINT = "https://your-resource.openai.azure.com/"
AZURE_OPENAI_KEY = "your-api-key"
AZURE_OPENAI_DEPLOYMENT_NAME = "your-gpt-deployment"
```

**Task Classes**:
- `PreviousCrisesTask`: Generates insights from operational learning
- `DrefSummaryTask`: Creates economic sector analysis and situational overviews
- `RRCapacityTask`: Processes capacity assessment questions

## Data Sources Integration

### IFRC API Client

Unified async client for all external API calls:

```python
async with IFRCAPIClient() as client:
    event = await client.get_event_detail(event_id)
    learning = await client.get_ops_learning(country_id, disaster_type_id)
```

### DREF Data Management

Integration with `per/dref_temp/dref_utils`:

```python
from per.dref_temp.dref_utils import dref_manager, DREFFilters

# Event → Field Reports → DREF linking
filters = DREFFilters(field_report_ids=field_report_ids)
dref_data = dref_manager.get_data("basic", filters)
latest_version = dref_manager.get_latest_dref_version(dref_data[0])
```

## Development

### Local Setup

```bash
# Environment variables
export DJANGO_SECRET_KEY=RANDOM-STRING-FOR-SECRET-KEYS
export AZURE_OPENAI_ENDPOINT=https://your-openai.openai.azure.com/
export AZURE_OPENAI_KEY=your-api-key
export AZURE_OPENAI_DEPLOYMENT_NAME=gpt-4
export AZURE_STORAGE_CONNECTION_STRING=DefaultEndpointsProtocol=https...
export CACHE_REDIS_URL=redis://localhost:6379/1api-version=2025-01-01-preview
export AZURE_ACCOUNT_NAME=ifrcemergencypage
export AZURE_ACCOUNT_KEY=your-acc-key
export AZURE_CONTAINER=ifrc-blob
```

### Testing

```python
# Direct usage
from per.ucl_research.ops_learning_summary4 import PreviousCrisesTask
from per.ucl_research.ifrc_client import IFRCAPIClient

async def test_endpoint():
    async with IFRCAPIClient() as client:
        learning = await client.get_ops_learning(194, 12)
        task = PreviousCrisesTask()
        insights = task.generate_ai_summary([{"related_ops_learning": learning}])
```

### URL Configuration

```python
from per.ucl_research.ucl_views import (
    PreviousCrisesInsightsView, RapidResponseCapacityQuestionsView,
    DrefSummaryView, DrefSituationalOverviewView
)

urlpatterns = [
    path('api/v2/previous-crises-insights/', PreviousCrisesInsightsView.as_view()),
    path('api/v2/rr-capacity-questions/', RapidResponseCapacityQuestionsView.as_view()),
    path('api/v2/dref-summary/', DrefSummaryView.as_view()),
    path('api/v2/dref-situational-overview/', DrefSituationalOverviewView.as_view()),
]
```