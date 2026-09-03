# Obsidian — the Zugzwang design system

The tokens live in `chess-frontend/src/styles/obsidian.css`. That file is the
source of truth; this document explains the reasoning a stylesheet cannot
carry. If the two disagree, the stylesheet is right and this file is stale.

---

## 1. Philosophy

**Zugzwang teaches.** The board is the thing being taught, so the board is the
lit object and everything around it frames rather than competes. Any change
that makes a panel louder than the board is wrong even if the panel looks
better in isolation.

Three rules follow from that, and they decide most arguments:

1. **Colour is spent in one place.** Ice is the AI's voice — its pick, its
   narration, its focus ring — and nothing else may use it. Amber is thinking
   or pending. Red is destructive or failed. Green is verified. Four colours,
   four meanings, no decoration. A fifth accent is not a design decision to be
   made lightly; it is a claim that the app has a fifth kind of thing to say.
2. **Raised is lighter, in both themes.** This is why one set of elevation
   tokens serves light and dark, and why a component written against
   `--surface-raised` needs no theme-specific rules at all.
3. **A component states its appearance; its container states its layout.** See
   §9 — this one has cost the project real time twice.

A corollary that has already caught one bug: **a status colour must match the
status.** The unread dot on a rail tab was red, so a panel that had merely
updated read as a panel that had failed. New content is always the AI having
produced something, so the dot is ice.

**Light mode is a design, not an inversion.** It is the same room with the
lights on: a cool paper ground with white surfaces raised out of it. Flat white
everywhere is the failure mode — with no ground, elevation has to be faked with
borders and everything collapses into grey-on-grey.

---

## 2. Theming

Dark is the default at `:root`. Light applies in two cases:

| Situation | Selector |
|---|---|
| User chose light | `:root[data-theme="light"]` |
| User chose dark | `:root` (the default) wins |
| User chose neither, OS asks for light | `@media (prefers-color-scheme: light)` guarded on `:root:not([data-theme="dark"]):not([data-theme="light"])` |

`useTheme` (`src/hooks/useTheme.ts`) owns the DOM contract. `system` is a real,
persisted state rather than the absence of a choice, and it **removes** the
attribute rather than setting `data-theme="system"` — any attribute value would
suppress the CSS's OS branch.

**Component CSS must never branch on the theme.** Read a semantic token and get
the right answer in both. The two legitimate exceptions in the codebase are
`ThemeToggle.css`, which swaps which icon is shown, and the `--board-*` tokens,
which the board reads through an inline style.

---

## 3. Colour

A semantic colour keeps its **meaning** across themes but not its **value**.
Ice is `#7fd4ff` on near-black and `#0b6f96` on white — the same voice, legible
on each ground. `#7fd4ff` on white is 1.6:1 and unusable, so a system that
carried one value per meaning would be broken in one of its two themes by
construction.

Every semantic colour ships as a set of four, because contrast is a property of
a *pair* and the pair is what the token has to encode:

| Suffix | Use |
|---|---|
| `--x` | the colour itself: text, icon, or a solid fill |
| `--x-soft` | a tinted background for a badge, row, or panel |
| `--x-border` | a border on a soft background |
| `--x-contrast` | **the only** text colour legible on the solid fill |

Never put `--text-primary` on a solid `--ai` fill. Use `--ai-contrast`.

### Surfaces

| Token | Role |
|---|---|
| `--bg` | the page |
| `--bg-gradient` | the board's lit surround |
| `--surface-sunken` | wells, inputs, segmented-control tracks, the board frame |
| `--surface` | panels |
| `--surface-raised` | controls and cards sitting on a panel |
| `--surface-hover` / `--surface-active` | interaction states of a raised thing |

### Text

Checked against `--surface` in each theme, and both tiers match:

| Token | Dark | Light |
|---|---|---|
| `--text-primary` | 15.8:1 | 17.9:1 |
| `--text-secondary` | 8.1:1 | 8.6:1 |
| `--text-muted` | 4.6:1 | 4.9:1 |

`--text-muted` is at the AA floor deliberately. It is for metadata that a
reader skims past; it is **not** for body copy, and putting a sentence someone
must actually read into it is a bug.

---

## 4. Type

Three families, each with a job. Do not add a fourth.

