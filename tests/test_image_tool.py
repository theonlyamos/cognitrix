from dataclasses import dataclass, field

import pytest

from cognitrix.artifacts import reset_session, set_session
from cognitrix.media.types import (
    MediaNotFoundError,
    MediaOwnership,
    ResolvedImage,
)
from cognitrix.providers.gemini_image import (
    GeminiImageError,
    ProviderImage,
)
from cognitrix.tools.utils import (
    ArtifactRef,
    ToolExecutionContext,
    reset_execution_context,
    set_execution_context,
)


@pytest.fixture(autouse=True)
def media_context():
    artifact_token = set_session('session-1', 'agent-1', 'user-1')
    execution_token = set_execution_context(
        ToolExecutionContext(
            user_id='user-1',
            allowed_agents=frozenset({'authorized-but-not-current'}),
        )
    )
    try:
        yield
    finally:
        reset_execution_context(execution_token)
        reset_session(artifact_token)


@dataclass
class FakeProvider:
    outputs: list[ProviderImage]
    calls: list[dict] = field(default_factory=list)

    async def generate(self, prompt, *, source, aspect_ratio):
        self.calls.append({
            'prompt': prompt,
            'source': source,
            'aspect_ratio': aspect_ratio,
        })
        return self.outputs.pop(0)


@dataclass
class FakeMediaAssets:
    resolved: dict[str, ResolvedImage] = field(default_factory=dict)
    resolve_calls: list[tuple] = field(default_factory=list)
    store_calls: list[dict] = field(default_factory=list)

    async def resolve_image(self, artifact_id, ownership, variant='original'):
        self.resolve_calls.append((artifact_id, ownership, variant))
        return self.resolved[artifact_id]

    async def store_generated_image(self, data, metadata, ownership):
        artifact_id = f'generated-{len(self.store_calls) + 1}'
        artifact_ref = ArtifactRef(
            id=artifact_id,
            mime_type='image/png',
            filename=f'{artifact_id}.png',
            width=2,
            height=3,
            origin='generated',
        )
        self.store_calls.append({
            'data': data,
            'metadata': metadata,
            'ownership': ownership,
            'ref': artifact_ref,
        })
        self.resolved[artifact_id] = ResolvedImage(
            ref=artifact_ref,
            variant='original',
            mime_type='image/png',
            data=data,
        )
        return artifact_ref


def _resolved_source(origin: str) -> ResolvedImage:
    return ResolvedImage(
        ref=ArtifactRef(
            id='source-1',
            mime_type='image/png',
            filename='source.png',
            origin=origin,
        ),
        variant='original',
        mime_type='image/png',
        data=b'exact sanitized master bytes',
    )


@pytest.mark.asyncio
@pytest.mark.parametrize('origin', ['uploaded', 'generated'])
async def test_generate_image_resolves_uploaded_and_generated_sources_identically(
    monkeypatch,
    origin,
):
    from cognitrix.tools import image as image_module

    source = _resolved_source(origin)
    service = FakeMediaAssets(resolved={'source-1': source})
    provider = FakeProvider([ProviderImage(b'provider bytes', 'image/webp')])
    monkeypatch.setattr(image_module, 'media_assets', service)
    monkeypatch.setattr(image_module, 'image_provider', provider)

    result = await image_module.generate_image.run(
        '  Make it blue  ',
        source_artifact_id='source-1',
        aspect_ratio='16:9',
    )

    ownership = MediaOwnership('session-1', 'user-1', 'agent-1')
    assert service.resolve_calls == [('source-1', ownership, 'original')]
    assert provider.calls == [{
        'prompt': 'Make it blue',
        'source': source,
        'aspect_ratio': '16:9',
    }]
    assert provider.calls[0]['source'].data == b'exact sanitized master bytes'
    assert service.store_calls == [{
        'data': b'provider bytes',
        'metadata': {
            'prompt': 'Make it blue',
            'source_artifact_id': 'source-1',
            'model': 'gemini-3.1-flash-image',
        },
        'ownership': ownership,
        'ref': service.store_calls[0]['ref'],
    }]
    assert result.outcome.status == 'success'
    assert result.outcome.text == 'Image generated.'
    assert result.outcome.artifacts == [service.store_calls[0]['ref']]
    assert result.outcome.artifacts[0].origin == 'generated'


@pytest.mark.asyncio
async def test_generate_image_stores_provider_bytes_without_resolving_a_source(
    monkeypatch,
):
    from cognitrix.tools import image as image_module

    service = FakeMediaAssets()
    provider = FakeProvider([ProviderImage(b'brand new image')])
    monkeypatch.setattr(image_module, 'media_assets', service)
    monkeypatch.setattr(image_module, 'image_provider', provider)

    result = await image_module.generate_image.run('A new image')

    assert service.resolve_calls == []
    assert provider.calls[0]['source'] is None
    assert provider.calls[0]['aspect_ratio'] == '1:1'
    assert service.store_calls[0]['data'] == b'brand new image'
    assert service.store_calls[0]['metadata']['source_artifact_id'] is None
    assert result.outcome.artifacts[0].id == 'generated-1'


