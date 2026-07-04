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


@dataclass
class ChunkMatch:
    chunk: DocChunk
    distance: float


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


async def delete_source_chunks(
    session: AsyncSession,
    *,
    source: str,
    external_id: str,
) -> None:
    """Delete all chunks for a source file (used before streaming a fresh set)."""
    await session.execute(
        delete(DocChunk).where(
            DocChunk.source == source,
            DocChunk.external_id == external_id,
        )
    )
    await session.commit()


async def add_chunks(
    session: AsyncSession,
    *,
    source: str,
    external_id: str,
    chunks: list[ChunkInput],
) -> int:
    """Append a batch of chunks without deleting existing ones."""
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
    sources: list[str] | None = None,
    record_types: list[str] | None = None,
    limit: int = 5,
) -> list[DocChunk]:
    """Backward-compatible wrapper returning only chunks."""
    matches = await search_chunk_matches(
        session,
        embedding=embedding,
        include_financial=include_financial,
        sources=sources,
        record_types=record_types,
        limit=limit,
    )
    return [match.chunk for match in matches]


async def search_chunk_matches(
    session: AsyncSession,
    *,
    embedding: list[float],
    include_financial: bool,
    sources: list[str] | None = None,
    record_types: list[str] | None = None,
    limit: int = 5,
    max_distance: float | None = None,
) -> list[ChunkMatch]:
    """Return the closest chunks by cosine distance with optional filtering."""
    distance_expr = DocChunk.embedding.cosine_distance(embedding).label("distance")
    stmt = select(DocChunk, distance_expr)
    if not include_financial:
        stmt = stmt.where(DocChunk.is_financial.is_(False))
    if sources:
        stmt = stmt.where(DocChunk.source.in_(sources))
    if record_types:
        stmt = stmt.where(DocChunk.record_type.in_(record_types))
    stmt = stmt.order_by(distance_expr).limit(limit)
    result = await session.execute(stmt)

    matches: list[ChunkMatch] = []
    for chunk, distance in result.all():
        numeric_distance = float(distance)
        if max_distance is not None and numeric_distance > max_distance:
            continue
        matches.append(ChunkMatch(chunk=chunk, distance=numeric_distance))
    return matches


async def count_doc_chunks(session: AsyncSession) -> int:
    result = await session.execute(select(func.count()).select_from(DocChunk))
    return int(result.scalar_one())


async def count_doc_chunks_by_source(session: AsyncSession) -> dict[str, int]:
    result = await session.execute(
        select(DocChunk.source, func.count()).group_by(DocChunk.source)
    )
    return {source: int(count) for source, count in result.all()}


async def count_doc_chunks_by_record_type(
    session: AsyncSession,
    *,
    source: str | None = None,
    include_financial: bool = True,
) -> dict[str, int]:
    stmt = select(DocChunk.record_type, func.count()).group_by(DocChunk.record_type)
    if source is not None:
        stmt = stmt.where(DocChunk.source == source)
    if not include_financial:
        stmt = stmt.where(DocChunk.is_financial.is_(False))
    result = await session.execute(stmt)
    return {record_type: int(count) for record_type, count in result.all()}
