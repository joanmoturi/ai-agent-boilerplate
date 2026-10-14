from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, StringConstraints


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Credentials(StrictModel):
    email: EmailStr = Field(max_length=254)
    password: str = Field(min_length=12, max_length=128)


class UserResponse(StrictModel):
    model_config = ConfigDict(extra="forbid", strict=True, from_attributes=True)
    id: str
    email: str


class TokenResponse(StrictModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    user: UserResponse


class ProvidersResponse(StrictModel):
    local: bool
    oidc: bool


class RunRequest(StrictModel):
    prompt: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)]


class RunResponse(StrictModel):
    model_config = ConfigDict(extra="forbid", strict=True, from_attributes=True)
    id: str
    status: Literal["running", "completed", "failed"]
    answer: str | None
    error: str | None
    steps: int


class Arithmetic(StrictModel):
    operation: Literal["add", "subtract", "multiply", "divide"]
    left: str = Field(pattern=r"^-?\d{1,12}(\.\d{1,8})?$")
    right: str = Field(pattern=r"^-?\d{1,12}(\.\d{1,8})?$")


class Decision(StrictModel):
    """All fields required, nullable alternatives compatible with strict OpenAI output."""

    action: Literal["tool", "final"]
    tool: Arithmetic | None
    answer: str | None = Field(min_length=1, max_length=8000)
