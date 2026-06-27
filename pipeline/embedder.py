import asyncio
import voyageai
from typing import Optional
from config import config

BATCH_SIZE = 100
MAX_RETRIES = 3

# Errors where retrying can never help — fail fast, don't waste time
NON_RETRYABLE_MARKERS = (
    "api key",
    "unauthorized",
    "401",
    "invalid_api_key",
    "authentication",
)


def _is_retryable(error: Exception) -> bool:
    error_str = str(error).lower()
    return not any(marker in error_str for marker in NON_RETRYABLE_MARKERS)


def _get_provider_category(provider: str) -> str:
    if provider == "voyage":
        return "voyage"
    if provider == "local":
        return "local"
    return "openai_compatible"  # openai, mistral, cohere, ollama, custom


async def _embed_batch_once(
    batch: list[str],
    provider: str,
    model_name: str,
    api_key: str,
    base_url: Optional[str],
    input_type: str,  # "document" | "query"
) -> list[list[float]]:
    category = _get_provider_category(provider)

    if category == "voyage":
        client = voyageai.Client(api_key=api_key)
        result = client.embed(batch, model=model_name, input_type=input_type)
        return result.embeddings

    elif category == "local":
        from sentence_transformers import SentenceTransformer

        model = SentenceTransformer(model_name)
        return model.encode(batch).tolist()

    else:
        from openai import AsyncOpenAI

        client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        response = await client.embeddings.create(input=batch, model=model_name)
        return [r.embedding for r in response.data]


async def _embed_batch_with_retry(
    batch: list[str],
    provider: str,
    model_name: str,
    api_key: str,
    base_url: Optional[str],
    input_type: str,
) -> list[list[float]]:
    """
    Retries transient failures (network blips, rate limits, momentary
    5xx errors) up to MAX_RETRIES times with exponential backoff
    (1s, 2s, 4s). Fails immediately on errors retrying can't fix,
    such as invalid API keys.
    """
    last_error = None

    for attempt in range(MAX_RETRIES):
        try:
            return await _embed_batch_once(
                batch, provider, model_name, api_key, base_url, input_type
            )
        except Exception as e:
            last_error = e

            if not _is_retryable(e):
                raise

            if attempt < MAX_RETRIES - 1:
                await asyncio.sleep(2**attempt)  # 1s, 2s, 4s

    raise last_error


async def embed_texts(
    texts: list[str],
    provider: str = None,
    model_name: str = None,
    api_key: str = None,
    base_url: Optional[str] = None,
) -> list[list[float]]:
    """
    Embeds a list of texts for storage (input_type="document").
    Batches in groups of BATCH_SIZE. Each batch is retried
    independently — one failing batch never blocks or invalidates
    batches that already succeeded.
    """
    if not texts:
        return []

    provider = provider or config.embedding_provider
    model_name = model_name or config.embedding_model_name
    api_key = api_key or config.resolved_embedding_api_key()
    base_url = base_url or config.embedding_base_url

    all_embeddings = []
    for i in range(0, len(texts), BATCH_SIZE):
        batch = texts[i : i + BATCH_SIZE]
        embeddings = await _embed_batch_with_retry(
            batch,
            provider,
            model_name,
            api_key,
            base_url,
            input_type="document",
        )
        all_embeddings.extend(embeddings)

    return all_embeddings


async def embed_query(
    query: str,
    provider: str = None,
    model_name: str = None,
    api_key: str = None,
    base_url: Optional[str] = None,
) -> list[float]:
    """
    Embeds a single search query (input_type="query"). Voyage AI
    uses asymmetric embeddings — query vectors are optimized
    differently from document vectors for better retrieval.
    """
    provider = provider or config.embedding_provider
    model_name = model_name or config.embedding_model_name
    api_key = api_key or config.resolved_embedding_api_key()
    base_url = base_url or config.embedding_base_url

    embeddings = await _embed_batch_with_retry(
        [query],
        provider,
        model_name,
        api_key,
        base_url,
        input_type="query",
    )
    return embeddings[0]