@pytest.mark.asyncio
async def test_three_call_edit_chain_uses_each_child_as_the_next_parent(monkeypatch):
    from cognitrix.tools import image as image_module

    service = FakeMediaAssets()
    provider = FakeProvider([
        ProviderImage(b'generation one'),
        ProviderImage(b'generation two'),
        ProviderImage(b'generation three'),
    ])
    monkeypatch.setattr(image_module, 'media_assets', service)
    monkeypatch.setattr(image_module, 'image_provider', provider)

    first = await image_module.generate_image.run('Create the base')
    second = await image_module.generate_image.run(
        'First edit', source_artifact_id=first.outcome.artifacts[0].id
    )
    third = await image_module.generate_image.run(
        'Second edit', source_artifact_id=second.outcome.artifacts[0].id
    )

    assert [call['source'].ref.id if call['source'] else None for call in provider.calls] == [
        None,
        'generated-1',
        'generated-2',
    ]
    assert [call['metadata']['source_artifact_id'] for call in service.store_calls] == [
        None,
        'generated-1',
        'generated-2',
    ]
    assert third.outcome.artifacts[0].id == 'generated-3'


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('prompt', 'aspect_ratio', 'code'),
    [
        ('   ', None, 'invalid_prompt'),
        ('prompt', 'square', 'invalid_aspect_ratio'),
    ],
)
async def test_invalid_input_fails_before_media_resolution_or_provider(
    monkeypatch,
    prompt,
    aspect_ratio,
    code,
):
    from cognitrix.tools import image as image_module

    service = FakeMediaAssets(resolved={'source-1': _resolved_source('uploaded')})
    provider = FakeProvider([ProviderImage(b'unused')])
    monkeypatch.setattr(image_module, 'media_assets', service)
    monkeypatch.setattr(image_module, 'image_provider', provider)

    result = await image_module.generate_image.run(
        prompt,
        source_artifact_id='source-1',
        aspect_ratio=aspect_ratio,
    )

    assert result.outcome.error.code == code
    assert service.resolve_calls == []
    assert provider.calls == []
    assert service.store_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize('aspect_ratio', ['1:4', '1:8', '4:1', '8:1'])
async def test_generate_image_accepts_extended_gemini_aspect_ratios(
    monkeypatch,
    aspect_ratio,
):
    from cognitrix.tools import image as image_module

    service = FakeMediaAssets()
    provider = FakeProvider([ProviderImage(b'image')])
    monkeypatch.setattr(image_module, 'media_assets', service)
    monkeypatch.setattr(image_module, 'image_provider', provider)

    result = await image_module.generate_image.run('prompt', aspect_ratio=aspect_ratio)

    assert result.outcome.status == 'success'
    assert provider.calls[0]['aspect_ratio'] == aspect_ratio


@pytest.mark.asyncio
async def test_generate_image_maps_typed_provider_error_to_public_outcome(monkeypatch):
    from cognitrix.tools import image as image_module

    class FailingProvider:
        async def generate(self, *args, **kwargs):
            raise GeminiImageError(
                'image_provider_error',
                'Image provider request failed: safe detail',
            )

    monkeypatch.setattr(image_module, 'media_assets', FakeMediaAssets())
    monkeypatch.setattr(image_module, 'image_provider', FailingProvider())

    result = await image_module.generate_image.run('prompt')

    assert result.outcome.status == 'error'
    assert result.outcome.error.code == 'image_provider_error'
    assert result.outcome.error.message == 'Image provider request failed: safe detail'


@pytest.mark.asyncio
async def test_generate_image_maps_typed_media_error_without_calling_provider(
    monkeypatch,
):
    from cognitrix.tools import image as image_module

    class MissingMedia(FakeMediaAssets):
        async def resolve_image(self, *args, **kwargs):
            raise MediaNotFoundError('Artifact was not found')

    provider = FakeProvider([ProviderImage(b'unused')])
    monkeypatch.setattr(image_module, 'media_assets', MissingMedia())
    monkeypatch.setattr(image_module, 'image_provider', provider)

    result = await image_module.generate_image.run(
        'edit', source_artifact_id='missing'
    )

    assert result.outcome.status == 'error'
    assert result.outcome.error.code == 'image_generation_error'
    assert result.outcome.error.message == 'Artifact was not found'
    assert provider.calls == []
