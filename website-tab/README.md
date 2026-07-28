# Market Summary — website tab

A self-contained **Next.js App Router page** that adds a "Market Summary" tab to
an existing site: browse the bot's daily briefings by date — market tone +
sentiment-trend chart, Sector Watch, Positioning & Regime, market snapshot, top
gainers/news, the full analyst briefing, and a per-date PDF download link.

Built for: **Next.js 16 (App Router) + React 19 + TypeScript**, client
components reading **Supabase** directly via `@supabase/supabase-js`. No route
handlers, no server code, no Python in the site.

Verified: compiles and prerenders clean on Next 16.2 / React 19.2 (strict TS).

---

## Integrating into the existing site (2 steps)

1. **Copy the folder** `app/market-summary/` into the site's `app/` directory.
   Everything it needs lives inside it (components, data layer, styles, sample
   data) — all imports are relative, so no alias/config changes.

2. **Install the one dependency** (if the site doesn't already have it):

   ```bash
   npm install @supabase/supabase-js
   ```

That's it — `/market-summary` now works. Add a nav link to `/market-summary`
wherever the site keeps its tabs. Deep links work too: `/market-summary?date=2026-07-24`.

## Data modes

The page picks its data source **once, from env vars**:

| Mode | When | What it reads |
|---|---|---|
| **Sample** (default) | `NEXT_PUBLIC_SUPABASE_URL` unset | Bundled `lib/sample-data.json` — 12 days of REAL briefings/quotes/positioning exported from the bot's history, plus clearly-fake sector-watch / news / gainers rows (those tables have no historical store; real rows accumulate once the pipeline writes to Supabase). The header shows a "sample data" badge. |
| **Live** | Both env vars set (locally in `.env.local`, on Vercel in project env settings) | The Supabase tables in `schema.sql`. |

```bash
# .env.local / Vercel env — flips the tab live; no code change
NEXT_PUBLIC_SUPABASE_URL=https://<project>.supabase.co
NEXT_PUBLIC_SUPABASE_ANON_KEY=<anon public key>
```

The sample JSON is loaded via dynamic `import()`, so live deployments don't
ship it to the browser.

## Going live later (when Supabase is set up)

1. Create a Supabase project → SQL editor → run **`schema.sql`** (tables +
   public-read RLS; writes require the service-role key, which only the Python
   job will hold — the site's anon key can only SELECT).
2. Set the two `NEXT_PUBLIC_*` env vars on Vercel and redeploy.
3. Point the Python pipeline at Supabase (a `supabase_sync.py` step in the
   bot repo — not part of this folder) so each daily run upserts one batch of
   rows per session date, and backfill history once.

## Design notes

- **Theming**: every color flows through CSS custom properties defined once at
  the top of `page.module.css`, with dark values under both
  `prefers-color-scheme` and a `[data-theme="dark"]` scope (a site theme toggle
  wins in both directions). Reskinning to the site's brand = editing that one
  token block.
- **Chart**: dependency-free inline SVG line chart with crosshair + tooltip,
  keyboard navigation (arrows / Enter to open a day), click-to-open-date, and a
  "view as table" fallback so no value is hover-gated.
- **Markdown**: the briefing prose renders through a minimal built-in renderer
  (headings, lists, quotes, tables, bold, links) — React-escaped, raw HTML
  stripped, so headline text is inert. Swap in `react-markdown` later if needed.
- **Missing data degrades per-section**: a date with no sector-watch rows just
  omits that card; a date with no briefing shows an empty state.
- The **PDF link** points at the public GitHub repo's committed
  `summaries/market_summary_<date>.pdf`.

## Folder map

```
website-tab/
├── README.md                ← this file
├── schema.sql               ← Supabase contract (run when project is created)
└── app/market-summary/
    ├── page.tsx             ← the tab (client component)
    ├── page.module.css      ← all styles + theme tokens
    ├── components/
    │   ├── DateNav.tsx          date select + prev/next
    │   ├── ToneCard.tsx         market-tone stat tile
    │   ├── TrendChart.tsx       sentiment line chart (SVG)
    │   ├── Markdown.tsx         minimal briefing renderer
    │   ├── SectorWatchTable.tsx
    │   ├── PositioningTable.tsx
    │   ├── SnapshotTables.tsx   quotes by section
    │   └── GainersNews.tsx
    └── lib/
        ├── types.ts             row shapes (mirror schema.sql)
        ├── data.ts              supabase reads + sample fallback
        └── sample-data.json     real history export for demo mode
```
