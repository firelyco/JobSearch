# JobSearch

A free, self-hosted job posting monitor that pulls fresh openings from public ATS APIs (Greenhouse, Lever, Ashby, Workday) every 15 minutes and surfaces them on a static GitHub Pages dashboard.

Built to solve "notify me the instant a matching role is posted" without scraping LinkedIn, paying for a job board, or running infrastructure.

## How it works

```
GitHub Actions cron (every 15 min)
  → Python poller hits 4 ATS APIs concurrently
  → Filters and scores matches against your role config
  → Writes jobs.json to docs/
  → GitHub Pages auto-rebuilds
  → Dashboard at firelyco.github.io/JobSearch reads jobs.json
```

The dashboard is a single HTML page. Status tracking (applied / interviewing / rejected) lives in your browser's localStorage so your job-search state isn't in the repo.

## Setup

1. Clone this repo locally
2. Edit `config/companies.yml` to pick the companies you want to monitor
3. Edit `config/role_config.yml` to tune your role keywords, locations, and score weights
4. Push to GitHub
5. Enable GitHub Pages: Settings → Pages → Source: "Deploy from a branch" → Branch: `main`, folder: `/docs`
6. The Actions workflow runs automatically every 15 minutes (or trigger manually under the Actions tab)
7. Visit `https://<your-username>.github.io/JobSearch/`

## Architecture decisions (and why)

- **Why public ATS APIs, not LinkedIn?** LinkedIn's own alerts have an 18-48 hour crawl delay. ATS APIs return fresh postings within minutes of being posted. Free, legal, no auth.
- **Why GitHub Actions, not Railway?** Free, no infra, state lives in the repo itself, the workflow IS the deploy mechanism.
- **Why static page + localStorage?** Zero backend cost. Status state stays on your device. Easy to graduate to a real app later — the poller code is fully reusable.
- **Why rule-based scoring, not AI?** Ship v0 fast. Add Claude API scoring in v0.1 once the regex limits become annoying.

## File layout

```
JobSearch/
├── config/
│   ├── companies.yml       # ATS sources to monitor
│   └── role_config.yml     # role keywords, locations, score weights
├── src/
│   ├── adapters/           # one per ATS platform
│   │   ├── greenhouse.py
│   │   ├── lever.py
│   │   ├── ashby.py
│   │   └── workday.py
│   ├── scorer.py           # rule-based 0-100 score
│   ├── dedupe.py           # state.json round-trip
│   └── poll.py             # main entrypoint
├── docs/                   # served by GitHub Pages
│   ├── index.html          # the dashboard
│   ├── styles.css
│   ├── app.js
│   └── jobs.json           # written by poller, read by page
├── state/
│   └── seen_jobs.json      # dedupe state
└── .github/workflows/
    └── poll.yml            # cron schedule
```

## Roadmap

- **v0** (this): notifications dashboard, rule-based scoring
- **v0.1**: AI-based scoring with Claude Haiku for better signal/noise
- **v0.2**: resume tailoring per job (Claude Sonnet, outputs .docx)
- **v1**: real app with auth, multi-user, email/SMS notifications

## License

MIT