- **Sora** (`--font-display`) — headings, figures, button labels.
- **Manrope** (`--font-body`) — everything read as prose.
- **JetBrains Mono** (`--font-mono`) — only where digits or notation must line
  up in a column: SAN, evaluations, clocks, accuracy percentages. Monospace
  used for atmosphere is the tell it is trying to avoid.

Scale: `--text-2xs` 11px through `--text-2xl` 32px, with `--text-base` 14px as
the app default. Line height is `--leading-tight` for headings, `--leading-snug`
for single-line controls, `--leading-normal` for prose.

**No uppercase labels, and no tracking above 0.02em.** Thirteen rules across
the two stylesheets used to set 9–13px uppercase tracked to 0.05–0.14em. That
treatment is the commonest generated-UI tell and it slows reading at exactly
the size that can least afford it. Weight and colour carry emphasis instead.

Prose is capped at `--measure` (68ch); the one-line body of an empty state is
capped tighter at 34ch.

---

## 5. Space

A 4px base, `--space-1` (4px) through `--space-16` (64px). Everything that
positions anything uses a step. The old CSS carried roughly 200 one-off pixel
values, which is what made the spacing rhythm read as accidental rather than
designed.

---

## 6. Radii, depth, elevation

Radii are tiered so the radius itself signals scale: `--radius-xs` 6px for
things inside a control, up to `--radius-lg` 16px for the board and panels.
`--radius-pill` is reserved for genuinely pill-shaped things — badges, toggles —
and is never applied to a plain button.

Elevation is `--elev-1` through `--elev-3`, with `--inset-1` / `--inset-2` for
recessed wells. The two themes build depth differently on purpose:

- **Dark** — cast shadow + hairline + a top inset highlight, because a raised
  surface in a dark room catches light on its top edge.
- **Light** — a tighter, low-opacity shadow and a slightly darker border. A lit
  room casts short shadows, and an inset white highlight is invisible on white.

Coloured glows (`--glow-ai`, `--glow-warn`, `--glow-danger`) mark AI state only.
They are much weaker in light mode, where a bright halo on white reads as a
rendering artefact rather than emphasis.

---

## 7. Motion

Three durations, one easing:

| Token | Use |
|---|---|
| `--dur-fast` 0.14s | hover, press — must feel instant |
| `--dur-base` 0.22s | colour, shadow, state changes |
| `--dur-slow` 0.32s | layout: panels, drawers, the theme swap |

Motion answers an action or shows what changed. It never runs on its own to be
noticed. Animate the properties that actually change — `transition: all` drags
every layout property along and turns a colour change into a visible reflow.

`prefers-reduced-motion` is honoured **globally** in `obsidian.css`. It was
previously scoped to `.chess-container`, which meant the entire sandbox ignored
it. Do not re-scope it.

> Historical note: `--cp-t-base` was defined as `var(--cp-t-base)` — a
> self-reference, so it resolved to nothing and silently killed 13 transitions
> including every `.action-btn` state change. It is `0.22s` now.

---

## 8. The board

Square colours are theme tokens (`--board-light`, `--board-dark`), passed
through `customLightSquareStyle` / `customDarkSquareStyle` as literal
`var(...)` strings. react-chessboard writes those into an inline style and a
custom property resolves there, so the board re-colours on a theme switch with
no re-render and no listener.

A board tuned for a near-black room is muddy on paper, so the values differ.
The light board is `#dbe3ec` / `#8296ac` — deliberately not paler: the white
pieces are a near-white fill with a thin outline, and on a very light square
they had almost no contrast.

Move feedback (`--sq-selected`, `--sq-legal`, `--sq-capture`, `--sq-last`,
`--sq-check`) uses amber/green/red, **not** ice. Ice means the AI is speaking;
it does not mean a square is selectable.

The board is the primary visual anchor and sizes with the viewport (460–620px),
capped by viewport height so the player strips are never pushed off screen.
`ChessBoard.tsx` computes it and publishes it as `--board-size`, which
`.chess-board-wrapper` reads — the frame and the board it frames cannot
disagree.

---

## 9. Component conventions

**A component states its appearance; its container states its layout.**

`.action-btn` declares no `width`, no `flex`, and no grid placement. Only the
container knows whether a button shares a row with three others or sits beside
a text input. Baking `flex: 1` and later `width: 100%` into the class is what
let a button reach out and collapse an unrelated sibling — the chat input to
28px, then the sandbox prompt input to 30px, the same bug twice in two
components. Both were then patched with two-class overrides that existed purely
to undo the class. A button cannot cause that bug if it never states how much
room it wants, so all of it is gone: the class is appearance-only, and
`.action-buttons` / `.mode-buttons` declare the two-column grid themselves.

