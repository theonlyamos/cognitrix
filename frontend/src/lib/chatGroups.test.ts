import { beforeEach, describe, expect, it } from 'vitest';
import {
  ARCHIVED_KEY, PANEL_DEFAULT, PANEL_MAX, PANEL_MIN, clampPanelWidth, groupChats, loadClosedSections, loadPanelWidth,
  saveClosedSections, savePanelWidth,
} from './chatGroups';

const chats = [
  { id: 'a1', kind: 'agent' as const, agent_id: 'a', updated_at: '2026-11-13T10:00:00Z' },
  { id: 't1', kind: 'team' as const, team_id: 't', updated_at: '2026-11-13T09:00:00Z' },
  { id: 'b1', kind: 'agent' as const, agent_id: 'b', updated_at: '2026-11-13T08:00:00Z', pinned: true },
  { id: 'a2', kind: 'agent' as const, agent_id: 'a', updated_at: '2026-11-12T10:00:00Z' },
  { id: 'b2', kind: 'agent' as const, agent_id: 'b', updated_at: '2026-11-11T10:00:00Z' },
  { id: 'a3', kind: 'agent' as const, agent_id: 'a', updated_at: '2026-11-01T10:00:00Z', archived: true },
];

describe('groupChats', () => {
  it('puts pinned on top, archived last and sections by newest chat', () => {
    const g = groupChats(chats);
    expect(g.pinned.map((c) => c.id)).toEqual(['b1']);
    expect(g.archived.map((c) => c.id)).toEqual(['a3']);
    expect(g.groups.map((s) => s.key)).toEqual(['agent:a', 'team:t', 'agent:b']);
    expect(g.groups[0].chats.map((c) => c.id)).toEqual(['a1', 'a2']);
    expect(g.groups[1]).toMatchObject({ kind: 'team', id: 't' });
    // b's newest chat is pinned, which still counts for ordering.
    expect(g.groups[2].latest).toBe('2026-11-13T08:00:00Z');
  });

  it('mixes pinned and archived chats into their sections while searching', () => {
    const g = groupChats(chats, { searching: true });
    expect(g.pinned).toEqual([]);
    expect(g.archived).toEqual([]);
    expect(g.groups.find((s) => s.key === 'agent:a')?.chats.map((c) => c.id)).toEqual(['a1', 'a2', 'a3']);
    expect(g.groups.find((s) => s.key === 'agent:b')?.chats.map((c) => c.id)).toEqual(['b1', 'b2']);
  });
});

describe('remembered list settings', () => {
  beforeEach(() => localStorage.clear());

  it('starts with only the archive closed and remembers changes', () => {
    expect([...loadClosedSections()]).toEqual([ARCHIVED_KEY]);
    saveClosedSections(new Set(['agent:a']));
    expect([...loadClosedSections()]).toEqual(['agent:a']);
  });

  it('keeps the panel width within limits', () => {
    expect(loadPanelWidth()).toBe(PANEL_DEFAULT);
    savePanelWidth(9999);
    expect(loadPanelWidth()).toBe(PANEL_MAX);
    expect(clampPanelWidth(10)).toBe(PANEL_MIN);
  });
});
