import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
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
    expect(screen.getByTestId('artifact-placeholder')).toHaveStyle({ aspectRatio: '1200 / 800' });
    expect(observerOptions()).toContainEqual(expect.objectContaining({ rootMargin: '400px 0px' }));

    vi.mocked(api.get).mockResolvedValue({ data: new Blob(['thumbnail']) });
    act(() => intersect(screen.getByTestId('artifact-placeholder')));
    await waitFor(() => expect(api.get).toHaveBeenCalledWith('/artifacts/image-1?variant=thumbnail', expect.objectContaining({ responseType: 'blob', signal: expect.any(AbortSignal) })));
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

    vi.mocked(api.get).mockResolvedValueOnce({ data: new Blob(['thumbnail']) });
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
});
