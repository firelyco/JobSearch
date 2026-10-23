# JobSearch

A free, self-hosted job posting monitor that pulls fresh openings from public ATS APIs (Greenhouse, Lever, Ashby, Workday) once daily at 6:00 AM Eastern (America/New_York, following daylight-saving changes) and surfaces them on a static GitHub Pages dashboard.

Built to solve "notify me the instant a matching role is posted" without scraping LinkedIn, paying for a job board, or running infrastructure.

## How it works

```
GitHub Actions cron (daily, 6 AM Eastern)
  → Python poller hits 4 ATS APIs concurrently
  → Filters and scores matches against your role config
  → Writes jobs.json to docs/
  → GitHub Pages auto-rebuilds
  → Dashboard at firelyco.github.io/JobSearch reads jobs.json
```

The dashboard is a single HTML page. Status tracking (applied / interviewing / rejected) lives in your browser's localStorage so your job-search state isn't in the repo.

## Daily refresh and assessment

The scheduled job pull starts once each day at **6:00 AM Eastern**, using `America/New_York` so it stays at 6 AM in EST and EDT. GitHub may delay scheduled starts. Code pushes do not trigger extra job pulls; manual refresh remains available in Actions.

Each successful pull triggers profile-fit assessment and salary verification. Assessment batches continue automatically while eligible pending jobs remain, reusing completed results. The September 15, 2026 cutoff and existing paid-attempt limits still apply; exhausted failures remain unresolved for inspection rather than triggering unlimited paid retries. Salary verification follows the pull and has no separate recurring schedule.

## Setup

1. Clone this repo locally
2. Edit `config/companies.yml` to pick the companies you want to monitor
3. Edit `config/role_config.yml` to tune your role keywords, locations, and score weights
4. Push to GitHub
5. Enable GitHub Pages: Settings → Pages → Source: "Deploy from a branch" → Branch: `main`, folder: `/docs`
6. The Actions workflow runs automatically once daily at 6:00 AM Eastern (America/New_York, following daylight-saving changes) (or trigger manually under the Actions tab)
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

## Employer-independent discovery

The original `config/companies.yml` feeds are preserved. The poller also reads
`config/discovery.yml`: public Remotive/Arbeitnow feeds, public role searches,
and automatically learned Greenhouse, Lever, Ashby and Workday boards.
New employer boards persist in `state/discovered_boards.json`; they rotate
through a bounded polling budget. Job IDs from existing feeds take precedence
when a discovery result has the same canonical URL, preserving application
statuses, cached fit assessments and resume links.

Add `BRAVE_SEARCH_API_KEY` as a GitHub Actions secret to enable cross-company
web searches. Without it, public feeds and learned boards still run, but the
dashboard explicitly reports web discovery as not configured. Searches use only
public role keywords, not the private profile. Search snippets are leads:
recognized ATS boards are fetched directly; employer pages must provide a
structured JobPosting before a generic result becomes a job record.

Public feed/search responses are cached for six hours. Remotive attribution and
links are retained; its public feed delays postings by 24 hours and recommends
at most four calls daily. Arbeitnow primarily covers Europe. These feeds alone
are not sufficient for comprehensive US/startup discovery. Relevant career
pages may require another adapter, and blocked/redirected pages are reported.
No source combination guarantees every opening.

The 14-day publication cutoff is disabled: a listing can remain relevant while
open. Broader responsibility matches go to existing profile-based fit scoring;
hard junior/title/company exclusions still apply. The dashboard includes an
adjacent-role filter selected by default, source coverage and missing-URL checks.
On source failure, previous jobs are retained with explicitly unverified open
status instead of falsely treating an outage as closure. Known pagination limits
are shown as incomplete coverage.

Investigate or learn a missing posting without manually adding a company:

```sh
python -m src.explain_missing --url https://jobs.ashbyhq.com/new-company/job-id
python -m src.explain_missing --url https://jobs.ashbyhq.com/new-company/job-id --learn
```

The command distinguishes published-but-filtered jobs, unsupported pages and
employers not discovered in the snapshot. `--learn` records a supported employer
board for future polls. Commit the registry when learning locally. A poll must
run and publish its outputs before new discoveries appear on GitHub Pages.

Regression checks:

```sh
python -m unittest discover -s tests -q
```

Frozen fixtures in `tests/fixtures/baseline_*` capture the company configuration,
136 jobs and their pre-change scores. Tests assert unchanged configured fetch
tasks, scores, reasons and identities, plus full-poll retention when adding a new
employer. Tests use mocked network/LLM calls and do not spend API credits.


### Collection coverage and observed-listing audit

The daily 6 AM New York refresh collects supported employer feeds and discovers new employers through public directories and paginated Brave searches. GitHub and Microsoft have direct public-feed connectors; Walmart uses its current Workday host and employer-provided TPM discipline facet. Workday pages beyond 200 results. Connectors that expose a total report collected/expected counts, and incomplete enumeration is flagged rather than reported as success.

`docs/coverage_audit.json` reconciles `config/coverage_watchlist.json` with each refresh, records missing observed listings and title-screened candidates, and exposes source failures. Exact requisition matches are distinguished from title/location matches needing an exact employer link. Multi-location Workday candidates resolve their locations before assessment selection. Plain TPM/EPM titles enter JD-based level review at any employer; International and Associate Director titles are not treated as interns or junior associates. Cached or deferred discovered boards never count as freshly verified.

This measures coverage of specific employer queries and known postings, not every active internet listing. LinkedIn repost dates may differ from the original employer date. The September 15 assessment cutoff remains in force even when an older job is still active. Collection never implies a completed current-skill fit assessment. Existing verdicts and the two-attempt paid inference budget are preserved.

Location display enrichment preserves a current completed assessment when the employer snapshot, skill and verified US eligibility match. In that case city names are stored as supplementary `resolved_locations`; they do not change the assessment identity. Unassessed roles and changed substantive inputs still require review.
