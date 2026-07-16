"""Provider-neutral image artifact storage and resolution."""

from __future__ import annotations

import asyncio
import io
import logging
import os
import re
import struct
import time
import uuid
import weakref
from collections.abc import Awaitable, Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from PIL import Image, UnidentifiedImageError

from cognitrix import artifacts as artifact_store
from cognitrix.artifacts import Artifact, ref, variant_path
from cognitrix.media.processing import (
    _ACCEPTED_FORMATS,
    _ProcessedImage,
    _is_valid_thumbnail,
    _make_thumbnail,
    _process_image,
    _run_thread_joined,
    run_media_cpu,
)
from cognitrix.media.types import (
    ImageVariant,
    MediaAccessError,
    MediaNotFoundError,
    MediaOwnership,
    MediaQuotaError,
    MediaValidationError,
    ResolvedImage,
    ResolvedMediaFile,
    StagedAttachment,
)
from cognitrix.tools.utils import ArtifactRef

logger = logging.getLogger('cognitrix.log')

MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_SESSION_ARTIFACTS = 20
MAX_SESSION_BYTES = 100 * 1024 * 1024
_VALID_VARIANTS = {'original', 'vision', 'thumbnail'}
_SESSION_LOCKS: weakref.WeakValueDictionary = weakref.WeakValueDictionary()
_ARTIFACT_LOCKS: weakref.WeakValueDictionary = weakref.WeakValueDictionary()

Image.init()
_PILLOW_ACCEPTORS = tuple(
    (image_format.upper(), plugin[1])
    for image_format, plugin in Image.OPEN.items()
    if plugin[1] is not None
)


def _shared_lock(cache: weakref.WeakValueDictionary, key: str) -> asyncio.Lock:
    lock = cache.get(key)
    if lock is None:
        lock = asyncio.Lock()
        cache[key] = lock
    return lock


def _artifact_variant_paths(artifact: Artifact) -> list[Path]:
    paths = []
    for variant, field in (
        ('original', 'storage_key'),
        ('vision', 'vision_storage_key'),
        ('thumbnail', 'thumbnail_storage_key'),
    ):
        if variant != 'original' and not getattr(artifact, field):
            continue
        paths.append(variant_path(artifact, variant))
    return paths


def _retained_physical_bytes(artifacts: Sequence[Artifact]) -> int:
    retained = 0
    seen: set[Path] = set()
    for artifact in artifacts:
        for path in _artifact_variant_paths(artifact):
            if path in seen:
                continue
            seen.add(path)
            try:
                retained += path.stat().st_size
            except OSError:
                continue
    return retained


def _variant_mime(artifact: Artifact, variant: ImageVariant) -> str:
    return 'image/webp' if variant == 'thumbnail' else artifact.mime_type


def _safe_filename(filename: str | None, artifact_id: str, extension: str) -> str:
    basename = Path(filename or '').name
    stem = Path(basename).stem
    stem = re.sub(r'[^A-Za-z0-9._-]+', '-', stem).strip('.-_')
    if not stem:
        stem = f'image-{artifact_id[:8]}'
    return f'{stem[:120]}{extension}'


def _log_ingest_metric(
    *,
    status: str,
    origin: str,
    input_bytes: int,
    processed: _ProcessedImage | None,
    started: float,
) -> None:
    logger.info(
        'media_ingest status=%s origin=%s input_bytes=%d original_bytes=%d '
        'vision_bytes=%d thumbnail_bytes=%d width=%d height=%d media_ingest_ms=%.2f',
        status,
        origin,
        input_bytes,
        len(processed.original) if processed else 0,
        len(processed.vision) if processed else 0,
        len(processed.thumbnail) if processed and processed.thumbnail else 0,
        processed.width if processed else 0,
        processed.height if processed else 0,
        (time.perf_counter() - started) * 1000,
    )


async def _read_path(path: Path) -> bytes:
    return await asyncio.to_thread(path.read_bytes)


