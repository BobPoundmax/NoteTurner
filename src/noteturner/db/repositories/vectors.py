from dataclasses import dataclass

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from noteturner.db.models import DocChunk


@dataclass
class ChunkInput:
    content: str
    embedding: list[float]
    chunk_index: int
    title: str | None = None
    record_type: str = "unknown"
    is_financial: bool = False
    payload: dict | None = None


async def replace_file_chunks(
    session: AsyncSession,
    *,
    source: str,
    external_id: str,
    chunks: list[ChunkInput],
) -> int:
    """Replace all chunks for a given source file with a fresh set."""
    await session.execute(
        delete(DocChunk).where(
            DocChunk.source == source,
            DocChunk.external_id == external_id,
        )
    )
    for chunk in chunks:
        session.add(
            DocChunk(
                source=source,
                external_id=external_id,
                record_type=chunk.record_type,
                title=chunk.title,
                chunk_index=chunk.chunk_index,
                content=chunk.content,
                embedding=chunk.embedding,
                is_financial=chunk.is_financial,
                payload=chunk.payload,
            )
        )
    await session.commit()
    return len(chunks)


async def search_chunks(
    session: AsyncSession,
    *,
    embedding: list[float],
    include_financial: bool,
    limit: int = 5,
) -> list[DocChunk]:
    """Return the closest chunks by cosine distance. Financial chunks are
    excluded unless ``include_financial`` is True (i.e. the requester is admin)."""
    stmt = select(DocChunk)
    if not include_financial:
        stmt = stmt.where(DocChunk.is_financial.is_(False))
    stmt = stmt.order_by(DocChunk.embedding.cosine_distance(embedding)).limit(limit)
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def count_doc_chunks(session: AsyncSession) -> int:
    result = await session.execute(select(func.count()).select_from(DocChunk))
    return int(result.scalar_one())
