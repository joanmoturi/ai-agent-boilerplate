"""Optional embedding ingestion/search; not part of the agent's tool allowlist."""

import math
from typing import Annotated

import openai
from fastapi import APIRouter, HTTPException
from pydantic import Field
from sqlalchemy import select

from app.auth import SessionDep, SettingsDep, UserDep
from app.models import Document
from app.schemas import StrictModel

router = APIRouter(prefix="/documents", tags=["documents"])
EMBEDDING_MODEL = "text-embedding-3-small"


class DocumentRequest(StrictModel):
    content: Annotated[str, Field(min_length=1, max_length=12000)]


class SearchRequest(StrictModel):
    query: Annotated[str, Field(min_length=1, max_length=4000)]
    limit: Annotated[int, Field(ge=1, le=20)] = 5


class DocumentResponse(StrictModel):
    id: str
    content: str
    embedding_model: str


async def embed(text: str, settings: SettingsDep) -> list[float]:
    if not settings.openai_api_key:
        raise HTTPException(503, detail="openai_not_configured")
    try:
        async with openai.AsyncOpenAI(
            api_key=settings.openai_api_key.get_secret_value(), timeout=20, max_retries=0
        ) as client:
            result = await client.embeddings.create(
                model=EMBEDDING_MODEL, input=text, dimensions=1536
            )
        vector = result.data[0].embedding
        if len(vector) != 1536 or not all(math.isfinite(value) for value in vector):
            raise ValueError("invalid embedding")
        return vector
    except (openai.APIError, ValueError, IndexError) as exc:
        raise HTTPException(502, detail="embedding_service_error") from exc


@router.post("", response_model=DocumentResponse, status_code=201)
async def create_document(
    body: DocumentRequest, user: UserDep, session: SessionDep, settings: SettingsDep
) -> DocumentResponse:
    if session.get_bind().dialect.name != "postgresql":
        raise HTTPException(503, detail="vector_search_requires_postgresql")
    vector = await embed(body.content, settings)
    document = Document(
        user_id=user.id, content=body.content, embedding_model=EMBEDDING_MODEL, embedding=vector
    )
    session.add(document)
    await session.commit()
    return DocumentResponse(
        id=document.id, content=document.content, embedding_model=document.embedding_model
    )


@router.post("/search", response_model=list[DocumentResponse])
async def search_documents(
    body: SearchRequest, user: UserDep, session: SessionDep, settings: SettingsDep
) -> list[DocumentResponse]:
    if session.get_bind().dialect.name != "postgresql":
        raise HTTPException(503, detail="vector_search_requires_postgresql")
    vector = await embed(body.query, settings)
    documents = (
        await session.scalars(
            select(Document)
            .where(
                Document.user_id == user.id,
                Document.embedding_model == EMBEDDING_MODEL,
                Document.embedding.is_not(None),
            )
            .order_by(Document.embedding.cosine_distance(vector))
            .limit(body.limit)
        )
    ).all()
    return [
        DocumentResponse(id=doc.id, content=doc.content, embedding_model=doc.embedding_model)
        for doc in documents
    ]
