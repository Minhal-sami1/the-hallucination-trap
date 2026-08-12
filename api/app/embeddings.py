from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import numpy as np

from api.app.config import get_settings


@lru_cache(maxsize=2)
def _load_model(model_name: str, cache_dir: str):
    from fastembed import TextEmbedding

    cache_path = Path(cache_dir)
    cache_path.mkdir(parents=True, exist_ok=True)
    local_model = cache_path / "embedding"
    if (local_model / "model_optimized.onnx").exists():
        supported_name = model_name.rsplit("@", 1)[0]
        return TextEmbedding(
            model_name=supported_name,
            cache_dir=cache_dir,
            specific_model_path=str(local_model),
        )
    if "@" in model_name:
        supported_name, revision = model_name.rsplit("@", 1)
        return TextEmbedding(
            model_name=supported_name,
            cache_dir=cache_dir,
            revision=revision,
        )
    return TextEmbedding(model_name=model_name, cache_dir=cache_dir)


def embed_documents(texts: list[str]) -> list[list[float]]:
    settings = get_settings()
    model = _load_model(settings.embedding_model, str(settings.model_cache_dir))
    values = model.embed(texts, batch_size=32, parallel=0)
    return [np.asarray(value, dtype=np.float32).tolist() for value in values]


def embed_query(text: str) -> list[float]:
    return embed_documents([text])[0]
