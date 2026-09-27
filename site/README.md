# FactoryLab site

A static site: plain HTML, CSS and JavaScript, no build step, no npm. It explains the
project, follows the factory once a world is running, and maps the factory's parts. The
ideas come from *The Superdark Factory* (Poliks, Alonso-Trillo, Dunn, Scott-Douglas,
Springett; https://superdark.ai/). The site credits them and is not affiliated with them.

## Preview

The pages fetch JSON, so serve the directory over HTTP (opening the files directly does
not work):

```bash
python3 -m http.server 8791 --bind 127.0.0.1 --directory site
# then open http://127.0.0.1:8791/
```

## Pages

| File | What it is |
|---|---|
| `index.html` | Home: what the project is, the three classes (interactive), ways in, credit to the essay |
| `ideas.html` | The philosophy in plain words, ten sections, with a contents rail |
| `factory.html` | An interactive map of the factory's parts, a traced turn of the loop, and the pathology gauntlet |
| `progress.html` | The observatory: follows the factory itself once a world runs. Today it says no world is live |
| `assets/style.css` | All styles |
| `assets/site.js` | Shared: reveal on scroll, the status bar, the class ladder, the home canvas |
| `assets/factory.js` | The map's content (`NODES`, `TRACE`) and the gauntlet's criteria text |
| `assets/progress.js` | Renders the observatory from `data/progress.json` |
| `data/progress.json` | **The one real data file.** Edit this to update the Progress page and status bar |
| `data/example.json` | A fabricated world, shown only when a visitor asks, under an EXAMPLE banner |

`progress.html?example=1` opens straight into the fabricated example.

## Updating the Progress page

Edit `data/progress.json` only. The layout reads it. A running world's public outputs
(its wake page of sealed aggregates) are meant to feed this file later.

What may go in it: aggregates and public facts. What must never go in it: any seat's own
scores, history or learner state; who judged whom; prompts; wallet addresses, account,
order or client identifiers; balances or open positions; anything read from a diary
while its world lives. Money is integer micro-USD. Times are ISO 8601 UTC.

```jsonc
{
  "updated": "2026-09-27T00:00:00Z",
  "example": false,                 // true only in data/example.json
  "status": { "live": false, "headline": "No world is live yet.", "line": "…" },
  "worlds": [{
    "name": "Edition 7",            // a public label, never an account id
    "venue": "Hyperliquid testnet",
    "genesis": "2026-10-01T00:00:00Z",
    "state": "running",             // running | ended
    "ended": null,                  // or { "at": iso, "cause": "balance_zero | compute_unaffordable | explicit_kill" }
    "versions": [                   // behavioural versions: regimes, not configs
      { "v": 0, "from": "2026-10-01", "to": null, "gap": 0.12, "note": "public description" }
    ],
    "objectives": [                 // what the factory set itself
      { "at": "2026-10-03", "text": "…", "via": "metric card | motion | registration", "state": "proposed | adopted | retired" }
    ],
    "pathologies": {                // the gauntlet's verdict, now and one entry per day
      "stable_failure": { "now": "present | absent | unknown", "history": ["unknown", "absent"] },
      "thrash": {}, "overfitting": {}, "learning_death": {}
    },
    "charter": {
      "edition": 7,
      "norms": ["…"],
      "revisions": [{ "at": "…", "kind": "genesis | amendment | edition", "text": "…" }],
      "constraints": [{ "card": "…", "role": "…", "lambda": 0.12, "saturated": false }],
      "lambda_total": [0.1, 0.2]    // daily samples from genesis
    },
    "evaluation": {
      "compute_share": { "evaluators": 0.58, "producers": 0.29, "governance": 0.13 },
      "tiers_reached": 3, "families": 4,
      "disagreement": [], "variance": [], "autocorrelation": []   // daily samples
    },
    "consequences": {               // totals only
      "settled": { "count": 0, "paid_off": 0.0, "declines_right": 0.0 },
      "spend_micro_usd": { "inference": 0, "venue_fees": 0, "data": 0, "hosting": 0 },
      "treasury_micro_usd": { "in": 0, "out": 0 }
    }
  }]
}
```

Any field may be missing; its panel then shows a dash. `data/example.json` is a complete
instance of the schema with invented values.

## Rules for editing

- Content is our paraphrase. Quote the essay rarely, under 15 words, attributed and linked.
- Never present the site as the essay's, and never imply the authors endorse it.
- Keep `prefers-reduced-motion` working: CSS animations stop, the home canvas draws one
  still frame, and SVG animations are paused in `site.js`.
- External requests: Google Fonts only.
