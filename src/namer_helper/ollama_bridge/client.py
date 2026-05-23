"""
Thin HTTP client for the Ollama /api/generate endpoint.
"""

from __future__ import annotations

import json

import requests


class OllamaError(Exception):
    pass


class OllamaClient:
    def __init__(self, base_url: str = "http://localhost:11434", timeout: int = 30) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def is_available(self) -> bool:
        try:
            r = requests.get(f"{self.base_url}/api/tags", timeout=5)
            return r.status_code == 200
        except requests.RequestException:
            return False

    def generate(self, model: str, prompt: str) -> str:
        """Send prompt to Ollama, return raw response text."""
        url = f"{self.base_url}/api/generate"
        payload = {
            "model": model,
            "prompt": prompt,
            "stream": False,
            "format": "json",
        }
        try:
            r = requests.post(url, json=payload, timeout=self.timeout)
            r.raise_for_status()
        except requests.RequestException as exc:
            raise OllamaError(f"Ollama request failed: {exc}") from exc

        data = r.json()
        return data.get("response", "")
