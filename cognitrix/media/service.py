"""Provider-neutral image artifact storage and resolution."""

from __future__ import annotations

import asyncio
import logging
import os
import re
import time
import uuid
import weakref
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from cognitrix import artifacts as artifact_store
from cognitrix.artifacts import Artifact, ref, variant_path
from cognitrix.media.processing import (
    _ProcessedImage,
    _make_thumbnail,
    _process_image,
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


def _shared_lock(cache: weakref.WeakValueDictionary, key: str) -> asyncio.Lock:
    lock = cache.get(key)
    if lock is None:
        lock = asyncio.Lock()
        cache[key] = lock
    return lock


def _artifact_variant_paths(artifact: Artifact) -> list[Path]:
    paths = []
    for variant in _VALID_VARIANTS:
        try:
            paths.append(variant_path(artifact, variant))
        except ValueError:
            continue
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
            await asyncio.to_thread(path.unlink, missing_ok=True)
        except OSError:
            pass


async def _delete_paths(paths: Sequence[Path]) -> None:
    for path in dict.fromkeys(paths):
        await asyncio.to_thread(path.unlink, missing_ok=True)


class MediaAssetService:
    async def ingest_staged_image(
        self,
        staged: StagedAttachment,
        ownership: MediaOwnership,
    ) -> ArtifactRef:
        started = time.perf_counter()
        try:
            data = await _read_path(staged.path)
        except BaseException:
            _log_ingest_metric(
                status='failure',
                origin='uploaded',
                input_bytes=0,
                processed=None,
                started=started,
            )
            raise
        return await self._store_image(
            data,
            origin='uploaded',
            ownership=ownership,
            filename=staged.filename,
            metadata={},
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
            processed = await run_media_cpu(_process_image, data)
            if len(processed.original) > MAX_IMAGE_BYTES:
                raise MediaValidationError('Sanitized image exceeds the 10 MiB limit')
            artifact = await self._commit_processed(
                processed,
                origin=origin,
                ownership=ownership,
                filename=filename,
                metadata=metadata,
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
        artifact_id = str(uuid.uuid4())
        namespace = artifact_store._storage_namespace(ownership.session_id or 'local')
        original_key = f'{namespace}/{artifact_id}-original{processed.extension}'
        vision_key = f'{namespace}/{artifact_id}-vision{processed.extension}'
        thumbnail_key = f'{namespace}/{artifact_id}-thumbnail.webp'
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

            await asyncio.to_thread(write)
            touched.append(path)

        async def promote(name: str) -> None:
            await asyncio.to_thread(os.replace, temps[name], finals[name])
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

            session_lock = _shared_lock(_SESSION_LOCKS, ownership.session_id or 'local')
            async with session_lock:
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
                    _id=artifact_id,
                    session_id=ownership.session_id,
                    user_id=ownership.user_id,
                    agent_id=ownership.agent_id,
                    storage_key=original_key,
                    vision_storage_key=vision_key,
                    thumbnail_storage_key=thumbnail_key if thumbnail_retained else None,
                    origin=origin,
                    mime_type=processed.mime_type,
                    filename=_safe_filename(filename, artifact_id, processed.extension),
                    width=processed.width,
                    height=processed.height,
                    size_bytes=len(processed.original),
                    prompt=str(metadata.get('prompt') or '')[:1000],
                    source_artifact_id=metadata.get('source_artifact_id'),
                    model=str(metadata.get('model') or ''),
                )
                await artifact.save()
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
                if existing is not None and await asyncio.to_thread(existing.is_file):
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
                    if existing is not None and await asyncio.to_thread(existing.is_file):
                        return artifact

                retained_artifacts = await Artifact.find(
                    {'session_id': artifact.session_id}
                ) or []
                retained_bytes = await asyncio.to_thread(
                    _retained_physical_bytes,
                    retained_artifacts,
                )
                if retained_bytes + len(thumbnail) > MAX_SESSION_BYTES:
                    raise MediaQuotaError(
                        'This session has reached its retained image storage limit'
                    )

                namespace = artifact_store._storage_namespace(artifact.session_id)
                thumbnail_key = f'{namespace}/{artifact_id}-thumbnail.webp'
                final = artifact_store._root() / thumbnail_key
                temp = final.with_name(f'{final.name}.{uuid.uuid4().hex}.tmp')
                previous_key = artifact.thumbnail_storage_key

                def write_and_promote() -> None:
                    temp.parent.mkdir(parents=True, exist_ok=True)
                    temp.write_bytes(thumbnail)
                    os.replace(temp, final)

                try:
                    await asyncio.to_thread(write_and_promote)
                    artifact.thumbnail_storage_key = thumbnail_key
                    await artifact.save()
                except BaseException:
                    artifact.thumbnail_storage_key = previous_key
                    await _remove_paths([temp, final])
                    raise
                finally:
                    await _remove_paths([temp])
                return artifact

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
        async with _shared_lock(_SESSION_LOCKS, session_key):
            artifacts = []
            for artifact_id in unique_ids:
                artifact = await Artifact.get(artifact_id)
                if artifact is None:
                    continue
                self._check_ownership(artifact, ownership)
                artifacts.append(artifact)
            paths = [
                path
                for artifact in artifacts
                for path in _artifact_variant_paths(artifact)
            ]
            await _delete_paths(paths)
            for artifact in artifacts:
                await Artifact.delete_many({'id': str(artifact.id)})

    async def delete_session_media(
        self,
        session_id: str,
        ownership: MediaOwnership | None = None,
    ) -> None:
        if ownership is not None and session_id != ownership.session_id:
            raise MediaAccessError('Session belongs to a different owner')
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
            await _delete_paths(paths)
            await Artifact.delete_many({'session_id': session_id})
            directory = artifact_store._root() / artifact_store._storage_namespace(session_id)
            try:
                await asyncio.to_thread(directory.rmdir)
            except OSError:
                pass


media_assets = MediaAssetService()
