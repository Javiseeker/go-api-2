import requests
from typing import List, Optional, Any


class EventAPIClient:
    def __init__(self, base_url: str = "https://goadmin.ifrc.org"):
        self.base_url = base_url
        self.session = requests.Session()

    def get_events(self, **params) -> List[dict]:
        """
        Get list of events with optional filtering parameters.
        
        Args:
            **params: Query parameters for filtering events
            
        Returns:
            List of event dictionaries
        """
        try:
            response = self.session.get(f"{self.base_url}/api/v2/event/", params=params)
            response.raise_for_status()
            data = response.json()
            return data['results']
        except requests.RequestException as e:
            print(f"Error fetching events: {e}")
            return []

    def get_event_detail(self, event_id: int) -> Optional[dict]:
        """
        Get detailed information for a specific event.
        
        Args:
            event_id: The ID of the event
            
        Returns:
            Event dictionary or None if not found
        """
        try:
            response = self.session.get(f"{self.base_url}/api/v2/event/{event_id}/")
            response.raise_for_status()
            data = response.json()
            return data
        except requests.RequestException as e:
            print(f"Error fetching event detail: {e}")
            return None