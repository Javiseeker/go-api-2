import requests
from api.serializers import ListEventSerializer, DetailEventSerializer
from typing import List, Optional, Any
 

#  https://goadmin.ifrc.org/api/v2/event/?id={event_id}

class EventAPIClient:
    def __init__(self, base_url: str= "https://goadmin.ifrc.org"):
        self.base_url = base_url
        self.session = requests.Session()

    def get_events(self, **params) -> List[dict]:
        try:
            response = self.session.get(f"{self.base_url}/api/v2/event/", params=params)
            response.raise_for_status()
            data = response.json()
            
            serializer = ListEventSerializer(data=data['results'], many=True)
            if serializer.is_valid():
                return list(serializer.data)  # Access data after validation
            else:
                print(f"Validation errors: {serializer.errors}")
                return data['results']
        except requests.RequestException as e:
            print(f"Error fetching events: {e}")
            return []

    def get_event_detail(self, event_id: int) -> Optional[dict]:
        try:
            response = self.session.get(f"{self.base_url}/api/v2/event/{event_id}/")
            response.raise_for_status()
            data = response.json()
            
            serializer = DetailEventSerializer(data=data)
            if serializer.is_valid():
                return dict(serializer.data)  # Access data after validation
            else:
                print(f"Validation errors: {serializer.errors}")
                return data
        except requests.RequestException as e:
            print(f"Error fetching event detail: {e}")
            return None