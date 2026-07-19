# Phase 1 — single-file offline demo

`demo.html` is the complete Phase 1 application: dashboard, data entry, records, targets-vs-collections, printable reports, and audit log — all in one HTML file with sample data embedded. Open it in any browser; nothing to install. Data you enter stays in that browser's localStorage.

This is also the file to host for a **live demo** (GitHub Pages, Netlify, etc.) — it's fully static.

To rebuild it after editing the template or sample data:

```bash
cd _build_sources
python3 build.py ../demo.html
```

`build.py` injects `seed.json` (sample transactions), `minnames.json` (ministry full names), and the letterhead logos into `app_template.html`.

> All sample data — transactions, amounts, account numbers, targets, and names — is fictitious.
