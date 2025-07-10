import requests
from typing import List, Optional, Any


class FieldReportAPIClient:
    def __init__(self, base_url: str = "https://goadmin.ifrc.org"):
        self.base_url = base_url
        self.session = requests.Session()

    def get_field_reports(self, **params) -> List[dict]:
        """
        Get list of field reports with optional filtering parameters.
        
        Args:
            **params: Query parameters like event, country, dtype, etc.
            
        Returns:
            List of field report dictionaries
        """
        try:
            response = self.session.get(f"{self.base_url}/api/v2/field-report/", params=params)
            response.raise_for_status()
            data = response.json()
            return data['results']
        except requests.RequestException as e:
            print(f"Error fetching field reports: {e}")
            return []

    def get_field_reports_by_event(self, event_id: int) -> List[dict]:
        """
        Get field reports for a specific event.
        
        Args:
            event_id: The ID of the event
            
        Returns:
            List of field report dictionaries for the event
        """
        try:
            response = self.session.get(f"{self.base_url}/api/v2/field-report/?event={event_id}")
            response.raise_for_status()
            data = response.json()
            return data['results']
        except requests.RequestException as e:
            print(f"Error fetching field reports for event {event_id}: {e}")
            return []