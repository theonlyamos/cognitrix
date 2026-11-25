---
name: design-drafts
description: >
  Make design drafts for a screen, feature, app, page or redesign: two or three distinct options drawn on
  one comparison artifact (key states, light and dark, phone and desktop), checked with screenshots,
  with a recommended pick and a "which one?" question; then build only the pick. Use whenever the user
  asks for designs, mockups, wireframes, drafts, layouts, UI ideas or a redesign.
args:
  - name: request
    description: What to design, in the user's words
    required: true
tags: [design, drafts, mockup, ui, ux, redesign, artifacts]
category: design
version: "1.0.0"
author: cognitrix
safety:
  risk-level: low
---

# Design drafts

The user wants: "$(arg request)"

Show options before building anything. The person picks from real pictures, not from a description,
and nothing gets built that they would have to undo.

## 1. Look first

- Find out what exists: read the project's current screens, components, theme colours, fonts and spacing
  (the working folders, the repository, screenshots the person shared, or the live app). Drafts should
  look like they belong in that product, using its real colours and words, unless the person asked for
  something new.
- Work out the key states the design must handle. Usually: the normal filled state, empty, loading or
  working, an error, and the main interaction (a menu open, a dialog, a hover or a pressed button).
  Pick the three to five that matter most for this request.
- Ask only if a wrong guess would waste the whole round (for example, you can't tell which screen they
  mean). Otherwise assume, and say what you assumed on the page.

## 2. Make two or three really different options

- Each option is a different idea, not a colour swap: a different layout, a different way of showing
  the information, or a different interaction. Name them A, B and C with a short descriptive name
  ("A: Side panel", "B: Inline cards").
- Each option shows its key states on both a **desktop** frame (about 1280 wide) and a **phone** frame
  (390 wide), in **light and dark**. Use realistic content, never lorem ipsum.
- Under each option, write one or two lines: what it's good at and what it gives up.

## 3. Put them all on one comparison artifact

Publish ONE artifact (Publish Artifact, format "html") titled like "<Feature> drafts".

- Draw every screen inline in the page itself, as fixed-size boxes (a desktop box 1280x800 and a phone
  box 390x844, scaled down with `transform: scale()` inside a wrapper of the scaled size). **Never use
  iframes**: many viewers show them as empty boxes.
- Lay it out as: a short intro (what this is for, what you assumed), then one section per option with
  its states side by side (desktop and phone, light and dark), then a "Recommendation" section.
- Show light and dark by drawing each screen twice with the theme's colours set on the box (for example
  `.light{--bg:#fff;--fg:#111}` and `.dark{--bg:#111;--fg:#eee}`), so both are visible at once whatever
  the viewer's own theme is. The page around them follows `prefers-color-scheme`.
- Add `<meta name="viewport" content="width=device-width,initial-scale=1">`. On a phone the frames
  stack in one column and fit the width.
- Mark your recommended option clearly, with one line on why.

## 4. Check it before you share it

Run **Check Artifact** on it (all four views). Look at every screenshot:
- Every option and state is there and drawn, nothing blank or cut off.
- Text is readable in both light and dark frames, and on the page itself in dark mode.
- Nothing overflows sideways on the phone view.
- No script errors.
Use `scroll_y` to see further down a long page. Fix what's wrong, publish again with the same
artifact_id, and check again. Only share it once it looks right.

## 5. Ask which one

- Reply in two or three lines: what you made and which option you recommend, and why.
- Then ask with **Ask User**: "Which one should I build?", one option per draft (A, B, C) with its short
  name, your recommended one set as recommended_option_id, and free text allowed for "A but with B's
  header" style answers.
- Don't build anything until they answer. If they ask for changes instead, update the same artifact
  (same artifact_id, a note on what changed), check it again and ask again.
- Save the choice to memory when it is a lasting preference (for example "prefers dense layouts").

## 6. Build only the pick

- Build the chosen option, with the changes they asked for, and nothing from the options they didn't pick.
- Match the drafts: same layout, states, words, and both themes and both screen sizes.
- When it is built, look at the real thing (Check App for a Cognitrix app, Check Artifact for a page,
  screenshots of the running app otherwise) in light and dark, phone and desktop, and show the person
  the finished result with Share File or a screenshot.
