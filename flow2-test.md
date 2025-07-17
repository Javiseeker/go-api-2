# PerDrefLLMSummaryView Complete Technical Flow

## Endpoint Configuration

- **URL:** `/api/v2/per-dref-summary/`
- **Method:** `GET` only
- **Authentication:** None (public endpoint)
- **Location:** `per/drf_views.py:374-575`

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
2. **Process:** Extract field report IDs
3. **Error Path:** No field reports → `404 Not Found`

### Phase 4: DREF Data Retrieval

1. **Service:** `DREFManager`
2. **Sources (tried sequentially):**
   - "basic" (primary)
   - "op-update" (operational update)
   - "final-report" (final report)
3. **Filter:** `DREFFilters(field_report_ids=field_report_ids)`
4. **Error Path:** No DREF found → `404 Not Found`

### Phase 5: DREF Data Transformation

1. **Selection:** First matching DREF
2. **Dictionary Creation:** Extract key fields:
   - `operation_objective`, `response_strategy`
   - `amount_requested`, `total_targeted_population`
   - `planned_interventions`, `country_details`
   - Budget breakdown processing

### Phase 6: AI Summary Generation

1. **Service:** `DrefSummaryTask.generate_dref_summaries()`
2. **Azure OpenAI Configuration:**
   - Temperature: `0.7`
   - API Version: `"2023-05-15"`
   - **Token Limits:**
     - **Prompt tokens:** `10,000`
     - **Data tokens:** `8,000`

### Phase 7: Dual Summary Creation

#### Operational Summary

- **Input:** `operation_objective`, `response_strategy`, context fields
- **Processing:** Token validation, data truncation if needed
- **Output:** 3-line plain text summary
- **Focus:** Objectives, strategy, key operational details

#### Budget Summary

- **Input:** `budget_fields`, `planned_interventions`
- **Processing:** Budget breakdown calculation, JSON formatting
- **Output:** Structured JSON with:
  - `budget_overview`
  - `sectoral_breakdown`
  - `financial_analysis`
  - `operational_costs`

### Phase 8: Response Assembly

1. **Serializer:** `PerDrefLLMSummarySerializer`
2. **Response Structure:**

   ```json
   {
     "operational_summary": "string",
     "budget_summary": {},
     "metadata": {
       "dref_id": "int",
       "dref_source": "string",
       "event_id": "int",
       "status": "success|partial_success|failed",
       "errors": []
     }
   }
   ```

---

## Error Handling Matrix

| Error Type       | HTTP Code | Trigger                | Response              |
| ---------------- | --------- | ---------------------- | --------------------- |
| Missing event_id | 400       | No id parameter        | Bad Request           |
| Invalid event_id | 400       | Non-integer id         | Bad Request           |
| Event not found  | 404       | API returns 404        | Not Found             |
| No field reports | 404       | Empty field_reports    | Not Found             |
| No DREF data     | 404       | No matching DREF       | Not Found             |
| Service failure  | 500       | External API/AI errors | Internal Server Error |

---

## External Dependencies

1. **Event API Client:**
   - Base URL: `https://goadmin.ifrc.org`
   - Session management with `requests`
   - Timeout handling
2. **DREF Manager:**
   - File-based data (JSON files)
   - In-memory caching
   - Multiple source fallback
3. **Azure OpenAI Service:**
   - API key authentication
   - Token counting with `tiktoken`
   - Temperature `0.7` for balanced creativity

---

## Performance Characteristics

- **Token Management:** 8,000 token limit for data, automatic truncation
- **Sequential Processing:** DREF sources tried in order
- **External API Dependency:** Single points of failure
- **Memory Usage:** In-memory DREF data caching

---

## Security Considerations

- **No Authentication:** Public endpoint
- **Input Validation:** Basic integer validation only
- **API Key Security:** Environment variable storage
- **External API Calls:** Potential SSRF vulnerabilities

---
