# per/dref_temp/examples.py

"""
Simple examples of using DREF utilities
"""

from .dref_utils import dref_manager, DREFFilters

def get_all_operations():
    """Get all DREF operations"""
    operations = dref_manager.get_data('basic')
    
    return [{
        'id': op.id,
        'title': op.title,
        'appeal_code': op.appeal_code,
        'country': op.country_details.name,
        'disaster_type': op.disaster_type_details.name,
        'people_affected': op.number_of_people_affected,
        'budget': op.total_dref_allocation,
        'event_date': op.event_date
    } for op in operations]

def search_by_country(country_name):
    """Find operations by country"""
    filters = DREFFilters(country_name=country_name)
    operations = dref_manager.get_data('basic', filters)
    
    return operations

def search_by_disaster_type(disaster_type):
    """Find operations by disaster type"""
    filters = DREFFilters(disaster_type_name=disaster_type)
    operations = dref_manager.get_data('basic', filters)
    
    return operations

def search_by_field_report_ids(field_report_ids):
    """Find DREFs by field report IDs"""
    filters = DREFFilters(field_report_ids=field_report_ids)
    operations = dref_manager.get_data('basic', filters)
    
    return operations

def search_recent_operations(days=30):
    """Get recent operations"""
    from datetime import datetime, timedelta
    
    cutoff_date = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
    filters = DREFFilters(created_from=cutoff_date)
    operations = dref_manager.get_data('basic', filters)
    
    return operations

def search_large_operations(min_people=50000):
    """Get large scale operations"""
    filters = DREFFilters(min_people_affected=min_people)
    operations = dref_manager.get_data('basic', filters)
    
    return operations

def get_countries():
    """Get list of available countries"""
    countries = dref_manager.get_unique_countries('basic')
    return [{'id': c.id, 'name': c.name, 'iso': c.iso} for c in countries]

def get_disaster_types():
    """Get list of available disaster types"""
    disaster_types = dref_manager.get_unique_disaster_types('basic')
    return [{'id': dt.id, 'name': dt.name} for dt in disaster_types]