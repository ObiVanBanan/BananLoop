# Visual direction — BananLoop Research Preview

This document captures the reference layout for **future README artwork and a possible standalone landing page**. It is an aesthetic handoff, **not** evidence that those UI features already ship.

## Visual identity

- **Mascot:** approachable banana explorer/astronaut with a friendly expression (no aggressive stance or streetwear). On a small moon, working on a laptop, with a few technical props.
- **Setting:** 16-bit / pixel-art universe; textured planets, orbit paths, small spacecraft and ASCII-terminal details. Space is a metaphor for exploration across many kinds of software projects, **not** a claim that the product is exclusively a research assistant.
- **Palette:** night background `#07111D`, navy panels `#0B1B2B`, banana-yellow `#F4BD35`, gold highlights `#FFCF57`, cool-muted body text `#B9C9D8`, success green `#29C77E` only for verified success.
- **Type:** bold block/pixel headings for marketing art; readable monospace for code/terminal; normal high-legibility font for long docs.
- **Hero copy:** `BANANLOOP_` / `Autonomous AI Engine` / `Fresh context. Persistent memory. Measurable progress.`
- **Loop strip:** `PROPOSE → SELECT → IMPLEMENT → EVALUATE → LEARN → REPEAT`. For technical architecture also show hard gates, independent evaluator, and the champion decision.
- **Tone:** developer tool, energetic but precise. Avoid inflated benchmark claims.

## Content hierarchy inspired by the mockup

1. Hero banner.
2. Honest version, supported Python, MIT license badges.
3. Short product promise and six real capabilities.
4. **Recorded** terminal demonstration and **recorded** fixture metric table, not an animated fictitious live run.
5. How the loop works, with a text-accessible architecture.
6. Quick start with Linux/macOS and Windows commands (or links to the tested CLI reference).
7. Use cases and clearly separated **shipped / experimental / planned** roadmap.
8. Security and limitations: reproducibility, trusted environments, DEV vs validation/holdout.

## Fidelity and implementation boundaries

GitHub README renders Markdown and a restricted subset of HTML. It does **not** host custom CSS/JavaScript tabs, interactive charts or full GitHub navigation layouts. Implement the realistic subset in README; any interactive landing page belongs in a separate deployable site and must be labelled as such.

Stars/forks/watchers cannot be fabricated. The mockup's numerical progress line, terminal output and tabs are **illustrations** until backed by real recorded artifacts and working interactions.

Avoid writing `pip install bananloop` unless a distribution has been successfully published and tested; the current installation route is clone + `pip install -e .`.

## Art delivery and review

The owner will manually upload and approve the replacement hero image. Until that file exists in the repository, keep README pointing to the existing committed `assets/bananloop-banner.svg` to avoid a broken image. Do not silently overwrite an image on behalf of the owner.

Suggested exported hero ratio: around **1600:450** (wide), dark- and light-GitHub-safe with sufficient text contrast; also verify the image at **mobile widths**. Source image may be raster PNG/WebP; use SVG only when the asset is actually vector, not when simply wrapping a raster.

Review visuals for trademark rights, accessible alt text and factual claims before final promotion.
