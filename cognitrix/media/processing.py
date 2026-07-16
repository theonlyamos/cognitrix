"""Bounded worker execution and private Pillow transforms for media assets."""

from __future__ import annotations

import asyncio
import io
import logging
import os
import weakref
from collections.abc import Callable
from dataclasses import dataclass
from typing import TypeVar

from PIL import Image, ImageOps, UnidentifiedImageError

from cognitrix.media.types import MediaValidationError

__all__ = ['run_media_cpu']

logger = logging.getLogger('cognitrix.log')

T = TypeVar('T')

_DEFAULT_MEDIA_PROCESSING_CONCURRENCY = 2
_MIN_MEDIA_PROCESSING_CONCURRENCY = 1
_MAX_MEDIA_PROCESSING_CONCURRENCY = 8
MAX_IMAGE_PIXELS = 20_000_000
VISION_MAX_EDGE = 1568
THUMBNAIL_MAX_EDGE = 384
_ACCEPTED_FORMATS = {'JPEG', 'PNG', 'WEBP', 'GIF', 'BMP', 'TIFF'}


def _parse_media_processing_concurrency(raw: str | None) -> int:
    try:
        value = int(raw) if raw else _DEFAULT_MEDIA_PROCESSING_CONCURRENCY
    except (TypeError, ValueError):
        logger.warning(
            'Invalid COGNITRIX_MEDIA_PROCESSING_CONCURRENCY=%r; using %s',
            raw,
            _DEFAULT_MEDIA_PROCESSING_CONCURRENCY,
        )
        return _DEFAULT_MEDIA_PROCESSING_CONCURRENCY
    return max(
        _MIN_MEDIA_PROCESSING_CONCURRENCY,
        min(_MAX_MEDIA_PROCESSING_CONCURRENCY, value),
    )


MEDIA_PROCESSING_CONCURRENCY = _parse_media_processing_concurrency(
    os.getenv('COGNITRIX_MEDIA_PROCESSING_CONCURRENCY')
)
_MEDIA_PROCESSING_LIMITERS: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()


def _media_processing_limiter() -> asyncio.Semaphore:
    loop = asyncio.get_running_loop()
    configured = MEDIA_PROCESSING_CONCURRENCY
    entry = _MEDIA_PROCESSING_LIMITERS.get(loop)
    if entry is None or entry[0] != configured:
        entry = (configured, asyncio.Semaphore(configured))
        _MEDIA_PROCESSING_LIMITERS[loop] = entry
    return entry[1]


async def run_media_cpu(func: Callable[..., T], *args, **kwargs) -> T:
    """Run CPU-heavy media work off-loop under the process concurrency cap."""
    async with _media_processing_limiter():
        return await _run_thread_joined(func, *args, **kwargs)


async def _run_thread_joined(func: Callable[..., T], *args, **kwargs) -> T:
    """Do not let task cancellation outlive its underlying worker thread."""
    worker = asyncio.create_task(asyncio.to_thread(func, *args, **kwargs))
    try:
        return await asyncio.shield(worker)
    except asyncio.CancelledError:
        while not worker.done():
            try:
                await asyncio.shield(worker)
            except asyncio.CancelledError:
                continue
            except BaseException:
                break
        if worker.done() and not worker.cancelled():
            try:
                worker.result()
            except BaseException:
                pass
        raise


@dataclass(frozen=True)
class _ProcessedImage:
    original: bytes
    vision: bytes
    thumbnail: bytes | None
    mime_type: str
    extension: str
    width: int
    height: int


def _validate_dimensions(size: tuple[int, int]) -> None:
    width, height = size
    if width <= 0 or height <= 0 or width * height > MAX_IMAGE_PIXELS:
        raise MediaValidationError('Image exceeds the 20,000,000 pixel limit')


def _has_alpha(image: Image.Image) -> bool:
    return image.mode in {'RGBA', 'LA', 'PA'} or (
        image.mode == 'P' and image.info.get('transparency') is not None
    )


def _encode(image: Image.Image, image_format: str, *, vision: bool = False) -> bytes:
    output = io.BytesIO()
    if image_format == 'JPEG':
        image.save(
            output,
            format='JPEG',
            quality=90 if vision else 95,
            optimize=True,
        )
    else:
        image.save(output, format='PNG', optimize=True)
    return output.getvalue()