If you find yourself writing a more specific selector to *undo* a property, the
property is in the wrong place. Move it, do not out-specify it.

### One job, one treatment

Two controls that do the same job look the same. The header's `Play` / `Learn`
switch and the analysis rail's `Coach` / `Review` / `Progress` / `Chat` /
`Board` tabs both answer "which view am I looking at", and both are the same
segmented control: a sunken track with the active option raised out of it. They
used to be a track and a row of free-floating pills, which read as two design
systems sharing a page.

### States

Every interactive surface needs hover, active, focus, and disabled. Focus is
one global `:focus-visible` treatment in the AI accent, because focus is
attention.

Empty, loading and error are states of a panel, not afterthoughts. `EmptyState`
handles the first two: a short title naming the situation and one line saying
what will fill it, centred in the panel — a sentence pinned to the top of 300px
of nothing reads as content that failed to load. `tone="thinking"` switches it
to amber with a live region, so a screen reader is told rather than left
watching a still panel.

### Writing

Name a control by what it does when pressed, and keep the name through the
flow: the button that says **Publish** produces a toast that says **Published**.
Sentence case. Errors say what happened and what to do; they do not apologise
and they are never vague. An empty screen is an invitation to act.

The header's mode control is the worked example. It used to be one button
labelled with its *destination* — `Learner Mode` / `Back to game` — so the mode
you were actually in never appeared on screen; you inferred it from the label of
the control that would leave it. It is now a two-option segmented control,
`Play` / `Learn`, with the active option marked by `aria-current` so the
accessible name and the visual state cannot drift apart.

---

## 10. Accessibility

- Contrast: every text tier is AA or better in **both** themes (§3). Check the
  pair, not the colour.
- `:focus-visible` is global and must stay visible on every surface, including
  coloured fills.
- Semantic elements: a thing that does something is a `<button>`. `aria-current`
  marks a selected option; `aria-pressed` is for a genuine on/off toggle and
  is deliberately absent from `ThemeToggle`, where light and dark are two equal
  states and "pressed" would imply dark is the "on" one.
- Icon-only buttons need a real label naming the destination, and are only
  acceptable where the glyph is genuinely universal. Sun/moon qualifies; almost
  nothing else in this app does.
- `prefers-reduced-motion` is global.

---

## 11. Responsive

Reference breakpoints (CSS cannot interpolate a variable into a media query, so
these are documented here and written literally):

| | |
|---|---|
| 640px | phone |
| 900px | tablet / small laptop — panels begin stacking |
| 1200px | laptop — the design target |
| 1440px | desktop |

The board must stay usable at every width; panels collapse, stack or become
drawers around it. Small screens are not the desktop layout scaled down.

---

## 12. Migration status

The `--cp-*` names are a **seam, not part of the system**. They alias the
semantic tokens so that 73 KB of existing component CSS became theme-aware
without being edited, letting components migrate one at a time with the app
working at every step instead of in one unverifiable rewrite.

New CSS uses the semantic names. As a component migrates, its `--cp-*` uses go
with it and the alias block shrinks. `--cp-hi` / `--cp-sh` are the exception:
they are consumed as raw `R, G, B` triples, so they cannot alias a hex token and
remain declared per theme.

---

## 13. Verifying a change

The rendered application is the judge. A typecheck proves nothing about layout,
and every UI bug this project has found was found by driving the live app.

- Check both themes. A change that only looks right in dark is half done.
- Check the widths of anything sharing a row with an input (§9).
- Check the console. It should be silent.
- Run axe (`wcag2a`/`wcag2aa`/`wcag21a`/`wcag21aa`) over both themes in both
  modes. The bar is zero violations, and it is currently met.
- Never dim a token with `opacity` to make it quieter. The muted tier is
  already checked against its ground; stacking opacity on it is how the only
  contrast failure in the app got in (4.03:1 dark, 3.43:1 light). Pick a
  different token instead. The one legitimate exception is the board
  coordinates, which sit on squares of two different colours and so have no
  single correct foreground.
- Wait out `animationDuration={300}` before asserting on board DOM, or you will
  read the *previous* position and think the move did not apply.
