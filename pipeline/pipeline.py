#!/usr/bin/env python3
"""
act-today pipeline
  Input -> Research -> AI analysis -> Structured output -> Database -> Change detection -> Top-N

Usage
  python pipeline.py run --input companies.csv                 # live: fetch sites (+ news if TAVILY_API_KEY)
  python pipeline.py run --input companies.csv --offline fixtures/t0   # no network, reads <slug>.txt
  python pipeline.py top --n 5                                  # today's shortlist from the database
  python pipeline.py changes --name Olyv                        # what changed between the last two snapshots

Standard library only. ANTHROPIC_API_KEY enables the LLM analyser; without it a rule-based fallback runs
(lower quality, clearly labelled, but the whole flow still works).
"""
import argparse, csv, datetime as dt, hashlib, json, os, re, sqlite3, sys
import urllib.request, urllib.error
from html.parser import HTMLParser

MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5-5")
WEIGHTS = {"trigger": .35, "fit": .30, "budget": .20, "reach": .15}
HALF_LIFE_DAYS = 14

# kind -> (verdict, strength 0-3, why). Same table as the dashboard.
RULES = {
    "funding_round":      ("signal", 3, "New capital usually opens new tooling budgets within a quarter."),
    "leadership_c_suite": ("signal", 3, "New executives review tools and processes early (heuristic)."),
    "expansion_plan":     ("signal", 2, "Scaling operations outgrow manual workflows."),
    "launch":             ("signal", 2, "A launch creates integration and support load."),
    "use_of_funds_tech":  ("signal", 2, "Stated spend on tech or AI is direct buying intent."),
    "valuation_up":       ("watch",  1, "Supports budget confidence; not a trigger on its own."),
    "ipo_filing":         ("watch",  1, "IPO prep is a long horizon with quiet-period limits."),
    "leadership_board":   ("watch",  1, "Board appointments rarely change tooling budgets."),
    "metric_claim":       ("watch",  1, "Self-reported metric; context only."),
    "rebrand":            ("noise",  0, "Cosmetic unless paired with a product pivot."),
    "secondary_sale":     ("noise",  0, "Investor liquidity: no new money enters the company."),
}
TRADE_PRESS = ("entrackr.com", "yourstory.com", "economictimes.indiatimes.com", "business-standard.com",
               "techcrunch.com", "inc42.com", "moneycontrol.com", "livemint.com", "dealstreetasia.com")

# ----------------------------------------------------------------------------- research
class _Text(HTMLParser):
    def __init__(self):
        super().__init__(); self.out = []; self.skip = 0
    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript", "svg"): self.skip += 1
    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript", "svg") and self.skip: self.skip -= 1
    def handle_data(self, d):
        if not self.skip and d.strip(): self.out.append(d.strip())

def html_to_text(html):
    p = _Text(); p.feed(html); return re.sub(r"\s+", " ", " ".join(p.out))

