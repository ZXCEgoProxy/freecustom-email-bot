import aiohttp
import asyncio
from typing import Optional, Dict, Any, List
from datetime import datetime, timedelta
import re
from config import Config

class FreeCustomAPIError(Exception):
    pass

class FreeCustomAPIClient:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.base_url = f"{Config.API_BASE_URL}/v1"  # Add /v1 to base URL
        self.session: Optional[aiohttp.ClientSession] = None

    async def __aenter__(self):
        self.session = aiohttp.ClientSession(
            headers={
                # Try different auth methods
                'Authorization': f'Bearer {self.api_key}',
                'X-API-Key': self.api_key,  # Alternative header
                'api-key': self.api_key,    # Another alternative
                'Content-Type': 'application/json'
            }
        )
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.session:
            await self.session.close()

    async def _make_request(self, method: str, endpoint: str, **kwargs) -> Dict[str, Any]:
        """Make authenticated request to API"""
        if not self.session:
            raise FreeCustomAPIError("Client session not initialized")

        url = f"{self.base_url}{endpoint}"

        # Use Bearer token for all endpoints (API works with Bearer)
        headers = dict(self.session.headers)

        # Always add api_key as query parameter
        if 'params' not in kwargs:
            kwargs['params'] = {}
        kwargs['params']['api_key'] = self.api_key

        # Make request with specific headers
        try:
            async with self.session.request(method, url, headers=headers, **kwargs) as response:
                response_text = await response.text()

                if response.status == 401:
                    raise FreeCustomAPIError("Invalid API key")
                elif response.status == 429:
                    raise FreeCustomAPIError("Rate limit exceeded")
                elif response.status >= 400:
                    raise FreeCustomAPIError(f"API error: {response.status} - {response_text}")

                return await response.json()
        except aiohttp.ClientError as e:
            raise FreeCustomAPIError(f"Network error: {str(e)}")

    async def validate_api_key(self) -> bool:
        """Validate API key by making a test request"""
        try:
            # Try to get inboxes list - this should work if API key is valid
            await self._make_request('GET', '/inboxes')
            return True
        except FreeCustomAPIError:
            return False

    async def get_domains(self) -> List[Dict[str, Any]]:
        """Get available domains"""
        response = await self._make_request('GET', '/domains')
        return response.get('data', [])

    async def get_inboxes(self) -> List[Dict[str, Any]]:
        """Get list of user's inboxes"""
        response = await self._make_request('GET', '/inboxes')
        return response.get('data', [])

    async def delete_inbox(self, email: str) -> bool:
        """Delete an inbox"""
        try:
            await self._make_request('DELETE', f'/inboxes/{email}')
            return True
        except FreeCustomAPIError:
            return False

    async def get_messages(self, email: str, limit: int = 50, offset: int = 0) -> List[Dict[str, Any]]:
        """Get messages for an inbox"""
        params = {'limit': limit, 'offset': offset}
        response = await self._make_request('GET', f'/inboxes/{email}/messages', params=params)
        return response.get('data', [])

    async def get_message(self, email: str, message_id: str) -> Dict[str, Any]:
        """Get message content"""
        response = await self._make_request('GET', f'/inboxes/{email}/messages/{message_id}')
        return response

    async def extract_otp(self, email: str) -> Dict[str, Any]:
        """Extract OTP code from inbox (paid feature)"""
        response = await self._make_request('GET', f'/inboxes/{email}/otp')
        return response

    async def create_email(self, domain: Optional[str] = None, name: Optional[str] = None) -> Dict[str, Any]:
        """Create a new temporary email"""
        # Prepare request data according to API documentation
        data = {}
        if domain:
            data['domain'] = domain
        if name:
            data['name'] = name
        # If neither domain nor name specified, API will generate random ones

        # Use correct endpoint with Bearer token
        response = await self._make_request('POST', '/inboxes', json=data)

        # Response should contain the created email
        if 'email' not in response:
            raise FreeCustomAPIError("API did not return email address")

        return response

    # Legacy methods for backward compatibility
    async def get_emails(self, email: str) -> Dict[str, Any]:
        """Get emails for a specific address (legacy)"""
        return await self.get_inboxes()

    async def get_email_messages(self, email: str) -> List[Dict[str, Any]]:
        """Get all messages for an email (legacy)"""
        return await self.get_messages(email)

    async def delete_email(self, email: str) -> bool:
        """Delete an email address (legacy)"""
        return await self.delete_inbox(email)

    async def extract_otp(self, message_body: str) -> Optional[str]:
        """Extract OTP codes from message body"""
        # Common OTP patterns
        patterns = [
            r'\b\d{4,8}\b',  # 4-8 digit codes
            r'code[:\s]+([A-Za-z0-9]{4,})',  # "code: XXXX"
            r'verification[:\s]+([A-Za-z0-9]{4,})',  # "verification: XXXX"
            r'otp[:\s]+([A-Za-z0-9]{4,})',  # "otp: XXXX"
        ]

        for pattern in patterns:
            match = re.search(pattern, message_body, re.IGNORECASE)
            if match:
                return match.group(1) if match.groups() else match.group(0)

        return None

    @staticmethod
    def parse_expiry_time(expires_in: Optional[int]) -> Optional[datetime]:
        """Parse expiry time from API response"""
        if expires_in:
            return datetime.now() + timedelta(seconds=expires_in)
        return None