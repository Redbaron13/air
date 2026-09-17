# Baron Aerial Media — Full Site Audit & Launch Plan
**Repo:** github.com/Redbaron13/baronaerialsite1-2pub · **Live:** baronaerial.com
**Audited:** 7 September 2026 · **Launch target:** 8 September 2026

---

## 0. Access, method, and the one-line verdict

Both the repository and the live site were fully accessible. Nothing was blocked.

**Method.** Full source read of `src/`, `server/`, `public/`; byte-hash comparison of every media file; a headless Chromium crawl of all 33 live routes at 1440px, 834px (tablet) and 390px (phone); no-JavaScript render test; network weight measurement; accessibility probes; and a live end-to-end submission of the contact form.

**Verdict.** The writing, the domain expertise, and the underlying concepts here are genuinely strong — the LAANC/Part 107/SGI material is better than almost anything a competitor in North Jersey is publishing, and the Kuzuri Kijiji mapping packet is a real credential. But the site as deployed has four defects that each independently undermine the launch, and all four are cheap to fix:

1. **The homepage hero photograph does not render at all** — desktop, tablet, phone, light theme, dark theme. A drone photography company's front door currently shows a black (or cream) rectangle.
2. **A phone can't open the navigation menu on the homepage.** A CSS sizing trap pushes the hamburger button ~470px off-screen.
3. **A third-party script from `grok.com` loads on every page**, alongside a Grok PWA manifest and Grok icons. This is the app-builder's scaffolding shipped to production.
4. **Every page shares one identical `<title>`**, there is no canonical tag, no structured data, no sitemap, and the site's own stylesheet 404s on first paint.

Plus the thing you already suspected: **the images do not match the copy**, and I can tell you exactly why in code. It is not a content problem. It is a missing architectural layer.

---

## 1. The image ↔ copy mismatch — root cause analysis

This is the issue you flagged, and it is worse than a few wrong pictures. Every mismatch traces to the same five structural causes.

### 1.1 Cause A — there is no asset metadata layer

In `src/data/site.ts`, an image is bound to copy as a bare path string:

```ts
{
  slug: "real-estate",
  name: "Real Estate Aerial Media",
  summary: "Cinematic listing films, twilight stills, and neighborhood context…",
  image: "/media/neighborhood-gold.webp",   // ← nothing describes what is IN this file
}
```

The file has no record of what it depicts. The *only* description of any photograph is whatever prose happens to reference it. So the same file referenced from three places acquires three different, contradictory descriptions. Concrete proof: `/media/neighborhood-film.mp4` is described in `site.ts` as *"Promotional aerial flyover of Embark apartments, the parking deck, and the new ShopRite at Brick Church Station"* and in `src/routes/capabilities.$slug.tsx:90` as *"Twilight aerial flyover of a North Jersey neighborhood — roofs, streets, parked cars."* Same file. Two authors' worth of drift, in one codebase.

### 1.2 Cause B — filenames are used as semantic keys, and the filenames lie

Names like `svc-inspections`, `work-tower`, `hero-city`, `neighborhood-gold` were assigned by *intended slot*, not by *content*. When the photograph behind a slot changed, the name did not. The names are now actively misleading:

| Filename | What the name claims | What the file actually shows (per your own captions) |
|---|---|---|
| `work-tower.webp` | A tower | Close-range residential roof, chimney and siding |
| `hero-city.webp` | City | Coastal pier, marina slips and open water |
| `neighborhood-gold.webp` | A neighborhood | A soccer pitch and parking at golden hour |
| `rail-station.webp` | A rail station | Highway ramps and a parking lot |
| `work-residential.webp` | A residential aerial | Close-range shingles, windows and siding |
| `work-interchange.webp` | An interchange | Correct — but it is a duplicate file (see below) |

Any future automated pipeline that keys off filenames — including the R2 migration you're planning — will inherit and amplify these lies.

### 1.3 Cause C — `alt` text is derived from the sales copy, not from the picture

Three separate components generate alt text from marketing prose:

```tsx
// src/components/image-card.tsx:31
<img src={image} alt={title} />              // alt = "Real Estate Aerial Media"

// src/routes/capabilities.$slug.tsx:32
<img src={service.image} alt={service.summary} />   // alt = a 200-character sales paragraph

// src/routes/work.$slug.tsx:44
<img src={item.image} alt={item.summary} />
```

Two consequences. First, a screen-reader user hears the pitch, not the picture — and on `/capabilities/*` they hear an entire marketing paragraph read as an image description, which is a WCAG 1.1.1 failure in substance even though an `alt` attribute technically exists. Second, and more important to you: **because alt is generated from the copy, a wrong image is structurally invisible.** The alt will always agree with the copy no matter which file is behind it. There is no signal anywhere in the system that can ever disagree, so nothing can catch the error — not a linter, not Google, not a reviewer skimming the code.

### 1.4 Cause D — six photographs are shipped under fourteen filenames

Byte-identical duplicates confirmed by MD5:

| Group | Files sharing one identical image |
|---|---|
| 1 | `about-panorama.webp` = `hero-city.webp` |
| 2 | `svc-inspections.webp` = `work-tower.webp` |
| 3 | `svc-construction.webp` = `svc-solar.webp` |
| 4 | `featured-night.webp` = `work-night.webp` |
| 5 | `svc-damage.webp` = `svc-real-estate.webp` = `work-residential.webp` |
| 6 | `svc-mapping.webp` = `work-interchange.webp` |

Group 5 is the damaging one: **the same single photograph of house siding is filed as your Real Estate proof, your Property Damage proof, and a residential portfolio piece.** A broker and an adjuster looking at the same site are being shown the same picture as evidence of two different competencies.

Separately, seven `svc-*.webp` files (~1.5 MB) plus `trust-airspace.webp`, `work-night.webp` and `logo.jpg` are **referenced nowhere in the codebase** — dead weight from an earlier version of the data model.

### 1.5 Cause E — the copy was rewritten to apologize for the mismatches

This is the most commercially costly finding in the report. Rather than replacing wrong images, the copy was patched to pre-empt the mismatch. Six live examples:

- Inspections: *"The sample stills on this site are a residential roof and siding, **not a tower**."*
- Damage: *"The sample still is a residential envelope, **not a storm scene**."*
- Events: *"The sample still is a recreation field and running track at twilight, **not a concert**."*
- Roof + Solar: *"The commercial still on this page is a warehouse roof and lot — array-specific stills are scoped per site."*
- Recreation field: *"**Not** a roof-and-street neighborhood still."*
- Twilight neighborhood: *"It is **not** the Embark / ShopRite promotional flyover."*

Read as a prospect reads it, every one of these says: *the thing I am showing you is not the thing you asked about.* You are targeting people who buy on confidence. A page that argues with its own photograph does the opposite of what it costs you to build. **Every one of these disclaimers should be deleted, and the underlying image replaced or the service card retired until you have the shot.**

### 1.6 Flagged mismatch register (each individually, as requested)