def http(url, data=None, headers=None, timeout=20):
    req = urllib.request.Request(url, data=data, headers={"User-Agent": "act-today/1.0", **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")

def slug(s): return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")

def tier_for(url, own_domain):
    host = re.sub(r"^https?://(www\.)?", "", url).split("/")[0]
    if own_domain and own_domain in host: return "T1"        # the company speaking about itself
    if any(t in host for t in TRADE_PRESS): return "T2"       # outlet with original reporting
    return "T3"                                               # aggregator / unknown

def research(name, url, offline=None):
    """Returns (text, sources). sources = [{origin, tier, url}] — one entry per distinct origin."""
    if offline:
        path = os.path.join(offline, slug(name) + ".txt")
        if not os.path.exists(path): return "", []
        with open(path, encoding="utf-8") as fh: txt = fh.read()
        return txt, [{"origin": f"fixture:{slug(name)}", "tier": "T2", "url": "file://" + path}]
    chunks, sources = [], []
    own = re.sub(r"^https?://(www\.)?", "", url).split("/")[0]
    for path in ("", "/about", "/careers", "/blog"):
        try:
            t = html_to_text(http(url.rstrip("/") + path))[:6000]
            if t: chunks.append(f"[SOURCE {url.rstrip('/') + path}]\n{t}"); 
            if t and not any(s["origin"] == own for s in sources):
                sources.append({"origin": own, "tier": "T1", "url": url})
        except Exception:
            continue
    key = os.getenv("TAVILY_API_KEY")
    if key:
        try:
            body = json.dumps({"api_key": key, "query": f"{name} funding OR hires OR launches OR expansion",
                               "max_results": 6, "days": 30}).encode()
            res = json.loads(http("https://api.tavily.com/search", body, {"Content-Type": "application/json"}))
            seen = set()
            for r in res.get("results", []):
                host = re.sub(r"^https?://(www\.)?", "", r["url"]).split("/")[0]
                chunks.append(f"[SOURCE {r['url']}]\n{r.get('content','')[:1500]}")
                if host not in seen:               # republished copies from one host count once
                    seen.add(host); sources.append({"origin": host, "tier": tier_for(r["url"], own), "url": r["url"]})
        except Exception as e:
            print(f"  ! news search failed: {e}", file=sys.stderr)
    return "\n\n".join(chunks), sources

# ----------------------------------------------------------------------------- analysis
SYSTEM = """You are a sales-intelligence analyst. Use ONLY the evidence provided.
Return one JSON object and nothing else, with exactly these keys:
company, summary, signals[{kind,fact,date,evidence_url}], automation_opportunities[{idea,basis}],
personas[{role,name,why}], risks[], factors{trigger,fit,budget,reach}, conflicts[{field,claims[{value,source}],sev}]
Rules: kind must be one of: %s.
basis is "stated" or "inferred". factors are integers 0-100. Use null for anything unknown; never guess names,
numbers or dates. If two sources disagree, add a conflict (sev "open" if you cannot resolve it, else "minor").""" % ", ".join(RULES)

REQUIRED = ("company", "summary", "signals", "automation_opportunities", "personas", "risks", "factors", "conflicts")

def validate(a):
    for k in REQUIRED:
        if k not in a: raise ValueError(f"missing key: {k}")
    for s in a["signals"]:
        if s.get("kind") not in RULES: raise ValueError(f"unknown signal kind: {s.get('kind')}")
    for f in ("trigger", "fit", "budget", "reach"):
        v = a["factors"].get(f)
        if not isinstance(v, int) or not 0 <= v <= 100: raise ValueError(f"factor {f} must be int 0-100")
    return a

def extract_json(text):
    m = re.search(r"\{.*\}", text, re.S)
    if not m: raise ValueError("no JSON object in model output")
    return json.loads(m.group(0))

def llm_analyze(name, evidence):
    key = os.environ["ANTHROPIC_API_KEY"]; err = None
    for attempt in range(2):
        prompt = f"Company: {name}\n\nEVIDENCE\n{evidence[:24000]}"
        if err: prompt += f"\n\nYour previous answer was invalid ({err}). Return corrected JSON only."
        body = json.dumps({"model": MODEL, "max_tokens": 2000, "system": SYSTEM,
                           "messages": [{"role": "user", "content": prompt}]}).encode()
        raw = json.loads(http("https://api.anthropic.com/v1/messages", body,
                              {"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"}, 90))
        text = "".join(b.get("text", "") for b in raw["content"] if b["type"] == "text")
        try: return validate(extract_json(text))
        except Exception as e: err = str(e)
    raise RuntimeError(f"LLM output invalid after retry: {err}")

PATTERNS = [  # fallback analyser: (kind, regex)
    ("funding_round", r"(?:raised|raises|secured|secures)\s+(?:Rs\.?|INR|₹|\$|US\$)\s?[\d.,]+\s?(?:crore|cr|million|mn|billion|bn|M|B)?"),
    ("leadership_c_suite", r"appoint(?:ed|s)\s+[A-Z][\w.'-]+(?:\s[A-Z][\w.'-]+){0,3}\s+as\s+(?:its\s+)?(?:Chief|CFO|CTO|CEO|COO|CMO|Head)"),
    ("expansion_plan", r"(?:expand\w*|from\s+\d+\s+to\s+\d+|\d+\+?\s+dark stores)"),
    ("launch", r"(?:launch(?:ed|es)?|emerged? from stealth)"),
    ("use_of_funds_tech", r"(?:AI model|technology|tech(?:nology)? (?:improvement|stack)|AI development)"),
    ("secondary_sale", r"(?:sold|exit(?:ed|s)?|offload\w*).{0,60}stake"),
    ("ipo_filing", r"\b(?:IPO|DRHP|RHP)\b"),
    ("rebrand", r"formerly (?:known as )?[A-Z]\w+"),
]
FIT_WORDS = ("operations", "workflow", "support", "manual", "logistics", "orders", "compliance",
             "customers", "inventory", "reporting", "reconciliation", "onboarding")

def rule_analyze(name, evidence):
    signals = []
    for kind, rx in PATTERNS:
        for m in list(re.finditer(rx, evidence, re.I if kind not in ("leadership_c_suite",) else 0))[:2]:
            s = max(0, m.start() - 60); signals.append({"kind": kind, "fact": evidence[s:m.end() + 60].strip(),
                                                        "date": None, "evidence_url": None})
    strong = sum(1 for s in signals if RULES[s["kind"]][1] >= 2)
    fit = min(95, 30 + 6 * sum(evidence.lower().count(w) for w in FIT_WORDS))
    people = re.findall(r"(?i:CEO|CFO|CTO|founder|co-founder)[,:]?\s+([A-Z][a-z]+\s[A-Z][a-z]+)", evidence)
    return {"company": name, "summary": "(rule-based fallback; no LLM key set)", "signals": signals,
            "automation_opportunities": [], "personas": [{"role": "founder/decision maker", "name": n, "why": "named in text"} for n in people[:3]],
            "risks": ["Rule-based analysis: verify every fact before acting."],
            "factors": {"trigger": min(95, 35 + 20 * strong), "fit": int(fit),
                        "budget": 70 if any(s["kind"] == "funding_round" for s in signals) else 40,
                        "reach": 70 if people else 35},
            "conflicts": []}

def analyze(name, evidence):
    if os.getenv("ANTHROPIC_API_KEY"):
        try: return llm_analyze(name, evidence), "llm"
        except Exception as e: print(f"  ! LLM failed ({e}); using fallback", file=sys.stderr)
    return rule_analyze(name, evidence), "rules"

# ----------------------------------------------------------------------------- scoring
def freshness(age_days): return 0.4 + 0.6 * 0.5 ** (age_days / HALF_LIFE_DAYS)

def confidence(origins, conflicts):
    tiers = [o["tier"] for o in origins]; c = .35
    if "T1" in tiers: c += .25
    if len(origins) >= 2: c += .15
    if len(origins) >= 3: c += .10
    c -= .07 * sum(1 for k in conflicts if k.get("sev") == "open")
    return max(.2, min(.95, round(c, 2)))

def score(a, origins, age_days, w=WEIGHTS):
    f = a["factors"]; conf = confidence(origins, a["conflicts"])
    raw = w["trigger"] * f["trigger"] * freshness(age_days) + w["fit"] * f["fit"] + w["budget"] * f["budget"] + w["reach"] * f["reach"]
    final = raw * (.75 + .25 * conf)
    strength = max([RULES[s["kind"]][1] for s in a["signals"]] or [0])
    if strength >= 2 and f["fit"] < 45: v = "Low fit"
    elif strength >= 2 and conf >= .6: v = "Act"
    elif strength >= 2: v = "Verify first"
    elif strength == 1: v = "Watch"
    else: v = "Skip"
    return {"raw": round(raw, 1), "final": round(final, 1), "confidence": conf, "verdict": v, "strength": strength}

def action_line(name, a, verdict):
    p = next((x for x in a["personas"] if x.get("name")), None)
    who = f"{p['name']} ({p['role']})" if p else "the decision maker (name not found: check LinkedIn)"
    top = max(a["signals"], key=lambda s: RULES[s["kind"]][1], default=None)
    why = f" Trigger: {top['kind'].replace('_', ' ')}." if top else ""
    return {"Act": f"Reach out to {who}.{why}", "Verify first": f"Verify facts from a second source, then approach {who}.{why}",
            "Low fit": "Strong signal but unlikely buyer; consider a partnership angle.", "Watch": "No buying trigger yet.", "Skip": "Ignore."}[verdict]

# ----------------------------------------------------------------------------- storage
def db_connect(path=None):
    if os.getenv("DATABASE_URL", "").startswith("postgres"):
        sys.exit("Postgres: run schema.sql, then adapt db_connect() with psycopg (SQLite is the zero-setup default).")
    con = sqlite3.connect(path or "intel.db"); con.row_factory = sqlite3.Row
    con.executescript("""
    CREATE TABLE IF NOT EXISTS companies(id INTEGER PRIMARY KEY, name TEXT, url TEXT UNIQUE, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
    CREATE TABLE IF NOT EXISTS snapshots(id INTEGER PRIMARY KEY, company_id INT, taken_at TEXT DEFAULT CURRENT_TIMESTAMP,
        content_hash TEXT, analysis TEXT, sources TEXT, analyser TEXT);
    CREATE TABLE IF NOT EXISTS scores(snapshot_id INTEGER PRIMARY KEY, raw REAL, final REAL, confidence REAL, verdict TEXT, action TEXT);
    CREATE TABLE IF NOT EXISTS changes(id INTEGER PRIMARY KEY, company_id INT, snapshot_id INT, kind TEXT, fact TEXT, verdict TEXT, strength INT, why TEXT);
    CREATE TABLE IF NOT EXISTS done(company_id INT, day TEXT, PRIMARY KEY(company_id, day));
    """); return con

def norm(s): return re.sub(r"\W+", " ", s.lower()).strip()

def diff(prev, cur):
    """Signals present now that were not in the previous snapshot. Same kind+fact text = unchanged = ignored."""
    seen = {(s["kind"], norm(s["fact"])) for s in (prev["signals"] if prev else [])}
    out = []
    for s in cur["signals"]:
        if (s["kind"], norm(s["fact"])) in seen: continue
        v, st, why = RULES[s["kind"]]; out.append({**s, "verdict": v, "strength": st, "why": why})
    return out

def process(con, name, url, offline=None, today=None):
    text, sources = research(name, url, offline)
    if not text: print(f"  - {name}: no evidence found, skipped"); return
    a, analyser = analyze(name, text)
    h = hashlib.sha256(text.encode()).hexdigest()
    row = con.execute("SELECT id FROM companies WHERE url=?", (url,)).fetchone()
    cid = row["id"] if row else con.execute("INSERT INTO companies(name,url) VALUES(?,?)", (name, url)).lastrowid
    prev = con.execute("SELECT analysis FROM snapshots WHERE company_id=? ORDER BY id DESC LIMIT 1", (cid,)).fetchone()
    if prev and con.execute("SELECT 1 FROM snapshots WHERE company_id=? AND content_hash=? ORDER BY id DESC LIMIT 1", (cid, h)).fetchone():
        print(f"  = {name}: unchanged since last run"); return
    sid = con.execute("INSERT INTO snapshots(company_id,content_hash,analysis,sources,analyser) VALUES(?,?,?,?,?)",
                      (cid, h, json.dumps(a), json.dumps(sources), analyser)).lastrowid
    changes = diff(json.loads(prev["analysis"]) if prev else None, a)
    dates = [s["date"] for s in a["signals"] if s.get("date")]
    age = (dt.date.today() - dt.date.fromisoformat(max(dates))).days if dates else 7   # unknown date: assume a week old
    sc = score(a, sources, age); act = action_line(name, a, sc["verdict"])
    con.execute("INSERT INTO scores VALUES(?,?,?,?,?,?)", (sid, sc["raw"], sc["final"], sc["confidence"], sc["verdict"], act))
    for c in changes:
        con.execute("INSERT INTO changes(company_id,snapshot_id,kind,fact,verdict,strength,why) VALUES(?,?,?,?,?,?,?)",
                    (cid, sid, c["kind"], c["fact"], c["verdict"], c["strength"], c["why"]))
    con.commit()
    print(f"  + {name}: {sc['final']:>5} [{sc['verdict']}] conf={sc['confidence']} analyser={analyser} new_signals={len(changes)}")

def latest(con):
    return con.execute("""SELECT c.id cid, c.name, s.final, s.raw, s.confidence, s.verdict, s.action FROM companies c
        JOIN snapshots sn ON sn.id=(SELECT MAX(id) FROM snapshots WHERE company_id=c.id) JOIN scores s ON s.snapshot_id=sn.id
        ORDER BY s.final DESC""").fetchall()

def cmd_top(con, n):
    today = dt.date.today().isoformat()
    done = {r[0] for r in con.execute("SELECT company_id FROM done WHERE day=?", (today,))}
    rows = [r for r in latest(con) if r["verdict"] in ("Act", "Verify first") and r["cid"] not in done][:n]
    print(f"\nToday's {n} (of {len(latest(con))} watched):")
    for i, r in enumerate(rows, 1): print(f"{i}. {r['name']:<18} {r['final']:>5}  [{r['verdict']}]  {r['action']}")
    if not rows: print("Nothing worth acting on today.")

def cmd_changes(con, name):
    r = con.execute("SELECT id FROM companies WHERE name=?", (name,)).fetchone()
    if not r: sys.exit("unknown company")
    rows = con.execute("SELECT * FROM changes WHERE company_id=? ORDER BY id DESC LIMIT 20", (r["id"],)).fetchall()
    for c in rows: print(f"[{c['verdict'].upper():6}] {c['kind']}: {c['fact'][:110]}\n         why: {c['why']}")

def main():
    ap = argparse.ArgumentParser(); sp = ap.add_subparsers(dest="cmd", required=True)
    r = sp.add_parser("run"); r.add_argument("--input", required=True); r.add_argument("--offline"); r.add_argument("--db")
    t = sp.add_parser("top"); t.add_argument("--n", type=int, default=5); t.add_argument("--db")
    c = sp.add_parser("changes"); c.add_argument("--name", required=True); c.add_argument("--db")
    a = ap.parse_args(); con = db_connect(a.db)
    if a.cmd == "run":
        for row in csv.DictReader(open(a.input, encoding="utf-8", newline="")):
            try: process(con, row["name"], row["url"], a.offline)
            except Exception as e: print(f"  ! {row['name']}: {e}", file=sys.stderr)   # one failure never stops the batch
        cmd_top(con, 5)
    elif a.cmd == "top": cmd_top(con, a.n)
    else: cmd_changes(con, a.name)

if __name__ == "__main__":
    main()
