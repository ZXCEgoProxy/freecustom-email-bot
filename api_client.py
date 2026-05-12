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
        self.base_url = Config.API_BASE_URL
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

        # Try Bearer token first, but also add api_key as query parameter as fallback
        if 'params' not in kwargs:
            kwargs['params'] = {}
        kwargs['params']['api_key'] = self.api_key

        print(f"DEBUG: Making {method} request to {url}")
        print(f"DEBUG: Headers: {dict(self.session.headers)}")
        print(f"DEBUG: Params: {kwargs.get('params', {})}")

        try:
            async with self.session.request(method, url, **kwargs) as response:
                print(f"DEBUG: Response status: {response.status}")
                response_text = await response.text()
                print(f"DEBUG: Response body: {response_text[:500]}...")

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
            # Try to get domains list as a test
            await self._make_request('GET', '/domains')
            return True
        except FreeCustomAPIError:
            return False

    async def get_domains(self) -> List[str]:
        """Get available domains"""
        response = await self._make_request('GET', '/domains')
        return response.get('domains', [])

    async def create_email(self, domain: Optional[str] = None) -> Dict[str, Any]:
        """Create a new temporary email"""
        data = {}
        if domain:
            data['domain'] = domain

        response = await self._make_request('POST', '/emails', json=data)
        return response

    async def get_emails(self, email: str) -> Dict[str, Any]:
        """Get emails for a specific address"""
        response = await self._make_request('GET', f'/emails/{email}')
        return response

    async def get_email_messages(self, email: str) -> List[Dict[str, Any]]:
        """Get all messages for an email"""
        response = await self._make_request('GET', f'/emails/{email}/messages')
        return response.get('messages', [])

    async def delete_email(self, email: str) -> bool:
        """Delete an email address"""
        try:
            await self._make_request('DELETE', f'/emails/{email}')
            return True
        except FreeCustomAPIError:
            return False

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