| # | Where | Image served | What the copy promises | Severity |
|---|---|---|---|---|
| M1 | `/capabilities/real-estate` + homepage card | `neighborhood-gold.webp` — **a soccer pitch** | "Cinematic listing films, twilight stills, neighborhood context" | **10** |
| M2 | `/capabilities/damage` | `work-residential.webp` — house siding, identical file to the Real Estate proof | "Time-stamped stills… for owners, adjusters" | **10** |
| M3 | `/capabilities/roof-solar` | `warehouse-lot.webp` — a warehouse parking lot | "Overview and detail imagery of roofs and **solar arrays**" | **9** |
| M4 | `/capabilities/inspections` | `work-tower.webp` — a residential roof close-up | "Roofs, facades, chimneys and envelopes" (name says tower) | **7** |
| M5 | `/capabilities/events` | `park-twilight.webp` — an empty running track | "Aerial coverage planned around the venue, people, timing" | **8** |
| M6 | Homepage mid-page band | `about-panorama.webp` — **a coastal marina**, duplicate of `hero-city.webp` | A North Jersey operator's brand band | **7** |
| M7 | `/work/east-orange-cityscape` | A coastal marina | URL says East Orange cityscape; title says "Waterfront, Pier, And Marina" | **7** |
| M8 | `/work/rail-station` | Highway ramps and a parking lot | URL says rail station; copy admits the site isn't identified | **6** |
| M9 | `/capabilities/construction` video | `neighborhood-film.mp4` labelled "North Jersey neighborhood" | Same file labelled "Embark + ShopRite promotional flyover" elsewhere | **6** |
| M10 | `/work/night-plaza` vs `/work/night-lots` | Two near-identical lit-parking-lot entries | Portfolio padding — reads as thin inventory | **5** |
| M11 | All `/capabilities/*` heroes | — | `alt` = the full marketing summary paragraph | **6** |
| M12 | 10 orphaned media files | — | ~2 MB shipped, referenced nowhere | **3** |

---

## 2. The R2 pipeline that actually fixes this

Moving files to R2 without fixing the data model will move the problem, not solve it. The fix is to make **the asset the source of truth about itself**, and make the copy *reference* an asset by ID rather than by path.

### 2.1 The shape

Replace `image: "/media/x.webp"` everywhere with `assetId: "bam-eo-kuzuri-ortho-01"`, and add one manifest — generated, not hand-written — that R2 and the site share:

```jsonc
// assets.manifest.json  — generated by the pipeline, committed, read at build time
{
  "bam-eo-kuzuri-ortho-01": {
    "key": "captures/2026-07-06-kuzuri-kijiji/ortho-01",   // R2 object key prefix
    "job":      "BAM-EO-KUZURI-202607",
    "captured": "2026-07-06",
    "sensor":   "DJI FC3411",
    "geo":      { "site": "Kuzuri Kijiji", "city": "East Orange", "state": "NJ" },
    "subject":  ["orthomosaic", "townhouse-complex", "parking", "nadir"],
    "serviceTags": ["mapping"],          // ← which service lines this image may represent
    "describes": "Orthomosaic of a 1973 townhouse complex — roofs, lots and parking, 1.24 in/px GSD",
    "alt":       "Orthomosaic of a townhouse complex showing roofs, parking lots and access roads from directly above",
    "credit":    "Baron Aerial Media",
    "width": 1086, "height": 1304,
    "variants": [400, 800, 1200, 1600, 2400],
    "formats":  ["avif", "webp", "jpeg"],
    "lqip":     "data:image/webp;base64,…",   // 20px blur placeholder
    "sha256":   "…"                            // dedupe key
  }
}
```

Two fields carry all the weight:

- **`describes` / `alt`** — written once, about the *photograph*, by whoever culled the flight. It becomes the alt text everywhere the asset appears. Copy can no longer overwrite it.
- **`serviceTags`** — the allow-list of service lines an asset is permitted to illustrate. A build-time check fails the build if `/capabilities/damage` requests an asset whose `serviceTags` don't include `damage`. **That single rule makes M1–M5 impossible to ship again.**

### 2.2 The processing stages

```
ingest → hash & dedupe → EXIF/telemetry extract → describe → variant build → upload → manifest emit → verify
```

1. **Hash & dedupe.** SHA-256 every source file. If two inputs hash identically, they become one asset with one ID. This alone collapses your 14 files into 6 and makes Group 5 structurally impossible.
2. **EXIF / telemetry extract.** Pull capture timestamp, GPS, sensor model, focal length, gimbal pitch from the DJI metadata. Gimbal pitch near −90° tags the asset `nadir`; shallower tags it `oblique`. This is free, accurate, and immediately fixes the "captured" stats you're currently typing by hand.
3. **Describe.** Two-step. A vision model produces a first-pass `describes` string from the pixels; a human confirms or corrects it once, at ingest. The point is that the description is written *looking at the photograph*, never inferred from the page it will land on. Store both the machine draft and the confirmed text so you can audit drift later.
4. **Variant build.** Generate AVIF + WebP + JPEG at 400/800/1200/1600/2400 wide, plus a base64 LQIP. Use `sharp`. Today you ship a single 2560px WebP to a 390px phone; this stage alone removes roughly 80% of your image bytes.
5. **Upload.** `PUT` to R2 under an immutable content-addressed key. Set `Cache-Control: public, max-age=31536000, immutable`. **Set `Access-Control-Allow-Origin` on the bucket** — see the warning below.
6. **Verify.** Fail the build on: an asset referenced but absent from the manifest; an asset with an empty `alt`; a service page referencing an asset whose `serviceTags` don't match; two manifest entries sharing a `sha256`.

### 2.3 R2 migration landmine you must handle before you move

`src/components/webgl/ortho-stage.tsx` reads pixel data from the DEM image to build terrain:

```ts
ctx.drawImage(img, 0, 0, w, h);
const data = ctx.getImageData(0, 0, w, h);   // ← throws SecurityError on a cross-origin image
```

The moment `kiji-dem.webp` is served from a different origin, this canvas becomes tainted and `getImageData` throws. It is inside a `try/catch` that silently falls back to using the *colour* map as the displacement map — so the 3D terrain won't error, it will just quietly become garbage: roof colour interpreted as elevation. **You will not get a warning.** Fix: set CORS on the R2 bucket and add `crossorigin="anonymous"` to the texture loader.

While you're in that file, the height derivation is also wrong on its own terms:

```ts
const height = r * 0.72 + g * 0.22 - b * 0.18 + 32;
```

DroneDeploy DEM exports use a jet/viridis colour ramp in which low and high elevations *both* have high channel values in different channels. A linear RGB combination is not monotonic against that ramp, so the surface you're rendering is partly inverted. For the exact audience you want — adjusters, engineers, survey-adjacent GCs — a visibly wrong DEM is worse than no DEM. Fix properly: export a 16-bit single-channel GeoTIFF/PNG height map from DroneDeploy and displace from that, or bake an exact colormap→height lookup table for the palette you actually export.

### 2.4 Cost note

R2 has zero egress fees, so this migration also removes the bandwidth cost of the oversized images you're currently serving. Put Cloudflare Images or a Worker in front for on-the-fly resizing only if you'd rather not pre-generate variants — pre-generating is cheaper and more predictable at your volume.

---

## 3. Critical technical defects (with root cause and fix)

### 3.1 — Severity 10 — The homepage hero photograph never renders

