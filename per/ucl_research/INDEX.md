# PER Module Index

This document provides a comprehensive index of all files in the `per/` directory, with special focus on the `ucl_research/` subdirectory.

## Directory Structure

```
per/
├── ucl_research/          # UCL Research specific functionality
├── migrations/            # Database migrations
├── dref_temp/            # DREF temporary files
├── locale/               # Localization files
├── management/           # Django management commands
├── fixtures/             # Test fixtures
└── __pycache__/          # Python cache files
```

## UCL Research Directory (`per/ucl_research/`)

### Core Files

#### 1. `ops_learning_summary4.py` (131KB, 2841 lines)
**Purpose**: Consolidated operational learning summary processor
**Key Classes**:
- `BaseAITask`: Base class with Azure OpenAI integration
- `OpsLearningSummaryTask`: Complex ops learning analysis
- `DrefSummaryTask`: DREF-specific operations
- `RRCapacityTask`: Rapid response capacity analysis
- `PerformanceMonitor`: Execution time tracking

**Key Functions**:
- `generate_summary()`: Generate AI-powered summaries
- `fetch_ops_learnings()`: Fetch operational learnings from database
- `slice_dataframe()`: Process large datasets efficiently

#### 2. `ucl_views.py` (32KB, 730 lines)
**Purpose**: UCL Research specific Django views
**Key Features**:
- API endpoints for UCL research functionality
- Integration with Azure OpenAI services
- Rapid response capacity analysis endpoints

#### 3. `ifrc_client.py` (14KB, 455 lines)
**Purpose**: IFRC API client for data retrieval
**Key Classes**:
- `IFRCAPIClient`: Main API client with async support
- `LegacyEventAPIClient`: Legacy event API client
- `LegacyFieldReportAPIClient`: Legacy field report API client

**Key Methods**:
- `get_event_detail()`: Retrieve event details
- `get_events()`: Fetch events with filtering
- `get_field_reports()`: Get field reports
- `get_ops_learning()`: Retrieve operational learning data

#### 4. `rapid_response_parser.py` (21KB, 521 lines)
**Purpose**: Rapid response capacity question processing
**Key Classes**:
- `RapidResponseCapacityParser`: Main parser class

**Key Features**:
- Excel file generation with styling
- Question data processing from JSON
- Country-specific analysis
- Source tracking and validation

#### 5. `serializers.py` (5.9KB, 121 lines)
**Purpose**: Django REST Framework serializers for UCL research
**Key Serializers**:
- `PerDrefLLMSummarySerializer`: DREF LLM summary serialization
- `PerDrefSituationalOverviewSerializer`: Situational overview serialization
- `IFRCEventLearningSerializer`: Event learning data serialization
- `IFRCEventSummarySerializer`: Event summary serialization
- `RRCapacityQuestionsResponseSerializer`: RR capacity questions response

#### 6. `blob_upload.py` (1.7KB, 55 lines)
**Purpose**: Azure Blob Storage upload functionality
**Key Functions**:
- `upload_to_blob()`: Upload files to Azure Blob Storage
- Standalone functionality copied from `per.blob_upload`

#### 7. `__init__.py` (1.2KB, 29 lines)
**Purpose**: Package initialization and exports

#### 8. `README.md` (41KB, 1061 lines)
**Purpose**: Comprehensive documentation for UCL research module

#### 9. `rr_parsed_excel.json` (30KB, 213 lines)
**Purpose**: JSON data file containing parsed rapid response questions

## Main PER Directory Files

### Core Models and Views

#### `models.py` (35KB, 882 lines)
**Purpose**: Django models for PER functionality
**Key Models**:
- `OpsLearningCacheResponse`: Cached operational learning responses
- `OpsLearningPromptResponseCache`: Prompt response caching
- `PerDrefLLMSummary`: DREF LLM summaries
- `PerDrefSituationalOverview`: Situational overviews

#### `views.py` (22KB, 475 lines)
**Purpose**: Main Django views for PER functionality
**Key Features**:
- API endpoints for PER operations
- Request handling and response formatting

#### `drf_views.py` (72KB, 1778 lines)
**Purpose**: Django REST Framework views
**Key Features**:
- RESTful API endpoints
- Advanced filtering and pagination
- Complex query handling

### Serialization and Validation

#### `serializers.py` (45KB, 1378 lines)
**Purpose**: Comprehensive serialization for PER models
**Key Features**:
- Model serialization
- Nested object handling
- Validation logic

#### `validators.py` (966B, 33 lines)
**Purpose**: Custom validation logic for PER models

### Azure Integration

#### `azure_client_2.py` (32KB, 684 lines)
**Purpose**: Enhanced Azure client functionality
**Key Features**:
- Azure OpenAI integration
- Advanced client management
- Error handling and retry logic

#### `azure_service.py` (3.3KB, 75 lines)
**Purpose**: Azure service layer abstraction

#### `blob_upload.py` (691B, 22 lines)
**Purpose**: Azure Blob Storage upload utilities

### Learning and Analysis

#### `ops_learning_summary.py` (48KB, 1085 lines)
**Purpose**: Original operational learning summary processor

#### `ops_learning_summary2.py` (47KB, 1069 lines)
**Purpose**: Enhanced version with additional features

