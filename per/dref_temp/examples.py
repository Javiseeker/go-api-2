# per/dref_temp/examples.py

"""
Examples of how to use DREF utilities in Django views and other parts of the project
Updated with event_map_file_id filter and performance optimizations
"""

from .dref_utils import dref_manager, DREFFilters
from .models import DREFData

# Example 1: Basic usage in Django views
def get_dref_operations_view_example():
    """Example for use in drf_views.py"""
    
    # Load all final report operations
    operations = dref_manager.get_data('final-report')
    
    # Convert to simple dict format for API response
    operations_data = []
    for op in operations:
        operations_data.append({
            'id': op.id,
            'title': op.title,
            'appeal_code': op.appeal_code,
            'country': op.country_details.name,
            'disaster_type': op.disaster_type_details.name,
            'people_affected': op.number_of_people_affected,
            'event_date': op.event_date,
            'budget': op.total_dref_allocation,
            'event_map_file_id': op.event_map_file.id if op.event_map_file else None
        })
    
    return operations_data

# Example 2: Filtered data for API endpoints
def get_filtered_operations(country=None, disaster_type=None, year=None):
    """Example of filtering for API responses"""
    
    filters = DREFFilters()
    
    if country:
        filters.country_name = country
    if disaster_type:
        filters.disaster_type_name = disaster_type
    if year:
        filters.event_date_from = f"{year}-01-01"
        filters.event_date_to = f"{year}-12-31"
    
    # Now more efficient - only parses matching records
    operations = dref_manager.get_data('final-report', filters)
    
    return [{
        'id': op.id,
        'title': op.title,
        'appeal_code': op.appeal_code,
        'country': op.country_details.name,
        'disaster_type': op.disaster_type_details.name,
        'people_affected': op.number_of_people_affected,
        'event_date': op.event_date,
        'budget': op.total_dref_allocation,
        'event_map_file_id': op.event_map_file.id if op.event_map_file else None
    } for op in operations]


def search_operations(query):
    """Example search function"""
    
    filters = DREFFilters(search_text=query)
    operations = dref_manager.get_data('final-report', filters)
    
    return [{
        'id': op.id,
        'title': op.title,
        'appeal_code': op.appeal_code,
        'country': op.country_details.name,
        'disaster_type': op.disaster_type_details.name,
        'relevance_score': 1.0,  # You could implement actual relevance scoring
        'event_map_file_id': op.event_map_file.id if op.event_map_file else None
    } for op in operations]

# Example 5: Country-specific operations
def get_operations_by_country(country_iso):
    """Get operations for a specific country"""
    
    filters = DREFFilters(country_iso=country_iso)
    operations = dref_manager.get_data('final-report', filters)
    
    return operations

# Example 6: Recent operations
def get_recent_operations(days=30):
    """Get recent operations"""
    from datetime import datetime, timedelta
    
    cutoff_date = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
    filters = DREFFilters(created_from=cutoff_date)
    
    operations = dref_manager.get_data('basic', filters)
    
    return operations

# Example 7: Large scale operations
def get_large_scale_operations(min_people=50000):
    """Get large scale operations"""
    
    filters = DREFFilters(min_people_affected=min_people)
    operations = dref_manager.get_data('final-report', filters)
    
    return operations

# Example 8: Operations by disaster type
def get_operations_by_disaster_type(disaster_type):
    """Get operations by disaster type"""
    
    filters = DREFFilters(disaster_type_name=disaster_type)
    operations = dref_manager.get_data('final-report', filters)
    
    return operations

# Example 9: NEW - Operations by event map file ID
def get_operations_by_event_map_file(event_map_file_id):
    """Get operations by event map file ID"""
    
    filters = DREFFilters(event_map_file_id=event_map_file_id)
    operations = dref_manager.get_data('basic', filters)
    
    return operations

# Example 10: NEW - Find all operations sharing the same event map
def get_related_operations_by_event_map(operation_id):
    """Find operations that share the same event map file"""
    
    # First get the operation to find its event map file ID
    operation = dref_manager.get_data('basic', DREFFilters(id=operation_id))
    if not operation or not operation[0].event_map_file:
        return []
    
    event_map_file_id = operation[0].event_map_file.id
    
    # Find all operations with the same event map file ID
    filters = DREFFilters(event_map_file_id=event_map_file_id)
    related_operations = dref_manager.get_data('basic', filters)
    
    # Exclude the original operation
    return [op for op in related_operations if op.id != operation_id]

