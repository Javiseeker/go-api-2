import httpx
import asyncio
from typing import List, Dict, Optional, Any
from contextlib import asynccontextmanager


class IFRCAPIClient:
    """Unified async HTTP client for IFRC API interactions"""
    
    def __init__(self):
        self.base_url = "https://goadmin.ifrc.org/api/v2"
        self.client = None
    
    async def __aenter__(self):
        """Async context manager entry"""
        self.client = httpx.AsyncClient(timeout=30.0)
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit"""
        if self.client:
            await self.client.aclose()
    
    async def close(self):
        """Close the HTTP client"""
        if self.client:
            await self.client.aclose()
    
    async def get_ops_learning(
        self, 
        country_id: int, 
        disaster_type_id: Optional[int] = None, 
        max_results: int = 20
    ) -> List[Dict[str, Any]]:
        """Get operational learning data for a country and optional disaster type"""
        try:
            # This is a placeholder implementation
            # In a real scenario, you would make actual API calls to the IFRC API
            # For now, return empty list to prevent errors
            return []
        except Exception as e:
            print(f"Error fetching ops learning: {e}")
            return []
    
    async def get_event_detail(self, event_id: int) -> Optional[Dict[str, Any]]:
        """Get detailed information about a specific event"""
        try:
            # This is a placeholder implementation
            # In a real scenario, you would make actual API calls to the IFRC API
            # For now, return None to prevent errors
            return None
        except Exception as e:
            print(f"Error fetching event detail: {e}")
            return None