def _raster_signature_kind(data: bytes) -> str | None:
    """Return supported/unsupported for recognizable raster containers."""
    if (
        data.startswith(b'\x89PNG\r\n\x1a\n')
        or data.startswith(b'\xff\xd8\xff')
        or data.startswith((b'GIF87a', b'GIF89a'))
        or data.startswith(b'BM')
        or data.startswith((b'II*\x00', b'MM\x00*'))
        or (len(data) >= 12 and data[:4] == b'RIFF' and data[8:12] == b'WEBP')
    ):
        return 'supported'
    if (
        data.startswith((b'\x00\x00\x01\x00', b'\x00\x00\x02\x00'))
        or data.startswith(b'8BPS')
        or data.startswith(b'\x00\x00\x00\x0cjP  \r\n\x87\n')
        or (
            len(data) >= 12
            and data[4:8] == b'ftyp'
            and data[8:12] in {b'avif', b'heic', b'heix', b'hevc', b'mif1'}
        )
    ):
        return 'unsupported'

    prefix = data[:16]
    for image_format, accept in _PILLOW_ACCEPTORS:
        try:
            accepted = accept(prefix)
        except (IndexError, OSError, SyntaxError, TypeError, ValueError, struct.error):
            continue
        if accepted and not isinstance(accepted, (str, bytes)):
            return 'supported' if image_format in _ACCEPTED_FORMATS else 'unsupported'

    # Some Pillow plugins have no prefix acceptor. Image.open only parses their
    # bounded in-memory header here; pixel decoding remains in _process_image.
    try:
        with Image.open(io.BytesIO(data)) as image:
            image_format = (image.format or '').upper()
    except (
        Image.DecompressionBombError,
        UnidentifiedImageError,
        OSError,
        SyntaxError,
        ValueError,
    ):
        return None
    if image_format:
        return 'supported' if image_format in _ACCEPTED_FORMATS else 'unsupported'
    return None


def _process_staged_snapshot(
    staged: StagedAttachment,
    data: bytes,
    *,
    allow_unrecognized: bool,
) -> tuple[int, _ProcessedImage | None]:
    if len(data) > MAX_IMAGE_BYTES:
        raise MediaValidationError('Source image exceeds the 10 MiB limit')
    if len(data) != staged.size_bytes:
        raise MediaValidationError('Staged attachment size changed')
    signature = _raster_signature_kind(data)
    if signature is None:
        if allow_unrecognized:
            return len(data), None
        raise MediaValidationError('Attachment is not a valid image')
    if signature == 'unsupported':
        raise MediaValidationError('Unsupported image format')
    return len(data), _process_image(data)


def _read_and_process_staged(
    staged: StagedAttachment,
    *,
    allow_unrecognized: bool = False,
) -> tuple[int, _ProcessedImage | None]:
    """Read one bounded upload snapshot and decode that exact snapshot."""
    with staged.path.open('rb') as stream:
        data = stream.read(MAX_IMAGE_BYTES + 1)
    return _process_staged_snapshot(
        staged,
        data,
        allow_unrecognized=allow_unrecognized,
    )


def _read_and_validate_thumbnail(path: Path) -> bool:
    try:
        with path.open('rb') as stream:
            data = stream.read(MAX_IMAGE_BYTES + 1)
    except OSError:
        return False
    if len(data) > MAX_IMAGE_BYTES:
        return False
    return _is_valid_thumbnail(data)


async def _read_bounded_image_path(path: Path) -> bytes:
    try:
        size = await asyncio.to_thread(lambda: path.stat().st_size)
    except OSError as exc:
        raise MediaNotFoundError('Artifact variant is unavailable') from exc
    if size > MAX_IMAGE_BYTES:
        raise MediaValidationError('Source image exceeds the 10 MiB limit')
    return await _read_path(path)


async def _remove_paths(paths: Sequence[Path]) -> None:
    for path in dict.fromkeys(paths):
        try:
            await _run_thread_joined(path.unlink, missing_ok=True)
        except OSError:
            pass


async def _run_transaction_joined(operation: Awaitable[Any]) -> Any:
    """Cancel a transaction once, then wait for its rollback to finish."""
    transaction = asyncio.create_task(operation)
    try:
        return await asyncio.shield(transaction)
    except asyncio.CancelledError:
        transaction.cancel()
        while not transaction.done():
            try:
                await asyncio.shield(transaction)
            except asyncio.CancelledError:
                continue
            except BaseException:
                break
        if transaction.done() and not transaction.cancelled():
            try:
                transaction.result()
            except BaseException:
                pass
        raise


async def _settle_operation(operation: Awaitable[Any]) -> Any:
    """Let a started operation reach a definitive outcome."""
    mutation = asyncio.create_task(operation)
    try:
        return await asyncio.shield(mutation)
    except asyncio.CancelledError:
        while not mutation.done():
            try:
                await asyncio.shield(mutation)
            except asyncio.CancelledError:
                continue
            except BaseException:
                break
        return mutation.result()


