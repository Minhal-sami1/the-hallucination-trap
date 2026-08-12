"""Fetch pinned ONNX artifacts with resume support and hash verification."""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import quote

import httpx


@dataclass(frozen=True, slots=True)
class Artifact:
    repository: str
    revision: str
    filename: str
    destination: str
    sha256: str | None = None


EMBEDDING_REPOSITORY = "qdrant/paraphrase-multilingual-MiniLM-L12-v2-onnx-Q"
EMBEDDING_REVISION = "faf4aa4225822f3bc6376869cb1164e8e3feedd0"
RERANKER_REPOSITORY = "onnx-community/gte-multilingual-reranker-base"
RERANKER_REVISION = "ee64367e35a2db0da46bb6497e13a18f8bd585cb"

ARTIFACTS = (
    Artifact(EMBEDDING_REPOSITORY, EMBEDDING_REVISION, "config.json", "embedding/config.json"),
    Artifact(
        EMBEDDING_REPOSITORY,
        EMBEDDING_REVISION,
        "model_optimized.onnx",
        "embedding/model_optimized.onnx",
        "634d0f66c29dc934c8fa72b8a4fe91dd4d420a22f1d82a241058d4316e659a99",
    ),
    Artifact(
        EMBEDDING_REPOSITORY,
        EMBEDDING_REVISION,
        "ort_config.json",
        "embedding/ort_config.json",
    ),
    Artifact(
        EMBEDDING_REPOSITORY,
        EMBEDDING_REVISION,
        "special_tokens_map.json",
        "embedding/special_tokens_map.json",
    ),
    Artifact(
        EMBEDDING_REPOSITORY,
        EMBEDDING_REVISION,
        "tokenizer.json",
        "embedding/tokenizer.json",
        "fa685fc160bbdbab64058d4fc91b60e62d207e8dc60b9af5c002c5ab946ded00",
    ),
    Artifact(
        EMBEDDING_REPOSITORY,
        EMBEDDING_REVISION,
        "tokenizer_config.json",
        "embedding/tokenizer_config.json",
    ),
    Artifact(EMBEDDING_REPOSITORY, EMBEDDING_REVISION, "unigram.json", "embedding/unigram.json"),
    Artifact(
        RERANKER_REPOSITORY,
        RERANKER_REVISION,
        "tokenizer.json",
        "reranker/tokenizer.json",
    ),
    Artifact(
        RERANKER_REPOSITORY,
        RERANKER_REVISION,
        "onnx/model_int8.onnx",
        "reranker/model_int8.onnx",
        "ccf51dba7f8aa9205753761cfaa68c55f741792501463a3bf25d7e5bcdac7c35",
    ),
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _url(artifact: Artifact) -> str:
    filename = quote(artifact.filename, safe="/")
    return (
        f"https://huggingface.co/{artifact.repository}/resolve/"
        f"{artifact.revision}/{filename}?download=true"
    )


def _download(client: httpx.Client, artifact: Artifact, root: Path) -> None:
    destination = root / artifact.destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and (
        artifact.sha256 is None or _sha256(destination) == artifact.sha256
    ):
        print(f"ready {artifact.destination}", flush=True)
        return

    partial = destination.with_suffix(destination.suffix + ".part")
    for attempt in range(1, 6):
        offset = partial.stat().st_size if partial.exists() else 0
        headers = {"Range": f"bytes={offset}-"} if offset else {}
        try:
            with client.stream("GET", _url(artifact), headers=headers) as response:
                response.raise_for_status()
                append = offset > 0 and response.status_code == 206
                if not append:
                    offset = 0
                mode = "ab" if append else "wb"
                downloaded = offset
                next_report = downloaded + 32 * 1024 * 1024
                with partial.open(mode) as output:
                    for chunk in response.iter_bytes(1024 * 1024):
                        output.write(chunk)
                        downloaded += len(chunk)
                        if downloaded >= next_report:
                            print(
                                f"fetching {artifact.destination}: "
                                f"{downloaded / 1024 / 1024:.0f} MiB",
                                flush=True,
                            )
                            next_report += 32 * 1024 * 1024
            if artifact.sha256 is not None:
                actual_hash = _sha256(partial)
                if actual_hash != artifact.sha256:
                    partial.unlink(missing_ok=True)
                    raise RuntimeError(
                        f"SHA-256 mismatch for {artifact.destination}: {actual_hash}"
                    )
            partial.replace(destination)
            print(f"verified {artifact.destination}", flush=True)
            return
        except Exception as exc:
            if attempt == 5:
                raise RuntimeError(
                    f"Failed to fetch {artifact.destination} after {attempt} attempts"
                ) from exc
            delay = 2**attempt
            print(
                f"retry {artifact.destination} after {type(exc).__name__}; "
                f"waiting {delay}s",
                flush=True,
            )
            time.sleep(delay)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    root = args.destination.resolve()
    root.mkdir(parents=True, exist_ok=True)
    timeout = httpx.Timeout(connect=30, read=120, write=30, pool=30)
    with httpx.Client(follow_redirects=True, timeout=timeout) as client:
        for artifact in ARTIFACTS:
            _download(client, artifact, root)
    manifest = {
        "format": 1,
        "artifacts": [asdict(artifact) for artifact in ARTIFACTS],
    }
    (root / "models.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    print("Pinned model artifacts are complete.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
