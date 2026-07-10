# Interface Audit Remediation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Resolve every finding in the approved interface audit in the order `/adapt`, `/harden`, `/normalize`, `/optimize`, then restore lint coverage and verify the authenticated app.

**Architecture:** Keep the current React/Vite structure and Technical / Signal design. Add a stateful mobile shell around the existing sidebar, strengthen shared UI primitives so fixes propagate across pages, split Markdown rendering behind a lazy boundary, and use Vitest plus Testing Library for behavioral regression coverage.

**Tech Stack:** React 18, TypeScript 5.5, Vite 5, Tailwind CSS 3.4, React Router 6, Vitest, Testing Library, jsdom, ESLint 9.

## Global Constraints

- Preserve every existing route, API contract, and desktop workflow.
- Preserve the established "Technical / Signal" visual identity.
- Every audited core function must work at 320 CSS pixels without horizontal clipping or hover-only controls.
- Interactive controls touched by this work must expose a 44-pixel mobile target.
- Focus indicators must provide at least 3:1 contrast against `--bg`, `--panel`, and `--panel-2` in light and dark themes.
- Do not add a command palette; remove the dead Search & Run affordance.
- Do not virtualize transcripts unless measurement proves memoization and lazy loading insufficient.
- Follow red-green-refactor for every production behavior change.

---

### Task 1: Test harness and adaptive application shell

**Files:**
- Modify: `frontend/package.json`
- Modify: `frontend/pnpm-lock.yaml`
- Modify: `frontend/vite.config.ts`
- Create: `frontend/src/test/setup.ts`
- Create: `frontend/src/components/AppLayout.test.tsx`
- Modify: `frontend/src/components/AppLayout.tsx`
- Modify: `frontend/src/components/Sidebar.tsx`

**Interfaces:**
- `AppLayout` owns `mobileNavOpen: boolean` and renders `Sidebar` with `mobileOpen` and `onNavigate`.
- `Sidebar` accepts `{ mobileOpen?: boolean; onNavigate?: () => void }` while retaining desktop collapse persistence.
- The shell exposes `button[aria-label="Open navigation"]`, `#primary-navigation`, and `main#main-content`.

- [ ] **Step 1: Install the test-only dependencies and configure Vitest**

Run from `frontend/`:

```powershell
pnpm add -D vitest jsdom @testing-library/react @testing-library/jest-dom @testing-library/user-event globals
```

Add scripts:

```json
"test": "vitest run",
"test:watch": "vitest"
```

Add this Vite test configuration:

```ts
test: {
  environment: 'jsdom',
  globals: true,
  setupFiles: './src/test/setup.ts',
  css: true,
},
```

Create `src/test/setup.ts`:

```ts
import '@testing-library/jest-dom/vitest';
```

- [ ] **Step 2: Write the failing mobile-shell tests**

Cover these behaviors in `AppLayout.test.tsx` using `MemoryRouter`, nested `Routes`, and a mocked authenticated user context:

```tsx
it('opens and closes primary navigation on mobile', async () => {
  renderShell();
  const trigger = screen.getByRole('button', { name: 'Open navigation' });
  expect(trigger).toHaveAttribute('aria-expanded', 'false');
  await userEvent.click(trigger);
  expect(trigger).toHaveAttribute('aria-expanded', 'true');
  expect(screen.getByRole('complementary', { name: 'Primary navigation' })).toHaveAttribute('data-mobile-open', 'true');
  await userEvent.keyboard('{Escape}');
  expect(trigger).toHaveAttribute('aria-expanded', 'false');
});

it('closes mobile navigation after following a route', async () => {
  renderShell();
  await userEvent.click(screen.getByRole('button', { name: 'Open navigation' }));
  await userEvent.click(screen.getByRole('link', { name: 'Agents' }));
  expect(screen.getByRole('button', { name: 'Open navigation' })).toHaveAttribute('aria-expanded', 'false');
});

it('renders authenticated content in a named main landmark', () => {
  renderShell();
  expect(screen.getByRole('main')).toHaveAttribute('id', 'main-content');
});
```

- [ ] **Step 3: Run the tests and verify RED**