**Symptom.** At 1440px, 834px and 390px, in both light and dark themes, the homepage hero is a flat black (dark) or flat cream (light) rectangle. The image loads successfully — `naturalWidth: 1800`, `complete: true`, `opacity: 1`, `object-fit: cover` — and is then painted underneath the page background.

**Root cause.** In `src/routes/index.tsx`:

```tsx
<section className="relative flex min-h-svh items-center overflow-hidden pt-24">
  <div className="absolute inset-0 -z-20 overflow-hidden">   {/* z-index: -20 */}
    <img src="/media/city-dusk.webp" className="ken-burns" />
  </div>
  <div className="absolute inset-0 -z-10 bg-[linear-gradient(90deg,var(--color-ink)…)]" />
```

`position: relative` with `z-index: auto` **does not create a stacking context.** Neither does the page shell above it (`position: static`). So the `-z-20` layer is hoisted to the root stacking context and painted *behind* `div.flex.min-h-svh.bg-ink`, which has an opaque `rgb(10,14,10)` background. Verified live: `document.elementFromPoint()` over the hero returns the `SECTION`, never the `IMG`.

**Fix (one class).** Add `isolate` to the hero section:

```tsx
<section className="relative isolate flex min-h-svh items-center overflow-hidden pt-24">
```

`isolation: isolate` forces a stacking context so the negative z-index layers stay inside the section. Then re-check the same pattern on the second panorama band lower down the page.

**Second, related fix.** In light theme, `--color-ink` resolves to the cream paper colour, so the `linear-gradient(90deg, var(--color-ink) 0%, …)` overlay becomes an opaque cream wash — the photo would still be invisible even after the stacking fix. Give the overlay a theme-independent dark scrim (`rgba(10,14,10,…)` literal, not the token), and switch it to a vertical gradient below `md:` so the photograph survives on a phone:

```css
/* mobile: bottom-up scrim so the image is visible */
@media (max-width: 767px) {
  .hero-scrim { background: linear-gradient(0deg, rgba(10,14,10,0.95) 20%, rgba(10,14,10,0.25) 100%); }
}
```

**Third.** Delete the caption *"Behind this headline: a July 8 dusk still of dense city fabric…"*. Premium brands do not narrate their own hero photograph. Once the photo is visible it is redundant; while it is invisible it is confusing.

### 3.2 — Severity 10 — Mobile navigation is unreachable on the homepage

**Symptom.** On a 390px phone and an 834px tablet, the homepage `<header>` computes to **924px wide**. The theme toggle sits at x=804 and the hamburger at x=860 — both entirely off-screen. There is no way to open the menu from the homepage on a phone. Every other page is fine (header = 390px).

**Root cause — isolated by DOM bisection.** `src/components/field-stills.tsx:136`:

```tsx
<ol className="mt-12 grid gap-14">          {/* grid container */}
  <li>                                       {/* grid item — min-width: auto */}
    <div className="reveal">
      <div className="timeline-rail mt-4 flex gap-2 pb-2">
        <button className="timeline-card w-36 shrink-0">…</button>   {/* ×6 */}
```

`.timeline-rail` correctly sets `overflow-x: auto`, but **`overflow` on a plain block box does not zero out its min-content contribution to an ancestor's intrinsic sizing.** The `<li>` is a grid item, so its `min-width` defaults to `auto` = min-content = the full unwrapped flex line: 6 × 144px + 5 × 8px gap = **904px**. The `<li>` refuses to shrink, the grid track becomes 904px, the document scroll width becomes 924px, and the fixed header stretches to the expanded layout viewport. Measured live: rail `width: 904, scrollWidth: 904`; `<li> width: 904, min-width: auto`.

**Fix (one class).**

```tsx
<li className="min-w-0">
```

Add `min-w-0` to the `<li>` (and as a habit to any flex/grid item that wraps a horizontal scroller). Then add a global guard so this class of bug can never silently ship again:

```css
html { overflow-x: clip; }
```

Verify after the fix that `document.documentElement.scrollWidth === 390` at a 390px viewport on `/`.

### 3.3 — Severity 9 — A `grok.com` script runs on every page

The production HTML head contains:

```html
<script src="https://grok.com/grok-app-builder/extensions.js" defer></script>
<link rel="manifest" href="/__grok/manifest.webmanifest">
<link rel="apple-touch-icon" href="/__grok/icon-180.png">
<meta property="x:game:image" content="https://baronaerial.com/x-banner.jpg">
```

Injected by `server/middleware/grok-pwa.ts`, with assets in `public/__grok/` (including `logo-grok.svg`, `ob-ipad.png`). This is builder scaffolding that was never stripped for production.

Why it matters at your price point: it is third-party JavaScript with full DOM access on your business site; it is a privacy disclosure you have not made in your own Privacy page; it is an availability dependency on a domain you don't control; and any technically literate prospect — exactly the adjusters and developer-side engineers you're targeting — who opens View Source sees that your site is running someone else's builder. The `x:game:image` meta tag is an X gaming-platform property, meaningless here.

**Fix.** Remove the `grok-pwa` middleware from the server entry, delete `public/__grok/`, delete `server/middleware/grok-pwa.ts` and `server/virtual-grok-og-identity.d.ts`, drop the `x:game:*` meta from `__root.tsx`, and ship your own `site.webmanifest`. Also rename the package from `"app-builder-workspace"` in `package.json`.

### 3.4 — Severity 9 — Every page has the same `<title>`; the route heads are silently ignored

All 33 routes render `<title>Baron Aerial Media</title>`. The carefully written per-route titles — "Mission Planner — Baron Aerial Media", "Plan a Drone Mission", "About — Baron Aerial Media" — never reach the browser.

**Root cause.** Route heads declare the title as a top-level key:

```ts
head: () => ({
  title: "Mission Planner — Baron Aerial Media",   // ← not a recognised head key
  meta: [{ name: "description", content: "…" }],
})
```

TanStack Start's `head()` consumes `meta`, `links`, `scripts` and `styles`. A top-level `title` is not part of that contract and is discarded. Meanwhile `__root.tsx` puts the title *inside* `meta`:

```ts
meta: [ …, { title: APP_NAME }, … ]   // ← this is the only title that renders
```

So the root's generic title wins on every page.

