import requests
import logging
from utils.auth import get_access_token
from config.settings import GRAPH_BASE_URL

logger = logging.getLogger(__name__)

class GraphClient:
    """Client for interacting with Microsoft Graph API"""
    
    def __init__(self):
        self.base_url = GRAPH_BASE_URL
        self.access_token = None
    
    def _ensure_token(self):
        """Ensure we have a valid access token"""
        if not self.access_token:
            self.access_token = get_access_token()
    
    def _get_headers(self):
        """Get headers for API requests"""
        self._ensure_token()
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json"
        }
    
    def get(self, endpoint, params=None):
        """Make a GET request to the Graph API"""
        url = f"{self.base_url}{endpoint}"
        
        try:
            response = requests.get(url, headers=self._get_headers(), params=params)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.HTTPError as e:
            logger.error(f"HTTP error: {e}")
            logger.error(f"Response text: {response.text}")
            raise
        except Exception as e:
            logger.error(f"Error in API request: {e}")
            raise
    
    def patch(self, endpoint, data):
        """Make a PATCH request to the Graph API"""
        url = f"{self.base_url}{endpoint}"
        
        try:
            response = requests.patch(url, headers=self._get_headers(), json=data)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.HTTPError as e:
            logger.error(f"HTTP error: {e}")
            logger.error(f"Response text: {response.text}")
            raise
        except Exception as e:
            logger.error(f"Error in API request: {e}")
            raise
    
    def post(self, endpoint, data):
        """Make a POST request to the Graph API"""
        url = f"{self.base_url}{endpoint}"
        
        try:
            response = requests.post(url, headers=self._get_headers(), json=data)
            response.raise_for_status()
            return response.json() if response.content else None
        except requests.exceptions.HTTPError as e:
            logger.error(f"HTTP error: {e}")
            logger.error(f"Response text: {response.text}")
            raise
        except Exception as e:
            logger.error(f"Error in API request: {e}")
            raise
            
    def get_binary(self, endpoint):
        """Make a GET request that returns binary data"""
        url = f"{self.base_url}{endpoint}"
        
        try:
            response = requests.get(url, headers=self._get_headers())
            response.raise_for_status()
            return response.content
        except Exception as e:
            logger.error(f"Error downloading binary content: {e}")
            raise