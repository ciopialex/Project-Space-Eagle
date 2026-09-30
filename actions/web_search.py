"""Bounded search across Gemini Grounding and DuckDuckGo.

A valid result is any non-empty answer. Earlier code required more than sixty
characters to count as a success, which silently discarded correct short
answers and produced false 'No news found' reports when combined with one
backend failure.

Every backend here runs against a shared deadline in an owned thread pool.
Previously only news had a deadline, and it waited for a flag without
cancelling or bounding the underlying network calls — leaving worker threads
pinned to hung sockets indefinitely.
"""
from __future__ import annotations

import concurrent.futures
import time
import warnings
from typing import Any, Callable, Dict, List, Optional, Tuple

import config
from core import models

NEWS_DEADLINE: float = 10.0
DEADLINE: float = 20.0


def _client_generate(query: str) -> Any:
    """Invokes Gemini Grounding search. Separated for clean test mocking."""
    client = config.get_client()
    return client.models.generate_content(
        model=models.DEFAULT,
        contents=query,
        config={"tools": [{"google_search": {}}]}
    )


def _gemini(query: str, deadline: float) -> str:
    """Executes a search via Gemini Google Search grounding."""
    if time.monotonic() > deadline:
        raise TimeoutError("Deadline expired before calling Gemini")
    
    resp = _client_generate(query)
    if not hasattr(resp, "candidates") or not resp.candidates:
        raise ValueError("Gemini response blocked or empty (no candidates)")
    
    candidate = resp.candidates[0]
    parts = getattr(getattr(candidate, "content", None), "parts", [])
    text_chunks = []
    for p in parts:
        txt = getattr(p, "text", "")
        if txt:
            text_chunks.append(txt)
            
    result = "".join(text_chunks).strip()
    if not result:
        raise ValueError("Gemini returned empty text content")
    return result


# `duckduckgo_search` announces on every instantiation that it has been renamed
# to `ddgs`. Registered once here, at import, rather than inside `_ddg`: tools
# run on a shared ThreadPoolExecutor, and `warnings.catch_warnings()` snapshots
# and restores the one process-wide filter list, so two overlapping searches
# leave whichever exits last restoring a snapshot the other had already
# modified — measured, a permanent stray "ignore RuntimeWarning" filter that
# outlives the block and silences the whole process.
#
# The real fix is the environment: `ddgs` is already in requirements.txt, and
# this warning means the venv is behind it.
warnings.filterwarnings(
    "ignore", category=RuntimeWarning,
    message=r".*has been renamed to.*")


def _ddg(query: str, kind: str = "text", n: int = 5, deadline: float = 0.0) -> List[Dict[str, Any]]:
    """Executes DuckDuckGo search/news with fallback."""
    if deadline and time.monotonic() > deadline:
        raise TimeoutError("Deadline expired before calling DDG")

    try:
        try:
            from ddgs import DDGS
        except ImportError:
            from duckduckgo_search import DDGS
    except ImportError:
        return []

    results: List[Dict[str, Any]] = []
    try:
        with DDGS() as ddgs_client:
            if kind == "news":
                try:
                    for r in ddgs_client.news(query, max_results=n):
                        results.append({
                            "title": r.get("title", ""),
                            "snippet": r.get("body", "") or r.get("snippet", ""),
                            "url": r.get("url", ""),
                            "source": r.get("source", "")
                        })
                except Exception:
                    # News failed, fall back to standard text search
                    kind = "text"

            if kind == "text" and not results:
                for r in ddgs_client.text(query, max_results=n):
                    results.append({
                        "title": r.get("title", ""),
                        "snippet": r.get("body", "") or r.get("snippet", ""),
                        "url": r.get("href", "") or r.get("url", ""),
                        "source": "DuckDuckGo"
                    })
    except Exception:
        return []

    return results


def _format(query: str, results: List[Dict[str, Any]], *, heading: str, snippet_limit: Optional[int] = None) -> str:
    """Formats structured search results into readable output."""
    if not results:
        return f"No results found for: {query}"
    
    lines = [f"=== {heading}: {query} ===", ""]
    for idx, r in enumerate(results, 1):
        title = r.get("title", "Untitled").strip()
        snippet = r.get("snippet", "").strip()
        if snippet_limit and len(snippet) > snippet_limit:
            snippet = snippet[:snippet_limit] + "..."
        url = r.get("url", "").strip()
        source = r.get("source", "").strip()
        
        lines.append(f"{idx}. {title}")
        if snippet:
            lines.append(f"   {snippet}")
        if source:
            lines.append(f"   Source: {source}")
        if url:
            lines.append(f"   Link: {url}")
        lines.append("")
    
    return "\n".join(lines).strip()


