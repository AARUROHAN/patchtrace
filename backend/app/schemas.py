from typing import Literal
from pydantic import BaseModel, Field, field_validator


class InspectRequest(BaseModel):
    source_type: Literal["demo", "github", "local"] = "demo"
    source: str = ""
    task: str = Field(min_length=12, max_length=3000)
    use_ai: bool = True

    @field_validator("source")
    @classmethod
    def trim_source(cls, value: str) -> str:
        return value.strip()


class CheckRequest(BaseModel):
    check_id: Literal["python_compile", "pytest"]
    confirm_execution: bool = False