def _encode_thumbnail(image: Image.Image) -> bytes:
    thumbnail = image.copy()
    try:
        thumbnail.thumbnail(
            (THUMBNAIL_MAX_EDGE, THUMBNAIL_MAX_EDGE),
            Image.Resampling.LANCZOS,
        )
        output = io.BytesIO()
        thumbnail.save(output, format='WEBP', quality=82, method=4)
        return output.getvalue()
    finally:
        thumbnail.close()


def _process_image(data: bytes) -> _ProcessedImage:
    try:
        with Image.open(io.BytesIO(data)) as source:
            source_format = (source.format or '').upper()
            if source_format not in _ACCEPTED_FORMATS:
                raise MediaValidationError('Unsupported image format')
            _validate_dimensions(source.size)
            source.seek(0)
            oriented = ImageOps.exif_transpose(source)
            try:
                _validate_dimensions(oriented.size)
                oriented.load()
                alpha = _has_alpha(oriented)
                pixels = oriented.convert('RGBA' if alpha else 'RGB')
            finally:
                if oriented is not source:
                    oriented.close()
    except MediaValidationError:
        raise
    except (Image.DecompressionBombError, UnidentifiedImageError, OSError, ValueError) as exc:
        raise MediaValidationError('Attachment is not a valid image') from exc

    try:
        master_format = 'JPEG' if source_format == 'JPEG' and not alpha else 'PNG'
        mime_type = 'image/jpeg' if master_format == 'JPEG' else 'image/png'
        extension = '.jpg' if master_format == 'JPEG' else '.png'
        original = _encode(pixels, master_format)

        vision_image = pixels.copy()
        try:
            vision_image.thumbnail(
                (VISION_MAX_EDGE, VISION_MAX_EDGE),
                Image.Resampling.LANCZOS,
            )
            vision = _encode(vision_image, master_format, vision=True)
        finally:
            vision_image.close()

        try:
            thumbnail = _encode_thumbnail(pixels)
        except Exception:
            thumbnail = None

        return _ProcessedImage(
            original=original,
            vision=vision,
            thumbnail=thumbnail,
            mime_type=mime_type,
            extension=extension,
            width=pixels.width,
            height=pixels.height,
        )
    except MediaValidationError:
        raise
    except (OSError, ValueError) as exc:
        raise MediaValidationError('Image could not be normalized') from exc
    finally:
        pixels.close()


def _make_thumbnail(data: bytes) -> bytes:
    try:
        with Image.open(io.BytesIO(data)) as source:
            source_format = (source.format or '').upper()
            if source_format not in _ACCEPTED_FORMATS:
                raise MediaValidationError('Unsupported image format')
            _validate_dimensions(source.size)
            source.seek(0)
            oriented = ImageOps.exif_transpose(source)
            try:
                _validate_dimensions(oriented.size)
                oriented.load()
                pixels = oriented.convert('RGBA' if _has_alpha(oriented) else 'RGB')
            finally:
                if oriented is not source:
                    oriented.close()
        try:
            return _encode_thumbnail(pixels)
        finally:
            pixels.close()
    except MediaValidationError:
        raise
    except (Image.DecompressionBombError, UnidentifiedImageError, OSError, ValueError) as exc:
        raise MediaValidationError('Artifact is not a valid image') from exc


def _is_valid_thumbnail(data: bytes) -> bool:
    """Verify that retained thumbnail bytes satisfy the derivative contract."""
    try:
        with Image.open(io.BytesIO(data)) as image:
            if (image.format or '').upper() != 'WEBP':
                return False
            width, height = image.size
            if (
                width <= 0
                or height <= 0
                or max(width, height) > THUMBNAIL_MAX_EDGE
                or getattr(image, 'n_frames', 1) != 1
            ):
                return False
            image.load()
            if image.getexif():
                return False
            metadata_keys = {'exif', 'icc_profile', 'xmp'}
            if metadata_keys.intersection(image.info):
                return False
            return True
    except (Image.DecompressionBombError, UnidentifiedImageError, OSError, ValueError):
        return False
