# Implementation Plan: Soundboard UI Revamp (Top Deck Popover & Modern Responsive Cards)

**Date:** 2026-10-28  
**Scope:** Fix top bar overflow at medium/narrow resolutions, eliminate audio card letter slicing/crowding, and introduce container-query responsive card grid with audio routing popover.

---

## 1. File Structure & Responsibilities
- `ui/index.html`:
  - Replace inline static routing controls (`.routing-controls`) with a trigger pill (`#btnRoutingPopover`) and a floating popover card (`#routingPopoverMenu`).
  - Keep all device selects (`cableDeviceSelect`, `monitorDeviceSelect`), sliders, labels, and toggles inside the popover with identical IDs so `main.py` and `app.js` maintain 100% backward compatibility.
  - Simplify `#btnReplayClip` text structure and deck action containers.
- `ui/app.js`:
  - Add popover show/hide/toggle behavior, click-outside handling, and Esc key dismissal.
  - Add summary text updater on `#routingStatusSummary` when volumes change.
  - Refactor `renderGrid()` card generation:
    - Omit timestamps (`Hoje 13:45`) and default `"Normal"` mood badges from card footer.
    - Set `.card-actions` to appear on hover / focus / active.
    - Wrap duration in `.card-duration` and ensure title has safety padding and line-height.
- `ui/styles.css`:
  - Style `.routing-pill-wrapper`, `.btn-routing-pill`, and `.routing-popover` with backdrop blur, borders, and shadows.
  - Add CSS Container Queries (`container-type: inline-size`) on `.sound-card`.
  - Fix typography slicing (`line-height: 1.35; padding-bottom: 2px; overflow-wrap: anywhere; text-wrap: balance;`).
  - Update `.sound-grid` and density modes with fluid `minmax` ranges (`minmax(130px, 1fr)`).
  - Clean up media queries for breakpoints (`<= 1100px`, `<= 768px`, `<= 500px`).

---

## 2. Step-by-Step Execution Plan

### Task 1: Refactor Top Deck Routing into a Floating Popover
- **Step 1.1**: Update `ui/index.html`:
  - Wrap the routing controls in `#routingPopoverMenu` with a trigger button `#btnRoutingPopover`.
  - Preserve all element IDs (`cableDeviceSelect`, `cableVolumeSlider`, `cableVolumeVal`, `monitorDeviceSelect`, `monitorVolumeSlider`, `monitorVolumeVal`, `micPassthroughToggle`, `overlapToggle`, `autoSplitToggle`).
- **Step 1.2**: Update `ui/app.js`:
  - Add event listeners for `#btnRoutingPopover` toggle, `#btnCloseRoutingPopover` close, click-outside dismissal, and Escape key dismissal.
  - Add helper function `updateRoutingSummary()` to keep the pill text in sync (e.g. `Cable: 100% | Fone: 75%`).
- **Step 1.3**: Update `ui/styles.css`:
  - Add styling for `.btn-routing-pill` and `.routing-popover` (fixed/absolute positioning below header, blur background, z-index 150).
  - Verify that `#btnRoutingPopover` is compact (~160px) and never pushes `#btnStopAll` or `#btnMiniOverlay` off-screen.

### Task 2: Card Architecture & Typographic Fixes
- **Step 2.1**: Update `ui/app.js` `renderGrid()`:
  - Clean card template:
    - Remove `.time-badge` from card footer.
    - Show `.mood-badge` only if `acoustic_mood` is not `"Normal"`.
    - Retain speaker badge, hotkey, duration, emoji, title, and action buttons.
- **Step 2.2**: Update `ui/styles.css`:
  - Add `container-type: inline-size; container-name: soundcard;` to `.sound-card`.
  - Fix `.card-title` with `line-height: 1.35; padding-bottom: 2px;` to prevent font descender slicing.
  - Set `.card-actions` default opacity to `0` on non-hover, fading in on `.sound-card:hover` or `.sound-card:focus-within`.
  - Set `.card-duration` font styling with tabular numbers.

### Task 3: Container Queries & Grid Fluidity
- **Step 3.1**: In `ui/styles.css`:
  - Redefine `.sound-grid`: `grid-template-columns: repeat(auto-fill, minmax(130px, 1fr)); grid-auto-rows: minmax(92px, auto); gap: 8px;`.
  - Add `@container soundcard (max-width: 145px)`:
    - Hide `.card-transcript`.
    - Adjust title font size to `12px`.
    - Compact padding `6px 8px`.
  - Add `@container soundcard (min-width: 210px)`:
    - Show single-line clamped `.card-transcript`.
- **Step 3.2**: Refine media queries:
  - `@media (max-width: 1100px)`: Clean single-row header.
  - `@media (max-width: 768px)`: Hide sidebar, search bar stacks nicely in 2 rows.
  - `@media (max-width: 500px)`: Streamlined mini-deck layout.

### Task 4: Verification & Test Suite
- **Step 4.1**: Run pytest test suite to ensure backend and store compatibility.
- **Step 4.2**: Verify layout rendering and no JS syntax errors in pywebview.
