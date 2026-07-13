# Chat Scroll Containment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep long conversations inside the Home transcript so neither the application shell nor the document can scroll.

**Architecture:** `AppLayout` remains the sole viewport owner. `Home` becomes a height-contained flex child, the transcript becomes the only vertical scroll container, and the auto-follow effect writes to that container's `scrollTop` instead of invoking descendant `scrollIntoView()`.

**Tech Stack:** React 18, TypeScript 5.5, Tailwind CSS 3.4, Vitest 3, Testing Library, jsdom.

## Global Constraints

- Preserve the current always-follow-latest chat behavior.
- Do not alter conversation persistence, SSE behavior, composer behavior, or message rendering.
- Use `min-h-0` throughout the nested flex chain and `overscroll-contain` on the transcript.
- Do not modify the user's unrelated edits in `cognitrix/cli/args.py` or `frontend/src/pages/AgentPage.tsx`.
- Browser verification must target `http://localhost:5173` exactly.

---

### Task 1: Contain and directly scroll the Home transcript

**Files:**
- Modify: `frontend/src/components/ChatMessageRow.test.tsx`
- Modify: `frontend/src/pages/Home.tsx:230-240`
- Modify: `frontend/src/pages/Home.tsx:590-610`
- Modify: `frontend/src/pages/Home.tsx:654-729`

**Interfaces:**
- Consumes: the existing `Home` message effect dependencies (`messages`, `waiting`, `planning`).
- Produces: a `transcriptRef: RefObject<HTMLDivElement>` attached to the element with `role="log"`; no public API changes.

- [ ] **Step 1: Write the failing scroll-boundary regression test**

In `frontend/src/components/ChatMessageRow.test.tsx`, keep the existing Home harness and replace the ad-hoc prototype assignment with a reusable mock:

```tsx
const scrollIntoViewMock = vi.fn();

beforeEach(() => {
  markdownRender.mockClear();
  scrollIntoViewMock.mockClear();
  homeHarness.messages = [];
  homeHarness.onMessage = null;
  localStorage.clear();
  localStorage.setItem('selectedAgentId', 'agent-1');
  localStorage.setItem('chatSession:agent-1', '');
  Element.prototype.scrollIntoView = scrollIntoViewMock;
});
```

Add this test inside `describe('ChatMessageRow', ...)`:

```tsx
it('keeps long-conversation auto-scroll inside the transcript', async () => {
  homeHarness.messages = [{
    id: 'assistant-1',
    role: 'assistant',
    content: 'first page',
  }];

  const renderHome = () => (
    <MemoryRouter future={{ v7_relativeSplatPath: true, v7_startTransition: true }}>
      <Home />
    </MemoryRouter>
  );
  const { rerender } = render(renderHome());
  const transcript = screen.getByRole('log');

  Object.defineProperty(transcript, 'scrollHeight', {
    configurable: true,
    value: 640,
  });
  Object.defineProperty(transcript, 'scrollTop', {
    configurable: true,
    writable: true,
    value: 0,
  });

  homeHarness.messages = [
    ...homeHarness.messages,
    { id: 'assistant-2', role: 'assistant', content: 'next page' },
  ];
  rerender(renderHome());

  await waitFor(() => expect(transcript.scrollTop).toBe(640));
  expect(scrollIntoViewMock).not.toHaveBeenCalled();
  expect(transcript).toHaveClass(
    'min-h-0',
    'overflow-y-auto',
    'overscroll-contain',
  );
  expect(transcript.parentElement).toHaveClass('min-h-0', 'overflow-hidden');
});
```

- [ ] **Step 2: Run the focused test and verify RED**

Run from `frontend`:

```powershell
pnpm test -- src/components/ChatMessageRow.test.tsx
```

Expected: FAIL because `transcript.scrollTop` remains `0` and `scrollIntoViewMock` is called.

- [ ] **Step 3: Implement direct transcript scrolling and height containment**

In `frontend/src/pages/Home.tsx`, replace the bottom-sentinel ref and effect:

```tsx
const transcriptRef = useRef<HTMLDivElement>(null);

useEffect(() => {
  const transcript = transcriptRef.current;
  if (transcript) transcript.scrollTop = transcript.scrollHeight;
}, [messages, waiting, planning]);
```

Use the following containment classes on the Home shell:

```tsx
<div className="flex h-full min-h-0 min-w-0 flex-1 overflow-hidden bg-bg text-fg">
```

Use `min-h-0` on the desktop conversation panel and chat column:

```tsx
<aside className="hidden min-h-0 w-60 flex-none flex-col border-r border-line bg-panel md:flex">
  {conversationPanel}
</aside>

<div className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden">
```

Attach the ref to the only vertical scroll owner:

```tsx
<div
  ref={transcriptRef}
  role="log"
  aria-live="polite"
  aria-relevant="additions text"
  className="min-h-0 flex-1 overscroll-contain overflow-y-auto"
>
```

Delete the old `endRef` declaration and the trailing `<div ref={endRef} />` sentinel.

- [ ] **Step 4: Run the focused test and verify GREEN**

Run from `frontend`:

```powershell
pnpm test -- src/components/ChatMessageRow.test.tsx
```

Expected: all `ChatMessageRow.test.tsx` tests PASS, including the new scroll-boundary regression.

- [ ] **Step 5: Run frontend regression verification**

Run from `frontend`:

```powershell
pnpm test
pnpm lint
pnpm build
```

Expected: all Vitest tests pass; ESLint reports no errors; TypeScript and Vite build successfully.

- [ ] **Step 6: Verify the defect in the in-app browser**

At `http://localhost:5173/home`:

1. Open a conversation long enough to exceed the viewport.
2. Scroll the transcript to the bottom and send another message.
3. Confirm only the transcript scroll position changes.
4. Confirm the header, composer, sidebar, and application background remain fixed.
5. In page evaluation, confirm `document.scrollingElement?.scrollTop === 0` and the Home shell's `scrollTop === 0` while the transcript's `scrollTop > 0`.
6. Test one desktop width and one narrow/mobile width.

Expected: no black section is revealed below the application and scroll momentum remains contained.

- [ ] **Step 7: Commit the isolated fix**

```powershell
git add frontend/src/components/ChatMessageRow.test.tsx frontend/src/pages/Home.tsx
git commit -m "fix(chat): contain transcript scrolling"
```

Expected: the commit contains only the Home scroll fix and its regression test.