Run: `pnpm test -- AppLayout.test.tsx`

Expected: FAIL because the navigation trigger, responsive drawer contract, and main landmark do not exist.

- [ ] **Step 4: Implement the adaptive shell**

`AppLayout` must render this structure:

```tsx
<div className="flex h-dvh overflow-hidden bg-bg text-fg">
  <button
    type="button"
    aria-label="Open navigation"
    aria-controls="primary-navigation"
    aria-expanded={mobileNavOpen}
    onClick={() => setMobileNavOpen(true)}
    className="fixed left-2 top-2 z-40 grid h-11 w-11 place-items-center rounded border border-line bg-panel text-fg md:hidden"
  >
    <MenuIcon />
  </button>
  {mobileNavOpen && (
    <button type="button" aria-label="Close navigation" className="fixed inset-0 z-40 bg-bg/70 md:hidden" onClick={() => setMobileNavOpen(false)} />
  )}
  <Sidebar mobileOpen={mobileNavOpen} onNavigate={() => setMobileNavOpen(false)} />
  <main id="main-content" className="flex min-h-0 min-w-0 flex-1 flex-col">
    <Outlet />
  </main>
</div>
```

Close the drawer on Escape and route navigation. On mobile, `Sidebar` is fixed, capped at `min(88vw, 280px)`, translated off-canvas when closed, and always expanded. At `md`, restore the existing static 232/58-pixel widths and collapse control.

- [ ] **Step 5: Run the focused tests and full type check**

Run: `pnpm test -- AppLayout.test.tsx`

Expected: PASS.

Run: `pnpm exec tsc -b`

Expected: exit 0.

- [ ] **Step 6: Commit**

```bash
git add frontend/package.json frontend/pnpm-lock.yaml frontend/vite.config.ts frontend/src/test/setup.ts frontend/src/components/AppLayout.test.tsx frontend/src/components/AppLayout.tsx frontend/src/components/Sidebar.tsx
git commit -m "feat(ui): add adaptive mobile shell"
```

---

### Task 2: Responsive content panels, headers, transcripts, and touch targets

**Files:**
- Create: `frontend/src/components/responsive-ui.test.tsx`
- Modify: `frontend/src/lib/components/ui/button.tsx`
- Modify: `frontend/src/components/list-ui.tsx`
- Modify: `frontend/src/components/form.tsx`
- Modify: `frontend/src/components/TranscriptView.tsx`
- Modify: `frontend/src/pages/Home.tsx`
- Modify: `frontend/src/pages/TaskDetail.tsx`
- Modify: `frontend/src/pages/TeamInteraction.tsx`

**Interfaces:**
- Shared page headers use `.app-page-header` and stack/wrap below `sm`.
- Chat and transcript rows use one column below `sm` and fixed speaker gutters at `sm+`.
- Home exposes a mobile `Conversations` toggle; TaskDetail exposes a mobile `Runs` toggle.

- [ ] **Step 1: Write failing responsive-contract tests**

```tsx
it('gives small buttons a 44px mobile target and compact desktop height', () => {
  render(<Button size="sm">Save</Button>);
  expect(screen.getByRole('button', { name: 'Save' })).toHaveClass('h-11', 'md:h-8');
});

it('stacks page-header content before the small breakpoint', () => {
  render(<PageHeader title="Tasks" subtitle="2 tasks"><Button>New</Button></PageHeader>);
  expect(screen.getByRole('banner')).toHaveClass('flex-col', 'sm:flex-row');
});

it('stacks transcript gutters on narrow screens', () => {
  render(<TranscriptView entries={[{ kind: 'user', content: 'Hello' }]} />);
  expect(screen.getByText('YOU').parentElement).toHaveClass('grid-cols-1', 'sm:grid-cols-[96px_1fr]');
});
```

- [ ] **Step 2: Run the tests and verify RED**

Run: `pnpm test -- responsive-ui.test.tsx`

Expected: FAIL on the missing responsive classes and target sizes.

- [ ] **Step 3: Implement shared responsive contracts**

Use these button sizes:

```ts
sm: 'h-11 px-3 text-[13px] md:h-8',
md: 'h-11 px-4 text-sm',
lg: 'h-12 px-6 text-sm',
icon: 'h-11 w-11 md:h-9 md:w-9',
```

Make PageHeader and PageForm headers stack on mobile, wrap action groups, and reserve left space for the mobile menu trigger through `.app-page-header`. Convert transcript/chat row grids from fixed columns to `grid-cols-1` with `sm:grid-cols-[…]`.

Add mobile sheet toggles for conversations and runs. Their content must use the same data and actions as desktop side panels; the desktop panels remain visible at `md+`. Delete controls must be visible on touch devices rather than only on hover.

- [ ] **Step 4: Run focused tests, type check, and 320px browser smoke test**

Run: `pnpm test -- responsive-ui.test.tsx`

Expected: PASS.

Run: `pnpm exec tsc -b`

Expected: exit 0.

Browser evidence at 320×800 must show the navigation closed by default, full-width primary content, reachable conversation/run toggles, and no clipped header actions.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/responsive-ui.test.tsx frontend/src/lib/components/ui/button.tsx frontend/src/components/list-ui.tsx frontend/src/components/form.tsx frontend/src/components/TranscriptView.tsx frontend/src/pages/Home.tsx frontend/src/pages/TaskDetail.tsx frontend/src/pages/TeamInteraction.tsx
git commit -m "fix(ui): adapt workflows for mobile"
```

---

### Task 3: Keyboard, form, navigation, and live-state accessibility

**Files:**
- Create: `frontend/src/components/SelectionRow.tsx`
- Create: `frontend/src/components/accessibility.test.tsx`
- Modify: `frontend/src/components/form.tsx`
- Modify: `frontend/src/components/Sidebar.tsx`
- Modify: `frontend/src/components/list-ui.tsx`
- Modify: `frontend/src/pages/Login.tsx`
- Modify: `frontend/src/pages/Signup.tsx`
- Modify: `frontend/src/pages/ApiKeys.tsx`
- Modify: `frontend/src/pages/Home.tsx`
- Modify: `frontend/src/pages/TaskDetail.tsx`
- Modify: `frontend/src/pages/TeamInteraction.tsx`

**Interfaces:**
- `SelectionRow` renders a native selection button with `aria-pressed` and an optional sibling trailing action.
- `Field` accepts `composite?: boolean`; composite children receive `aria-labelledby`, ordinary controls receive `id` plus `htmlFor`.
- `CheckList` forwards `aria-labelledby` to its `role="group"` container.

- [ ] **Step 1: Write failing accessibility tests**

Cover these exact behaviors:

```tsx
it('names simple controls and composite checkbox groups', () => {
  render(<><Field label="NAME"><Input /></Field><Field label="SCOPES" composite><CheckList options={opts} selected={new Set()} onToggle={() => {}} /></Field></>);
  expect(screen.getByRole('textbox', { name: 'NAME' })).toBeInTheDocument();
  expect(screen.getByRole('group', { name: 'SCOPES' })).toBeInTheDocument();
});

it('uses a keyboard-operable pressed button for selectable rows', async () => {
  const onSelect = vi.fn();
  render(<SelectionRow selected onSelect={onSelect}>Run 1</SelectionRow>);
  const row = screen.getByRole('button', { name: 'Run 1' });
  expect(row).toHaveAttribute('aria-pressed', 'true');
  row.focus();
  await userEvent.keyboard('{Enter}');
  expect(onSelect).toHaveBeenCalledOnce();
});

it('marks the active navigation destination and omits dead search UI', () => {
  renderSidebar('/tasks');
  expect(screen.getByRole('link', { name: 'Tasks' })).toHaveAttribute('aria-current', 'page');
  expect(screen.queryByRole('button', { name: /Search & run/i })).not.toBeInTheDocument();
});

