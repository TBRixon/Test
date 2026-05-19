"""
avm_client.py
Handles all communication with the Cotality (CoreLogic) AVM API.

Responsibilities:
  - Build and send authenticated requests
  - Retry with exponential backoff on transient failures
  - In-memory cache to avoid duplicate calls for the same address
  - Return a normalised dict or None on permanent failure
"""

import logging
import time
from functools import lru_cache
from typing import Optional

import requests

import config

logger = logging.getLogger(__name__)


class AVMClient:
    """
    Thin wrapper around the Cotality AVM REST endpoint.

    Usage:
        client = AVMClient()
        result = client.get_avm("10 Robin St Camira QLD 4300")
    """

    def __init__(self) -> None:
        if not config.COTALITY_API_KEY:
            logger.warning(
                "COTALITY_API_KEY is not set – all AVM calls will fail. "
                "Set it in your .env file."
            )
        self._session = requests.Session()
        self._session.headers.update(
            {
                "Authorization": f"Bearer {config.COTALITY_API_KEY}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            }
        )
        # Simple in-memory cache: address (normalised) → result dict | None
        self._cache: dict[str, Optional[dict]] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_avm(self, address: str) -> Optional[dict]:
        """
        Return AVM data for *address*, using the cache when available.

        Return structure:
            {
                "value":      float,
                "low":        float | None,
                "high":       float | None,
                "confidence": float | None,   # 0.0 – 1.0
                "date":       str,             # ISO date string
            }

        Returns None when the API is unreachable or returns an error.
        """
        key = self._normalise_address(address)
        if key in self._cache:
            logger.debug("Cache hit for address: %r", address)
            return self._cache[key]

        result = self._fetch_with_retry(address)
        self._cache[key] = result
        return result

    @property
    def cache_size(self) -> int:
        return len(self._cache)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _fetch_with_retry(self, address: str) -> Optional[dict]:
        """Attempt the API call up to MAX_RETRIES times with exponential backoff."""
        url = f"{config.COTALITY_BASE_URL.rstrip('/')}{config.AVM_ENDPOINT}"
        payload = {"address": address}

        delay = config.API_BACKOFF_BASE_S
        last_exc: Optional[Exception] = None

        for attempt in range(1, config.API_MAX_RETRIES + 1):
            try:
                response = self._session.post(
                    url,
                    json=payload,
                    timeout=config.API_TIMEOUT_S,
                )
                response.raise_for_status()
                return self._parse_response(response.json())

            except requests.exceptions.HTTPError as exc:
                status = exc.response.status_code if exc.response is not None else "?"
                # 4xx errors are not transient – bail immediately
                if exc.response is not None and 400 <= exc.response.status_code < 500:
                    logger.error(
                        "AVM call failed with %s for address %r – not retrying.",
                        status,
                        address,
                    )
                    return None
                last_exc = exc
                logger.warning(
                    "AVM attempt %d/%d failed (HTTP %s) for %r – retrying in %ds.",
                    attempt,
                    config.API_MAX_RETRIES,
                    status,
                    address,
                    delay,
                )

            except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as exc:
                last_exc = exc
                logger.warning(
                    "AVM attempt %d/%d network error for %r – retrying in %ds. (%s)",
                    attempt,
                    config.API_MAX_RETRIES,
                    address,
                    delay,
                    exc,
                )

            except Exception as exc:  # unexpected errors
                logger.exception("Unexpected error fetching AVM for %r: %s", address, exc)
                return None

            if attempt < config.API_MAX_RETRIES:
                time.sleep(delay)
                delay *= 2  # exponential backoff

        logger.error(
            "AVM permanently failed for %r after %d attempts. Last error: %s",
            address,
            config.API_MAX_RETRIES,
            last_exc,
        )
        return None

    @staticmethod
    def _parse_response(data: dict) -> Optional[dict]:
        """
        Normalise the raw Cotality JSON response into our internal dict.

        Expected Cotality response shape (adjust field names to match actual API):
        {
            "estimatedValue": 850000,
            "lowValue":       780000,
            "highValue":      920000,
            "confidence":     0.85,
            "valuationDate":  "2024-11-01"
        }
        """
        try:
            return {
                "value":      float(data["estimatedValue"]),
                "low":        float(data["lowValue"])        if data.get("lowValue")     else None,
                "high":       float(data["highValue"])       if data.get("highValue")    else None,
                "confidence": float(data["confidence"])      if data.get("confidence")   else None,
                "date":       str(data.get("valuationDate", "")),
            }
        except (KeyError, TypeError, ValueError) as exc:
            logger.error("Failed to parse AVM response: %s | data=%r", exc, data)
            return None

    @staticmethod
    def _normalise_address(address: str) -> str:
        """Lowercase and strip whitespace for cache keying."""
        return " ".join(address.lower().split())