async def _rollback_tombstones(moves: Sequence[tuple[Path, Path]]) -> None:
    rollback_error: BaseException | None = None
    for original, tombstone in reversed(moves):
        try:
            if await asyncio.to_thread(tombstone.exists):
                await _run_thread_joined(os.replace, tombstone, original)
        except BaseException as exc:
            rollback_error = rollback_error or exc
            logger.exception('Failed to roll back media deletion tombstone')
    if rollback_error is not None:
        raise rollback_error


async def _tombstone_delete(
    paths: Sequence[Path],
    delete_rows: Callable[[], Awaitable[Any]],
) -> None:
    moves: list[tuple[Path, Path]] = []
    try:
        for original in dict.fromkeys(paths):
            if not await asyncio.to_thread(original.exists):
                continue
            if not await asyncio.to_thread(original.is_file):
                raise MediaValidationError('Artifact variant is not a regular file')
            tombstone = original.with_name(
                f'.{original.name}.{uuid.uuid4().hex}.tombstone'
            )
            moves.append((original, tombstone))
            await _run_thread_joined(os.replace, original, tombstone)
        await delete_rows()
    except BaseException:
        await _rollback_tombstones(moves)
        raise
    await _settle_operation(
        _remove_paths([tombstone for _, tombstone in moves])
    )


