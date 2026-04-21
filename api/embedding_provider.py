"""
Embedding provider abstraction.

Supports OpenAI, Ollama, sentence-transformers, and remote (poly) backends
with a factory that reads env vars.

Query/document split:
  - embed() / embed_batch() = DOCUMENT mode (no instruction prefix)
  - embed_query() = QUERY mode (with instruction prefix for retrieval)
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
        """Generate embedding for a single text (DOCUMENT mode). Raises on failure."""

    async def embed_query(self, text: str) -> List[float]:
        """Generate embedding for a query (QUERY mode — with instruction prefix).

        Default: same as embed(). Subclasses override for instruction-aware models.
        """
        return await self.embed(text)

    async def embed_batch(self, texts: List[str]) -> List[List[float]]:
        """Generate embeddings for multiple texts (DOCUMENT mode). Default: sequential."""
        return [await self.embed(t) for t in texts]

    async def embed_or_none(self, text: str, is_query: bool = False) -> Optional[List[float]]:
        """Generate embedding, returning None on failure instead of raising."""
        try:
            if is_query:
                return await self.embed_query(text)
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

    def _supports_dimensions(self) -> bool:
        """`dimensions` param is supported on text-embedding-3-* models, not ada-002."""
        return self.model_name.startswith("text-embedding-3-")

    def _create_kwargs(self, input_):
        kwargs = {"model": self.model_name, "input": input_}
        if self._supports_dimensions():
            kwargs["dimensions"] = self.dimension
        return kwargs

    async def embed(self, text: str) -> List[float]:
        response = await asyncio.to_thread(
            self._client.embeddings.create,
            **self._create_kwargs(text),
        )
        return response.data[0].embedding

    async def embed_batch(self, texts: List[str]) -> List[List[float]]:
        response = await asyncio.to_thread(
            self._client.embeddings.create,
            **self._create_kwargs(texts),
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
        """Embed a single text (DOCUMENT mode). No instruction prefix."""
        embedding = await asyncio.to_thread(
            self._model.encode, text, normalize_embeddings=True
        )
        return embedding.tolist()

    async def embed_query(self, text: str) -> List[float]:
        """Embed a query (QUERY mode). Uses prompt_name='query' for instruction-aware models."""
        kwargs = {"normalize_embeddings": True}
        if self._has_query_prompt:
            kwargs["prompt_name"] = "query"
        embedding = await asyncio.to_thread(
            self._model.encode, text, **kwargs
        )
        return embedding.tolist()

    async def embed_batch(self, texts: List[str]) -> List[List[float]]:
        """Embed multiple texts (DOCUMENT mode). No instruction prefix."""
        embeddings = await asyncio.to_thread(
            self._model.encode, texts, normalize_embeddings=True
        )
        return [e.tolist() for e in embeddings]


class RemoteEmbeddingProvider(EmbeddingProvider):
    """Remote embedding service (e.g. poly FastAPI). Query/document split handled server-side."""

    def __init__(self, base_url: str, dimension: int = 1024,
                 model: str = "Qwen/Qwen3-Embedding-0.6B"):
        import httpx
        self._client = httpx.AsyncClient(
            base_url=base_url,
            # read=25s stays under MCP's 30s client budget so callers hit the
            # degrade-to-ILIKE fallback in unified-search rather than a raw
            # timeout. Healthy /embed latency is <1s; 25s is ~25× headroom.
            timeout=httpx.Timeout(connect=5.0, read=25.0, write=5.0, pool=5.0),
        )
        self.model_name = model
        self.dimension = dimension
        self._base_url = base_url

    async def _embed_texts(self, texts: List[str], is_query: bool) -> List[List[float]]:
        """Call remote /embed endpoint with retry."""
        payload = {"texts": texts, "is_query": is_query}
        last_err = None
        for attempt in range(2):
            try:
                resp = await self._client.post("/embed", json=payload)
                resp.raise_for_status()
                return resp.json()["embeddings"]
            except Exception as e:
                last_err = e
                if attempt == 0:
                    logger.warning(f"Remote embed attempt 1 failed ({e}), retrying...")
                    await asyncio.sleep(0.5)
        raise RuntimeError(f"Remote embedding failed after 2 attempts: {last_err}")

    async def embed(self, text: str) -> List[float]:
        """DOCUMENT mode — no instruction prefix."""
        results = await self._embed_texts([text], is_query=False)
        return results[0]

    async def embed_query(self, text: str) -> List[float]:
        """QUERY mode — with instruction prefix."""
        results = await self._embed_texts([text], is_query=True)
        return results[0]

    async def embed_batch(self, texts: List[str]) -> List[List[float]]:
        """DOCUMENT mode batch."""
        return await self._embed_texts(texts, is_query=False)


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

    if provider_name == "remote":
        remote_url = os.getenv("EMBEDDING_REMOTE_URL", "")
        if not remote_url:
            logger.error("EMBEDDING_PROVIDER=remote but EMBEDDING_REMOTE_URL is not set")
            raise SystemExit(1)
        p = RemoteEmbeddingProvider(
            base_url=remote_url,
            dimension=dim or 1024,
            model=model or "Qwen/Qwen3-Embedding-0.6B",
        )
        logger.info(f"Embedding provider: remote/{p.model_name} @ {remote_url} (dim={p.dimension})")
        return p

    logger.error(f"Unknown EMBEDDING_PROVIDER: {provider_name!r}")
    raise SystemExit(1)
