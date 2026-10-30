# App

Installable web app (PWA) for RepoCity RadarAuditor, deployed to Cloudflare Pages at app.radarauditor.speedofthespirit.dev: website check, evaluation with copyable fixes, report PDFs, quotes with prices from the CRM and the optimisation of an order. Invoices, payments and every outgoing mail belong to the CRM; **Angebot abschicken** hands the quote to `crm.send_quote`. Built with Vite, Preact, TypeScript, Supabase and Chart.js. Phone first: bottom navigation below 768 px, side navigation above.

## Development

```
cp .env.example .env.local   # fill in VITE_SUPABASE_URL and VITE_SUPABASE_ANON_KEY
npm install
npm run dev
```

Sign in with the operator account created in Supabase under Authentication, Users. Without the two variables the app shows a setup notice instead of starting.

| Command | Does |
|---|---|
| `npm run dev` | Development server with hot reload |
| `npm run check` | Type check (`tsc --noEmit`) and ESLint |
| `npm test` | Unit and component tests (Vitest) for scoring, grid, rankings, quotes and sending, formats |
| `npm run build` | Production build into `dist/` including service worker and manifest |
| `npm run icons` | Regenerates `public/favicon.svg` and `public/icons/*.png` from `src/brand/mark.json` |

The font file is not in the repository yet; see `public/fonts/README.md` and [SETUP.md](../Dokumentation/SETUP.md#6-the-archivo-font).

## Screenshots for the manual

`uv run python scripts/screenshots.py` (from the repository root) builds the app against a demo Supabase address, serves it locally, answers every Supabase call with German demo data and writes phone (390 × 844) and desktop (1280 × 800) pictures to `Dokumentation/bilder/`. `--font <file.woff2>` serves a font file as Archivo while `public/fonts/` has none. Needs Playwright with Chromium (`uv run playwright install chromium`).

## Deploy to Cloudflare Pages

| Setting | Value |
|---|---|
| Root directory | `app` |
| Build command | `npm run build` |
| Build output directory | `dist` (that is `app/dist` from the repository root) |
| Environment variables | `VITE_SUPABASE_URL`, `VITE_SUPABASE_ANON_KEY`, `NODE_VERSION=22` |

Pages serves `index.html` for unknown paths, so deep links like `/kunden/<id>` work. `public/_headers` sets the security headers; its Content-Security-Policy allows connections to `*.supabase.co` only, so a self-hosted Supabase under another domain needs that line extended.

## Open requests

For the administrator, Prüfen shows the requests from the form on repocity.speedofthespirit.dev/anfrage/ (`crm.open_audit_requests`) as a closed line below the button, only while there are any: company, contact, kind of meeting, preferred slot in German time, website. Tapping a request with a website puts it into the address field; the check starts as usual with **Prüfen**. A request without a website offers no check.

## Hand-off to the worker

The app writes rows; the Python worker does the rest. The conventions that are not visible in the schema: settings the worker reads are described at the top of `src/api/queries.ts`; the audit-plus-quote PDF is requested as a `documents` row of kind `audit_quote` with `file_path` null and data `{audit_id, quote_id, quote_number}` (`src/lib/offerDocs.ts`); quotes are sent through `crm.send_quote` once that PDF exists (`src/lib/quoteSending.ts`).
