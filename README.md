# Act Today: Product Engineer test submission

**Candidate:** Manjunath K N (Maxx), Bengaluru
**Live dashboard:** `[PASTE LIVE LINK]` (also in this folder as `dashboard/index.html`, open it in any browser)
**Time spent:** `[FILL IN]`

The short version: a daily "act today" desk for a founder. It watches companies, scores them with a formula that shows its working, says who to approach and what to write, and tells you honestly how much to trust each fact. A pipeline keeps it fresh without manual work.

---

## Assumptions (there was no spec, so I made these explicit)

1. **What the founder sells.** The JD doesn't say, so I assumed AI and automation products or services for funded Indian startups. If that's wrong, the weights are adjustable in the dashboard (Fit, Budget, Trigger, Reach) and the fit scores are one field per company.
2. **Where to look.** Instead of a random list of 12 companies, I used companies with a *fresh public trigger*: the 21–26 Sep 2026 funding report plus follow-up searches. Data is as of **29 Sep 2026**.
3. **"Acting" means a first conversation,** not a closed deal. So the unit of output is one concrete next step per company.

## What's real and what isn't

- **Real:** the 12 companies, the facts about them and the source links were researched from public sources on 29 Sep 2026. Every claim in the dashboard links to its source in the Evidence tab.
- **Judgement, made visible:** the 0–100 factor scores (fit, budget, reach, trigger) are my analyst judgements with a written reason next to each. Anything I inferred rather than read is tagged "inferred".
- **"Before" snapshots** in *What changed* come from what each source says about the prior state (previous round, prior stake, earlier name). Where no prior was found, the table says so.
- **Pipeline tested:** 6 unit tests pass, and I ran two offline snapshots end to end (output below). **Not tested live:** the Claude API call, Tavily news search and the n8n workflow, because they need keys and an n8n instance I didn't have. They're written to the documented APIs and need a smoke test.
- The offline fallback analyser is deliberately crude (regex rules). It exists so the whole flow runs without a key. The real analysis is the LLM path.

---

## The nine tasks

**1. Company intelligence.** Each company has a *Brief* tab: what they do, why now, where automation could fit (tagged "inferred"), watch-outs and good questions to ask. The pipeline produces the same structure as strict JSON from any company URL.

**2. Opportunity scoring.** `Score = (0.35·Trigger + 0.30·Fit + 0.20·Budget + 0.15·Reach) × (0.75 + 0.25·Confidence)`.
- Trigger fades with age (half-life 14 days), so stale news stops outranking fresh news.
- Confidence multiplies the score, so a great-looking company with one unverified source can't float to the top. At most 25% of a score can be lost to data doubt.
- The score bar shows this: solid = score now, hatched = what's held back until verified.
- Result: Olyv (72.8), Rio Health (70.1), Disha (68.9) lead.

**3. Right person.** For every company, plus the full reasoning for Olyv: the **new CFO, Ravi Sharma** first, because a finance-automation pitch needs the economic buyer and he is new in the seat. Then the CEO as sponsor after 4 business days, and the head of collections as champion (name not in sources, so I say "find via LinkedIn" instead of guessing). Where the sources don't give a title (Disha's founders), the dashboard says so.

**4. Outreach.** Drafts for the three "Act" companies, each about 100 words, one idea and one small ask. The *Why this doesn't read as generic* table lists every fact used and where it came from, and labels any opinion. Companies still awaiting verification get no draft on purpose: a message built on unverified facts is exactly the spam this is meant to avoid.

**5. Automation.** `pipeline/pipeline.py`: URL → research (site pages + news) → LLM analysis (strict JSON, validated, one retry) → score → database → diff. Standard library only, SQLite by default, `schema.sql` for Postgres. `n8n_workflow.json` is the same flow as an n8n workflow (webhook in, Claude, Postgres, notify). One bad company never stops the batch.

**6. Trigger detection.** Every signal type has a rule: *signal*, *context* or *noise*, with a reason. Examples from real data: Olyv's new CFO is a **signal**; Disha's rename from Curelink is **noise**; Mastercard selling its Pine Labs stake is **noise** (no new money enters the company); Spinny's IPO pre-filing is **context** (unconfirmed, long horizon). The pipeline's diff only reports signals that are *new* since the last snapshot.

