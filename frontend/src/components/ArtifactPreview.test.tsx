import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { StrictMode } from 'react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ArtifactPreview } from './ArtifactPreview';
import { api } from '@/lib/api';
import { installIntersectionObserver, intersect, observerOptions, resetIntersectionObservers, uninstallIntersectionObserver } from '@/test/intersection-observer';

vi.mock('@/lib/api', () => ({ api: { get: vi.fn() } }));

const artifact = { id: 'image-1', mime_type: 'image/png', filename: 'image.png', width: 1200, height: 800, origin: 'generated' as const };

describe('ArtifactPreview lazy image delivery', () => {
  beforeEach(() => {
    installIntersectionObserver();
    resetIntersectionObservers();
    vi.mocked(api.get).mockReset();
    Object.defineProperty(URL, 'createObjectURL', { configurable: true, value: vi.fn().mockReturnValue('blob:image') });
    Object.defineProperty(URL, 'revokeObjectURL', { configurable: true, value: vi.fn() });
  });

  afterEach(() => uninstallIntersectionObserver());

  it('does not request a thumbnail until its placeholder enters the preload margin', async () => {
    render(<ArtifactPreview artifact={artifact} />);

    expect(api.get).not.toHaveBeenCalled();
    expect(screen.getByTestId('artifact-placeholder')).toHaveStyle({
      aspectRatio: '1200 / 800',
      width: 'min(100%, 480px)',
    });
    expect(observerOptions()).toContainEqual(expect.objectContaining({ rootMargin: '400px 0px' }));

    vi.mocked(api.get).mockResolvedValue({ data: new Blob(['thumbnail']) });
    act(() => intersect(screen.getByTestId('artifact-placeholder')));
    await waitFor(() => expect(api.get).toHaveBeenCalledWith('/artifacts/image-1?variant=thumbnail', expect.objectContaining({ responseType: 'blob', signal: expect.any(AbortSignal) })));
  });

  it('sizes a ready landscape preview from the loaded thumbnail instead of its reservation', async () => {
    vi.mocked(api.get).mockResolvedValue({ data: new Blob(['thumbnail']) });
    render(<ArtifactPreview artifact={artifact} onEditSource={vi.fn()} />);
    const placeholder = screen.getByTestId('artifact-placeholder');

    expect(placeholder).toHaveStyle({
      aspectRatio: '1200 / 800',
      width: 'min(100%, 480px)',
    });

    act(() => intersect(placeholder));
    const image = await screen.findByRole('img', { name: 'Generated image' });

    expect(placeholder.style.aspectRatio).toBe('');
    expect(placeholder.style.width).toBe('');
    expect(image).toHaveClass('block');
    expect(screen.getByRole('button', { name: 'Use image.png as edit source' }).closest('.absolute')).toHaveClass(
      'bottom-2',
      'right-2',
    );
  });

  it('retries the thumbnail after a failed viewport request', async () => {
    vi.mocked(api.get).mockRejectedValueOnce(new Error('offline')).mockResolvedValueOnce({ data: new Blob(['thumbnail']) });
    render(<ArtifactPreview artifact={artifact} />);
    act(() => intersect());
    await userEvent.click(await screen.findByRole('button', { name: 'Retry image' }));
    await waitFor(() => expect(api.get).toHaveBeenCalledTimes(2));
  });

  it('aborts pending thumbnail work and revokes owned URLs on unmount', async () => {
    let reject!: (error: Error) => void;
    vi.mocked(api.get).mockImplementationOnce((_path, config) => new Promise((_, failure) => {
      reject = failure as (error: Error) => void;
      (config?.signal as AbortSignal).addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')));
    }) as never);
    const { unmount } = render(<ArtifactPreview artifact={artifact} />);
    act(() => intersect());
    const signal = vi.mocked(api.get).mock.calls[0][1]?.signal as AbortSignal;
    unmount();
    expect(signal.aborted).toBe(true);

  });

  it('revokes both thumbnail and original object URLs on unmount', async () => {
    vi.mocked(URL.createObjectURL).mockReturnValueOnce('blob:thumbnail').mockReturnValueOnce('blob:original');
    vi.mocked(api.get).mockResolvedValue({ data: new Blob(['image']) });
    const { unmount } = render(<ArtifactPreview artifact={artifact} />);
    act(() => intersect());
    await userEvent.click(await screen.findByRole('button', { name: 'Expand generated image' }));
    await screen.findByRole('img', { name: 'Generated image, full size' });
    unmount();
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:thumbnail');
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:original');
  });

  it('loads and reuses the original only when Expand or Download is requested', async () => {
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
    vi.mocked(api.get).mockResolvedValue({ data: new Blob(['image']) });
    render(<ArtifactPreview artifact={artifact} />);
    act(() => intersect());
    await screen.findByRole('img', { name: 'Generated image' });
    expect(api.get).toHaveBeenCalledTimes(1);

    await userEvent.click(screen.getByRole('button', { name: 'Expand generated image' }));
    await screen.findByRole('dialog', { name: 'Generated image preview' });
    expect(api.get).toHaveBeenCalledTimes(2);
    expect(api.get).toHaveBeenLastCalledWith('/artifacts/image-1?variant=original', expect.objectContaining({ responseType: 'blob', signal: expect.any(AbortSignal) }));
    await userEvent.click(screen.getByRole('button', { name: 'Download image.png' }));
    expect(api.get).toHaveBeenCalledTimes(2);
    click.mockRestore();
  });

  it('shares one original request across concurrent expand and download actions', async () => {
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
    let resolveOriginal!: (value: { data: Blob }) => void;
    vi.mocked(api.get).mockResolvedValueOnce({ data: new Blob(['thumbnail']) }).mockImplementationOnce(() => new Promise((resolve) => { resolveOriginal = resolve; }) as never);
    render(<ArtifactPreview artifact={artifact} />);
    act(() => intersect());
    await userEvent.click(await screen.findByRole('button', { name: 'Expand generated image' }));
    await userEvent.click(screen.getByRole('button', { name: 'Download image.png' }));
    expect(api.get).toHaveBeenCalledTimes(2);
    await act(async () => resolveOriginal({ data: new Blob(['original']) }));
    await screen.findByRole('img', { name: 'Generated image, full size' });
    click.mockRestore();
  });

  it('reuses a download-first original when opening the dialog', async () => {
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
    vi.mocked(api.get).mockResolvedValue({ data: new Blob(['image']) });
    render(<ArtifactPreview artifact={artifact} />);
    act(() => intersect());
    await userEvent.click(await screen.findByRole('button', { name: 'Download image.png' }));
    await waitFor(() => expect(api.get).toHaveBeenCalledTimes(2));
    await userEvent.click(screen.getByRole('button', { name: 'Expand generated image' }));
    await screen.findByRole('dialog', { name: 'Generated image preview' });
    expect(api.get).toHaveBeenCalledTimes(2);
    click.mockRestore();
  });

  it('surfaces an original fetch error and retries it from the expanded dialog', async () => {
    vi.mocked(api.get).mockResolvedValueOnce({ data: new Blob(['thumbnail']) }).mockRejectedValueOnce(new Error('offline')).mockResolvedValueOnce({ data: new Blob(['original']) });
    render(<ArtifactPreview artifact={artifact} />);
    act(() => intersect());
    await userEvent.click(await screen.findByRole('button', { name: 'Expand generated image' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Full image unavailable');
    await userEvent.click(screen.getByRole('button', { name: 'Retry full image' }));
    await screen.findByRole('img', { name: 'Generated image, full size' });
    expect(api.get).toHaveBeenCalledTimes(3);
  });

  it('surfaces a download error and retries the explicit download request', async () => {
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
    vi.mocked(api.get).mockResolvedValueOnce({ data: new Blob(['thumbnail']) }).mockRejectedValueOnce(new Error('offline')).mockResolvedValueOnce({ data: new Blob(['original']) });
    render(<ArtifactPreview artifact={artifact} />);
    act(() => intersect());
    await userEvent.click(await screen.findByRole('button', { name: 'Download image.png' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Download image.png failed');
    await userEvent.click(screen.getByRole('button', { name: 'Retry download image.png' }));
    await waitFor(() => expect(api.get).toHaveBeenCalledTimes(3));
    click.mockRestore();
  });

  it.each([
    ['Expand→Download', ['expand', 'download']],
    ['Download→Expand', ['download', 'expand']],
  ] as const)('keeps both action errors visible when one shared original request rejects (%s)', async (_label, order) => {
    let rejectOriginal!: (error: Error) => void;
    vi.mocked(api.get).mockResolvedValueOnce({ data: new Blob(['thumbnail']) }).mockImplementationOnce(() => new Promise((_, reject) => { rejectOriginal = reject; }) as never);
    render(<ArtifactPreview artifact={artifact} />);
    act(() => intersect());
    await screen.findByRole('button', { name: 'Expand generated image' });
    for (const action of order) {
      await userEvent.click(screen.getByRole('button', {
        name: action === 'expand' ? 'Expand generated image' : 'Download image.png',
      }));
    }
    expect(api.get).toHaveBeenCalledTimes(2);
    await act(async () => rejectOriginal(new Error('offline')));
    expect((await screen.findByText('Full image unavailable.', { exact: false })).closest('[role="alert"]')).not.toBeNull();
    expect(screen.getByText('Download image.png failed.', { exact: false }).closest('[role="alert"]')).not.toBeNull();
    expect(screen.getByRole('button', { name: 'Retry full image' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Retry download image.png' })).toBeInTheDocument();
  });

  it('keeps original actions functional after StrictMode effect replay', async () => {
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
    vi.mocked(api.get).mockResolvedValue({ data: new Blob(['image']) });
    render(<StrictMode><ArtifactPreview artifact={artifact} /></StrictMode>);
    act(() => intersect());
    await userEvent.click(await screen.findByRole('button', { name: 'Expand generated image' }));
    await screen.findByRole('img', { name: 'Generated image, full size' });
    await userEvent.click(screen.getByRole('button', { name: 'Download image.png' }));
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    click.mockRestore();
  });

  it('keeps the expanded dialog keyboard and focus behaviour', async () => {
    vi.mocked(api.get).mockResolvedValue({ data: new Blob(['image']) });
    render(<ArtifactPreview artifact={artifact} />);
    act(() => intersect());
    const expand = await screen.findByRole('button', { name: 'Expand generated image' });
    await userEvent.click(expand);
    const dialog = await screen.findByRole('dialog', { name: 'Generated image preview' });
    expect(screen.getByRole('button', { name: 'Close image preview' })).toHaveFocus();
    fireEvent.keyDown(dialog, { key: 'Escape' });
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(expand).toHaveFocus();
  });

  it('uses compact Lucide icons for image actions and the dialog close control', async () => {
    vi.mocked(api.get).mockResolvedValue({ data: new Blob(['image']) });
    render(<ArtifactPreview artifact={artifact} onEditSource={vi.fn()} />);
    act(() => intersect());

    const edit = await screen.findByRole('button', { name: 'Use image.png as edit source' });
    const expand = screen.getByRole('button', { name: 'Expand generated image' });
    const download = screen.getByRole('button', { name: 'Download image.png' });
    expect(edit.querySelector('.lucide-pencil')).not.toBeNull();
    expect(expand.querySelector('.lucide-maximize-2')).not.toBeNull();
    expect(download.querySelector('.lucide-download')).not.toBeNull();
    expect(edit).toHaveTextContent('');
    expect(expand).toHaveTextContent('');
    expect(download).toHaveTextContent('');

    await userEvent.click(expand);
    const close = await screen.findByRole('button', { name: 'Close image preview' });
    expect(close.querySelector('.lucide-x')).not.toBeNull();
    expect(close).toHaveTextContent('');
  });

  it('traps tab focus and closes the expanded dialog from its backdrop', async () => {
    vi.mocked(api.get).mockResolvedValue({ data: new Blob(['image']) });
    render(<ArtifactPreview artifact={artifact} />);
    act(() => intersect());
    const expand = await screen.findByRole('button', { name: 'Expand generated image' });
    await userEvent.click(expand);
    const dialog = await screen.findByRole('dialog', { name: 'Generated image preview' });
    const close = screen.getByRole('button', { name: 'Close image preview' });
    fireEvent.keyDown(dialog, { key: 'Tab' });
    expect(close).toHaveFocus();
    fireEvent.click(document.querySelector('div.fixed[aria-hidden="true"]')!);
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(expand).toHaveFocus();
  });
});
