# Local PM frontend dependencies

Migrated from the read-only design export (2026-10-16). No demo/mock/example data is included.

| Package | Version | License | Distribution |
|---|---|---|---|
| React / React DOM | 18.3.1 | MIT | bundled in app.js; licenses/react*-LICENSE |
| scheduler | 0.23.2 | MIT | React DOM dependency; licenses/scheduler-LICENSE |
| rough.js | 4.6.6 | MIT | design/vendor/rough.esm.js; licenses/roughjs-LICENSE |
| Noto Serif SC 600 (@fontsource/noto-serif-sc) | 5.3.1 | SIL OFL 1.1 | 101 original woff2 subsets; licenses/_fontsource_noto-serif-sc-LICENSE |
| LXGW WenKai Regular (lxgw-wenkai-webfont) | 1.7.0 | font: SIL OFL 1.1; package: MIT | 97 original woff2 subsets; licenses/lxgw-wenkai-webfont-* |
| esbuild (build tool only) | 0.25.10 | MIT | npm lockfile; not required at runtime |

Only the two weights referenced by design/styles/fonts.css are shipped (about 8 MB). Font binaries are unchanged and use the design export's private CSS family names. All runtime resources are local; no CDN, login, OAuth or cloud API is used. License originals are copied into licenses/ and the build output.