**7. Data reliability.** The Evidence tab lists, per company, where sources disagree and what I used. Real cases found:
- **Disha's round** was reported as $4.6M, $5.2M, $5.3M and ~$6M. All outlets share one INR figure from filings (Rs 43.88 Cr), so the USD gap is just exchange rates. I lead with INR and show USD as approximate.
- **Ema's HQ** appears as Mountain View, Bengaluru and "Indian startup". The likely cause is that its lead investor is Bengaluru-based (my hypothesis, labelled as such).
- **Sol's HQ** is San Francisco per its press release but "Bengaluru-based" in a lower-tier outlet. Left **open** instead of picking one.
- **Olyv's HQ** was New Delhi in the press-release dateline, which is the PR agency's location, not the company's.

Rules, in order: primary source beats rewrite; exact beats rounded; earliest dated report beats syndicated copies; ten outlets repeating one press release count as one origin; if still unclear, say "unconfirmed" and don't quote a number. Open conflicts reduce confidence.

**8. Dashboard.** `dashboard/index.html`, single file, no build step. Ranked list, detail tabs, adjustable weights, dark mode and mobile layout.

**9. Requirement change ("only the 5 things worth acting on today").** My answer was to change the *product*, not just trim the list:
- Default view is now **Today's 5**, each with one sentence of action.
- Only **Act** or **Verify first** companies qualify; low-fit, noise-only and watch-list companies are excluded even if their score is high.
- A **Below the line** section keeps the other seven visible with a one-line reason, so nothing disappears silently.
- **Mark done** removes an item and the next candidate slides in, so the list is a daily to-do and not a static report.
- If fewer than five qualify, it shows fewer. It never pads.
- The old 12-company view is one toggle away.

---

## Run it

```bash
cd pipeline
python3 -m unittest tests.test_pipeline            # 6 tests, no network needed

# Demo with two points in time (no keys, no network):
python3 pipeline.py run --input fixtures/companies_demo.csv --offline fixtures/t0 --db demo.db
python3 pipeline.py run --input fixtures/companies_demo.csv --offline fixtures/t1 --db demo.db
python3 pipeline.py changes --name Olyv --db demo.db
python3 pipeline.py top --n 5 --db demo.db

# Live, with real analysis (edit companies.csv with real URLs first):
export ANTHROPIC_API_KEY=...   TAVILY_API_KEY=...     # see .env.example
python3 pipeline.py run --input companies.csv
```

Demo output, second run (the diff correctly separates signal from noise):

```
[SIGNAL] leadership_c_suite: ... Olyv appoints Ravi Sharma as Chief Financial Officer ...
[NOISE ] secondary_sale: Pine Labs saw Mastercard exit its stake ...   <- investor liquidity, ignored
```

n8n: import `n8n_workflow.json`, attach Postgres credentials, set `ANTHROPIC_API_KEY`, `TAVILY_API_KEY`, then:
`curl -X POST <n8n-url>/webhook/act-today -H 'Content-Type: application/json' -d '{"name":"Olyv","url":"https://..."}'`

## Where it's weak, and what I'd do in week one

1. **Run the live path** (Claude + Tavily + n8n) against 20 real companies and measure how often the JSON validates and how often a human disagrees with the verdict.
2. **Replace my judged factor scores with measured ones** where possible (hiring volume from careers pages, tech-stack signals), and keep the judgement only for fit.
3. **Learn from outcomes.** Log which "Act" companies replied, then tune the weights from that. Right now the weights are my best guess.
4. **Per-field confidence** instead of per-company, so "funding amount: high, HQ: low" is shown precisely.
5. **Scheduled digest** to email or Slack at 9am with the five items.

## AI tools used

Claude for research, code and drafting. `[ADD ANYTHING ELSE YOU USED, AND ONE LINE ON WHAT YOU CHECKED OR CHANGED YOURSELF]`
