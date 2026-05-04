# OpRadar
### AI-powered opportunity assessor for consultants and job seekers

OpRadar reads job postings and consultancy calls from a Notion database, fetches their full text from the web, and uses Claude to score each opportunity against your personal profile across seven weighted dimensions. Results — scores, fit analysis, risks, positioning advice, and level-of-effort estimates — are written back to Notion so you can review and prioritize from any device.

## What it does

- **Fetches job postings automatically** — static pages, JavaScript-rendered sites (via Playwright), and PDF terms of reference all supported
- **Scores fit across 7 weighted dimensions** — Technical Fit, Thematic Fit, Modality, Compensation, Geography, Deadline Practicality, and Strategic Value
- **Produces structured criterion-by-criterion analysis** — not a summary, but an honest competitive assessment with named evidence mapped to specific requirements
- **Estimates level of effort in working days** — min/max range with phase-by-phase breakdown based on scope and deliverables
- **Writes everything back to Notion** — scores, recommendation, positioning advice, LOE, inferred title and organization, all in one structured database you can filter and sort

## How it works

```
You add a URL to Notion
        ↓
assess.py fetches the page (static → Playwright fallback → PDF)
        ↓
Claude assesses fit against your profile.yaml
        ↓
Results written back to Notion (Status → Assessed)
```

## Prerequisites

- Python 3.9 or later
- A Notion account (free tier works)
- An Anthropic API key — [console.anthropic.com](https://console.anthropic.com) — pay per use, approximately **USD 0.04–0.06 per assessment** at current Claude Sonnet pricing
- Comfort running Python scripts in a terminal

## Setup

### 1. Clone the repo

```bash
git clone https://github.com/petermartinezllabata/OpRadar.git
cd OpRadar
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
playwright install chromium
```

### 3. Create your Notion integration and database

**Create an internal integration:**

1. Go to [notion.so/profile/integrations](https://www.notion.so/profile/integrations)
2. Click **New integration**, give it a name (e.g. "OpRadar"), and save
3. Copy the **Internal Integration Secret** — this is your `NOTION_TOKEN`

**Create the Notion database:**

1. Create a new full-page database in Notion (type `/database`)
2. Open the database, click **Share** (top right), and connect your integration by name
3. Copy the **database ID** from the URL:
   ```
   https://notion.so/yourworkspace/792c6c4ae8b448f79441bc493af9b94f?v=...
                                   ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
                                   This 32-character string is your NOTION_DATABASE_ID
   ```

### 4. Configure API keys

```bash
cp .env.example .env
```

Edit `.env` and fill in your three values:

```
ANTHROPIC_API_KEY=sk-ant-...
NOTION_TOKEN=secret_...
NOTION_DATABASE_ID=792c6c4...
```

### 5. Set up the database schema

```bash
python setup.py
```

This creates all required Notion properties (columns) if they don't already exist. Safe to run multiple times.

### 6. Fill in your profile

```bash
cp profile_example.yaml profile.yaml
```

Edit `profile.yaml` with your own details. `profile.yaml` is gitignored and never committed — it stays on your machine only.

### 7. Run

```bash
python assess.py
```

The script processes all Notion pages with `Status = Pending` (or blank Status). Results are written back and Status is set to `Assessed`.

To verify your Anthropic API key is working before the first real run:

```bash
python assess.py --test-api
```

## Scoring dimensions

| Dimension | Weight | What it measures |
|---|---|---|
| Technical Fit | 30% | Direct match between required skills and your demonstrated competencies |
| Thematic Fit | 20% | Alignment between the sector/context and your track record |
| Modality Fit | 15% | Compatibility with your remote/in-person preference and current workload |
| Compensation Fit | 15% | Whether the rate or salary aligns with your daily rate target |
| Geographic Fit | 5% | Location and work authorization compatibility |
| Deadline Practicality | 5% | Whether the application deadline is realistic given current date |
| Strategic Value | 10% | Whether the role builds toward your stated professional priorities |

Scores are on a 1–5 scale per dimension, weighted into an overall score out of 100.

**Recommendation thresholds:** 80–100 = Strong Apply | 65–79 = Worth Reviewing | 50–64 = Maybe | below 50 = Skip

## Adding opportunities

1. Open your Notion database
2. Create a new page (row)
3. Paste the job posting URL into the **URL** column
4. Leave **Status** blank (or set it to **Pending**)
5. Run `python assess.py`

**Notes:**
- Status = Pending and Status = blank both trigger assessment
- LinkedIn URLs do not work — LinkedIn blocks automated access. Open the posting, copy the original URL from the job description, and use that instead
- Indeed URLs are also blocked — use the employer's direct application page

## Automating runs

### Windows Task Scheduler

1. Open Task Scheduler → Create Basic Task
2. Set the trigger (e.g. daily at 08:00)
3. Action: **Start a program**
   - Program: `python`
   - Arguments: `C:\path\to\OpRadar\assess.py`
   - Start in: `C:\path\to\OpRadar`

### Mac / Linux (cron)

```bash
crontab -e
```

Add a line to run daily at 08:00:

```
0 8 * * * cd /path/to/OpRadar && python assess.py >> logs/cron.log 2>&1
```

## Limitations

- **LinkedIn and Indeed block automated access** — save the destination URL instead
- **JavaScript-heavy job boards** may return incomplete content; Playwright fallback handles most cases but some sites actively block headless browsers
- **PDF Terms of Reference** are supported via pypdf — scanned PDFs (image-only) return no text
- **Assessment quality depends on extractable text** — if a posting is behind a login wall, the script will mark it as Fetch Failed
- **2000-character Notion block limit** — very long analysis fields are truncated at 2000 characters

## Contributing

Issues and pull requests are welcome. Please open an issue first for significant changes.

## License

MIT
