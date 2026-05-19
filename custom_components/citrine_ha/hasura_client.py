"""Hasura GraphQL helper for charger discovery."""

from __future__ import annotations

import asyncio
from typing import Any

import aiohttp


class HasuraError(Exception):
    """Base Hasura error."""


class HasuraAuthError(HasuraError):
    """Auth error for Hasura."""


class HasuraClient:
    """Simple GraphQL client for Hasura."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        *,
        url: str,
        token: str | None,
        request_timeout: int,
        verify_ssl: bool,
    ) -> None:
        self._session = session
        self._url = url.rstrip("/")
        self._token = token
        self._request_timeout = request_timeout
        self._verify_ssl = verify_ssl

    async def ping(self) -> None:
        """Validate Hasura endpoint and auth."""
        await self.query("query { __typename }")

    async def query(
        self,
        query: str,
        variables: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        headers = {
            "Content-Type": "application/json",
        }
        if self._token:
            headers["x-hasura-admin-secret"] = self._token

        payload = {"query": query, "variables": variables or {}}

        timeout = aiohttp.ClientTimeout(total=self._request_timeout)
        try:
            async with self._session.post(
                self._url,
                json=payload,
                headers=headers,
                timeout=timeout,
                ssl=self._verify_ssl,
            ) as resp:
                data = await resp.json(content_type=None)
                if resp.status in (401, 403):
                    raise HasuraAuthError("Hasura authentication failed")
                if resp.status >= 400:
                    raise HasuraError(f"Hasura HTTP error: {resp.status}")
                if "errors" in data:
                    raise HasuraError(str(data["errors"]))
                return data
        except asyncio.TimeoutError as err:
            raise HasuraError("Hasura request timed out") from err
        except aiohttp.ClientError as err:
            raise HasuraError(f"Hasura transport error: {err}") from err
