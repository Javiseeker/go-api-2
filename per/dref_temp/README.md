# DREF Utils

Simple utilities for working with DREF operations data in Django.

## Quick Start

```python
from per.dref_temp import dref_manager, DREFFilters

# Get all operations
operations = dref_manager.get_data('basic')

# Search by country
haiti_ops = dref_manager.get_data('basic', DREFFilters(country_name='haiti'))

# Search by field report IDs (for linking with events)
field_report_ids = [17010, 17005, 17003]
matching_drefs = dref_manager.get_data('basic', DREFFilters(field_report_ids=field_report_ids))
```

## Common Filters

```python
filters = DREFFilters(
    country_name="haiti",           # Search by country
    disaster_type_name="earthquake", # Search by disaster type
    field_report_ids=[123, 456],    # Match multiple field reports
    min_people_affected=10000,      # Minimum people affected
    event_date_from="2024-01-01",   # Events after date
    search_text="hurricane"         # Text search
)

operations = dref_manager.get_data('basic', filters)
```

## Data Sources

- `'basic'` - Current operations and applications
- `'final-report'` - Completed operations with results
- `'op-update'` - Operations with updates/changes

## Operation Data

Each operation includes:

```python
{
    'id': 123,
    'title': 'Haiti Earthquake Response',
    'appeal_code': 'MDRHT008',
    'country_details': {'name': 'Haiti', 'iso': 'HT'},
    'disaster_type_details': {'name': 'Earthquake'},
    'number_of_people_affected': 100000,
    'total_dref_allocation': 500000,
    'event_date': '2024-01-15',
    'field_report': 17010  # Links to field reports
}
```

## Helper Functions

```python
# Get lists for dropdowns
countries = dref_manager.get_unique_countries('basic')
disaster_types = dref_manager.get_unique_disaster_types('basic')

# Clear cache if JSON files updated
dref_manager.clear_cache()
```

## Common Use Cases

### Find DREF by Event
```python
def find_dref_by_event(event_id):
    # Get field reports for event
    field_reports = get_field_reports_for_event(event_id)
    field_report_ids = [fr['id'] for fr in field_reports]
    
    # Find matching DREFs
    filters = DREFFilters(field_report_ids=field_report_ids)
    return dref_manager.get_data('basic', filters)
```

### Search Operations
```python
def search_operations(country=None, disaster_type=None):
    filters = DREFFilters()
    if country:
        filters.country_name = country
    if disaster_type:
        filters.disaster_type_name = disaster_type
    
    return dref_manager.get_data('basic', filters)
```

That's it! Simple and straightforward. Test