it('exposes completed and failed tool states as text', () => {
  renderToolRows();
  expect(screen.getByText('Completed')).toHaveClass('sr-only');
  expect(screen.getByText('Failed')).toHaveClass('sr-only');
});
```

- [ ] **Step 2: Run tests and verify RED**

Run: `pnpm test -- accessibility.test.tsx`

Expected: FAIL because composite labels, SelectionRow, `aria-current`, status text, and dead-control removal are missing.

- [ ] **Step 3: Implement semantic controls and labels**

Use `SelectionRow` for Home conversations and TaskDetail current/legacy runs. Keep destructive actions as sibling buttons to avoid nested interactive elements. Add explicit accessible names to all icon-only back links and the chat composer.

Replace Signup's local Field with the shared Field. Label API key secret inputs with stable IDs. Apply `composite` to tools, steps, schedules, checkbox groups, and allowlists.

Remove Search & Run and its advertised shortcut. Add `aria-current="page"` to the active Sidebar link.

- [ ] **Step 4: Implement restrained status announcements**

- Chat history: `role="log"`, `aria-live="polite"`, `aria-relevant="additions text"`.
- Streaming response subtree: `aria-live="off"` until complete.
- Waiting/loading indicators: `role="status"` and accessible text.
- Errors and approval requests: `role="alert"`.
- Tool states: visible running text plus `.sr-only` Completed/Failed text.

- [ ] **Step 5: Run accessibility tests and full frontend tests**

Run: `pnpm test -- accessibility.test.tsx`

Expected: PASS.

Run: `pnpm test`

Expected: all tests pass.

Run: `pnpm exec tsc -b`

Expected: exit 0.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/SelectionRow.tsx frontend/src/components/accessibility.test.tsx frontend/src/components/form.tsx frontend/src/components/Sidebar.tsx frontend/src/components/list-ui.tsx frontend/src/pages/Login.tsx frontend/src/pages/Signup.tsx frontend/src/pages/ApiKeys.tsx frontend/src/pages/Home.tsx frontend/src/pages/TaskDetail.tsx frontend/src/pages/TeamInteraction.tsx
git commit -m "fix(a11y): harden interface semantics"
```

---

### Task 4: Alpha-aware theme tokens and compliant focus indicators

**Files:**
- Create: `frontend/src/test/theme-contract.test.ts`
- Modify: `frontend/tailwind.config.js`
- Modify: `frontend/src/app.css`
- Modify: `frontend/src/lib/components/ui/input.tsx`
- Modify: `frontend/src/lib/components/ui/select.tsx`
- Modify: `frontend/src/lib/components/ui/textarea.tsx`
- Modify: `frontend/src/lib/components/ui/button.tsx`
- Modify: `frontend/src/lib/components/ui/tabs.tsx`
- Modify: `frontend/src/pages/Home.tsx`

**Interfaces:**
- Existing CSS color variables remain valid CSS colors for raw styles.
- Parallel `--*-rgb` channel variables back Tailwind colors with `<alpha-value>`.
- `--focus` is `#4e6b00` in light mode and `#c6f24e` in dark mode.

- [ ] **Step 1: Write the failing theme contract test**

Read `app.css` and `tailwind.config.js` from the test and assert:

```ts
expect(css).toContain('--focus:#4e6b00');
expect(css).toContain('--focus:#c6f24e');
expect(config).toContain('rgb(var(--accent-rgb) / <alpha-value>)');
expect(config).toContain('rgb(var(--danger-rgb) / <alpha-value>)');
expect(config).toContain('rgb(var(--ok-rgb) / <alpha-value>)');
```

Calculate WCAG contrast inside the test and assert both focus colors are at least 3:1 against every corresponding background token.

- [ ] **Step 2: Run the test and verify RED**

Run: `pnpm test -- theme-contract.test.ts`

Expected: FAIL because RGB channels and compliant focus tokens do not exist.

- [ ] **Step 3: Add RGB channels and alpha-aware Tailwind colors**

Keep values such as `--accent:#c6f24e` for raw CSS and add channels such as `--accent-rgb:198 242 78`. Configure Tailwind colors as:

```js
accent: {
  DEFAULT: 'rgb(var(--accent-rgb) / <alpha-value>)',
  foreground: 'rgb(var(--on-accent-rgb) / <alpha-value>)',
  ink: 'rgb(var(--accent-ink-rgb) / <alpha-value>)',
},
danger: {
  DEFAULT: 'rgb(var(--danger-rgb) / <alpha-value>)',
  ink: 'rgb(var(--danger-ink-rgb) / <alpha-value>)',
},
ok: 'rgb(var(--ok-rgb) / <alpha-value>)',
```

