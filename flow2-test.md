# PerDrefLLMSummaryView Complete Technical Flow

## Endpoint Configuration

- **URL:** `/api/v2/per-dref-summary/`
- **Method:** `GET` only
- **Authentication:** None (public endpoint)
- **Location:** `per/drf_views.py:367-494`
- **Purpose:** Generates AI-powered summaries for DREF operations using Azure OpenAI

---

## Detailed Request Flow

### Phase 1: Request Validation

1. **Input:** Query parameter `id` (event_id)
2. **Validation:** Convert to integer
3. **Error Paths:**
   - Missing `id` → `400 Bad Request`
   - Invalid integer → `400 Bad Request`

### Phase 2: Event Data Retrieval

1. **Service:** `EventAPIClient()`
2. **External API Call:** `https://goadmin.ifrc.org/api/v2/event/{event_id}/`
3. **Error Path:** Event not found → `404 Not Found`

### Phase 3: Field Reports Processing

1. **Extract:** `event.get("field_reports", [])`
2. **Process:** Extract field report IDs: `[fr['id'] for fr in field_reports]`
3. **Error Path:** No field reports → `404 Not Found`

### Phase 4: DREF Data Retrieval

1. **Service:** `DREFManager` (from `dref_temp/dref_utils.py`)
2. **Sources (tried sequentially):**
   - "basic" (primary) → `dref.json`
   - "op-update" (operational update) → `dref-op-update.json`
   - "final-report" (final report) → `dref-final-report.json`
3. **Filter:** `DREFFilters(field_report_ids=field_report_ids)`
4. **Data Parsing:** JSON → Dataclass objects (PlannedIntervention, NeedIdentified, etc.)
5. **Error Path:** No DREF found → `404 Not Found`

### Phase 5: DREF Data Transformation

1. **Selection:** First matching DREF
2. **Dictionary Creation:** Convert dataclass to dict with key fields:
   - `id`, `title`, `operation_objective`, `response_strategy`
   - `amount_requested`, `total_targeted_population`
   - `planned_interventions`, `national_society_actions`, `needs_identified`
   - `country_details`, `disaster_type_details`
   - `event_date`, `end_date`, `operation_timeframe`

### Phase 6: AI Summary Generation

1. **Service:** `DrefSummaryTask.generate_dref_summaries(dref_dict)` (from `ops_learning_summary3.py`)
2. **Input:** Complete DREF dictionary with all operational data
3. **Process Flow:**
   - **Phase 6.1:** Generate operational summary (3 lines maximum)
   - **Phase 6.2:** Organize data by humanitarian sectors using fuzzy matching
   - **Phase 6.3:** Generate per-sector summaries with AI processing
   - **Phase 6.4:** Assemble results with status and error tracking
4. **Output:** Structured response with operational summary, sector data, and processing status

---

## AI-Powered Summary Generation (DrefSummaryTask.generate_dref_summaries)

### Phase 6.1: Operational Summary Generation

1. **Method:** `generate_operational_summary(dref_data)`
2. **AI Analysis of:**
   - `operation_objective`, `response_strategy`, `title`
   - `total_targeted_population`, `people_in_need`, `amount_requested` 
   - `operation_timeframe`, `country_details`, `disaster_type_details`
   - `event_date`, `end_date`
3. **AI Processing:** Azure OpenAI with specific prompts for executive summary
4. **Output Format:** Exactly 3 lines:
   - Line 1: Overall objective of the operation
   - Line 2: Strategic rationale and approach
   - Line 3: Key operational details (population, timeline, budget)
5. **Fallback:** Simple text extraction if AI unavailable

### Phase 6.2: Sector Data Organization

1. **Method:** `organize_data_by_sector(dref_data)`
2. **Primary Data Source:** `planned_interventions` (defines available sectors)
3. **Sector Matching Process:**
   - **Step 1:** Create sectors from `planned_interventions.title`
   - **Step 2:** Match `needs_identified` to sectors by title with fuzzy matching
   - **Step 3:** Match `national_society_actions` to sectors by title
4. **Fuzzy Matching Logic:** Handles common title mismatches:
   - `multi_purpose_cash_grants` ↔ `multi_purpose_cash`
   - `shelter_housing_and_settlements` ↔ `shelter`
   - `water_sanitation_and_hygiene` ↔ `wash`
   - Partial string matching for similar titles

### Phase 6.3: Per-Sector AI Processing

**Method:** `generate_sector_summaries(organized_data)`

For each sector with matched data:

For each identified sector, generates three components:

#### A. Needs Summary (`generate_needs_summary`)
- **Input:** Combined descriptions from sector's `needs_identified`
- **AI Process:** Azure OpenAI with 2-sentence constraint
- **Fallback:** Simple text extraction when LLM fails
- **Output:** 2-sentence summary of critical humanitarian needs

#### B. Actions Taken Summary (`generate_actions_taken_summary`)
- **Input:** Combined `national_society_actions` + `planned_interventions` descriptions
- **AI Process:** Azure OpenAI with 2-sentence constraint
- **Output:** 2-sentence summary of actions taken/planned by National Society

#### C. Future Actions Processing (`process_planned_interventions`)
- **Input:** `planned_interventions` with indicators and budgets
- **Process:** Extract indicators, budgets, people targeted (no AI involved)
- **Output:** Structured data with indicators, budgets, descriptions

### Phase 6.4: Result Assembly

**Method combines:**
- Operational summary from Phase 6.1
- All sector summaries from Phase 6.3
- Status determination:
  - `"success"`: Both operational and sector summaries generated
  - `"partial_success"`: Only one component generated successfully  
  - `"failed"`: Neither component generated successfully
- Error collection from all phases

**Return Structure:**
```json
{
  "operational_summary": "3-line executive summary",
  "sectors": [
    {
      "title": "health",
      "title_display": "Health",
      "needs_summary": "2-sentence AI-generated needs summary",
      "actions_taken_summary": "2-sentence AI-generated actions summary", 
      "future_actions": [
        {
          "indicators": [{"title": "...", "people_targeted": 100}],
          "budget": 50000,
          "description": "...",
          "people_targeted_total": 1000
        }
      ]
    }
  ],
  "status": "success|partial_success|failed",
  "errors": ["error messages if any"]
}
```

---

## Azure OpenAI Configuration

- **Temperature:** `0.7` (balanced creativity for summary generation)
- **API Version:** `"2023-05-15"`
- **Token Limits:**
  - **Prompt tokens:** `10,000` (with automatic truncation)
  - **Data tokens:** `8,000` (with automatic truncation)
- **Models:** Uses `AzureOpenAiChat()` client from Django settings
- **Token Counting:** Uses `tiktoken` library for accurate token measurement
- **Fallback Strategy:** Simple text extraction when LLM service unavailable
- **Error Handling:** Graceful degradation with comprehensive status reporting

---

## Response Assembly

### Phase 7: Final Response Assembly

1. **Data Extraction from AI Summary:**
   - `summaries = DrefSummaryTask.generate_dref_summaries(dref_dict)`
   - Extracts `operational_summary` and `sectors` from AI response
   - AI processing status and errors are handled internally

2. **Response Structure Assembly:**
   - Combines AI-generated summaries with DREF metadata
   - Extracts operational update details if available
   - Builds metadata from original DREF dataclass

3. **Serializer:** `PerDrefLLMSummarySerializer`
4. **Final Response Structure:**

```json
{
  "operational_summary": "AI-generated 3-line operational summary",
  "sectors": [
    {
      "title": "health",
      "title_display": "Health", 
      "needs_summary": "AI-generated 2-sentence needs summary",
      "actions_taken_summary": "AI-generated 2-sentence actions summary",
      "future_actions": [
        {
          "indicators": [{"title": "People reached with health services", "people_targeted": 1000}],
          "budget": 50000,
          "description": "Provide emergency health services...",
          "people_targeted_total": 1000
        }
      ]
    }
  ],
  "dref_type": "Emergency" | "Imminent" | "Loan",
  "dref_onset": "Sudden" | "Slow",
  "metadata": {
    "dref_id": 123,
    "dref_title": "Emergency Response Operation",
    "dref_date": "2024-01-15",
    "dref_created_at": "2024-01-15T10:00:00Z",
    "dref_budget_file": "budget_preview_url_or_null",
    "dref_op_update_number": 1
  }
}
```

---

## Error Handling Matrix

| Error Type              | HTTP Code | Trigger                     | Response                  |
| ----------------------- | --------- | --------------------------- | ------------------------- |
| Missing event_id        | 400       | No id parameter             | Bad Request               |
| Invalid event_id        | 400       | Non-integer id              | Bad Request               |
| Event not found         | 404       | API returns 404             | Not Found                 |
| No field reports        | 404       | Empty field_reports         | Not Found                 |
| No DREF data            | 404       | No matching DREF            | Not Found                 |
| Unicode encoding error  | 500       | Emoji in debug prints       | Internal Server Error     |
| Azure OpenAI failure    | 500       | LLM service unavailable     | Fallback summary used     |
| Data parsing error      | 500       | Dataclass conversion fails  | Internal Server Error     |
| Service failure         | 500       | External API/AI errors      | Internal Server Error     |

---

