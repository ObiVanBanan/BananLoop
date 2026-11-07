# BananLoop — Galaxy Design Exploration (2026-11-07)

This is a **synthetic, visual-only study** following the UI-01.2 review. It does not run BananLoop, change its graph, call a provider, or use a real campaign. Engine semantics are preserved by keeping research dependencies immutable and annotations separate.

## Open it

**From GitHub**: open `index.html`. It is self-contained (HTML/CSS/JavaScript in one file) and needs **no npm installation or network**. **From the downloadable ZIP**: open `BananLoop_Galaxy_Design_Exploration.html`, or the split `index.html` next to `styles.css` and `app.js`.

### Three directions

| Variant | Design intent | Trade-off |
| --- | --- | --- |
| **01 Atelier** | warm, editorial, minimal, light; largest typographic contrast | less distinctive as a cosmic product |
| **02 Banana Noir** | dark navy, subtle ASCII motion, banana-gold accents, modern data cards | requires careful contrast at overview zoom |
| **03 Signal Room** | technical CRT-grid, bright green, square-edged console | more intense; can fatigue in long sessions |

Switch between them using **Appearance** in the top toolbar. This switcher exists for design comparison only and would not be in the finalized UI.

### Interactions

- **Wheel** over Galaxy zooms under the mouse pointer; **drag empty area** to pan.
- **Drag any research node or AND/OR gate** to rearrange. Edges update; only the viewport layout changes.
- **Double click empty canvas** to add a personal note. Or use `Note`.
- **Arrow** -> select source -> target to draw a **dashed user annotation**; solid edges are immutable research dependencies.
- **Fit**, **F**, and zoom +/- adjust the camera. The mini-map supports click-to-navigate.
- **Collapse the campaign rail** via its top arrow; **drag its right border** to resize on desktop.
- **Select a node** to open an animated Inspector. Status help explains `READY` vs `COMPLETED` vs `PENDING`.
- The Settings dialog explains future Codex CLI / ChatGPT subscription access versus API billing; no fake connection or credential entry is implemented.
- Layout, theme, notes, and personal arrows are saved in local browser storage where browser security allows it.

### Interaction scope and honesty

- The displayed `RAG Quality Boost` graph is an **illustrative fixture**, not a canonical snapshot.
- Research edges are encoded separately from personal arrows. Dragging nodes **never alters** Engine dependency meaning.
- The mock provider settings contain **no active connection**, token storage, or sign-in UI.
- The original `/v1/campaigns/{id}/graph` v2 response lacks the complete safe topology required for production; wait for a read-only Graph DTO.
- This artifact does **not** include an actual installed React Flow or shadcn runtime. React Flow source-only migration spike lives in `react-flow-spike/`, unbuilt because npm registry access was unavailable.

## Technical files

- Repository `index.html`: self-contained working no-dependency reference implementation. The ZIP additionally includes split `index.html`, `styles.css`, and `app.js`.
- `BananLoop_Galaxy_Design_Exploration.html`: standalone ZIP artifact.
- `react-flow-spike/`: React Flow implementation starting point and source-owned shadcn-style Button.
- `DESIGN_REVIEW.md`: comparison, critique, implementation recommendation.
- `qa_browser.py`: screenshot + interaction checks on Chromium (using inline content to work around restricted file navigation).
- `qa_persistence.py`: localStorage rehydration simulation on an opaque test origin.
- `Design_Comparison.png`: three-theme contact sheet (included in the downloadable ZIP; screenshots are not checked in to this draft PR).

## QA executed

- Chromium headless with Playwright: **8 research cards, 2 logic gates, existing note**, theme switch, drag, mousewheel zoom, note, arrow, sidebar collapse, settings/status dialogs, viewport widths 1440/1024/768/1920 and no document horizontal overflow.
- Local-storage restoration logic tested with an injected same-origin-style storage shim because Chromium in this environment blocks local file and host navigation.
- JavaScript syntax check passed (`node --check app.js`); no page exceptions in smoke test.
- **Not tested:** real browser file-origin storage, external service, real Engine API, hosted CI, npm/Vite build, actual React Flow integration, performance at 100/300 nodes.

## Next integration slice

After picking an approved direction, migrate production Galaxy to `@xyflow/react` and shadcn UI (or source-owned equivalents); implement user annotation persistence independently of immutable Engine graph. Test large graphs and retain independent evaluator isolation. Do not merge the unbuilt spike directly into production without a network-enabled install and typecheck.