Apply the same pattern to bg, panel, panel-2, fg, and fg-dim.

- [ ] **Step 4: Implement the focus and reduced-motion contract**

Use a two-pixel solid focus outline with two-pixel offset. Remove component-level `outline-none` or `shadow-none` classes that suppress it. In reduced-motion mode set `scroll-behavior:auto` in addition to shortening animations.

- [ ] **Step 5: Verify unit contract and compiled utilities**

Run: `pnpm test -- theme-contract.test.ts`

Expected: PASS.

Run: `pnpm build`

Expected: exit 0.

Inspect the emitted CSS and verify selectors exist for `bg-danger/5`, `bg-accent/5`, `border-accent/40`, `border-danger/40`, `border-ok/40`, and `text-fg-dim/70`.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/test/theme-contract.test.ts frontend/tailwind.config.js frontend/src/app.css frontend/src/lib/components/ui/input.tsx frontend/src/lib/components/ui/select.tsx frontend/src/lib/components/ui/textarea.tsx frontend/src/lib/components/ui/button.tsx frontend/src/lib/components/ui/tabs.tsx frontend/src/pages/Home.tsx
git commit -m "fix(theme): restore alpha and focus tokens"
```

---

### Task 5: Lazy Markdown and stable streaming rows

**Files:**
- Create: `frontend/src/components/MarkdownMessage.tsx`
- Create: `frontend/src/components/ChatMessageRow.tsx`
- Create: `frontend/src/components/ChatMessageRow.test.tsx`
- Modify: `frontend/src/pages/Home.tsx`

**Interfaces:**
- `MarkdownMessage` is the only module importing `react-markdown`, `remark-gfm`, and `rehype-highlight`.
- `ChatMessageRow` is `memo`-wrapped and accepts `{ message, isLast, streaming }`.
- `ChatMessageRow` lazy-loads `MarkdownMessage` inside its row-local Suspense boundary; Home renders the memoized row without importing Markdown packages.

- [ ] **Step 1: Record the before measurement**

Run: `pnpm build`

Record: Home 360.53 kB raw / 109.74 kB gzip; base 250.81 kB raw / 83.27 kB gzip.

- [ ] **Step 2: Write the failing stable-row test**

Mock `MarkdownMessage`, render a completed assistant row, rerender with the same message object while changing unrelated parent state, and assert the mocked Markdown renderer ran once. Add a source-boundary assertion that Home no longer statically imports Markdown packages.

```tsx
expect(markdownRender).toHaveBeenCalledTimes(1);
expect(homeSource).not.toMatch(/from 'react-markdown'|from 'remark-gfm'|from 'rehype-highlight'/);
```

- [ ] **Step 3: Run the test and verify RED**

Run: `pnpm test -- ChatMessageRow.test.tsx`

Expected: FAIL because the row component and lazy Markdown boundary do not exist.

- [ ] **Step 4: Extract and memoize message rendering**

Move Markdown renderer configuration into `MarkdownMessage.tsx` and export it as default. In `ChatMessageRow`:

```tsx
const MarkdownMessage = lazy(() => import('@/components/MarkdownMessage'));
```

Render completed assistant content through a row-local Suspense fallback containing the same plain text. Move message/tool row markup into the memoized component; preserve message object identity for all unchanged rows. This location keeps the exact three-prop row interface and makes the stable-row Markdown mock exercise the real render boundary without context injection.

- [ ] **Step 5: Verify tests and after measurement**

Run: `pnpm test -- ChatMessageRow.test.tsx`

Expected: PASS.

Run: `pnpm build`

Expected: Home's initial route chunk is materially smaller and a separate Markdown/highlight chunk appears. The empty-chat path must not request the Markdown chunk; verify in the in-app browser network log or observed page assets.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/MarkdownMessage.tsx frontend/src/components/ChatMessageRow.tsx frontend/src/components/ChatMessageRow.test.tsx frontend/src/pages/Home.tsx
git commit -m "perf(chat): defer markdown rendering"
```

