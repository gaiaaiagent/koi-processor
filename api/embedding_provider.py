"""
Embedding provider abstraction.

Supports OpenAI and Ollama backends with a factory that reads env vars.
Voyage provider deferred to a future phase.
"""

import asyncio
import logging
import os
from abc import ABC, abstractmethod
from typing import List, Optional

logger = logging.getLogger(__name__)


class EmbeddingProvider(ABC):
    """Abstract base class for embedding providers."""

    dimension: int
    model_name: str

    @abstractmethod
    async def embed(self, text: str) -> List[float]:
        """Generate embedding for a single text. Raises on failure."""

    async def embed_batch(self, texts: List[str]) -> List[List[float]]:
        """Generate embeddings for multiple texts. Default: sequential."""
        return [await self.embed(t) for t in texts]

    async def embed_or_none(self, text: str) -> Optional[List[float]]:
        """Generate embedding, returning None on failure instead of raising."""
        try:
            return await self.embed(text)
        except Exception as e:
            logger.warning(f"Embedding failed ({self.model_name}): {e}")
            return None


class OpenAIEmbeddingProvider(EmbeddingProvider):
    """OpenAI embeddings via the openai package (existing dep)."""

    def __init__(self, api_key: str, model: str = "text-embedding-ada-002",
                 dimension: Optional[int] = None):
        from openai import OpenAI
        self._client = OpenAI(api_key=api_key)
        self.model_name = model
        self.dimension = dimension or self._default_dimension(model)

    @staticmethod
    def _default_dimension(model: str) -> int:
        defaults = {
            "text-embedding-ada-002": 1536,
            "text-embedding-3-small": 1536,
            "text-embedding-3-large": 3072,
        }
        return defaults.get(model, 1536)

    async def embed(self, text: str) -> List[float]:
        response = await asyncio.to_thread(
            self._client.embeddings.create,
            model=self.model_name,
            input=text,
        )
        return response.data[0].embedding

    async def embed_batch(self, texts: List[str]) -> List[List[float]]:
        response = await asyncio.to_thread(
            self._client.embeddings.create,
            model=self.model_name,
            input=texts,
        )
        return [d.embedding for d in response.data]


class OllamaEmbeddingProvider(EmbeddingProvider):
    """Ollama embeddings via HTTP (uses httpx, existing dep)."""

    def __init__(self, model: str = "nomic-embed-text",
                 base_url: str = "http://localhost:11434",
                 dimension: Optional[int] = None):
        import httpx
        self._client = httpx.AsyncClient(base_url=base_url, timeout=120.0)
        self.model_name = model
        self.dimension = dimension or self._default_dimension(model)

    @staticmethod
    def _default_dimension(model: str) -> int:
        defaults = {
            "nomic-embed-text": 768,
            "mxbai-embed-large": 1024,
            "bge-large": 1024,
            "all-minilm": 384,
        }
        return defaults.get(model, 768)

    async def embed(self, text: str) -> List[float]:
        resp = await self._client.post("/api/embeddings", json={
            "model": self.model_name,
            "prompt": text,
        })
        resp.raise_for_status()
        return resp.json()["embedding"]

    async def embed_batch(self, texts: List[str]) -> List[List[float]]:
        """Batch embed using /api/embed endpoint (Ollama 0.4+)."""
        resp = await self._client.post("/api/embed", json={
            "model": self.model_name,
            "input": texts,
        })
        resp.raise_for_status()
        return resp.json()["embeddings"]


class SentenceTransformerProvider(EmbeddingProvider):
    """Local sentence-transformers embeddings (no network calls)."""

    def __init__(self, model: str = "BAAI/bge-large-en-v1.5",
                 dimension: Optional[int] = None):
        from sentence_transformers import SentenceTransformer
        self._model = SentenceTransformer(model)
        self.model_name = model
        self.dimension = dimension or self._model.get_sentence_embedding_dimension()
        self._has_query_prompt = "query" in (self._model.prompts or {})
        logger.info(f"SentenceTransformer loaded: {model} (dim={self.dimension}, query_prompt={self._has_query_prompt})")

    async def embed(self, text: str) -> List[float]:
        """Embed a single text (query-time). Uses prompt_name='query' for instruction-aware models."""
        kwargs = {"normalize_embeddings": True}
        if self._has_query_prompt:
            kwargs["prompt_name"] = "query"
        embedding = await asyncio.to_thread(
            self._model.encode, text, **kwargs
        )
        return embedding.tolist()

    async def embed_batch(self, texts: List[str]) -> List[List[float]]:
        """Embed multiple texts (document storage). No instruction prefix."""
        embeddings = await asyncio.to_thread(
            self._model.encode, texts, normalize_embeddings=True
        )
        return [e.tolist() for e in embeddings]


def create_embedding_provider() -> Optional[EmbeddingProvider]:
    """
    Factory: read env vars and return a provider (or None if disabled).

    Precedence:
      EMBEDDING_PROVIDER="openai"  → requires OPENAI_API_KEY
      EMBEDDING_PROVIDER="ollama"  → uses OLLAMA_BASE_URL
      EMBEDDING_PROVIDER=""        → disabled (explicit)
      EMBEDDING_PROVIDER unset + OPENAI_API_KEY set → auto-select openai
      EMBEDDING_PROVIDER unset + no key             → disabled
    """
    provider_env = os.environ.get("EMBEDDING_PROVIDER")  # None if unset
    model = os.getenv("EMBEDDING_MODEL", "")
    dim_str = os.getenv("EMBEDDING_DIMENSION", "")
    dim = int(dim_str) if dim_str else None

    # Explicit empty string = disabled
    if provider_env is not None and provider_env.strip() == "":
        logger.info("Embedding provider explicitly disabled (EMBEDDING_PROVIDER='')")
        return None

    # Determine effective provider
    if provider_env is not None:
        provider_name = provider_env.strip().lower()
    else:
        # Backward compat: unset EMBEDDING_PROVIDER + key present → openai
        if os.getenv("OPENAI_API_KEY", ""):
            provider_name = "openai"
            logger.info("Auto-selecting openai embedding provider (OPENAI_API_KEY present)")
        else:
            logger.info("No embedding provider configured (no EMBEDDING_PROVIDER, no OPENAI_API_KEY)")
            return None

    if provider_name == "openai":
        api_key = os.getenv("OPENAI_API_KEY", "")
        if not api_key:
            logger.error("EMBEDDING_PROVIDER=openai but OPENAI_API_KEY is not set")
            raise SystemExit(1)
        p = OpenAIEmbeddingProvider(
            api_key=api_key,
            model=model or "text-embedding-ada-002",
            dimension=dim,
        )
        logger.info(f"Embedding provider: {p.model_name} (dim={p.dimension})")
        return p

    if provider_name == "ollama":
        base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
        p = OllamaEmbeddingProvider(
            model=model or "nomic-embed-text",
            base_url=base_url,
            dimension=dim,
        )
        logger.info(f"Embedding provider: ollama/{p.model_name} (dim={p.dimension})")
        return p

    if provider_name == "sentence_transformers":
        p = SentenceTransformerProvider(
            model=model or "BAAI/bge-large-en-v1.5",
            dimension=dim,
        )
        logger.info(f"Embedding provider: st/{p.model_name} (dim={p.dimension})")
        return p

    logger.error(f"Unknown EMBEDDING_PROVIDER: {provider_name!r}")
    raise SystemExit(1)