## External Dependencies

1. **Event API Client:**
   - Base URL: `https://goadmin.ifrc.org`
   - Session management with `requests`
   - Timeout handling
   
2. **DREF Manager:**
   - File-based data from `per/dref_temp/`
   - JSON files: `dref.json`, `dref-op-update.json`, `dref-final-report.json`
   - Dataclass parsing with fallback for dict/dataclass compatibility
   - In-memory caching with `_cache` and `_parsed_cache`
   
3. **Azure OpenAI Service:**
   - API key authentication via environment variables
   - Token counting with `tiktoken`
   - Temperature `0.7` for balanced creativity
   - Fallback mechanism for service unavailability

---

## Data Flow Architecture

```
Event ID → Event API → Field Reports → DREF Manager → Latest DREF Version
    ↓
DREF Dictionary Creation → DrefSummaryTask.generate_dref_summaries()
    ↓
Phase 6.1: Operational Summary (AI)
├── Analyze operation objectives, strategy, population, budget
├── Generate 3-line executive summary via Azure OpenAI
└── Fallback to simple text extraction if AI fails
    ↓
Phase 6.2: Sector Organization
├── Extract sectors from planned_interventions
├── Match needs_identified to sectors (fuzzy matching)
└── Match national_society_actions to sectors (fuzzy matching)
    ↓
Phase 6.3: Per-Sector AI Processing
├── Needs Summary: AI-generated 2-sentence summary
├── Actions Summary: AI-generated 2-sentence summary
└── Future Actions: Structured data extraction (no AI)
    ↓
Phase 6.4: Result Assembly (status tracking, error collection)
    ↓
Response Assembly → PerDrefLLMSummarySerializer → HTTP Response
```

---

## Performance Characteristics

- **AI Processing:** Multiple Azure OpenAI calls (1 operational + N sector summaries)
- **Token Management:** 
  - 10,000 token limit for prompts, automatic truncation
  - 8,000 token limit for data, automatic truncation
  - Accurate token counting with `tiktoken` library
- **Sequential Processing:** DREF sources tried in order of preference (basic → op-update → final-report)
- **Fuzzy Matching:** O(n*m) complexity for sector title matching
- **LLM Call Pattern:** 1 operational + N sector calls (potentially parallelizable)
- **Memory Usage:** In-memory DREF data caching with dual cache system
- **Fallback Performance:** Simple text extraction maintains functionality when AI unavailable
- **Status Tracking:** Comprehensive success/failure reporting for each AI component

---

## Security Considerations

- **No Authentication:** Public endpoint
- **Input Validation:** Basic integer validation only
- **API Key Security:** Environment variable storage for Azure OpenAI
- **External API Calls:** Potential SSRF vulnerabilities from Event API
- **Unicode Handling:** Fixed encoding issues for Windows compatibility
- **Error Exposure:** Debug prints may expose internal data structure

---

## Key Technical Implementation Details

### DrefSummaryTask.generate_dref_summaries Return Structure
The AI service returns a structured response that PerDrefLLMSummaryView processes:

```python
{
    "operational_summary": str | None,      # 3-line executive summary 
    "sectors": List[Dict[str, Any]],        # Sector-based summaries
    "status": str,                          # "success", "partial_success", "failed"
    "errors": List[str]                     # Error messages if any issues occurred
}
```

### AI Processing Flow
1. **Operational Summary:** Single Azure OpenAI call analyzing overall DREF operation
2. **Sector Summaries:** Multiple Azure OpenAI calls (one per identified sector)
3. **Future Actions:** Non-AI structured data extraction from planned interventions
4. **Status Determination:** Based on success/failure of AI components
5. **Error Tracking:** Comprehensive logging of any processing failures

---

## Recent Improvements

1. **Fixed Unicode Encoding:** Removed emoji characters causing Windows crashes
2. **Added Fuzzy Matching:** Improved sector title matching with common mappings
3. **LLM Fallback:** Graceful degradation when Azure OpenAI unavailable
4. **Enhanced Debugging:** Clear success/failure indicators in logs
5. **Data Type Compatibility:** Improved dict/dataclass handling
6. **Response Cleaning:** Automatic whitespace and line break removal
7. **Status Tracking:** Added comprehensive AI processing status reporting

---

## Known Limitations

1. **Single Event Processing:** Only handles one event at a time
2. **Sequential DREF Search:** Could be optimized with parallel processing
3. **Hard-coded Mappings:** Sector title mappings need manual maintenance
4. **No Caching:** Each request triggers full data processing
5. **Limited Error Recovery:** Some failure modes still cause 500 errors
6. **Debug Verbosity:** Extensive logging may impact performance

---