---

### Task 6: ESLint 9 flat configuration

**Files:**
- Create: `frontend/eslint.config.js`
- Modify: frontend source files only when lint identifies a real defect

**Interfaces:**
- `pnpm lint` checks all TypeScript/TSX files and ignores `dist`.
- The config uses `@eslint/js`, `typescript-eslint`, `eslint-plugin-react-hooks`, `eslint-plugin-react-refresh`, and `globals`.

- [ ] **Step 1: Capture the failing lint command**

Run: `pnpm lint`

Expected: FAIL with “ESLint couldn't find an eslint.config” before the config is added.

- [ ] **Step 2: Add the flat config**

```js
import js from '@eslint/js';
import globals from 'globals';
import reactHooks from 'eslint-plugin-react-hooks';
import reactRefresh from 'eslint-plugin-react-refresh';
import tseslint from 'typescript-eslint';

export default tseslint.config(
  { ignores: ['dist'] },
  {
    files: ['**/*.{ts,tsx}'],
    extends: [js.configs.recommended, ...tseslint.configs.recommended],
    languageOptions: { ecmaVersion: 2020, globals: globals.browser },
    plugins: { 'react-hooks': reactHooks, 'react-refresh': reactRefresh },
    rules: {
      ...reactHooks.configs.recommended.rules,
      'react-refresh/only-export-components': ['warn', { allowConstantExport: true }],
    },
  },
);
```

- [ ] **Step 3: Run lint, fix real findings, and rerun**

Run: `pnpm lint`

Expected: exit 0 with no errors. Fix source issues rather than broadly disabling rules; document any narrowly scoped compatibility exception in the config.

- [ ] **Step 4: Run all frontend gates**

Run: `pnpm test && pnpm lint && pnpm build`

Expected: all commands exit 0.

- [ ] **Step 5: Commit**

```bash
git add frontend/eslint.config.js frontend/src
git commit -m "chore(frontend): restore lint coverage"
```

---

### Task 7: Authenticated browser verification and audit closure

**Files:**
- Modify only files required by a reproduced verification failure

**Interfaces:**
- Browser target: `http://localhost:5173`
- Credentials when login is required: `webtest@example.com` / `testpass123`
- Test widths: 320×800, 768×900, and 1440×900.

- [ ] **Step 1: Verify mobile navigation and reflow at 320×800**

Confirm navigation is closed initially; open and close it with click and Escape; navigate to Chat, Agents, Tasks, Teams, and API Keys. At each route assert `document.documentElement.scrollWidth === document.documentElement.clientWidth`, one main landmark exists, and primary actions remain visible.

- [ ] **Step 2: Verify keyboard and accessible names**

Use role/name locators to operate the navigation, conversation/run selectors, form controls, back links, mobile panel toggles, and theme toggle. Confirm the active navigation item reports `aria-current="page"` and Search & Run is absent.

- [ ] **Step 3: Verify mobile targets and theme focus**

At 320px inspect visible buttons/links and confirm every touched control has a minimum 44-pixel bounding box. Tab through both themes and confirm a visible focus outline. Use computed styles to verify the focus color token and alpha-modified error/status backgrounds.

- [ ] **Step 4: Verify tablet and desktop layouts**

At 768px and 1440px confirm the desktop sidebar and secondary panels appear at the intended breakpoints, no headers clip, and the desktop collapse preference still works.

- [ ] **Step 5: Run fresh final gates and audit searches**

Run:

```powershell
pnpm test
pnpm lint
pnpm build
rg -n '<div[^>]*onClick' src/pages/Home.tsx src/pages/TaskDetail.tsx
rg -n 'Search & run|text-fg-dim/70|bg-danger/5|border-(accent|danger|ok)/' src
```

Expected: tests, lint, and build exit 0; no mouse-only selectable rows or dead search control remain; alpha-token usages remain and their compiled selectors were verified in Task 4.

- [ ] **Step 6: Commit any verification-only fixes**

If verification required changes, commit them as:

```bash
git add frontend
git commit -m "fix(ui): close browser verification gaps"
```
