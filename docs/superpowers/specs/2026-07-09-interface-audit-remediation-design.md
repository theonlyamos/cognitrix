# Interface Audit Remediation Design

## Scope

Resolve the interface audit findings in the approved order without changing
backend contracts or the established "Technical / Signal" visual identity:

1. adapt the authenticated experience for mobile and touch;
2. harden keyboard, semantic, form, and live-state accessibility;
3. normalize theme-alpha utilities and focus indicators;
4. optimize the measured Home route and streaming render path;
5. restore ESLint 9 coverage and verify the result in the authenticated app.

The implementation preserves every existing route and desktop workflow. It
does not add a new command palette, change API behavior, or introduce a large
component library.

## Considered approaches

### Selected: shared adaptive primitives with focused page changes

Introduce a small mobile-shell contract in `AppLayout` and `Sidebar`, extend
the existing shared form/list primitives with semantics, and update only the
pages whose layouts or interactions need adaptation. This keeps the design
system recognizable, addresses recurring defects at their source, and avoids a
full navigation or component rewrite.

### Rejected: CSS-only shrinking

Simply reducing widths and font sizes would leave the desktop information
architecture intact on phones, preserve hover-only actions, and fail the
44-pixel touch-target requirement.

### Rejected: replace the shell and UI library

A new navigation framework or component suite could solve several issues, but
would create unnecessary visual churn, migration risk, and bundle cost for a
targeted remediation.

## Responsive shell and content

`AppLayout` owns mobile navigation state. Below the `md` breakpoint, the
desktop sidebar becomes an off-canvas dialog-style drawer with a backdrop and
a persistent 44-pixel menu trigger in the page shell. Navigation closes the
drawer after route changes, Escape closes it, and desktop collapse behavior
continues to use the existing preference.

Shared page headers gain a mobile-safe layout: titles remain visible, primary
actions stay reachable, and secondary actions wrap below rather than clip.
Dense transcript rows stack their speaker/status gutter above content on small
screens. Conversation and run-history panels remain available through compact
mobile toggles instead of disappearing.

All interactive controls touched by this work use at least a 44-pixel target on
mobile while retaining the existing denser desktop rhythm at `md` and above.

## Accessibility and resilience

Clickable conversation and run rows become native buttons. Selected rows use
`aria-pressed`, navigation links use `aria-current="page"`, and nested delete
controls remain independently operable.

The shared `Field` component assigns stable IDs to labelable controls and uses
`aria-labelledby` for composite groups. `CheckList` becomes a named checkbox
group. Signup fields and API-key secret fields receive explicit labels. Icon
links receive context-specific accessible names.

The authenticated content is a `main` landmark and active navigation links use
`aria-current="page"`. Chat history is a polite log; waiting states are status
messages; errors and approval requests are assertive alerts. Tool chips expose
running, completed, and failed text to assistive technology without causing
token-by-token announcement noise.

The nonfunctional Search & Run control is removed. Building a command palette
is outside this remediation, and leaving a dead shortcut would remain a false
affordance.

## Theme normalization

Theme colors are represented as RGB channel variables and referenced through
Tailwind color functions that include `<alpha-value>`. Existing names such as
`bg-danger/5` and `border-ok/40` then compile correctly in both themes.

A dedicated focus token provides at least 3:1 contrast against `--bg`,
`--panel`, and `--panel-2` in light and dark themes. Focus is rendered as a
two-layer outline so it remains visible beside borders. Reduced-motion mode
also disables smooth scrolling.

## Performance

Measure production chunks before and after. Keep route-level splitting, then
move Markdown rendering into a lazy component so the syntax-highlighting stack
does not block the empty-chat path. Extract memoized message and tool rows so a
streaming token updates the active response without rebuilding every completed
row. Avoid virtualization until measurement shows it is necessary.

## Tooling and tests

Add Vitest, Testing Library, jsdom, and jest-dom as frontend development
dependencies. Tests cover responsive shell semantics, keyboard-selectable
rows, accessible fields/groups, live states, active navigation, and focus/token
CSS invariants. Each production behavior follows a red-green cycle.

Add an ESLint 9 flat configuration using the already-declared TypeScript,
React Hooks, and React Refresh plugins. Final verification runs frontend tests,
lint, TypeScript/Vite production build, compiled-CSS assertions, and
authenticated in-app-browser checks at 320, 768, and desktop widths using the
provided test account.

## Success criteria

- Every audited core function is available at 320 CSS pixels without
  horizontal clipping or hidden-only-on-hover controls.
- All audited interactions are keyboard operable and expose names, roles,
  values, selection, and status changes.
- Focus indicators meet the 3:1 WCAG 2.2 requirement in both themes.
- Tailwind emits every alpha-modified design-token utility used by the app.
- The Home route's empty-chat load excludes Markdown/highlighting code, and
  completed message rows do not rerender for each streamed token.
- Tests, lint, and the production build exit successfully.
