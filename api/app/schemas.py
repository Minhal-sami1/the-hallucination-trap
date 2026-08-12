"""HTTP request and response schemas."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator


class AuditRequest(BaseModel):
    question: str = Field(min_length=8, max_length=3000)
    language: Literal["en", "ar"] = "en"
    mode: Literal["cached", "live"] = "cached"
    seed_id: str | None = Field(default=None, max_length=80)

    @model_validator(mode="after")
    def normalize_question(self) -> AuditRequest:
        self.question = " ".join(self.question.split())
        return self