def _first_valid(producers: List[Callable[[], str]], deadline: float) -> Optional[str]:
    """Runs producers concurrently and returns the first non-empty valid result."""
    timeout = max(0.01, deadline - time.monotonic())
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=len(producers))
    try:
        futures = [executor.submit(p) for p in producers]
        done, not_done = concurrent.futures.wait(
            futures,
            timeout=timeout,
            return_when=concurrent.futures.FIRST_COMPLETED
        )
        for f in done:
            try:
                res = f.result(timeout=0)
                if res and res.strip():
                    executor.shutdown(wait=False, cancel_futures=True)
                    return res.strip()
            except Exception:
                pass
        
        rem_timeout = max(0.001, deadline - time.monotonic())
        if rem_timeout > 0.005 and not_done:
            for f in concurrent.futures.as_completed(not_done, timeout=rem_timeout):
                try:
                    res = f.result(timeout=0)
                    if res and res.strip():
                        executor.shutdown(wait=False, cancel_futures=True)
                        return res.strip()
                except Exception:
                    pass
    except Exception:
        pass
    finally:
        executor.shutdown(wait=False, cancel_futures=True)
        
    return None


def _news(query: str) -> str:
    """Fetches latest news concurrently across backends bounded by NEWS_DEADLINE."""
    dl = time.monotonic() + NEWS_DEADLINE

    def run_gemini() -> str:
        q = f"Latest news on {query}. Include dates and sources."
        return _gemini(q, deadline=dl)

    def run_ddg() -> str:
        items = _ddg(query, kind="news", n=5, deadline=dl)
        if items:
            return _format(query, items, heading="News Results", snippet_limit=200)
        return ""

    result = _first_valid([run_gemini, run_ddg], deadline=dl)
    if result:
        return result
    return f"No news found for: {query}"


_MODES: Dict[str, Tuple[str, str]] = {
    "search": ("{}", "Search Results"),
    "research": ("In-depth research on {}. Provide key facts, technical details, and context.", "Research Findings"),
    "price": ("Current price and purchasing options for {}. List exact prices, currency, and retailers.", "Price Comparison"),
}


from core.tool_result import ToolResult  # noqa: E402


def web_search(
    parameters: Optional[Dict[str, Any]] = None,
    response: Any = None,
    player: Any = None,
    session_memory: Any = None
) -> str:
    """Unified bounded web search handler."""
    params = parameters or {}
    query = str(params.get("query", "")).strip()
    items = params.get("items")
    aspect = str(params.get("aspect", "overview")).strip()
    mode = str(params.get("mode", "search")).lower().strip()

    # Compare mode forced if items list provided
    if items and isinstance(items, list):
        mode = "compare"
        items_str = ", ".join(str(it) for it in items)
        query = f"Compare {items_str} in terms of {aspect}."

    if not query:
        return ToolResult.failure(
            "No search query was given, so nothing was searched.",
            guidance="Ask the user what they want looked up, then call again.")

    if player and hasattr(player, "write_log"):
        try:
            player.write_log(f"Searching web for: {query} (mode={mode})")
        except Exception:
            pass

    if mode == "news":
        return ToolResult.success(_news(query), query=query, mode="news")

    dl = time.monotonic() + DEADLINE

    # Template mapping
    if mode in _MODES:
        template, heading = _MODES[mode]
        gemini_q = template.format(query)
    elif mode == "compare":
        gemini_q = query
        heading = "Comparison"
    else:
        gemini_q = query
        heading = "Search Results"

    def run_gemini() -> str:
        return _gemini(gemini_q, deadline=dl)

    def run_ddg() -> str:
        ddg_items = _ddg(query, kind="text", n=5, deadline=dl)
        if ddg_items:
            return _format(query, ddg_items, heading=heading)
        return ""

    result = _first_valid([run_gemini, run_ddg], deadline=dl)
    if result:
        return ToolResult.success(result, query=query, mode=mode)

    # Finding nothing is a real answer, not a failure. It is reported as a
    # success that plainly says so, rather than as prose the log cannot read.
    return ToolResult.success(f"No results found for: {query}",
                              query=query, mode=mode, results=0)