class MediaAssetService:
    async def ingest_staged_image(
        self,
        staged: StagedAttachment,
        ownership: MediaOwnership,
    ) -> ArtifactRef:
        result = await self.ingest_staged_image_if_recognized(staged, ownership)
        if result is None:
            raise MediaValidationError('Attachment is not a valid image')
        return result

    async def ingest_staged_image_if_recognized(
        self,
        staged: StagedAttachment,
        ownership: MediaOwnership,
        *,
        snapshot: bytes | None = None,
    ) -> ArtifactRef | None:
        """Ingest a recognized raster; return ``None`` only for other bytes."""
        started = time.perf_counter()
        status = 'failure'
        input_bytes = 0
        processed: _ProcessedImage | None = None
        try:
            if not ownership.session_id:
                raise MediaValidationError('Media ownership requires a session')
            async with _shared_lock(_SESSION_LOCKS, ownership.session_id):
                if snapshot is None:
                    input_bytes, processed = await run_media_cpu(
                        _read_and_process_staged,
                        staged,
                        allow_unrecognized=True,
                    )
                else:
                    input_bytes, processed = await run_media_cpu(
                        _process_staged_snapshot,
                        staged,
                        snapshot,
                        allow_unrecognized=True,
                    )
                if processed is None:
                    status = 'unrecognized'
                    return None
                if len(processed.original) > MAX_IMAGE_BYTES:
                    raise MediaValidationError('Sanitized image exceeds the 10 MiB limit')
                artifact = await _run_transaction_joined(
                    self._commit_processed(
                        processed,
                        origin='uploaded',
                        ownership=ownership,
                        filename=staged.filename,
                        metadata={},
                    )
                )
            status = 'success'
            return ref(artifact)
        finally:
            _log_ingest_metric(
                status=status,
                origin='uploaded',
                input_bytes=input_bytes,
                processed=processed,
                started=started,
            )

    async def store_generated_image(
        self,
        data: bytes,
        metadata: Mapping[str, Any] | None,
        ownership: MediaOwnership,
    ) -> ArtifactRef:
        values = dict(metadata or {})
        return await self._store_image(
            data,
            origin='generated',
            ownership=ownership,
            filename=values.get('filename'),
            metadata=values,
        )

    async def _store_image(
        self,
        data: bytes,
        *,
        origin: str,
        ownership: MediaOwnership,
        filename: str | None,
        metadata: Mapping[str, Any],
    ) -> ArtifactRef:
        started = time.perf_counter()
        status = 'failure'
        processed: _ProcessedImage | None = None
        try:
            if not ownership.session_id:
                raise MediaValidationError('Media ownership requires a session')
            async with _shared_lock(_SESSION_LOCKS, ownership.session_id):
                processed = await run_media_cpu(_process_image, data)
                if len(processed.original) > MAX_IMAGE_BYTES:
                    raise MediaValidationError('Sanitized image exceeds the 10 MiB limit')
                artifact = await _run_transaction_joined(
                    self._commit_processed(
                        processed,
                        origin=origin,
                        ownership=ownership,
                        filename=filename,
                        metadata=metadata,
                    )
                )
            status = 'success'
            return ref(artifact)
        finally:
            _log_ingest_metric(
                status=status,
                origin=origin,
                input_bytes=len(data),
                processed=processed,
                started=started,
            )

    async def _commit_processed(
        self,
        processed: _ProcessedImage,
        *,
        origin: str,
        ownership: MediaOwnership,
        filename: str | None,
        metadata: Mapping[str, Any],
    ) -> Artifact:
        storage_token = str(uuid.uuid4())
        namespace = artifact_store._storage_namespace(ownership.session_id or 'local')
        original_key = f'{namespace}/{storage_token}-original{processed.extension}'
        vision_key = f'{namespace}/{storage_token}-vision{processed.extension}'
        thumbnail_key = f'{namespace}/{storage_token}-thumbnail.webp'
        root = artifact_store._root()
        finals = {
            'original': root / original_key,
            'vision': root / vision_key,
            'thumbnail': root / thumbnail_key,
        }
        temps = {
            name: path.with_name(f'{path.name}.{uuid.uuid4().hex}.tmp')
            for name, path in finals.items()
        }
        touched: list[Path] = []

        async def write_temp(name: str, data: bytes) -> None:
            path = temps[name]

            def write() -> None:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)

            await _run_thread_joined(write)
            touched.append(path)

        async def promote(name: str) -> None:
            await _run_thread_joined(os.replace, temps[name], finals[name])
            touched.append(finals[name])

        try:
            await write_temp('original', processed.original)
            await write_temp('vision', processed.vision)

            thumbnail_retained = processed.thumbnail is not None
            if processed.thumbnail is not None:
                try:
                    await write_temp('thumbnail', processed.thumbnail)
                except Exception:
                    thumbnail_retained = False
                    await _remove_paths([temps['thumbnail']])

            retained_artifacts = await Artifact.find(
                {'session_id': ownership.session_id}
            ) or []
            if len(retained_artifacts) >= MAX_SESSION_ARTIFACTS:
                raise MediaQuotaError('This session has reached its retained image limit')
            retained_bytes = await asyncio.to_thread(
                _retained_physical_bytes,
                retained_artifacts,
            )
            new_bytes = len(processed.original) + len(processed.vision)
            if thumbnail_retained and processed.thumbnail is not None:
                new_bytes += len(processed.thumbnail)
            if retained_bytes + new_bytes > MAX_SESSION_BYTES:
                raise MediaQuotaError(
                    'This session has reached its retained image storage limit'
                )

            await promote('original')
            await promote('vision')
            if thumbnail_retained:
                try:
                    await promote('thumbnail')
                except Exception:
                    thumbnail_retained = False
                    await _remove_paths([temps['thumbnail'], finals['thumbnail']])

            artifact = Artifact(
                session_id=ownership.session_id,
                user_id=ownership.user_id,
                agent_id=ownership.agent_id,
                storage_key=original_key,
                vision_storage_key=vision_key,
                thumbnail_storage_key=thumbnail_key if thumbnail_retained else None,
                origin=origin,
                mime_type=processed.mime_type,
                filename=_safe_filename(filename, storage_token, processed.extension),
                width=processed.width,
                height=processed.height,
                size_bytes=len(processed.original),
                prompt=str(metadata.get('prompt') or '')[:1000],
                source_artifact_id=metadata.get('source_artifact_id'),
                model=str(metadata.get('model') or ''),
            )
            await _settle_operation(artifact.save())
            return artifact
        except BaseException:
            await _remove_paths([*temps.values(), *finals.values(), *touched])
            raise
        finally:
            await _remove_paths(list(temps.values()))

    @staticmethod
    def _check_ownership(artifact: Artifact, ownership: MediaOwnership) -> None:
        if artifact.session_id != ownership.session_id:
            raise MediaAccessError('Artifact belongs to a different session')
        if artifact.user_id != ownership.user_id:
            raise MediaAccessError('Artifact belongs to a different user')
        if artifact.agent_id != ownership.agent_id:
            raise MediaAccessError('Artifact belongs to a different agent')

    async def _authorized_artifact(
        self,
        artifact_id: str,
        ownership: MediaOwnership,
    ) -> Artifact:
        artifact = await Artifact.get(artifact_id)
        if artifact is None:
            raise MediaNotFoundError('Artifact was not found')
        self._check_ownership(artifact, ownership)
        return artifact

    async def _ensure_thumbnail(
        self,
        artifact: Artifact,
        ownership: MediaOwnership,
    ) -> Artifact:
        artifact_id = str(artifact.id)
        thumbnail_lock = _shared_lock(_ARTIFACT_LOCKS, artifact_id)
        async with thumbnail_lock:
            artifact = await self._authorized_artifact(artifact_id, ownership)
            if artifact.thumbnail_storage_key:
                try:
                    existing = variant_path(artifact, 'thumbnail')
                except ValueError:
                    existing = None
                if existing is not None and await run_media_cpu(
                    _read_and_validate_thumbnail,
                    existing,
                ):
                    return artifact

            try:
                original_path = variant_path(artifact, 'original')
            except ValueError as exc:
                raise MediaNotFoundError('Artifact original is unavailable') from exc
            if not await asyncio.to_thread(original_path.is_file):
                raise MediaNotFoundError('Artifact original is unavailable')
            original = await _read_bounded_image_path(original_path)
            thumbnail = await run_media_cpu(_make_thumbnail, original)

            session_lock = _shared_lock(_SESSION_LOCKS, artifact.session_id)
            async with session_lock:
                artifact = await self._authorized_artifact(artifact_id, ownership)
                if artifact.thumbnail_storage_key:
                    try:
                        existing = variant_path(artifact, 'thumbnail')
                    except ValueError:
                        existing = None
                    if existing is not None and await run_media_cpu(
                        _read_and_validate_thumbnail,
                        existing,
                    ):
                        return artifact

                previous_key = artifact.thumbnail_storage_key
                try:
                    previous_path = (
                        variant_path(artifact, 'thumbnail') if previous_key else None
                    )
                except ValueError:
                    previous_path = None
                replacement_bytes = 0
                if previous_path is not None:
                    try:
                        replacement_bytes = await asyncio.to_thread(
                            lambda: (
                                previous_path.stat().st_size
                                if previous_path.is_file()
                                else 0
                            )
                        )
                    except OSError:
                        replacement_bytes = 0

                retained_artifacts = await Artifact.find(
                    {'session_id': artifact.session_id}
                ) or []
                retained_bytes = await asyncio.to_thread(
                    _retained_physical_bytes,
                    retained_artifacts,
                )
                retained_after_replacement = max(
                    0,
                    retained_bytes - replacement_bytes,
                )
                if retained_after_replacement + len(thumbnail) > MAX_SESSION_BYTES:
                    raise MediaQuotaError(
                        'This session has reached its retained image storage limit'
                    )

                namespace = artifact_store._storage_namespace(artifact.session_id)
                thumbnail_key = f'{namespace}/{uuid.uuid4().hex}-thumbnail.webp'
                final = artifact_store._root() / thumbnail_key
                temp = final.with_name(f'{final.name}.{uuid.uuid4().hex}.tmp')

                def write_and_promote() -> None:
                    temp.parent.mkdir(parents=True, exist_ok=True)
                    temp.write_bytes(thumbnail)
                    os.replace(temp, final)

                async def persist_thumbnail() -> Artifact:
                    metadata_saved = False
                    try:
                        await _run_thread_joined(write_and_promote)
                        artifact.thumbnail_storage_key = thumbnail_key
                        await _settle_operation(artifact.save())
                        metadata_saved = True
                        if previous_path is not None and previous_path != final:
                            await _remove_paths([previous_path])
                    except BaseException:
                        if not metadata_saved:
                            artifact.thumbnail_storage_key = previous_key
                            await _remove_paths([temp, final])
                        raise
                    finally:
                        await _remove_paths([temp])
                    return artifact

                return await _run_transaction_joined(persist_thumbnail())

    async def resolve_ref(
        self,
        artifact_id: str,
        ownership: MediaOwnership,
    ) -> ArtifactRef:
        return ref(await self._authorized_artifact(artifact_id, ownership))

    async def resolve_image(
        self,
        artifact_id: str,
        ownership: MediaOwnership,
        variant: ImageVariant = 'original',
    ) -> ResolvedImage:
        resolved = await self.resolve_variant_file(artifact_id, ownership, variant)
        return ResolvedImage(
            ref=resolved.ref,
            variant=variant,
            mime_type=resolved.mime_type,
            data=await _read_bounded_image_path(resolved.path),
        )

    async def resolve_variant_file(
        self,
        artifact_id: str,
        ownership: MediaOwnership,
        variant: ImageVariant = 'original',
    ) -> ResolvedMediaFile:
        if variant not in _VALID_VARIANTS:
            raise MediaValidationError('Invalid media variant')
        artifact = await self._authorized_artifact(artifact_id, ownership)
        if variant == 'thumbnail':
            artifact = await self._ensure_thumbnail(artifact, ownership)
        try:
            path = variant_path(artifact, variant)
        except ValueError as exc:
            raise MediaNotFoundError('Artifact variant is unavailable') from exc
        if not await asyncio.to_thread(path.is_file):
            raise MediaNotFoundError('Artifact variant is unavailable')
        extension = '.webp' if variant == 'thumbnail' else path.suffix
        filename = _safe_filename(artifact.filename, str(artifact.id), extension)
        return ResolvedMediaFile(
            ref=ref(artifact),
            variant=variant,
            mime_type=_variant_mime(artifact, variant),
            filename=filename,
            path=path,
        )

    async def list_recent_refs(
        self,
        session_id: str,
        ownership: MediaOwnership,
        limit: int = 3,
    ) -> list[ArtifactRef]:
        if session_id != ownership.session_id:
            raise MediaAccessError('Session belongs to a different owner')
        try:
            bounded_limit = max(0, min(MAX_SESSION_ARTIFACTS, int(limit)))
        except (TypeError, ValueError) as exc:
            raise MediaValidationError('Invalid recent media limit') from exc
        if bounded_limit == 0:
            return []
        artifacts = await Artifact.find({'session_id': session_id}) or []
        authorized = []
        for artifact in artifacts:
            try:
                self._check_ownership(artifact, ownership)
            except MediaAccessError:
                continue
            authorized.append(artifact)
        authorized.sort(
            key=lambda item: (getattr(item, 'created_at', '') or '', str(item.id)),
            reverse=True,
        )
        return [ref(artifact) for artifact in authorized[:bounded_limit]]

    async def delete_artifacts(
        self,
        artifact_ids: Sequence[str],
        ownership: MediaOwnership,
    ) -> None:
        unique_ids = list(dict.fromkeys(str(value) for value in artifact_ids))
        session_key = ownership.session_id or 'local'

        async def transaction() -> None:
            async with _shared_lock(_SESSION_LOCKS, session_key):
                artifacts = []
                for artifact_id in unique_ids:
                    artifact = await Artifact.get(artifact_id)
                    if artifact is None:
                        continue
                    self._check_ownership(artifact, ownership)
                    artifacts.append(artifact)
                artifact_paths = [
                    (artifact, _artifact_variant_paths(artifact))
                    for artifact in artifacts
                ]
                for artifact, paths in artifact_paths:

                    async def delete_row() -> None:
                        deleted = await _settle_operation(
                            Artifact.delete_many({'id': str(artifact.id)})
                        )
                        if deleted == 0:
                            raise RuntimeError('Artifact metadata deletion did not complete')

                    await _tombstone_delete(paths, delete_row)

        await _run_transaction_joined(transaction())

    async def delete_session_media(
        self,
        session_id: str,
        ownership: MediaOwnership | None = None,
    ) -> None:
        if ownership is not None and session_id != ownership.session_id:
            raise MediaAccessError('Session belongs to a different owner')

        async def transaction() -> None:
            async with _shared_lock(_SESSION_LOCKS, session_id):
                artifacts = await Artifact.find({'session_id': session_id}) or []
                if ownership is not None:
                    for artifact in artifacts:
                        self._check_ownership(artifact, ownership)
                paths = [
                    path
                    for artifact in artifacts
                    for path in _artifact_variant_paths(artifact)
                ]

                async def delete_rows() -> None:
                    deleted = await _settle_operation(
                        Artifact.delete_many({'session_id': session_id})
                    )
                    if artifacts and deleted == 0:
                        raise RuntimeError('Session media metadata deletion did not complete')

                await _tombstone_delete(paths, delete_rows)
                directory = (
                    artifact_store._root()
                    / artifact_store._storage_namespace(session_id)
                )
                try:
                    await _run_thread_joined(directory.rmdir)
                except OSError:
                    pass

        await _run_transaction_joined(transaction())


media_assets = MediaAssetService()