**Fix.** Move `title` into the `meta` array on every route, and remove it from the root (or keep the root's only as a fallback that children override):

```ts
head: () => ({
  meta: [
    { title: "Drone Mapping & Orthomosaics in East Orange, NJ — Baron Aerial Media" },
    { name: "description", content: "…" },
    { property: "og:title", content: "…" },
    { property: "og:description", content: "…" },
    { property: "og:image", content: "https://baronaerial.com/media/kiji-ortho.webp" },
    { property: "og:url", content: "https://baronaerial.com/work/kuzuri-kijiji" },
    { property: "og:type", content: "article" },
  ],
  links: [{ rel: "canonical", href: "https://baronaerial.com/work/kuzuri-kijiji" }],
})
```

This is a one-hour change with the largest SEO return of anything in this document.

### 3.5 — Severity 9 — The stylesheet 404s; every visitor sees unstyled content first

The server-rendered HTML links `/assets/styles-BRU8ts3c.css`, which returns **404**. The real stylesheet, `/assets/styles-C2XcnkyD.css`, is injected by JavaScript after hydration.

Verified with JavaScript disabled: only the 404'd sheet is present, `body` background is `rgba(0,0,0,0)`, the `<h1>` renders at the browser-default 32px instead of the intended ~86px, and the page is a stack of giant white-on-white logo images and raw link text.

**Consequences.** A flash of unstyled content on every cold load; a completely unstyled site for anyone whose JS fails, is slow, or is blocked; and a broken first impression for any text-mode crawler or AI scraper.

**Root cause.** The server bundle's HTML template references a CSS hash from an earlier client build — a stale/partial deploy where the server output and the client assets came from different builds.

**Fix.** Do a clean `rm -rf dist .output node_modules/.vite && npm run build` and redeploy both server and client artifacts together. Then add a smoke test to the deploy: fetch `/`, extract every `<link rel="stylesheet">` href, and fail the deploy on any non-200.

### 3.6 — Severity 9 — The homepage is 16 MB over 70 requests

Measured transfer, all three viewports:

| Page | Requests | Transfer |
|---|---|---|
| **/** | 70 | **16.2 MB** (16.5 MB on mobile) |
| /work | 41 | 4.2 MB |
| /capabilities | 32 | 2.7 MB |
| /work/kuzuri-kijiji | 41 | 2.5 MB |
| /contact | 21 | 1.1 MB |

Four compounding causes:

1. **35+ `<link rel="preload" as="image">` in the homepage head** — including images that live on *other pages* (`kiji-dem`, `bancroft-curb`, `overpass-ground`, `south-munn-staging`). Every one is fetched at highest priority before anything else paints. This comes from React 19 auto-emitting preloads for images rendered during SSR, combined with rendering the entire FieldStills/Deliverables inventory on the homepage.
2. **Zero lazy loading.** All 52 homepage images have no `loading` attribute — all eager.
3. **No responsive images.** Source files are 1600–2560px wide; a 390px phone downloads the 2560px original. No `srcset`, no `sizes`.
4. **Brand logos are 4.3 MB of PNG.** `logo-lockup.png` is 1.0 MB; `logo-nav-on-dark.png` is 364 KB and is preloaded on every page to render a **49px-wide** nav mark.

**Fix, in order of return:**
- Convert the logo set to SVG (a few KB each). This alone removes ~4 MB.
- Add `loading="lazy"` + `decoding="async"` to every image below the fold; `fetchpriority="high"` + `loading="eager"` on the hero only.
- Add explicit `width`/`height` to all 52 images (also fixes cumulative layout shift — currently zero images declare dimensions).
- Ship `srcset`/`sizes` from the R2 variant set in §2.
- Stop rendering the full media inventory on the homepage; render the first job and lazy-mount the rest.

Target: homepage under 1.5 MB and under 25 requests.

### 3.7 — Severity 8 — Two autoplaying videos on the homepage

`src/components/field-film.tsx` renders two `<video autoplay muted loop controls>` elements totalling 3.7 MB (`neighborhood-film.mp4` 2.9 MB, `overpass.mp4` 0.9 MB). Both begin downloading and playing immediately on a phone, on cellular data. There are also no caption tracks.

**Fix.** Poster-first with a click-to-play overlay on `< md`, autoplay only on `md:` and up and only when in-viewport (IntersectionObserver). Re-encode to H.264 + AV1/VP9 at ~1.5 Mbps; the 7.5-second nadir hold should be well under 1 MB. Add a `<track kind="captions">` or, if silent, `muted` plus a visible "silent clip" label.

### 3.8 — Severity 8 — Contact form: no persistence, and a rate limiter that can lock out real prospects

**The form does work.** I submitted a live end-to-end test brief (Mapping mission, 19 Freeway Drive East, four deliverables, marked clearly as a test) — the server function returned **HTTP 200** and the success state rendered. Resend accepted the message, so a test email should be in your inbox. Please confirm you received it, and note whether it landed in spam.

Three real problems behind that success, all in `src/lib/submit-brief.ts`:

**(a) Leads are never stored.** The brief is emailed and nothing else. There is no database write, despite the project already carrying `pg`, `kysely` and a `migrations/` directory. If Resend has an outage, your API key expires, the domain's DKIM breaks, or the mail lands in spam, **the lead is gone with no record that it ever existed.** For a business where one commercial mapping job may be worth thousands, this is the highest-consequence backend defect on the site.

*Fix:* write the brief to Postgres first, then attempt delivery. Return success if the row was written even when the email fails, and add a `delivered_at` column plus a retry.

**(b) The rate limiter can lock out every anonymous visitor.**

```ts
function submissionClientId(): string {
  const forwardedFor = request?.headers.get("x-forwarded-for")?.split(",")[0]?.trim();
  return forwardedFor || request?.headers.get("x-real-ip")?.trim() || "unknown";
}
```

If neither header is present, **every visitor shares the bucket `"unknown"`** — five submissions from anyone in fifteen minutes blocks everybody else. You are behind Cloudflare, where `cf-connecting-ip` is the reliable header, and it isn't checked. The limiter is also a plain in-memory `Map` that resets on every deploy, doesn't work across instances, never evicts stale keys (an unbounded memory leak), and **counts a submission before delivery succeeds** — so a failed send burns one of the user's five attempts.

*Fix:* read `cf-connecting-ip` first; fail open rather than into a shared bucket when no IP is available; move the counter to Cloudflare KV or a Postgres table; and only increment after a successful write.

**(c) A validation failure returns a 500 instead of a field error.** In the handler, `parseMissionBriefSubmission(data)` sits *outside* the `try` block, so a Zod parse failure throws unhandled and the user sees a generic "something went wrong" rather than which field is bad. Move it inside the `try` and map `ZodError` to per-field messages.

**(d) The prospect gets no confirmation email.** Someone who sends a detailed mission brief receives nothing in writing. Send an auto-reply that restates their brief, sets a response-time expectation, and includes your phone number — it is free credibility and it prevents "did that go through?" churn.

### 3.9 — Severity 9 — There is no phone number, no email address, and no named human anywhere on the site

I grepped the entire codebase: no `tel:`, no `mailto:`, no address, no operator name. The About page says "owner-operated" and never names the owner. There is no photograph of you, no Part 107 certificate number, no insurance statement, no equipment list.

For your stated targets this is disqualifying in specific, predictable ways. A property manager with a leak at 4pm wants to call someone. An adjuster needs a named professional and a certificate of insurance before they can engage you. **Government and municipal contracting cannot proceed at all** without a registered business name, an address, a UEI, NAICS codes, and an insurance certificate. A multi-million-dollar GC's procurement team will not route a form submission from an anonymous website.

**Fix, before launch:** put a phone number and an email in the header CTA area and the footer; add a short About section with your name, your Part 107 certificate, your equipment, and your liability coverage; add a "Government & institutional" block naming your business registration, NAICS codes (541370 Surveying & Mapping Services, 541922 Commercial Photography, 541330 Engineering Services), UEI if you have one, and your insurance limits.

### 3.10 — Severity 7 — Accessibility gaps

Measured on the live homepage:

| Finding | Detail | WCAG |
|---|---|---|
| **22 buttons with no accessible name** | `field-stills.tsx:151` — each `timeline-card` button contains only `<img alt="">` and no text. Screen readers announce "button, button, button…" | 4.1.2 |
| Alt text is marketing prose | `/capabilities/*` hero alt = the full service summary | 1.1.1 |
| No skip link | First link is the logo; keyboard users tab through the whole header on every page | 2.4.1 |
| 3D airspace chart is mouse-only | Cells are click-only; the keyboard-accessible fallback grid renders only when unhydrated | 2.1.1 |
| `AirspaceChartLazy` ignores reduced motion | `SurveyCanvasLazy` and `OrthoStageLazy` check `usePrefersReducedMotion()`; `AirspaceChartLazy` checks only `useHydrated()` | 2.3.3 |
| Mobile menu has no focus trap and no Escape-to-close | `site-header.tsx` | 2.1.2 |
| Form has no live region | Step changes and the error message are not announced | 4.1.3 |
| Form fields lack `autocomplete` | No `autocomplete="name" / "email" / "tel"`, and phone is `type="text"` not `type="tel"` — no autofill and the wrong keyboard on mobile | 1.3.5 |
| LAANC colour ramp is red→yellow→green | `altColor()` in `airspace-chart.tsx` — 0 ft red vs 200 ft green is the classic red-green colourblind failure, in an audience heavily weighted to men in construction and adjusting | 1.4.1 |

You publish an `/accessibility` page. Shipping one while the homepage has 22 unnamed buttons is a credibility exposure as much as a compliance one.

**Quick wins:** add `aria-label={item.caption}` to each timeline card; add a skip link; add `usePrefersReducedMotion()` to `AirspaceChartLazy`; add `autocomplete` attributes and `type="tel"`; add `aria-live="polite"` to the form status paragraph; add numeric labels to every LAANC cell so colour is never the sole carrier of meaning.

### 3.11 — Severity 5 — Smaller code-level defects

- **The process-strip drone animation is mathematically broken.** `@keyframes drone-cruise` uses `translate3d(calc(100% - 3rem), 0, 0)`. In CSS, a percentage in `translate` resolves against **the element's own size**, not the parent's. The drone mark is 44px wide, so the target is `44px − 48px = −4px`. The drone travels from 0.88px to −4px — it does not visibly move. Fix: animate `left` from `0` to `calc(100% - 3rem)`, or use a CSS variable with the container width.
- **`PageShell` and `PageHero` accept a `tone` prop they never use.** Every call site passes `tone="light"` or `tone="dark"`; the components read `useTheme()` instead. Dead API — remove the prop or wire it up.
- **`document.body.style.cursor` is mutated on pointer enter/leave** in `airspace-chart.tsx`. If the component unmounts while hovered, the cursor stays `pointer` site-wide.
- **`/work/kuzuri-kijiji` prints the same paragraph twice** — the hero lead and the "What this capture was for" body are verbatim identical. Duplicate on-page content, and it reads as an editing mistake.
- **`plannerOptions` has 6 entries; `services` has 7.** "Event Aerial Media" is unreachable from the Mission Planner but appears at `/capabilities/events` and in the contact dropdown.
- **`robots.txt` has no `Sitemap:` directive and `/sitemap.xml` 404s.**
- **Deprecated exports left in place** — `HeroFlightLazy`, `HeroFlight` marked `@deprecated`.

---

## 4. Animation analysis — every animation, graded

Scale: **Utility** (does it help the user do something), **Communication** (does it explain a real concept), **Craft** (does it read as premium). Each out of 10.

### Tier 1 — Genuinely differentiating; invest here

**A1. 3D Airspace / LAANC chart** (`webgl/airspace-chart.tsx`) — Utility **8** · Communication **9** · Craft **6**
The strongest idea on the site. It makes an abstract regulatory concept — that airspace is a stack of volumes and that a LAANC cell returning 0 ft is a *no*, not a low ceiling — literally visible in three dimensions. No competitor in your market has anything like it. **Keep and invest.**
*Problems:* You cannot actually explore it. There are no `OrbitControls`; the whole rig is skewed by `state.pointer`, so on touch it sits at a fixed angle and on desktop you can only wiggle it. Only 5 of 56 cells are labelled, and altitudes appear nowhere in the 3D view except for the one selected cell. Nothing tells the user to click. The geography is loose — EWR is placed east of Newark with Newark Bay as a strip along the east edge; EWR is south of downtown and the Bay is west/south of the airport. The colour ramp fails for red-green colourblind viewers.
*Improve:* add `OrbitControls` with damping (zoom off by default); print the altitude on every column face; add a one-line instruction ("Click a cell — the 0 ft columns are where LAANC will not auto-authorize"); replace the ramp with a colourblind-safe sequential scale plus numerals; correct the schematic geography or drop the place labels entirely; add a keyboard-navigable cell grid alongside the canvas.

**A2. Orthomosaic 3D stage** (`webgl/ortho-stage.tsx`) — Utility **9** · Communication **9** · Craft **5**
Letting a prospect orbit an actual DEM-displaced orthomosaic of a real job is the single most persuasive thing on the site for developers, GCs and adjusters. It proves you deliver *data*, not pictures. **Keep — it is your best sales asset.**
*Problems:* the height map is wrong (§2.3), so a technical viewer sees implausible terrain; `enableZoom` is on, which traps the scroll wheel on desktop; `touchAction: none` means a phone user swiping to scroll past the canvas gets stuck orbiting instead; and the on-canvas hint "scroll to zoom" instructs users to do the thing that traps them.
*Improve:* fix the DEM decode; `enableZoom={false}` with a dedicated +/− control; require two fingers on touch (`OrbitControls` `touches: { ONE: ROTATE }` plus a "tap to activate" overlay); add a labelled scale bar and a north arrow — those two details are what make it read as a survey product rather than a toy.

**A3. Survey pattern flight** (`webgl/hero-flight.tsx`) — Utility **6** · Communication **5** · Craft **4**
The concept — a drone flying nadir / crosshatch / oblique patterns — is exactly right for explaining why a mapping flight costs what it costs. The execution undersells it.
*Problems:* the drone flies over the *finished orthomosaic*, so it appears to be flying over the output rather than producing it — conceptually backwards. The whole flight path is drawn at once, so the one thing worth showing (coverage accumulating from overlapping frames) never happens. `curve.getPointAt()` on a Catmull-Rom through a lawnmower gives non-uniform speed, so the drone lurches at row ends. Rotors spin at ~6 revolutions/second and strobe under aliasing; the semi-transparent disc already reads as motion blur, so spinning it makes it worse.
*Rethink into:* start with bare ground; draw the flight line progressively as the drone flies; drop a small camera-frustum footprint at each shutter point; let the footprints overlap and dissolve into the finished ortho as the last line completes. That is a 15-second loop that *explains photogrammetry*. Stop the rotor spin; slow the traverse; use `getPointAt` with arc-length reparameterisation for constant speed.

### Tier 2 — Working, keep with modifications

**A4. `Reveal` scroll fade-rise** — Utility **4** · Communication **3** · Craft **6**. Correctly implemented (IntersectionObserver, disconnects after firing, respects reduced motion). Slightly overused — nearly every block fades in, which makes the whole page feel like it is loading. *Modify:* reserve it for section openings; drop the per-card `delay={i * 70}` stagger on grids, which delays content by up to half a second for no gain.

**A5. Header scroll transition** — Utility **6** · Communication **4** · Craft **7**. Backdrop-blur and padding collapse on scroll. Genuinely good. *Keep as is.* Minor: throttle the scroll handler with `requestAnimationFrame`.

**A6. `ImageCard` hover** (scale 1.06 + arrow nudge) — Utility **5** · Communication **4** · Craft **7**. Restrained, correct easing, clear affordance. *Keep.* It will read far better once the images behind it are the right images.

**A7. `timeline-rail` scroll-snap filmstrip** — Utility **8** · Communication **6** · Craft **5**. Good pattern for browsing a job's deliverables. *Modify:* fix the `min-w-0` overflow bug (§3.2), give each thumbnail an accessible name, and add visible left/right controls — a scroll rail with no arrows reads as broken on desktop.

**A8. Nav underline scale-x** and **A9. Button `active:scale-[0.96]`** — Utility **4** · Craft **7**. Fine. *Keep.*

**A10. Hero text `stagger-in` / `page-hero-in`** — Utility **3** · Communication **3** · Craft **6**. Standard. *Keep,* but tighten: 800ms with a 520ms cascade means the last element lands 1.3s after paint. Cut to ~450ms with 60ms steps.

### Tier 3 — Not earning their place

**A11. Ken Burns on hero images** — Utility **2** · Communication **1** · Craft **3**. A 28-second infinite alternating zoom on a still photograph is a 2010 convention, and on the homepage the image it animates is invisible anyway. *Rethink:* replace with a single slow parallax on scroll, or a 6-second dissolve between two frames from the same job. If you keep any drift, make it 45s+ and under 3% scale so it reads as breathing, not zooming.

**A12. `process-drone` cruise** — Utility **1** · Communication **1** · Craft **1**. Broken (§3.11) — the drone doesn't move. *Fix or delete.* If fixed, the right version is: the drone flies left→right along the process strip and the green progress line draws *behind* it, so motion and progress are causally linked. That would raise Communication to 7.

**A13. `TiltCard` 3D tilt** — Utility **2** · Communication **1** · Craft **4**. Correctly gated to mouse only and to reduced-motion. But a 6° parallax tilt is a consumer-portfolio gesture, not a professional-services one. *Rethink:* replace with a subtle elevation + border-brightening on hover. Your audience should feel precision, not playfulness.

**A14. `AirspaceStack` CSS-3D layers** (`airspace-yaw`, `airspace-pulse`) — Utility **2** · Communication **5** · Craft **5**. The concept (stacked authorization layers) is good, but the whole component is `aria-hidden="true"` — decorative only — and it duplicates what A1 does far better. *Rethink:* either make it the mobile-friendly fallback for A1 (2D, labelled, accessible) or delete it.

**A15. `launch-pulse` on GO/NO-GO** — Utility **5** · Communication **6** · Craft **5**. A pulsing status indicator is genuinely useful here. *Modify:* pulse only the NO-GO/HOLD state; a pulsing GO is noise.

### What's missing — animations that would actually sell

Three concepts you don't have that would do more for your stated targets than anything currently on the page:

1. **Progress comparison wipe.** A draggable before/after slider across two same-viewpoint captures of the North Munn bridge, weeks apart. This is the entire value proposition of construction progress documentation, and right now the site only *describes* it. Highest ROI animation you could build. Utility 10.
2. **Ortho → DEM → 3D mesh transition.** One frame morphing through the three deliverables in the Kuzuri packet, with a scrubber. Explains the processing pipeline — "flying is only half the job" — visually instead of in prose. Utility 9.
3. **Coverage/GSD explainer.** A short loop showing altitude changing and the ground-sample-distance number changing with it. This is the single question technical buyers ask ("how much detail do I get?") and it turns a specification into an intuition. Utility 8.

All three are cheap: two are CSS/canvas, none needs new WebGL infrastructure.

**Overall animation verdict.** You have two world-class concepts (A1, A2) executed at maybe 55% of their potential, one good concept executed backwards (A3), a competent supporting layer, and four decorative effects that dilute the premium read. The path to "looks like a whole team built this" is not *more* animation — it is finishing the two technical showpieces to a survey-grade standard and deleting the consumer-portfolio gestures. Precision reads as scale; motion for its own sake reads as a template.

---

## 5. Copy and SEO strategy

### 5.1 What's already strong

The regulatory writing is a genuine moat. Pages that explain LAANC 0 ft cells, the distinction between a 107.41 authorization and a 107.205 waiver, SGI eligibility inside an NSSE TFR, and the NYPD permit layer are things almost no drone operator publishes. This content will earn links and will rank for high-intent long-tail queries. The recurring line "flying is only half the job — processing and deliverables are the product" is a strong, defensible positioning statement. Keep all of it.

### 5.2 What's structurally wrong

**Zero technical SEO foundation.** No unique titles (§3.4). No canonical tags on any page. No JSON-LD structured data on any page. No sitemap. `robots.txt` is two lines with no `Sitemap:` directive. No `og:description`, `og:url` or `og:type`; `og:title` is the generic brand name on all 33 pages, so sharing any page on LinkedIn — where your commercial-real-estate and construction audience actually lives — produces an identical, characterless card. For a business whose product is imagery, that is the worst possible place to be generic.

**Defensive voice.** Beyond the image disclaimers in §1.5, the copy repeatedly tells prospects what it *isn't* and what it *won't* do: "not a certified survey," "we do not stamp condition," "does not authorize a flight, confirm a date, or create a contract," "not RTK survey control, and not a sealed plat." Every one of these is legally correct and you should keep the substance — but they are currently distributed through the *sales* copy rather than concentrated in a scope-and-limitations block. The cumulative effect on a first read is an operator managing their own risk rather than a professional solving the buyer's problem. Move the disclaimers to a consistent, styled "Scope" panel at the foot of each capability page and let the sales copy sell.

**Site duplication.** `ProcessStrip` and the full `PreflightSection` render on `/`, `/about` *and* `/trust`. Three pages sharing large identical blocks compete with each other for the same queries and dilute all three.

**Thin and redundant portfolio entries.** `night-plaza` and `night-lots` are both lit parking lots. `rail-station` and `east-orange-cityscape` have URLs that contradict their own titles. Several entries lead with "the site is not labeled on the frame, so it is not named here" — which reads as filler. **Nine strong case studies beat sixteen entries where seven are unnamed stock-feeling frames.**

### 5.3 Keyword and page architecture

Your realistic wins are local + service + qualifier, not head terms.

| Page | Primary target | Supporting |
|---|---|---|
| `/` | drone photography Newark NJ | aerial imaging North Jersey, FAA Part 107 drone Newark |
| `/capabilities/mapping` | **drone orthomosaic mapping New Jersey** | photogrammetry survey NJ, 3D site model East Orange, DEM drone NJ |
| `/capabilities/construction` | construction progress drone documentation NJ | jobsite aerial photography North Jersey, NJDOT progress imaging |
| `/capabilities/inspections` | drone roof inspection New Jersey | aerial facade inspection NJ, commercial roof drone survey |
| `/capabilities/damage` | drone property damage documentation NJ | aerial claims documentation, adjuster drone imagery New Jersey |
| `/capabilities/real-estate` | real estate drone photography North Jersey | aerial listing photos NJ, twilight aerial listing |
| `/trust` | **LAANC authorization Newark Liberty Class B** | Part 107 waiver NJ, SGI waiver TFR drone, NYPD drone permit |
| `/work/kuzuri-kijiji` | East Orange orthomosaic case study | Kuzuri Kijiji redevelopment aerial survey |

`/trust` is your link magnet. Nobody else is writing usefully about flying commercially under EWR Class B, and that page can earn links from local development, engineering and insurance communities that will lift the whole domain.

### 5.4 Structured data to add (highest-value SEO work after titles)

Add to the root, rendered on every page:

```jsonc
{
  "@context": "https://schema.org",
  "@type": "ProfessionalService",
  "name": "Baron Aerial Media",
  "description": "FAA Part 107 aerial imaging, mapping and inspection…",
  "url": "https://baronaerial.com",
  "telephone": "+1-XXX-XXX-XXXX",          // required — see §3.9
  "email": "…",
  "address": { "@type": "PostalAddress", "addressLocality": "Newark", "addressRegion": "NJ", "addressCountry": "US" },
  "areaServed": [ { "@type": "State", "name": "New Jersey" }, { "@type": "City", "name": "New York" } ],
  "founder": { "@type": "Person", "name": "…", "hasCredential": "FAA Part 107 Remote Pilot Certificate" },
  "hasOfferCatalog": { "@type": "OfferCatalog", "itemListElement": [ /* one Service per capability */ ] }
}
```

Then per page type: `Service` on each `/capabilities/*`, `CreativeWork` (or `Article`) plus `ImageObject` on each `/work/*` — the `ImageObject` entries should be generated straight from the R2 manifest's `describes`/`alt`/`contentUrl`, which is the second payoff of building that manifest. `BreadcrumbList` everywhere. `FAQPage` on `/trust` for the LAANC and waiver questions — that one can win rich results outright.

Also: create a **Google Business Profile** for Newark and add `sameAs` links. For a local service business this typically outweighs everything else on this list combined.

### 5.5 Copy edits to make before launch

1. Delete all six "not a X" image disclaimers (§1.5).
2. Delete the hero caption that narrates the background photo.
3. De-duplicate the `/work/kuzuri-kijiji` paragraph.
4. Add a phone number, an email and a named operator (§3.9).
5. Add one sentence of proof to the homepage above the fold — "NJDOT bridge reconstruction, a 247-unit mapped site, and residential listings across North Jersey" — because right now the hero asserts capability and defers all evidence to a scroll.
6. Add a "Government & institutional" block: registration, NAICS codes, insurance, and a statement that you can work under a municipal or state contract vehicle.

---

## 6. Navigation and information architecture

**Current:** Work · Capabilities · Mission Planner · About, with Contact and "Plan a Mission" as trailing links.

**Problems:**

- **Two competing conversion paths.** "Plan a Mission" goes to `/mission-planner` from the header but to `/contact` from the homepage hero — the same label, two destinations. Then `/mission-planner` is a thin page (1,257 characters) that mostly forwards to `/contact` anyway. **Merge them:** make the planner step 0 of the contact brief, so the flow is one continuous funnel rather than a picker that hands off to a form.
- **Contact is de-emphasised.** It's a plain text link, not a button, and it's `hidden sm:inline` — so below 640px the only conversion affordances in the header are inside a hamburger menu that (today) can't be opened on the homepage. On phones, put a persistent "Plan a Mission" button in the header and consider a sticky bottom bar with **Call** and **Send a brief**.
- **Trust + Safety is buried in the footer.** It's your strongest differentiator and your best SEO asset, and it isn't in the primary nav. Promote it, and consider renaming it — "Trust + Safety" reads like a platform policy page. **"Airspace + Compliance"** tells a GC or an adjuster exactly what they're getting.
- **Work has 16 entries with no way to filter by what a buyer cares about.** Filters exist by category, but a broker wants "residential," an adjuster wants "roof/envelope," a developer wants "mapping." Reframe the filters around buyer type, not medium.
- **The 4-step form has no saved state.** Refresh or an accidental back-navigation loses everything. Persist to `sessionStorage` between steps.
- **Recommended nav:** `Work · Capabilities · Airspace + Compliance · About` with `Call` and a `Plan a Mission` button pinned right. Retire `/mission-planner` as a standalone destination.

---

## 7. The 24-hour plan (do these before you announce)

Ordered by impact-per-hour. Total: roughly 6–8 focused hours. Everything here is either a one-line change or a content edit.

| # | Fix | File | Effort | Sev |
|---|---|---|---|---|
| 1 | Add `isolate` to the hero `<section>` so the hero photo renders | `routes/index.tsx` | 1 min | 10 |
| 2 | Add `min-w-0` to the `<li>` in FieldStills; add `html { overflow-x: clip }` | `field-stills.tsx`, `styles.css` | 5 min | 10 |
| 3 | Verify at 390px that `scrollWidth === 390` and the hamburger is reachable | — | 10 min | 10 |
| 4 | Remove the grok script, `/__grok/`, the middleware and `x:game:*` meta; add your own manifest | `server/`, `public/`, `__root.tsx` | 30 min | 9 |
| 5 | Move `title` into the `meta` array on all 12 route heads; add `og:title`, `og:description`, `og:url`, `og:image`, and a canonical per page | all routes | 90 min | 9 |
| 6 | Clean rebuild + redeploy so the stylesheet stops 404ing; verify no unstyled flash | build | 20 min | 9 |
| 7 | Replace the Real Estate image (soccer pitch → an actual listing aerial) and the Damage image (siding → anything showing damage), or **temporarily remove the Damage and Events cards** if you have no correct shot | `data/site.ts` | 45 min | 10 |
| 8 | Delete all six "not a X" disclaimers and the hero photo caption | `data/site.ts`, `routes/index.tsx` | 20 min | 9 |
| 9 | Add a phone number and an email to the header and footer; name yourself on About | `site-footer.tsx`, `site-header.tsx`, `about.tsx` | 45 min | 9 |
| 10 | Add `loading="lazy"` + `decoding="async"` below the fold; `fetchpriority="high"` on the hero | components | 30 min | 8 |
| 11 | Convert the four logo PNGs to SVG (removes ~4 MB) | `public/brand/` | 45 min | 8 |
| 12 | Persist form submissions to Postgres before emailing | `submit-brief.ts` + migration | 60 min | 9 |
| 13 | Read `cf-connecting-ip` in the rate limiter; fail open instead of into a shared bucket | `submit-brief.ts` | 15 min | 8 |
| 14 | Add `aria-label` to the 22 timeline-card buttons; add a skip link | `field-stills.tsx`, `page-shell.tsx` | 25 min | 7 |
| 15 | Add `autocomplete` attributes and `type="tel"` to the form | `contact.tsx` | 10 min | 7 |
| 16 | Fix the duplicated paragraph on `/work/kuzuri-kijiji` | `work.$slug.tsx` | 5 min | 5 |
| 17 | Add `sitemap.xml` + `Sitemap:` in robots.txt; add the ProfessionalService JSON-LD | `public/`, `__root.tsx` | 45 min | 8 |
| 18 | Poster-first video on mobile; autoplay only ≥ md and in-viewport | `field-film.tsx` | 30 min | 8 |
| 19 | Re-run a 390 / 834 / 1440 pass and re-test the form | — | 30 min | — |

**If you only have three hours, do 1, 2, 4, 5, 7, 8, 9.** Those seven remove every launch-blocking defect and every credibility defect. The rest can follow the announcement.

---

## 8. The multi-day plan (next 3–10 days)

### Days 2–3 — Performance and the asset foundation
- Build the R2 pipeline from §2: hash-dedupe, EXIF extract, describe, variant build, manifest emit, build-time verification.
- Migrate `site.ts` from `image: "/path"` to `assetId:` and wire `alt` from the manifest, never from copy.
- Turn on the `serviceTags` build check so a wrong-service image cannot be merged again.
- Set R2 CORS and add `crossorigin="anonymous"` before anything touches `getImageData` (§2.3).
- Ship `srcset`/`sizes`; add explicit `width`/`height` everywhere. **Target: homepage under 1.5 MB.**
- Re-encode both videos; add captions.

### Days 3–5 — Fix the technical showpieces
- Replace the DEM height derivation with a real 16-bit height map from DroneDeploy.
- `enableZoom={false}` + two-finger gating on the ortho stage; add a scale bar and north arrow.
- Add `OrbitControls` and per-cell altitude labels to the airspace chart; colourblind-safe ramp; keyboard-accessible cell list; correct or remove the place labels.
- Rebuild the survey-pattern animation as progressive coverage accumulation (§4, A3).
- Fix or delete the process-strip drone (§3.11).
- Replace `TiltCard` with an elevation hover.

### Days 5–7 — Content and conversion
- Shoot or source the missing images: a real listing aerial, a genuine roof/siding damage frame, a solar array, an event. Retire any capability card you can't back with a true photograph — **six well-evidenced services beat seven where three apologize.**
- Cut the portfolio to your strongest 9 entries; fix the `east-orange-cityscape` and `rail-station` slugs so URLs match content; merge the two night-lot entries.
- Add the before/after progress slider for North Munn (§4) — the single highest-value new build.
- Merge Mission Planner into the contact brief; add `sessionStorage` step persistence; add the prospect auto-reply email.
- Add the Government & institutional block with NAICS codes and insurance.

### Days 7–10 — SEO and trust depth
- Per-page JSON-LD: `Service`, `CreativeWork` + `ImageObject` from the manifest, `BreadcrumbList`, `FAQPage` on the compliance page.
- Create and verify the Google Business Profile; submit the sitemap in Search Console.
- Rename Trust + Safety to Airspace + Compliance and promote it into the primary nav.
- De-duplicate `ProcessStrip`/`PreflightSection` across `/`, `/about` and `/trust`.
- Write two long-form pieces off the compliance page: "Flying commercially under Newark Liberty Class B" and "What an orthomosaic actually is, and when you need one instead of photos." These are the link-earning assets.
- Add a deploy smoke test that fails on a 404 stylesheet, a missing title, or a horizontal-overflow regression at 390px.

---

## 9. Form test result — please confirm

I completed and submitted a real mission brief through the live form:

- **Mission type:** Mapping + 3D Site Models
- **Location:** 19 Freeway Drive East, East Orange, NJ 07018
- **Timing:** Flexible window · **Target date:** "TEST — week of Sep 14"
- **Deliverables selected:** Photos, 3D model, Orthomosaic, Report
- **Name:** "Claude Site Audit (TEST)" · **Email:** your address · **Company:** "TEST — automated audit, please disregard"

The server function returned **HTTP 200** and the success panel rendered ("Brief received"), which only appears after Resend accepts the message. **Please confirm it arrived, and tell me whether it landed in spam** — if it did, SPF/DKIM/DMARC alignment on the sending domain needs attention before launch, and that would be a severity-9 addition to the 24-hour list.

Also worth knowing: multi-step validation only guards steps 1 and 3. Steps 2 and 4 rely on browser `required`, so a determined user can reach the final step with an empty site description. Not launch-blocking.

---

## 10. Severity summary

| Sev | Issue | Track |
|---|---|---|
| **10** | Hero photograph invisible on every viewport and theme | 24h |
| **10** | Mobile navigation unreachable on the homepage | 24h |
| **10** | Real Estate service card shows a soccer pitch | 24h |
| **10** | Damage service shows the same photo as Real Estate | 24h |
| **9** | `grok.com` third-party script in production | 24h |
| **9** | Identical `<title>` on all 33 pages; no canonical, no schema, no sitemap | 24h |
| **9** | Stylesheet 404s; unstyled first paint on every load | 24h |
| **9** | Homepage is 16 MB / 70 requests | 24h + days 2–3 |
| **9** | Form leads are never persisted | 24h |
| **9** | No phone, no email, no named human on the site | 24h |
| **9** | Copy apologizes for its own images (6 instances) | 24h |
| **8** | Rate limiter can lock out all anonymous visitors | 24h |
| **8** | Roof + Solar service shows no solar array | 24h |
| **8** | Events service shows an empty running track | 24h |
| **8** | 3.7 MB of video autoplays on mobile | 24h |
| **7** | 22 buttons with no accessible name | 24h |
| **7** | Alt text generated from marketing copy | Days 2–3 |
| **7** | DEM height map derived incorrectly | Days 3–5 |
| **7** | Ortho stage traps scroll on desktop and touch | Days 3–5 |
| **7** | Inspections image filed as "tower" | Days 5–7 |
| **7** | Marina photo used as a North Jersey brand band | Days 5–7 |
| **6** | Airspace chart is mouse-only and colourblind-unsafe | Days 3–5 |
| **6** | Same video described two different ways | Days 2–3 |
| **6** | URLs contradict their own content (`east-orange-cityscape`, `rail-station`) | Days 5–7 |
| **5** | Process-strip drone animation is mathematically broken | Days 3–5 |
| **5** | Duplicate paragraph on the Kuzuri page | 24h |
| **5** | Redundant portfolio entries | Days 5–7 |
| **4** | Mission Planner is a thin hand-off page | Days 5–7 |
| **3** | ~2 MB of orphaned media files | Days 2–3 |
| **3** | Dead `tone` props; deprecated exports | Days 3–5 |

---

## 11. The one thing to hold on to

Nothing in this report is a problem with your judgment about what the site should say. The regulatory depth, the insistence that processing is the product, the refusal to overstate what a visual map is — that is a real professional voice and it is the reason this can compete for work above its weight class.

What's broken is the layer *between* that voice and the visitor: a hero that doesn't render, a menu that can't open, filenames that lie about their contents, and titles that never reach the browser. Those are mechanical. Most of them are one line each. Fix the seven items in §7 and you launch tomorrow with a site that finally shows what you already know how to say.
