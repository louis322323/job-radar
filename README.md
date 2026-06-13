# Job-radar

Daglig pipeline: henter jobs fra aggregatorer + små orgs → dedup → gratis
keyword-filter (dine hårde filtre) → LLM-scoring efter `scoring-rules-compact.md`
→ email-digest + dashboard.

```
fetch (sources.yaml) → dedup (sqlite) → prefilter (filters.yaml, gratis, dræber ~90%)
→ LLM-score (kun overlevere, ~5-20/dag) → results.json + email
```

## Hvorfor det her er billigt

Kun jobs der overlever keyword-filteret rammer LLM'en. Ved 10–20 jobs/dag:
DeepSeek ≈ 5 kr/måned · Gemini Flash = gratis tier rækker · Claude Haiku ≈ få kr/måned.
Det er en API-nøgle med pay-per-use — helt adskilt fra claude.ai-abonnementets beskedgrænser.

## Opsætning (30 min)

### 1. Lokal test
```bash
pip install -r requirements.txt
python main.py --test-source jobindex-borgerinddragelse   # test én kilde
python main.py --dry-run                                   # hele pipelinen uden LLM/email
```

### 2. API-nøgle (vælg én)
- **DeepSeek** (default): platform.deepseek.com → API key → sæt `DEEPSEEK_API_KEY`
- **Gemini** (gratis): aistudio.google.com → API key → `GEMINI_API_KEY` + `LLM_PROVIDER=gemini`
- **Claude**: console.anthropic.com → `ANTHROPIC_API_KEY` + `LLM_PROVIDER=anthropic`

### 3. GitHub Actions (gratis cron i skyen — din Mac skal ikke være tændt)
1. Opret **privat** repo, push hele mappen
2. Settings → Secrets and variables → Actions:
   - Secret: `DEEPSEEK_API_KEY` (eller den du valgte)
   - Variable: `LLM_PROVIDER` (hvis ikke deepseek)
   - Email (valgfrit): `SMTP_HOST`=smtp.gmail.com, `SMTP_PORT`=587,
     `SMTP_USER`=din gmail, `SMTP_PASS`=app password (myaccount.google.com/apppasswords),
     `DIGEST_TO`=din email
3. Actions-fanen → "daily-job-radar" → Run workflow (manuel test)
4. Kører derefter selv hver morgen 07:30 og committer resultater tilbage

### 4. Dashboard på Netlify
Netlify → Add new site → Import from Git → vælg repoet →
**Publish directory: `dashboard`** → deploy. Hver radar-kørsel committer
`results.json`, og Netlify re-deployer automatisk. Sæt sitet privat
(Netlify password protection) hvis du ikke vil have det offentligt.

## Vedligehold

- **Nye søgeord/kilder**: redigér `config/sources.yaml` — Jobindex-RSS-linjer
  er bare URL-encodede søgninger, kopiér mønstret
- **Filteret er for stramt/løst**: redigér `config/filters.yaml`
- **Scoring-regler ændret**: overskriv `config/scoring-prompt.md`
- **`html_list`-kilder** (finn.no, eurobrussels, interamt): CSS-selektorer
  SKAL verificeres mod de rigtige sider første gang — kør `--test-source`
  og justér selektorerne i sources.yaml hvis output er tomt
- **`page_watch`-kilder** kan aldrig gå i stykker — de siger bare
  "siden har ændret sig, kig selv"

## Kendte begrænsninger

- Jobindex-RSS-URL-formatet skal verificeres ved første kørsel (kør --test-source).
  Hvis RSS ikke virker, har Jobindex også email-agenter som backup.
- Spanien (Barcelona/Madrid) er ikke automatiseret — ustabile kilder +
  sprogbarriere. Anbefaling: manuel månedlig gennemgang, eller tilføj en
  page_watch på en gemt hacesfalta.org-søge-URL.
- LinkedIn-kilder kan ikke scrapes (login-mur) — brug LinkedIn's egne jobagenter.
- Snippets fra lister er korte; LLM'en scorer på det tilgængelige. Jobs tæt
  på grænsen (65-75) fortjener et manuelt klik.
