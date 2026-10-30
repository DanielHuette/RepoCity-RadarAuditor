# Project page

Static page published on Cloudflare Pages (project `radarauditor`) at https://radarauditor.speedofthespirit.dev, German at `/`, English at `/en/`. No build step, no external requests; the Archivo font is self-hosted (OFL, see `fonts/LICENSE`).

Deploy from the repository root with a Cloudflare token that may edit Pages:

```
npx wrangler@4 pages deploy site --project-name radarauditor --branch main
```
