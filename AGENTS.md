# JobSearch publication rules

Before publishing code or job/fit data, compare the proposed shortlist with the last published `origin/main` snapshot using `node src/validate_shortlist.cjs`. Run it again with `--check-only` after rebasing and before pushing. Publish `docs/shortlist_validation.json` with the update.

Do not silently remove a previously reviewed Strong role because an assessment provider/model or context changed. Retain its prior verdict with a visible reassessment label only while the same skill, scoring rules and employer job inputs still match. A newer completed assessment takes precedence. A changed skill or posting requires fresh review; never present a stale result as currently validated.

An unchanged active job losing Strong without a completed replacement or an explicit eligibility reason must block publication. Report source removals, changed employer inputs, changed rules, and completed downgrades separately. Check the actual dashboard under the user's default filters as well as the automated comparison. Counts alone are insufficient.

Keep career fit, salary eligibility, and assessment freshness distinct. Exclude salary only when a published USD annual base maximum is below $250,000. Undisclosed salary stays eligible but unverified.
