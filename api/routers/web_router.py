"""Web curation endpoints (preview, evaluate, process, ingest, submissions, monitor).

Wraps the web_fetcher and llm_enricher modules to provide a REST API for
web content curation.  Only included when caps.web_sensor is True.
"""

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field


# -- Request / Response models -----------------------------------------------

class WebPreviewRequest(BaseModel):
    """Request to preview a URL before full ingestion."""
    url: str = Field(..., description="URL to fetch and preview")
    extract_entities: bool = Field(
        True, description="Whether to scan for known entities in content"
    )


class WebPreviewResponse(BaseModel):
    """Metadata and entity matches extracted from a URL."""
    url: str
    title: Optional[str] = None
    description: Optional[str] = None
    author: Optional[str] = None
    published_date: Optional[str] = None
    word_count: int = 0
    matching_entities: List[Dict[str, Any]] = []
    error: Optional[str] = None


class WebEvaluateRequest(BaseModel):
    """Request to evaluate content relevance using LLM enrichment."""
    url: str = Field(..., description="URL of content to evaluate")
    content: Optional[str] = Field(
        None, description="Pre-fetched content (skips re-fetch if provided)"
    )
    criteria: Optional[str] = Field(
        None, description="Evaluation criteria or focus area"
    )


class WebEvaluateResponse(BaseModel):
    """LLM evaluation result for web content."""
    url: str
    relevance_score: float = Field(
        ..., ge=0.0, le=1.0, description="Relevance to bioregional knowledge"
    )
    summary: str = ""
    suggested_entities: List[Dict[str, Any]] = []
    suggested_entity_type: Optional[str] = None
    rationale: str = ""


class WebProcessRequest(BaseModel):
    """Request to fully process (fetch + extract + enrich) a URL."""
    url: str
    auto_ingest: bool = Field(
        False, description="If true, ingest immediately after processing"
    )


class WebProcessResponse(BaseModel):
    """Result of full web content processing."""
    url: str
    status: str  # "processed", "rejected", "error"
    preview: Optional[WebPreviewResponse] = None
    evaluation: Optional[WebEvaluateResponse] = None
    error: Optional[str] = None


class WebIngestRequest(BaseModel):
    """Request to ingest a previously-processed URL into the knowledge graph."""
    url: str
    title: Optional[str] = None
    entity_type: str = Field("WebResource", description="Entity type to create")
    metadata: Dict[str, Any] = Field(default_factory=dict)


class WebIngestResponse(BaseModel):
    """Result of web content ingestion."""
    url: str
    entity_uri: Optional[str] = None
    status: str  # "ingested", "duplicate", "error"
    entities_created: int = 0
    relationships_created: int = 0


class WebSubmission(BaseModel):
    """A web content submission record."""
    url: str
    status: str
    submitted_at: Optional[str] = None
    processed_at: Optional[str] = None
    entity_uri: Optional[str] = None


class WebMonitorResponse(BaseModel):
    """Web sensor monitoring status."""
    enabled: bool
    urls_processed: int = 0
    urls_pending: int = 0
    last_scan_at: Optional[str] = None
    error_count: int = 0


# -- Router factory ----------------------------------------------------------

def create_router(pool, caps):
    """Return an APIRouter for web sensor endpoints.

    Only included when caps.web_sensor is True.

    Parameters
    ----------
    pool : asyncpg.Pool
        Database connection pool.
    caps : Capabilities
        Runtime capabilities (web_sensor, llm_enrichment flags).
    """
    router = APIRouter(prefix="/web", tags=["web"])

    @router.post("/preview", response_model=WebPreviewResponse)
    async def web_preview(body: WebPreviewRequest):
        """Fetch a URL and return metadata plus entity matches.

        Uses web_fetcher.fetch_and_preview() for HTML extraction and
        entity scanning against the local knowledge graph.
        """
        # TODO: Wire to web_fetcher.fetch_and_preview(body.url, pool)
        # from api.web_fetcher import fetch_and_preview
        raise HTTPException(
            status_code=501,
            detail="Web preview not yet wired to web_fetcher module",
        )

    @router.post("/evaluate", response_model=WebEvaluateResponse)
    async def web_evaluate(body: WebEvaluateRequest):
        """Evaluate content relevance using LLM enrichment.

        Calls llm_enricher to assess whether the content is relevant to
        bioregional knowledge commons topics and suggests entity types.
        """
        if not caps.llm_enrichment:
            raise HTTPException(
                status_code=501,
                detail="LLM enrichment not enabled for this deployment",
            )
        # TODO: Wire to llm_enricher.extract_from_content()
        # from api.llm_enricher import extract_from_content
        raise HTTPException(
            status_code=501,
            detail="Web evaluation not yet wired to llm_enricher module",
        )

    @router.post("/process", response_model=WebProcessResponse)
    async def web_process(body: WebProcessRequest):
        """Full pipeline: fetch, preview, evaluate, and optionally ingest.

        Combines preview + evaluate into a single call.  If auto_ingest is
        true and the content scores above threshold, it is ingested
        automatically.
        """
        # TODO: Orchestrate preview -> evaluate -> optional ingest
        raise HTTPException(
            status_code=501,
            detail="Web process pipeline not yet implemented",
        )

    @router.post("/ingest", response_model=WebIngestResponse)
    async def web_ingest(body: WebIngestRequest):
        """Ingest a processed URL into the knowledge graph.

        Creates an entity in entity_registry and links it via
        entity_relationships based on previously-extracted entities.
        """
        # TODO: Wire to ingest pipeline (entity creation + relationship linking)
        raise HTTPException(
            status_code=501,
            detail="Web ingest not yet wired to ingest pipeline",
        )

    @router.get("/submissions")
    async def web_submissions(
        status: Optional[str] = Query(None, description="Filter by status"),
        limit: int = Query(50, ge=1, le=200),
        offset: int = Query(0, ge=0),
    ):
        """List web content submissions and their processing status.

        Returns a paginated list of URLs that have been submitted for
        processing, along with their current status.
        """
        # TODO: Query web_submissions table when it exists
        raise HTTPException(
            status_code=501,
            detail="Web submissions tracking not yet implemented",
        )

    @router.get("/monitor", response_model=WebMonitorResponse)
    async def web_monitor():
        """Return web sensor health and activity metrics.

        Shows whether the web sensor is active, how many URLs are queued,
        and recent error counts.
        """
        # TODO: Wire to web_sensor status when available
        return WebMonitorResponse(
            enabled=caps.web_sensor,
            urls_processed=0,
            urls_pending=0,
            last_scan_at=None,
            error_count=0,
        )

    return router
