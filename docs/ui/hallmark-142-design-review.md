# UI-06 — Hallmark-inspired Banana Noir refresh (Draft PR #142)

Date: 2026-11-10
Reference: [Nutlope/hallmark](https://github.com/Nutlope/hallmark), skill v2.0.0 (MIT).
Target: `web-prototype/galaxy-spatial-canvas`, building on the existing connected UI and Studio launch flow.

## Product and audience

BananLoop is a local research workbench for a developer or researcher who wants to define a measurable goal, explore hypotheses, launch a bounded campaign and inspect *real* evidence. The design should be recognizable as this product rather than as a generic chatbot, SaaS landing page or artificial metrics dashboard.

The strongest product signature is the loop itself: question → hypothesis → independent check. In the empty Studio screen it becomes an understated orbital diagram, not a simulated research status.

## Existing UI review (code-level, not a screenshot audit)

- Studio: consistent chat-first flow, but a generic centred heading and stock blue-panel treatment made it visually interchangeable with other chat products.
- Home / Mission Control / Galaxy: inconsistent surface colours, uneven typography, many tightly spaced metadata labels and different treatments for the same brand accent.
- Mobile: the initially expanded chat sidebar could permanently squeeze a narrow viewport; the new behaviour starts collapsed and uses a dismissible overlay.
- Accessibility: visual states needed a single focus-visible style across shell and workbench; motion preferences must also affect programmatic chat scrolling.

## Design decisions

- **Direction:** custom Banana Noir, warm graphite surfaces with one yellow accent; restrained technical typography instead of gradients and multiple bright status indicators.
- **Hierarchy:** research question first in empty Studio; real dialogue in the transcript; explicit review / launch remains a secondary deliberate step.
- **Composition:** off-centre empty workbench with a CSS-only orbit, no fake data and no new backend state. The visual is hidden on narrow screens where useful content is more important.
- **Density:** conversations use a generous readable measure; navigation and metadata are visually quieter; the existing side panel preserves goal, evaluator gaps, budgets and confirmation.
- **System:** theme tokens in `src/hallmark-refresh.css` loaded after legacy styles for an isolated, reversible design pass. No API, Python Engine, launch restrictions or evaluator handling changed.

## Preservation boundaries

Every existing route, handler, ID, form, and launch confirmation remains wired. The chat still calls the hardened no-tools Codex bridge. Galaxy still presents projected Engine data rather than invented edges or outcomes. The frontend never gains authority to change the Engine or access validation/HOLDOUT contents.

The work is deliberately limited to UI layout, colour, typography, accessibility and responsive behaviour. It is **not** a claim of BananLoop v0.2 integration or production readiness.

## QA and acceptance

Automated frontend gates in `.github/workflows/studio-ui-smoke.yml` run model tests, React mount tests, TypeScript checks, Vite build and Studio worker tests. A mobile sidebar interaction regression is added to `tests/studioMount.test.tsx`.

Before approving the design, visually inspect these real app states at 1440 × 900, 1280 × 800, 768 × 1024 and 390 × 844:

1. Empty Home; disconnected local API; one real campaign in the index.
2. Empty Studio; chat with user/assistant turns; slow Codex response; retry after Codex failure.
3. Plan ready; missing evaluator; Full Auto review; Supervised confirmation; active / failed / needs-input launch.
4. Galaxy with a real committed graph; node inspector and selected personal arrow.
5. Keyboard tab order and visible focus; reduced-motion; sidebar close/reopen; long goal or long chat text.

No real authenticated Codex chat or browser screenshot was executed in this design edit, and the external Hallmark browser lint has **not** been run yet. Do not call the interface visually certified on the basis of a green TypeScript build alone.

For repeatable local Hallmark checks (Node 18+, browser for render mode):

```sh
npx skills add nutlope/hallmark --agent codex
# In a checkout of Hallmark, with Chrome / puppeteer-core available:
node skills/hallmark/scripts/sloplint.mjs /path/to/BananLoop/web-prototype/galaxy-spatial-canvas --render --scope app
```

Use `$hallmark audit` / `$hallmark redesign` from a local Codex environment with the installed skill for further review. This external skill is not vendored into BananLoop, so it cannot silently change the production dependency graph.

## Follow-up design questions

- Is the orbit illustration restrained enough, or should the workbench be strictly typographic?
- Should Galaxy be the primary navigation destination, or should Home/Studio remain primary until there is a real campaign?
- A live research transcript with multiple experiments still needs a user-validated information hierarchy once v0.2 Engine integration is ready.