# Example 11: Available options for dropdowns/filters
def get_filter_options():
    """Get available options for UI filters"""
    
    disaster_types = dref_manager.get_unique_disaster_types('final-report')
    countries = dref_manager.get_unique_countries('final-report')
    
    return {
        'disaster_types': [{'id': dt.id, 'name': dt.name} for dt in disaster_types],
        'countries': [{'id': c.id, 'name': c.name, 'iso': c.iso} for c in countries],
        'regions': [
            {'id': 1, 'name': 'Americas'},
            {'id': 2, 'name': 'Asia Pacific'},
            {'id': 3, 'name': 'Europe'},
            {'id': 4, 'name': 'MENA'},
            {'id': 5, 'name': 'Africa'}
        ]
    }

# Example 12: Complex filtering for advanced search
def advanced_search(country=None, disaster_type=None, min_budget=None, 
                   max_budget=None, date_from=None, date_to=None, 
                   event_map_file_id=None):
    """Advanced search with multiple filters"""
    
    filters = DREFFilters(
        country_name=country,
        disaster_type_name=disaster_type,
        min_budget=min_budget,
        max_budget=max_budget,
        event_date_from=date_from,
        event_date_to=date_to,
        event_map_file_id=event_map_file_id  # NEW
    )
    
    operations = dref_manager.get_data('final-report', filters)
    
    return operations

# Example 13: Performance-optimized queries
def get_high_impact_operations_optimized():
    """Example of using filters for better performance"""
    
    # ✅ GOOD: Use specific filters first
    # This will only parse records that match these criteria
    filters = DREFFilters(
        min_people_affected=100000,     # Early filter
        min_budget=500000,              # Early filter
        is_published=True,              # Early filter
        disaster_type_name='earthquake' # Later filter (after parsing)
    )
    
    operations = dref_manager.get_data('final-report', filters)
    
    return [{
        'id': op.id,
        'title': op.title,
        'impact_score': (op.number_of_people_affected or 0) * 0.7 + 
                       (op.total_dref_allocation or 0) * 0.3,
        'people_affected': op.number_of_people_affected,
        'budget': op.total_dref_allocation,
        'country': op.country_details.name,
        'disaster_type': op.disaster_type_details.name
    } for op in operations]

# Example 14: Multiple event map file IDs
def get_operations_by_multiple_event_maps(event_map_file_ids):
    """Get operations for multiple event map file IDs"""
    
    all_operations = []
    for event_id in event_map_file_ids:
        filters = DREFFilters(event_map_file_id=event_id)
        operations = dref_manager.get_data('basic', filters)
        all_operations.extend(operations)
    
    # Remove duplicates by ID
    seen_ids = set()
    unique_operations = []
    for op in all_operations:
        if op.id not in seen_ids:
            seen_ids.add(op.id)
            unique_operations.append(op)
    
    return unique_operations

# Example 15: Event map file analytics
def get_event_map_file_analytics():
    """Get analytics about event map file usage"""
    
    all_operations = dref_manager.get_data('basic')
    
    # Count operations by event map file
    event_map_usage = {}
    operations_without_map = 0
    
    for op in all_operations:
        if op.event_map_file:
            event_id = op.event_map_file.id
            if event_id not in event_map_usage:
                event_map_usage[event_id] = {
                    'count': 0,
                    'operations': [],
                    'countries': set(),
                    'disaster_types': set()
                }
            event_map_usage[event_id]['count'] += 1
            event_map_usage[event_id]['operations'].append(op.id)
            event_map_usage[event_id]['countries'].add(op.country_details.name)
            event_map_usage[event_id]['disaster_types'].add(op.disaster_type_details.name)
        else:
            operations_without_map += 1
    
    # Convert sets to lists for JSON serialization
    for event_id in event_map_usage:
        event_map_usage[event_id]['countries'] = list(event_map_usage[event_id]['countries'])
        event_map_usage[event_id]['disaster_types'] = list(event_map_usage[event_id]['disaster_types'])
    
    return {
        'total_operations': len(all_operations),
        'operations_with_map': len(all_operations) - operations_without_map,
        'operations_without_map': operations_without_map,
        'unique_event_maps': len(event_map_usage),
        'event_map_usage': event_map_usage,
        'most_used_event_maps': sorted(
            event_map_usage.items(), 
            key=lambda x: x[1]['count'], 
            reverse=True
        )[:10]
    }