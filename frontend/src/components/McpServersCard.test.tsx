import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { McpServersCard } from './McpServersCard';
import type { McpServer } from '@/lib/mcp';

const mcp = vi.hoisted(() => ({
  listMcpServers: vi.fn(),
  addMcpServer: vi.fn(),
  updateMcpServer: vi.fn(),
  deleteMcpServer: vi.fn(),
  testMcpServer: vi.fn(),
}));

vi.mock('@/lib/mcp', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/mcp')>()),
  ...mcp,
}));

const own: McpServer = {
  id: 'srv-1', ref: 'srv-1', name: 'notion', description: 'Docs', transport: 'http',
  url: 'https://mcp.example.com/mcp', command: null, args: [], header_names: ['Authorization'], env_names: [],
  timeout: 30, enabled: true, shared: false, source: 'database', can_edit: true, connected: false,
};
const shared: McpServer = {
  ...own, id: null, ref: 'files', name: 'files', description: '', transport: 'stdio', url: null,
  header_names: [], shared: true, source: 'config_file', can_edit: false,
};

beforeEach(() => {
  Object.values(mcp).forEach((fn) => fn.mockReset());
  mcp.listMcpServers.mockResolvedValue({ servers: [own, shared], can_add_local: false, can_add_shared: false });
});

describe('McpServersCard', () => {
  it('lists own and shared servers, and only own ones can be edited', async () => {
    render(<McpServersCard />);
    expect(await screen.findByText('notion')).toBeInTheDocument();
    expect(screen.getByText('shared · mcp.json')).toBeInTheDocument();
    expect(screen.getAllByRole('button', { name: 'Edit' })).toHaveLength(1);
    expect(screen.getAllByRole('button', { name: 'Test' })).toHaveLength(2);
  });

  it('shows tools and whether they ask first after a test', async () => {
    mcp.testMcpServer.mockResolvedValue({
      connected: true, error: null,
      tools: [
        { name: 'search', description: '', read_only: true, asks_first: false },
        { name: 'create_page', description: '', read_only: false, asks_first: false },
        { name: 'delete_page', description: '', read_only: false, asks_first: true },
      ],
    });
    render(<McpServersCard />);
    fireEvent.click((await screen.findAllByRole('button', { name: 'Test' }))[0]);
    expect(await screen.findByText('create_page')).toBeInTheDocument();
    expect(screen.getByText('reads only')).toBeInTheDocument();
    expect(screen.getByText('runs without asking')).toBeInTheDocument();
    expect(screen.getByText('asks first')).toBeInTheDocument();
    expect(mcp.testMcpServer).toHaveBeenCalledWith('srv-1');
  });

  it('keeps saved header values unless a new one is typed', async () => {
    mcp.updateMcpServer.mockResolvedValue(own);
    render(<McpServersCard />);
    fireEvent.click(await screen.findByRole('button', { name: 'Edit' }));
    expect(screen.queryByRole('option', { name: /Local command/ })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(mcp.updateMcpServer).toHaveBeenCalled());
    const [, input] = mcp.updateMcpServer.mock.calls[0];
    expect(input.headers).toEqual({ Authorization: '***' });
    expect(input.url).toBe('https://mcp.example.com/mcp');
  });

  it('adds a remote server', async () => {
    mcp.addMcpServer.mockResolvedValue(own);
    render(<McpServersCard />);
    fireEvent.click(await screen.findByRole('button', { name: 'Add server' }));
    fireEvent.change(screen.getByPlaceholderText('notion'), { target: { value: 'linear' } });
    fireEvent.change(screen.getByPlaceholderText('https://mcp.example.com/mcp'), { target: { value: 'https://mcp.linear.app/mcp' } });
    fireEvent.change(screen.getByLabelText('HEADERS name 1'), { target: { value: 'Authorization' } });
    fireEvent.change(screen.getByLabelText('HEADERS value 1'), { target: { value: 'Bearer abc' } });
    fireEvent.click(screen.getByRole('button', { name: 'Add' }));
    await waitFor(() => expect(mcp.addMcpServer).toHaveBeenCalled());
    expect(mcp.addMcpServer.mock.calls[0][0]).toMatchObject({
      name: 'linear', transport: 'http', url: 'https://mcp.linear.app/mcp', headers: { Authorization: 'Bearer abc' }, shared: false,
    });
  });
});
