"""
GraphQL client for StashApp.
"""

from __future__ import annotations

import requests


class StashError(Exception):
    pass


class StashClient:
    def __init__(
        self,
        url: str = "http://localhost:9999",
        api_key: str = "",
        timeout: int = 15,
    ) -> None:
        self.endpoint = url.rstrip("/") + "/graphql"
        self.timeout = timeout
        self._headers = {"Content-Type": "application/json"}
        if api_key:
            self._headers["ApiKey"] = api_key

    def is_available(self) -> bool:
        try:
            r = requests.get(
                self.endpoint.replace("/graphql", "/healthcheck"),
                timeout=5,
                headers=self._headers,
            )
            return r.status_code == 200
        except requests.RequestException:
            return False

    def query(self, gql: str, variables: dict | None = None) -> dict:
        """Execute a GraphQL query and return the data dict."""
        payload: dict = {"query": gql}
        if variables:
            payload["variables"] = variables
        try:
            r = requests.post(
                self.endpoint,
                json=payload,
                headers=self._headers,
                timeout=self.timeout,
            )
            r.raise_for_status()
        except requests.RequestException as exc:
            raise StashError(f"StashApp request failed: {exc}") from exc

        body = r.json()
        if "errors" in body:
            messages = [e.get("message", str(e)) for e in body["errors"]]
            raise StashError(f"GraphQL errors: {'; '.join(messages)}")

        return body.get("data", {})
