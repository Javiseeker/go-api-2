"""
IFRC Client for HTTP Requests
=============================

Unified HTTP client for all IFRC API requests using httpx with async/await syntax.
Consolidates all HTTP operations from the 4 views into a single, maintainable client.

Features:
- httpx async/await for all requests
- Comprehensive error handling
- Request logging and debugging
- Connection pooling and timeouts
- Retry logic with exponential backoff
"""

import asyncio
from typing import Dict, Any, List, Optional, Union
import httpx
from api.logger import logger


class IFRCAPIClient:
    """
    Unified async HTTP client for all IFRC API interactions.
    Replaces EventAPIClient, FieldReportAPIClient, and direct httpx calls.
    """
    
    DEFAULT_BASE_URL = "https://goadmin.ifrc.org"
    DEFAULT_TIMEOUT = 10.0
    MAX_RETRIES = 3
    
    def __init__(
        self, 
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = DEFAULT_TIMEOUT,
        max_retries: int = MAX_RETRIES
    ):
        self.base_url = base_url.rstrip('/')
        self.timeout = timeout
        self.max_retries = max_retries
        self._client = None
    
    async def __aenter__(self):
        """Async context manager entry"""
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(self.timeout),
            follow_redirects=True
        )
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit"""
        if self._client:
            await self._client.aclose()
    
    @property
    def client(self):
        """Get or create httpx client"""
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.timeout),
                follow_redirects=True
            )
        return self._client
    
    async def _make_request(
        self, 
        method: str, 
        endpoint: str, 
        params: Optional[Dict[str, Any]] = None,
        json_data: Optional[Dict[str, Any]] = None,
        data_type: str = "request"
    ) -> Dict[str, Any]:
        """
        Make HTTP request with retry logic and error handling.
        
        Args:
            method: HTTP method (GET, POST, etc.)
            endpoint: API endpoint path
            params: Query parameters
            json_data: JSON payload for POST requests
            data_type: Description for logging
            
        Returns:
            Response JSON data
        """
        url = f"{self.base_url}{endpoint}"
        
        for attempt in range(self.max_retries + 1):
            try:
                
                response = await self.client.request(
                    method=method,
                    url=url,
                    params=params,
                    json=json_data
                )
                
                response.raise_for_status()
                data = response.json()
                
                return data
                
            except httpx.HTTPStatusError as e:
                logger.warning(f"⚠️ HTTP {e.response.status_code} error for {data_type}: {e}")
                if e.response.status_code == 404:
                    return {}
                if attempt == self.max_retries:
                    raise
                    
            except httpx.RequestError as e:
                logger.warning(f"⚠️ Request error for {data_type} (attempt {attempt + 1}): {e}")
                if attempt == self.max_retries:
                    raise
                
            except Exception as e:
                logger.error(f"❌ Unexpected error for {data_type}: {e}")
                if attempt == self.max_retries:
                    raise
                
            # Exponential backoff
            if attempt < self.max_retries:
                wait_time = 2 ** attempt
                await asyncio.sleep(wait_time)
        
        return {}
    
    async def get_event_detail(self, event_id: int) -> Optional[Dict[str, Any]]:
        """
        Get detailed information for a specific event.
        Replaces EventAPIClient.get_event_detail()
        
        Args:
            event_id: The ID of the event
            
        Returns:
            Event dictionary or None if not found
        """
        try:
            data = await self._make_request(
                method="GET",
                endpoint=f"/api/v2/event/{event_id}/",
                data_type=f"event detail {event_id}"
            )
            return data if data else None
            
        except Exception as e:
            logger.error(f"Error fetching event detail {event_id}: {e}")
            return None
    
    async def get_events(self, **params) -> List[Dict[str, Any]]:
        """
        Get list of events with optional filtering parameters.
        Replaces EventAPIClient.get_events()
        
        Args:
            **params: Query parameters for filtering events
            
        Returns:
            List of event dictionaries
        """
        try:
            data = await self._make_request(
                method="GET",
                endpoint="/api/v2/event/",
                params=params,
                data_type="events list"
            )
            return data.get('results', [])
            
        except Exception as e:
            logger.error(f"Error fetching events: {e}")
            return []
    
    async def get_field_reports(self, **params) -> List[Dict[str, Any]]:
        """
        Get list of field reports with optional filtering parameters.
        Replaces FieldReportAPIClient.get_field_reports()
        
        Args:
            **params: Query parameters like event, country, dtype, etc.
            
        Returns:
            List of field report dictionaries
        """
        try:
            data = await self._make_request(
                method="GET",
                endpoint="/api/v2/field-report/",
                params=params,
                data_type="field reports"
            )
            return data.get('results', [])
            
        except Exception as e:
            logger.error(f"Error fetching field reports: {e}")
            return []
    
    async def get_field_reports_by_event(self, event_id: int) -> List[Dict[str, Any]]:
        """
        Get field reports for a specific event.
        Replaces FieldReportAPIClient.get_field_reports_by_event()
        
        Args:
            event_id: The ID of the event
            
        Returns:
            List of field report dictionaries for the event
        """
        try:
            return await self.get_field_reports(event=event_id)
            
        except Exception as e:
            logger.error(f"Error fetching field reports for event {event_id}: {e}")
            return []
    
    async def get_ops_learning(
        self, 
        country_id: Optional[int] = None, 
        disaster_type_id: Optional[int] = None,
        max_results: int = 6
    ) -> List[Dict[str, Any]]:
        """
        Get operational learning data with filtering.
        Used by RRCapacityQuestionsView and IFRCEventListView.
        
        Args:
            country_id: Filter by country ID
            disaster_type_id: Filter by disaster type ID
            max_results: Maximum number of results to return
            
        Returns:
            List of operational learning dictionaries
        """
        try:
            params = {
            "is_validated": "true",
            "limit": max_results,
            "appeal_code__country": country_id,
            }
            if disaster_type_id is not None:
                # actually filter by the nested event dtype field
                params["appeal__event_details__dtype"] = disaster_type_id
            
            data = await self._make_request(
                method="GET",
                endpoint="/api/v2/ops-learning/",
                params=params,
                data_type="ops learning"
            )
            return data.get('results', [])
            
        except Exception as e:
            logger.error(f"Error fetching ops learning: {e}")
            return []
    
    async def get_events_by_appeals(self, appeal_codes: Union[List[str], set]) -> List[Dict[str, Any]]:
        """
        Get events linked to specific appeal codes.
        Used by IFRCEventListView._fetch_events_by_appeals()
        
        Args:
            appeal_codes: List or set of appeal codes
            
        Returns:
            List of event dictionaries matching the appeal codes
        """
        matched_events = []
        
        try:
            for code in appeal_codes:
                events = await self.get_events(appeals__code=code)
                matched_events.extend(events)
                
            return matched_events
            
        except Exception as e:
            logger.error(f"Error fetching events by appeals: {e}")
            return []
    
    async def get_events_by_country_and_disaster_type(
        self, 
        country_id: int, 
        disaster_type_id: int,
        limit: int = 10
    ) -> List[Dict[str, Any]]:
        """
        Get events filtered by country and disaster type.
        Used by IFRCEventListView.
        
        Args:
            country_id: Country ID filter
            disaster_type_id: Disaster type ID filter
            limit: Maximum number of results
            
        Returns:
            List of event dictionaries
        """
        try:
            params = {
                'countries__in': country_id,
                'dtype': disaster_type_id,
                'limit': limit
            }
            
            return await self.get_events(**params)
            
        except Exception as e:
            logger.error(f"Error fetching events by country/disaster type: {e}")
            return []
    
    async def get_events_by_country_only(
        self, 
        country_id: int, 
        limit: int = 10
    ) -> List[Dict[str, Any]]:
        """
        Get events filtered by country only.
        Used by IFRCEventListView._fetch_events_by_country_only()
        
        Args:
            country_id: Country ID filter
            limit: Maximum number of results
            
        Returns:
            List of event dictionaries
        """
        try:
            params = {
                'countries__in': country_id,
                'limit': limit
            }
            
            return await self.get_events(**params)
            
        except Exception as e:
            logger.error(f"Error fetching events by country only: {e}")
            return []
    
    async def close(self):
        """Close the HTTP client"""
        if self._client:
            await self._client.aclose()
            self._client = None


# Utility functions for backward compatibility
async def get_event_detail_async(event_id: int) -> Optional[Dict[str, Any]]:
    """
    Standalone async function to get event detail.
    Drop-in replacement for EventAPIClient().get_event_detail()
    """
    async with IFRCAPIClient() as client:
        return await client.get_event_detail(event_id)


async def get_field_reports_by_event_async(event_id: int) -> List[Dict[str, Any]]:
    """
    Standalone async function to get field reports by event.
    Drop-in replacement for FieldReportAPIClient().get_field_reports_by_event()
    """
    async with IFRCAPIClient() as client:
        return await client.get_field_reports_by_event(event_id)


async def get_ops_learning_async(
    country_id: Optional[int] = None, 
    disaster_type_id: Optional[int] = None,
    **extra_params
) -> List[Dict[str, Any]]:
    """
    Standalone async function to get ops learning data.
    """
    async with IFRCAPIClient() as client:
        return await client.get_ops_learning(country_id, disaster_type_id, **extra_params)


# Synchronous wrappers for compatibility with existing sync views
def get_event_detail_sync(event_id: int) -> Optional[Dict[str, Any]]:
    """
    Synchronous wrapper for get_event_detail_async.
    Drop-in replacement for EventAPIClient().get_event_detail()
    """
    return asyncio.run(get_event_detail_async(event_id))


def get_field_reports_by_event_sync(event_id: int) -> List[Dict[str, Any]]:
    """
    Synchronous wrapper for get_field_reports_by_event_async.
    Drop-in replacement for FieldReportAPIClient().get_field_reports_by_event()
    """
    return asyncio.run(get_field_reports_by_event_async(event_id))


def get_ops_learning_sync(
    country_id: Optional[int] = None, 
    disaster_type_id: Optional[int] = None,
    **extra_params
) -> List[Dict[str, Any]]:
    """
    Synchronous wrapper for get_ops_learning_async.
    """
    return asyncio.run(get_ops_learning_async(country_id, disaster_type_id, **extra_params))


class LegacyEventAPIClient:
    """
    Legacy compatibility wrapper for EventAPIClient.
    Maintains the same interface but uses the new async client internally.
    """
    
    def __init__(self, base_url: str = IFRCAPIClient.DEFAULT_BASE_URL):
        self.base_url = base_url
    
    def get_event_detail(self, event_id: int) -> Optional[Dict[str, Any]]:
        """Legacy sync method"""
        return get_event_detail_sync(event_id)
    
    def get_events(self, **params) -> List[Dict[str, Any]]:
        """Legacy sync method"""
        return asyncio.run(
            get_events_async(**params)
        )


class LegacyFieldReportAPIClient:
    """
    Legacy compatibility wrapper for FieldReportAPIClient.
    Maintains the same interface but uses the new async client internally.
    """
    
    def __init__(self, base_url: str = IFRCAPIClient.DEFAULT_BASE_URL):
        self.base_url = base_url
    
    def get_field_reports(self, **params) -> List[Dict[str, Any]]:
        """Legacy sync method"""
        return asyncio.run(
            get_field_reports_async(**params)
        )
    
    def get_field_reports_by_event(self, event_id: int) -> List[Dict[str, Any]]:
        """Legacy sync method"""
        return get_field_reports_by_event_sync(event_id)


async def get_events_async(**params) -> List[Dict[str, Any]]:
    """Standalone async function to get events"""
    async with IFRCAPIClient() as client:
        return await client.get_events(**params)


async def get_field_reports_async(**params) -> List[Dict[str, Any]]:
    """Standalone async function to get field reports"""
    async with IFRCAPIClient() as client:
        return await client.get_field_reports(**params)