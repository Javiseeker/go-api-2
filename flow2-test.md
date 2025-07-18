# PerDrefLLMSummaryView Complete Technical Flow

## Endpoint Configuration

- **URL:** `/api/v2/per-dref-summary/`
- **Method:** `GET` only
- **Authentication:** None (public endpoint)
- **Location:** `per/drf_views.py:374-586`

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

1. **Service:** `DrefSummaryTask.generate_dref_summaries()` (from `ops_learning_summary3.py`)
2. **Sub-phases:**
   - **Pre-processing:** Get latest DREF version
   - **Operational Summary:** Generate 3-line operational summary
   - **Sector Summaries:** Generate per-sector summaries with needs analysis

---

## Sector-Based Summary Generation (New Feature)

### Phase 6.1: Sector Organization

1. **Primary Data Source:** `planned_interventions` (defines available sectors)
2. **Sector Matching Process:**
   - **Step 1:** Create sectors from `planned_interventions.title`
   - **Step 2:** Match `needs_identified` to sectors by title
   - **Step 3:** Match `national_society_actions` to sectors by title
3. **Fuzzy Matching:** Handle common title mismatches:
   - `multi_purpose_cash_grants` → `multi_purpose_cash`
   - `shelter_housing_and_settlements` → `shelter`
   - `water_sanitation_and_hygiene` → `wash`
   - Partial string matching for similar titles

### Phase 6.2: Per-Sector Summary Generation

For each sector with matched data:

#### A. Needs Summary
- **Input:** `needs_identified` descriptions for the sector
- **Process:** Azure OpenAI with 2-sentence limit
- **Fallback:** Simple text extraction when LLM fails
- **Output:** 2-sentence summary of critical needs

#### B. Actions Taken Summary
- **Input:** Combined `national_society_actions` + `planned_interventions` descriptions
- **Process:** Azure OpenAI with 2-sentence limit
- **Output:** 2-sentence summary of actions taken/planned

#### C. Future Actions Processing
- **Input:** `planned_interventions` with indicators
- **Process:** Extract indicators, budgets, people targeted
- **Output:** Structured future actions data

### Phase 6.3: Response Structure

Each sector returns:
```json
{
  "title": "health",
  "title_display": "Health",
  "needs_summary": "2-sentence summary of needs",
  "actions_taken_summary": "2-sentence summary of actions",
  "future_actions": [
    {
      "indicators": [{"title": "...", "people_targeted": 100}],
      "budget": 50000,
      "description": "...",
      "people_targeted_total": 1000
    }
  ]
}
```

---

## Azure OpenAI Configuration

- **Temperature:** `0.7`
- **API Version:** `"2023-05-15"`
- **Token Limits:**
  - **Prompt tokens:** `10,000`
  - **Data tokens:** `8,000`
- **Models:** Uses `AzureOpenAiChat()` client
- **Fallback:** Simple text extraction when LLM unavailable

---

## Response Assembly

### Phase 7: Final Response Structure

1. **Serializer:** `PerDrefLLMSummarySerializer`
2. **Response Structure:**

```json
{
  "operational_summary": "3-line operational summary",
  "sectors": [
    {
      "title": "health",
      "title_display": "Health", 
      "needs_summary": "2-sentence needs summary",
      "actions_taken_summary": "2-sentence actions summary",
      "future_actions": [...]
    }
  ],
  "dref_type": "Imminent",
  "dref_onset": "Sudden",
  "metadata": {
    "dref_id": 123,
    "dref_title": "Emergency Response",
    "dref_date": "2024-01-15",
    "dref_created_at": "2024-01-15T10:00:00Z",
    "dref_budget_file_created_by": "",
    "dref_op_update_number": 0,
    "operational_update_details": "Event: ..., Source: basic, Reports: 2"
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
Event ID → Event API → Field Reports → DREF Manager → Dataclass Objects
    ↓
JSON Parsing → Sector Organization → Fuzzy Title Matching
    ↓
Per-Sector Processing:
├── Needs Summary (LLM/Fallback)
├── Actions Summary (LLM/Fallback)  
└── Future Actions (Structured)
    ↓
Response Assembly → Serialization → HTTP Response
```

---

## Performance Characteristics

- **Token Management:** 8,000 token limit for data, automatic truncation
- **Sequential Processing:** DREF sources tried in order of preference
- **Fuzzy Matching:** O(n*m) complexity for sector title matching
- **LLM Calls:** Multiple Azure OpenAI calls per sector (parallelizable)
- **Memory Usage:** In-memory DREF data caching with dual cache system
- **Fallback Performance:** Simple text extraction when LLM fails

---

## Security Considerations

- **No Authentication:** Public endpoint
- **Input Validation:** Basic integer validation only
- **API Key Security:** Environment variable storage for Azure OpenAI
- **External API Calls:** Potential SSRF vulnerabilities from Event API
- **Unicode Handling:** Fixed encoding issues for Windows compatibility
- **Error Exposure:** Debug prints may expose internal data structure

---

## Recent Improvements

1. **Fixed Unicode Encoding:** Removed emoji characters causing Windows crashes
2. **Added Fuzzy Matching:** Improved sector title matching with common mappings
3. **LLM Fallback:** Graceful degradation when Azure OpenAI unavailable
4. **Enhanced Debugging:** Clear success/failure indicators in logs
5. **Data Type Compatibility:** Improved dict/dataclass handling
6. **Response Cleaning:** Automatic whitespace and line break removal

---

## Known Limitations

1. **Single Event Processing:** Only handles one event at a time
2. **Sequential DREF Search:** Could be optimized with parallel processing
3. **Hard-coded Mappings:** Sector title mappings need manual maintenance
4. **No Caching:** Each request triggers full data processing
5. **Limited Error Recovery:** Some failure modes still cause 500 errors
6. **Debug Verbosity:** Extensive logging may impact performance

---