#### `ops_learning_summary3.py` (30KB, 638 lines)
**Purpose**: Streamlined version with focused functionality

### Rapid Response

#### `rr_endpoint.py` (37KB, 817 lines)
**Purpose**: Rapid response endpoint functionality
**Key Features**:
- RR capacity questions handling
- Data processing and analysis

#### `rr_form_prompt.py` (20KB, 377 lines)
**Purpose**: Rapid response form prompt generation

### Administrative

#### `admin.py` (14KB, 411 lines)
**Purpose**: Django admin interface configuration

#### `admin_classes.py` (6.1KB, 153 lines)
**Purpose**: Custom admin classes and functionality

#### `permissions.py` (3.8KB, 103 lines)
**Purpose**: Custom permission classes and logic

### Utilities and Support

#### `utils.py` (1.1KB, 29 lines)
**Purpose**: General utility functions

#### `translation.py` (3.1KB, 147 lines)
**Purpose**: Translation and localization utilities

#### `cache.py` (2.5KB, 76 lines)
**Purpose**: Caching utilities and helpers

#### `custom_renderers.py` (2.3KB, 51 lines)
**Purpose**: Custom response renderers

#### `filter_set.py` (1.3KB, 55 lines)
**Purpose**: Custom filter sets for queries

#### `task.py` (4.6KB, 105 lines)
**Purpose**: Background task definitions

#### `event_api_client.py` (1.4KB, 46 lines)
**Purpose**: Event API client functionality

#### `field_report_api_client.py` (1.5KB, 46 lines)
**Purpose**: Field report API client functionality

### Testing

#### `test_views.py` (15KB, 357 lines)
**Purpose**: View testing and test cases

#### `tests.py` (0B, 0 lines)
**Purpose**: Placeholder for additional tests

#### `factories.py` (4.1KB, 157 lines)
**Purpose**: Test data factories

### Configuration

#### `apps.py` (164B, 8 lines)
**Purpose**: Django app configuration

#### `enums.py` (400B, 11 lines)
**Purpose**: Enumeration definitions

## Key Dependencies

### External Libraries
- `openai`: Azure OpenAI integration
- `pandas`: Data processing and analysis
- `httpx`: Async HTTP client
- `openpyxl`: Excel file generation
- `tiktoken`: Token counting for AI models

### Internal Dependencies
- `api.logger`: Logging functionality
- `api.models`: Core API models
- `api.utils`: Utility functions
- `deployments.models`: Deployment-related models
- `lang.tasks`: Translation tasks
- `main.lock`: Redis locking functionality

## File Size Summary

### Large Files (>10KB)
1. `ops_learning_summary4.py` - 131KB (2841 lines)
2. `drf_views.py` - 72KB (1778 lines)
3. `serializers.py` - 45KB (1378 lines)
4. `ops_learning_summary.py` - 48KB (1085 lines)
5. `ops_learning_summary2.py` - 47KB (1069 lines)
6. `rr_endpoint.py` - 37KB (817 lines)
7. `models.py` - 35KB (882 lines)
8. `azure_client_2.py` - 32KB (684 lines)
9. `ucl_views.py` - 32KB (730 lines)
10. `ops_learning_summary3.py` - 30KB (638 lines)
11. `rapid_response_parser.py` - 21KB (521 lines)
12. `rr_form_prompt.py` - 20KB (377 lines)
13. `test_views.py` - 15KB (357 lines)
14. `admin.py` - 14KB (411 lines)
15. `ifrc_client.py` - 14KB (455 lines)
16. `views.py` - 22KB (475 lines)

### Medium Files (1-10KB)
- `admin_classes.py` - 6.1KB
- `serializers.py` (ucl_research) - 5.9KB
- `task.py` - 4.6KB
- `factories.py` - 4.1KB
- `azure_service.py` - 3.3KB
- `translation.py` - 3.1KB
- `permissions.py` - 3.8KB
- `cache.py` - 2.5KB
- `custom_renderers.py` - 2.3KB
- `filter_set.py` - 1.3KB
- `__init__.py` (ucl_research) - 1.2KB
- `utils.py` - 1.1KB
- `event_api_client.py` - 1.4KB
- `field_report_api_client.py` - 1.5KB
- `blob_upload.py` (ucl_research) - 1.7KB

### Small Files (<1KB)
- `apps.py` - 164B
- `enums.py` - 400B
- `blob_upload.py` - 691B
- `validators.py` - 966B
- `tests.py` - 0B

## Special Notes

1. **UCL Research Focus**: The `ucl_research/` directory contains specialized functionality for UCL research projects, with the largest file being `ops_learning_summary4.py` which consolidates multiple operational learning processors.

2. **AI Integration**: Multiple files integrate with Azure OpenAI services for AI-powered analysis and summarization.

3. **Data Processing**: Heavy use of pandas for data manipulation and analysis across multiple files.

4. **Caching Strategy**: Comprehensive caching implementation across multiple files for performance optimization.

5. **Async Support**: Modern async/await patterns implemented in `ifrc_client.py` and other files.

6. **Excel Generation**: Advanced Excel file generation with styling in `rapid_response_parser.py`.

This index provides a comprehensive overview of the PER module structure, with special attention to the UCL research functionality that appears to be the primary focus of development. 