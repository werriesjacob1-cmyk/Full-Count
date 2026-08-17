#!/usr/bin/env python3
"""Lightweight network dependencies shared by daily and live grading.

The grader needs requests, MLB-StatsAPI, and (only for a few Statcast-only
final markets) pybaseball.  Importing the entire ``mlb_daily`` research
pipeline made every five-minute box-score check require pandas, NumPy,
BeautifulSoup, scikit-learn, and eager pybaseball initialization.  Keep the
same small interface without that unrelated import-time dependency chain.
"""
from __future__ import annotations

import random
import time

import requests

try:
    import statsapi
except ImportError as exc:  # fail clearly; every grading path needs box scores
    raise ImportError("grading requires the 'mlb-statsapi' package") from exc


UA_POOL = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
)


def retry_get(url, retries=3, backoff=2.0, **kwargs):
    """requests.get with bounded exponential backoff and UA rotation."""
    last_exc = None
    response = None
    base_headers = dict(kwargs.pop("headers", None) or {})
    for attempt in range(retries):
        headers = dict(base_headers)
        headers.setdefault("User-Agent", random.choice(UA_POOL))
        headers.setdefault("Accept-Language", "en-US,en;q=0.9")
        try:
            response = requests.get(url, headers=headers, **kwargs)
            if response.status_code == 200:
                return response
            if response.status_code in (403, 429, 500, 502, 503, 504) and attempt < retries - 1:
                time.sleep(backoff * (2 ** attempt) + random.uniform(0, 0.5))
                continue
            return response
        except requests.exceptions.RequestException as exc:
            last_exc = exc
            if attempt < retries - 1:
                time.sleep(backoff * (2 ** attempt) + random.uniform(0, 0.5))
    if last_exc:
        raise last_exc
    return response


def warn(message):
    print(f"    ⚠  {message}", flush=True)


class _LazyPybaseball:
    """Import pybaseball only for final markets that truly require Statcast."""

    _module = None

    def _load(self):
        if self._module is None:
            try:
                import pybaseball
            except ImportError as exc:
                raise RuntimeError(
                    "this Statcast-only grading market requires the 'pybaseball' package"
                ) from exc
            pybaseball.cache.enable()
            self._module = pybaseball
        return self._module

    def __getattr__(self, name):
        return getattr(self._load(), name)


pyb = _LazyPybaseball()
