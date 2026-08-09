"""Live web research used to ground the JD analysis.

The default provider is a lightweight DuckDuckGo HTML search via ``httpx`` —
no API keys required.  In production the operator can switch to Tavily
(``WEB_GROUNDING_PROVIDER=tavily``) which returns cleaner structured JSON
but requires an API key.  In both cases the service returns a normalized
:class:`GroundingResult` so upstream code never branches on provider.

Every public call is wrapped in an explicit ``asyncio.wait_for`` so that the
JD analysis route can detect timeouts and degrade to JD-only extraction
instead of blocking the client.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from typing import Literal

import httpx
from structlog import get_logger
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_random_exponential,
)

from app.core.config import get_settings
from app.core.exceptions import GroundingTimeoutError

log = get_logger(__name__)

Provider = Literal["duckduckgo", "tavily", "huggingface"]


@dataclass(slots=True)
class GroundingSearchHit:
    """Single normalized search result used by downstream LLM synthesis."""

    title: str
    url: str
    snippet: str


@dataclass(slots=True)
class GroundingResult:
    """Normalized grounding output consumed by the JD analysis service."""

    hits: list[GroundingSearchHit]
    provider: Provider
    query: str
    latency_ms: int


_DDGO_RE = re.compile(r"<a[^>]+href=\"([^\"]+)\"[^>]*>(.*?)</a>", re.IGNORECASE | re.DOTALL)


class WebGroundingService:
    """Run live web searches and return a :class:`GroundingResult`."""

    def __init__(self) -> None:
        self._settings = get_settings()
        self._http = httpx.AsyncClient(timeout=httpx.Timeout(12.0, connect=3.0))

    async def aclose(self) -> None:
        await self._http.aclose()

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------
    async def research_role(
        self,
        job_title: str,
        company_name: str | None,
    ) -> GroundingResult:
        """Search for interview-style intel about the role and company.

        Raises
        ------
        GroundingTimeoutError
            if the configured timeout is exceeded.  Callers are expected to
            catch this and continue with JD-only analysis rather than
            propagating a 5xx to the end user.
        """
        queries = [f"{job_title} interview questions experience 2025"]
        if company_name:
            queries.append(f"{company_name} {job_title} interview process")
        provider = self._settings.web_grounding_provider
        timeout = self._settings.web_grounding_timeout_seconds

        try:
            hits = await asyncio.wait_for(
                self._run_searches(provider, queries),
                timeout=timeout,
            )
        except asyncio.TimeoutError as exc:
            log.warning(
                "web_grounding.timeout",
                provider=provider,
                timeout_seconds=timeout,
                job_title=job_title,
            )
            raise GroundingTimeoutError(
                "Live web grounding timed out — degrading to JD-only analysis.",
                details={
                    "provider": provider,
                    "timeout_seconds": timeout,
                    "job_title": job_title,
                },
            ) from exc
        return GroundingResult(
            hits=hits[:8],
            provider=provider,
            query=" | ".join(queries),
            latency_ms=0,
        )

    # ------------------------------------------------------------------
    # Provider implementations
    # ------------------------------------------------------------------
    async def _run_searches(
        self, provider: Provider, queries: list[str]
    ) -> list[GroundingSearchHit]:
        results: list[GroundingSearchHit] = []
        if provider == "tavily":
            for q in queries:
                results.extend(await self._tavily_search(q))
        elif provider == "duckduckgo":
            for q in queries:
                results.extend(await self._duckduckgo_search(q))
        else:  # huggingface (stubbed — search is handled by Groq realtime tooling)
            for q in queries:
                results.extend(await self._duckduckgo_search(q))
        # Deduplicate on URL to avoid bloating the context window with copies.
        seen: set[str] = set()
        deduped: list[GroundingSearchHit] = []
        for hit in results:
            if hit.url in seen:
                continue
            seen.add(hit.url)
            deduped.append(hit)
        return deduped

    @retry(
        reraise=False,
        stop=stop_after_attempt(2),
        wait=wait_random_exponential(multiplier=0.3, max=2),
        retry=retry_if_exception_type(httpx.HTTPError),
    )
    async def _duckduckgo_search(self, query: str) -> list[GroundingSearchHit]:
        """Best-effort DuckDuckGo HTML lite search.  Returns [] on failure."""
        try:
            resp = await self._http.get(
                "https://html.duckduckgo.com/html/",
                params={"q": query},
                headers={"User-Agent": "InterviewPrepSim/0.1"},
            )
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            log.warning("web_grounding.ddg.failed", error=str(exc))
            return []
        text = resp.text
        # Cheap extraction: take the first result<a> + following snippet.
        snippet_re = re.compile(
            r"<a[^>]+class=\"result__a\"[^>]*href=\"([^\"]+)\"[^>]*>(.*?)</a>"
            r".*?<a[^>]+class=\"result__snippet\"[^>]*>(.*?)</a>",
            re.DOTALL | re.IGNORECASE,
        )
        hits: list[GroundingSearchHit] = []
        for url, title, snippet in snippet_re.findall(text):
            title_clean = re.sub("<[^>]+>", "", title).strip()
            snippet_clean = re.sub("<[^>]+>", "", snippet).strip()
            hits.append(
                GroundingSearchHit(
                    title=title_clean,
                    url=url,
                    snippet=snippet_clean,
                )
            )
        return hits

    @retry(
        reraise=False,
        stop=stop_after_attempt(2),
        wait=wait_random_exponential(multiplier=0.3, max=2),
        retry=retry_if_exception_type(httpx.HTTPError),
    )
    async def _tavily_search(self, query: str) -> list[GroundingSearchHit]:
        key = self._settings.tavily_api_key
        if not key:
            log.warning("web_grounding.tavily.no_key")
            return []
        try:
            resp = await self._http.post(
                "https://api.tavily.com/search",
                json={
                    "api_key": key,
                    "query": query,
                    "search_depth": "basic",
                    "max_results": 5,
                    "include_answer": False,
                },
            )
            resp.raise_for_status()
            body = resp.json()
        except httpx.HTTPError as exc:
            log.warning("web_grounding.tavily.failed", error=str(exc))
            return []
        hits: list[GroundingSearchHit] = []
        for row in body.get("results", [])[:5]:
            hits.append(
                GroundingSearchHit(
                    title=str(row.get("title", "")),
                    url=str(row.get("url", "")),
                    snippet=str(row.get("content", "")),
                )
            )
        return hits


_singleton: WebGroundingService | None = None


async def get_web_grounding_service() -> WebGroundingService:
    """Return the shared grounding service, constructing it lazily."""
    global _singleton
    if _singleton is None:
        _singleton = WebGroundingService()
    return _singleton


async def close_web_grounding_service() -> None:
    global _singleton
    if _singleton is not None:
        await _singleton.aclose()
        _singleton = None
