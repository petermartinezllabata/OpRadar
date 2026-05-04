"""
Opportunity Assessor Pipeline
Fetches pending opportunities from Notion, assesses them with Claude, and writes results back.

Notion database fields (all writable by this script):
  Recommendation (select), Overall Score (number), Technical Fit (number),
  Thematic Fit (number), Modality Fit (number), Compensation Fit (number),
  Geographic Fit (number), Deadline Practicality (number), Strategic Value (number),
  Why It Matches (rich_text), Main Risks / Gaps (rich_text),
  Suggested Positioning (rich_text), Countries (rich_text),
  Career Categories (rich_text), Days Left (number), Date Posted (date),
  Status (select), Type (select), Organization (rich_text), Deadline (date),
  Notes (rich_text), Name/title (title),
  LOE Min (number), LOE Max (number), LOE Notes (rich_text)
"""

import io
import json
import logging
import os
import sys
from datetime import date

import yaml
import anthropic
import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from notion_client import Client
from notion_client.errors import APIResponseError

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

BLOCKED_DOMAINS = [
    "indeed.com",
]


class BlockedDomainError(Exception):
    """Raised when a URL belongs to a domain that blocks automated access."""


# ---------------------------------------------------------------------------
# Profile
# ---------------------------------------------------------------------------

def load_profile() -> dict:
    """Load profile.yaml from the same directory as this script."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "profile.yaml")
    if not os.path.exists(path):
        print(
            "OpRadar: profile.yaml not found. "
            "Copy profile_example.yaml to profile.yaml and fill in your details."
        )
        sys.exit(1)
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def validate_profile(profile: dict) -> bool:
    """Return False if profile still contains placeholder values."""
    if not profile.get("name", "").strip():
        return False
    if profile.get("daily_rate_min", 0) == 0:
        return False
    summary = profile.get("experience_summary", "")
    if not summary.strip() or "# Write 4-6" in summary:
        return False
    return True


def build_system_prompt(profile: dict) -> str:
    """Build the system prompt dynamically from profile.yaml fields."""
    name = profile["name"]
    base_city = profile["base_city"]
    languages = ", ".join(profile.get("languages", []))
    experience_summary = profile.get("experience_summary", "").strip()
    core_competencies = profile.get("core_competencies", [])
    technical_tools = profile.get("technical_tools", [])
    institutional_background = profile.get("institutional_background", "").strip()
    thematic_areas = ", ".join(profile.get("thematic_areas", []))
    certifications = profile.get("certifications", [])
    rosters = profile.get("rosters", [])
    certs_and_rosters = "; ".join(certifications + rosters)
    current_role = profile.get("current_role", "")
    other_roles = profile.get("other_roles", [])
    other_roles_str = "\n".join(f"- {r}" for r in other_roles)
    availability = profile.get("availability", "")
    rate_min = profile.get("daily_rate_min", 0)
    rate_max = profile.get("daily_rate_max", 0)
    work_auth = ", ".join(profile.get("work_authorization", []))
    remote_preference = profile.get("remote_preference", "")
    priority_countries = profile.get("priority_countries", [])
    priority_countries_str = ", ".join(priority_countries)
    degree = profile.get("degree", "")
    strategic_priorities = profile.get("strategic_priorities", "").strip()
    exclude = profile.get("exclude", "").strip()

    competencies_str = "; ".join(core_competencies)
    tools_str = ", ".join(technical_tools)
    other_roles_section = f"\n{other_roles_str}" if other_roles_str else ""

    return f"""\
You are a consultancy and job opportunity assessor for {name}, a senior independent consultant based in {base_city}. Assess each opportunity against their profile and constraints. Return only valid JSON with no preamble or markdown.

## PROFILE

{experience_summary}

Languages: {languages}.

### Core technical strengths

Core competencies: {competencies_str}.

Technical tools: {tools_str}.

### Institutional track record

{institutional_background}

### Thematic areas with strong evidence

{thematic_areas}.

### Rosters and certifications

{certs_and_rosters}.

### Current roles and constraints

Active roles:
- {current_role}{other_roles_section}

Availability: {availability}. Cannot take on full-time or near-full-time external roles while current primary role continues. For full-time roles, assess as a potential future transition, not an immediate option.

Daily rate target: USD {rate_min}–{rate_max} for short technical international org work. Lower acceptable for longer engagements or strong portfolio-building value.

Location: {base_city}. Authorized to work in {work_auth}.

Preferred modality: {remote_preference}. Short field travel acceptable (up to 4–6 weeks). Extended deployment only if exceptional.

Degree gap risk: {degree} — not a quantitative degree. ATS risk for roles with hard quantitative or computer science degree requirements.

Strategic priorities: {strategic_priorities}

Exclusions: {exclude}

### Scoring rubric

Score each dimension 1 to 5, then compute weighted overall score out of 100.

Technical Fit (30%): Does the role directly require skills {name} demonstrably has — MEL systems, IM, survey design, dashboards, data analysis, displacement/migration analysis, mixed-methods research, humanitarian analysis, capacity strengthening? Score lower for pure programme management, coordination, communications, grants, admin, or logistics with no MEL/data/research content.

Thematic Fit (20%): Is the sector or context aligned with {name}'s track record? Score highest for humanitarian, migration, displacement, protection, refugee response, mixed movements, climate displacement, DRR, localization, innovation, AI for development, evidence systems, data for decision-making, community-based development, child protection, gender, environmental education, conservation MEL.

Modality Fit (15%): How compatible is the modality with {name}'s remote-first constraint and current workload?
- Remote or home-based: 5
- {priority_countries_str}: 4 to 5 depending on compensation and strategic value
- Other locations requiring relocation: 1 to 3
- For full-time roles: do not penalize non-remote heavily if based in priority countries and role is well compensated. Flag relocation requirement in compatibility_note.

Compensation Fit (15%): Is the rate or total contract likely within {name}'s target range? Score highest for UN P2/P3/P4 salaries, highly paid international consultant contracts, development bank consultancies, or major INGO consultancies at senior level.

Geographic Fit (5%): Is location or authorization compatible? Highest for remote and {base_city}. Positive for priority nearby countries. Lower for roles requiring relocation outside priority countries.

Deadline Practicality (5%): Is the deadline open and realistic? Score 5 if deadline is more than 7 days away and requirements are clear. Score lower for imminent deadlines or unclear application processes.

Strategic Value (10%): Does the role build toward {name}'s stated priorities, strengthen the consultancy portfolio, open new sectors, or increase roster eligibility? Score highest for roles that directly address the stated strategic gaps.

Thresholds: 80 to 100 = Strong Apply | 65 to 79 = Worth Reviewing | 50 to 64 = Maybe | below 50 = Skip

LOE ESTIMATION RULES:
- Estimate working days required to complete this assignment properly, based solely on the scope, deliverables, and timeline described in the opportunity text.
- Do not adjust for {name}'s current availability or constraints — this is a pure scope calculation.
- Base the estimate on: number of deliverables, complexity of each, expected research or data collection phases, reporting requirements, coordination demands, and any revision rounds implied.
- For consultancies with unclear scope, estimate conservatively and note the uncertainty in loe_notes.
- loe_min = lean but credible execution with no major complications
- loe_max = thorough execution including revisions, coordination overhead, and reasonable contingency
- If the ToR allows applying for partial scope (e.g. one of three research streams), estimate for the partial scope and note this in loe_notes.\
"""


# Template with <<NAME>> and <<RATE_RANGE>> markers substituted at runtime via
# build_user_prompt_template(). Braces in the JSON spec use {{ }} so .format()
# later only substitutes {extracted_text} and {type}.
_RAW_USER_PROMPT_TEMPLATE = """\
Assess this opportunity for <<NAME>>. Return ONLY valid JSON with no preamble or markdown fences.

OPPORTUNITY TEXT:
{extracted_text}

TYPE: {type}

You must think like a senior recruiter AND like <<NAME>>'s strategic advisor simultaneously.
First analyse what the hiring panel is really looking for and why.
Then map <<NAME>>'s specific evidence against those requirements.
Then give an honest competitive assessment.
Then produce a clear action recommendation.

Return exactly this JSON structure:

{{
  "overall_score": <number 0-100, weighted: technical_fit 30% + thematic_fit 20% + modality_fit 15% + compensation_fit 15% + geographic_fit 5% + deadline_practicality 5% + strategic_value 10%>,
  "recommendation": "<Strong Apply|Worth Reviewing|Maybe|Skip>",
  "inferred_type": "<Consultancy|Full-time|Roster>",
  "technical_fit": <1-5>,
  "thematic_fit": <1-5>,
  "modality_fit": <1-5>,
  "compensation_fit": <1-5>,
  "geographic_fit": <1-5>,
  "deadline_practicality": <1-5>,
  "strategic_value": <1-5>,
  "title": "<exact job or consultancy title from the page>",
  "organization": "<hiring organization name>",
  "deadline": "<Search the entire page text for any date associated with: application deadline, closing date, closing on, submit by, apply by, applications close, due date, receipt of applications, or any similar phrase. Return the date in YYYY-MM-DD format. If the year is not explicitly stated but the month and day are clear, infer the year as 2026. If no deadline is found anywhere on the page, return null>",
  "countries": "<comma-separated countries or regions, or Remote>",
  "career_categories": "<comma-separated from: MEL, Information Management, Research, Data Analysis, Evaluation, Protection, Migration, Facilitation, Capacity Building, Humanitarian Analysis, Survey Design, GIS, Innovation, Climate and DRR, Child Protection, Gender>",
  "notes": "<one sentence: what this role is, type, location, duration if mentioned>",
  "why_it_matches": "<structured criterion-by-criterion analysis using this exact format with line breaks between each criterion:
TECHNICAL FIT: [Strong/Moderate/Weak] — [2 sentences: which specific skills match which specific requirements, naming <<NAME>>'s concrete experiences]
THEMATIC FIT: [Strong/Moderate/Weak] — [2 sentences: how well the sector and context align with <<NAME>>'s track record]
MODALITY AND LOGISTICS: [Strong/Moderate/Weak] — [2 sentences: remote vs field, LOE vs current commitments, travel requirements]
COMPENSATION: [Strong/Moderate/Weak] — [1-2 sentences: estimated rate or total vs <<NAME>>'s target of <<RATE_RANGE>>/day for short technical work]
STRATEGIC VALUE: [Strong/Moderate/Weak] — [1-2 sentences: does this build evaluation credits, new sectors, roster eligibility, or consultancy track record]>",
  "main_risks_gaps": "<structured risk analysis using this exact format with line breaks between each item:
MAIN GAP: [1-2 sentences: the single biggest weakness in <<NAME>>'s fit for this specific role]
SCREENING RISK: [1 sentence: any ATS, degree, citizenship, or registration requirement that could filter <<NAME>> out automatically]
COMPETITIVE LANDSCAPE: [1-2 sentences: what other candidates likely look like and where <<NAME>> sits relative to them]
DEALBREAKER CHECK: [Yes/No] — [1 sentence: is there any single factor that makes this role incompatible regardless of fit]>",
  "suggested_positioning": "<actionable decision block using this exact format with line breaks between each item:
APPLY IF: [specific condition under which applying makes sense]
SKIP IF: [specific condition under which applying does not make sense]
LEAD WITH: [the 2-3 most powerful pieces of <<NAME>>'s evidence for this specific role]
SUPPRESS: [what to de-emphasize or not mention in the application]
POSITIONING ANGLE: [1-2 sentences: the core framing that would make <<NAME>>'s application stand out, grounded in their actual experience]>",
  "loe_min": <integer — minimum realistic working days to complete this assignment properly>,
  "loe_max": <integer — maximum realistic working days to complete this assignment properly>,
  "loe_notes": "<structured LOE breakdown using this exact format with line breaks:
SCOPE: [1 sentence describing what the assignment actually entails in terms of deliverables and timeline]
PHASES: [brief list of main work phases with estimated days each, e.g. Inception 3d / Tool design 5d / Data collection 8d / Analysis 6d / Reporting 5d]
RANGE BASIS: [1 sentence explaining what drives the min-max range — e.g. depends on number of field visits, team size, revision rounds]
SCENARIOS: [if the ToR allows different roles — e.g. lead consultant vs sub-consultant vs consortium — give a range per scenario in one line each]>"
}}\
"""


def build_user_prompt_template(profile: dict) -> str:
    """Substitute profile values into the raw template, leaving {extracted_text} and {type} intact."""
    rate_range = f"USD {profile['daily_rate_min']}–{profile['daily_rate_max']}"
    return (
        _RAW_USER_PROMPT_TEMPLATE
        .replace("<<NAME>>", profile["name"])
        .replace("<<RATE_RANGE>>", rate_range)
    )


# Populated at runtime by main() after loading profile.yaml
SYSTEM_PROMPT = ""
_USER_PROMPT_TEMPLATE = ""

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

os.makedirs("logs", exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    handlers=[
        logging.FileHandler("logs/run.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------

def load_env() -> tuple[str, str, str]:
    """Load and return (ANTHROPIC_API_KEY, NOTION_TOKEN, NOTION_DATABASE_ID)."""
    load_dotenv()
    try:
        api_key = os.environ["ANTHROPIC_API_KEY"]
        notion_token = os.environ["NOTION_TOKEN"]
        database_id = os.environ["NOTION_DATABASE_ID"]
    except KeyError as e:
        log.error("Missing environment variable: %s", e)
        sys.exit(1)
    return api_key, notion_token, database_id


# ---------------------------------------------------------------------------
# Notion helpers
# ---------------------------------------------------------------------------

def fetch_pending_opportunities(notion: Client, database_id: str) -> list[dict]:
    """Return all pages where Status = Pending or Status is empty."""
    results = []
    cursor = None
    while True:
        kwargs: dict = {
            "database_id": database_id,
            "filter": {
                "or": [
                    {"property": "Status", "select": {"equals": "Pending"}},
                    {"property": "Status", "select": {"is_empty": True}},
                ]
            },
        }
        if cursor:
            kwargs["start_cursor"] = cursor
        response = notion.databases.query(**kwargs)
        results.extend(response.get("results", []))
        if not response.get("has_more"):
            break
        cursor = response.get("next_cursor")
    return results


def _rich_text(text: str) -> list:
    """Truncate text to Notion's 2000-char block limit and return rich_text format."""
    return [{"text": {"content": str(text)[:2000]}}]


def write_to_notion(
    notion: Client, page_id: str, assessment: dict, deadline: str | None,
    type_was_blank: bool = False,
    current_name: str = "",
) -> None:
    """Write all output fields and update Status to Assessed."""
    # Use Claude's extracted deadline as fallback for Days Left if Notion had none
    effective_deadline = deadline or assessment.get("deadline")
    days_left = calculate_days_left(effective_deadline)
    today = date.today().isoformat()

    properties: dict = {
        "Recommendation":       {"select":    {"name": assessment["recommendation"]}},
        "Overall Score":        {"number":    assessment["overall_score"]},
        "Technical Fit":        {"number":    assessment["technical_fit"]},
        "Thematic Fit":         {"number":    assessment["thematic_fit"]},
        "Modality Fit":         {"number":    assessment["modality_fit"]},
        "Compensation Fit":     {"number":    assessment["compensation_fit"]},
        "Geographic Fit":       {"number":    assessment["geographic_fit"]},
        "Deadline Practicality":{"number":    assessment["deadline_practicality"]},
        "Strategic Value":      {"number":    assessment["strategic_value"]},
        "Why It Matches":       {"rich_text": _rich_text(assessment.get("why_it_matches", ""))},
        "Main Risks / Gaps":    {"rich_text": _rich_text(assessment.get("main_risks_gaps", ""))},
        "Suggested Positioning":{"rich_text": _rich_text(assessment.get("suggested_positioning", ""))},
        "Countries":            {"rich_text": _rich_text(assessment.get("countries", ""))},
        "Career Categories":    {"rich_text": _rich_text(assessment.get("career_categories", ""))},
        "Days Left":            {"number":    days_left},
        "Date Posted":          {"date":      {"start": today}},
        "Status":               {"select":    {"name": "Assessed"}},
        "LOE Min":              {"number":    assessment.get("loe_min")},
        "LOE Max":              {"number":    assessment.get("loe_max")},
        "LOE Notes":            {"rich_text": _rich_text(assessment.get("loe_notes", ""))},
    }

    if type_was_blank and assessment.get("inferred_type"):
        properties["Type"] = {"select": {"name": assessment["inferred_type"]}}

    extracted_org = assessment.get("organization")
    if extracted_org:
        properties["Organization"] = {"rich_text": _rich_text(extracted_org)}
    else:
        log.warning("Claude did not return an organization field — Organization not updated.")

    # Write Deadline only when Claude extracted one and Notion's field was blank
    extracted_deadline = assessment.get("deadline")
    if extracted_deadline and not deadline:
        try:
            date.fromisoformat(extracted_deadline)  # validate before sending to Notion
            properties["Deadline"] = {"date": {"start": extracted_deadline}}
        except (ValueError, TypeError):
            log.warning("Claude returned an unparseable deadline '%s' — Deadline not updated.", extracted_deadline)

    extracted_notes = assessment.get("notes")
    if extracted_notes:
        properties["Notes"] = {"rich_text": _rich_text(extracted_notes)}
    else:
        log.warning("Claude did not return a notes field — Notes not updated.")

    notion.pages.update(page_id=page_id, properties=properties)

    extracted_title = assessment.get("title")
    if extracted_title and current_name.strip() in ("", "Untitled"):
        try:
            notion.pages.update(
                page_id=page_id,
                properties={"Name": {"title": [{"text": {"content": str(extracted_title)[:2000]}}]}},
            )
        except Exception as e:
            log.warning("Could not write Name for page %s: %s", page_id, e)


def _set_fetch_failed(notion: Client, page_id: str, name: str, note: str | None = None) -> None:
    """Best-effort update of Status to Fetch Failed."""
    try:
        properties: dict = {"Status": {"select": {"name": "Fetch Failed"}}}
        if note:
            properties["Notes"] = {"rich_text": _rich_text(note)}
        notion.pages.update(page_id=page_id, properties=properties)
    except APIResponseError as e:
        log.error("Could not set Fetch Failed for '%s' (page %s): %s", name, page_id, e)


# ---------------------------------------------------------------------------
# Web fetching
# ---------------------------------------------------------------------------

def _playwright_fetch(url: str) -> str | None:
    """Fetch a JS-rendered page with headless Chromium. Returns raw HTML or None."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.goto(url, wait_until="networkidle", timeout=15000)
            return page.content()
        finally:
            browser.close()


def fetch_url(url: str) -> str | None:
    """GET the URL and return raw HTML, or extracted plain text for PDFs.

    Falls back to a headless Chromium fetch when static extraction yields
    fewer than 200 words (JS-rendered pages).

    Returns None if the URL is a PDF and pypdf extraction fails.
    Raises BlockedDomainError for domains that block automated access.
    Raises requests.RequestException on HTTP or network errors.
    """
    import pypdf
    from urllib.parse import urlparse

    domain = urlparse(url).netloc.lower().removeprefix("www.")
    if any(domain == bd or domain.endswith("." + bd) for bd in BLOCKED_DOMAINS):
        raise BlockedDomainError(
            "Domain blocks automated access — save the original job URL instead"
        )

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
    }
    response = requests.get(url, headers=headers, timeout=20)
    response.raise_for_status()

    content_type = response.headers.get("Content-Type", "")
    is_pdf = url.lower().split("?")[0].endswith(".pdf") or "application/pdf" in content_type

    if is_pdf:
        try:
            reader = pypdf.PdfReader(io.BytesIO(response.content))
            text = "\n".join(page.extract_text() or "" for page in reader.pages)
            if not text.strip():
                log.warning("pypdf extracted no text from %s", url)
                return None
            return text
        except Exception as e:
            log.warning("PDF extraction failed for %s: %s", url, e)
            return None

    if not response.text.strip():
        raise ValueError("Empty response body")

    html = response.text
    if len(extract_text(html).split()) < 200:
        log.info("Static fetch returned fewer than 200 words — trying playwright fallback.")
        try:
            pw_html = _playwright_fetch(url)
            if pw_html and len(extract_text(pw_html).split()) > len(extract_text(html).split()):
                log.info("Playwright returned more content — using rendered page.")
                return pw_html
        except Exception as e:
            log.warning("Playwright fallback failed: %s", e)

    return html


def extract_text(html: str) -> str:
    """Parse HTML, strip noise elements, and return up to 4000 words of main content."""
    soup = BeautifulSoup(html, "html.parser")

    for tag in soup(["script", "style", "nav", "header", "footer", "aside", "noscript", "iframe"]):
        tag.decompose()

    # Prefer semantic content containers over the full page body
    main = (
        soup.find("article")
        or soup.find("main")
        or soup.find(id="content")
        or soup.find(id="main")
        or soup.find(
            "div",
            class_=lambda c: c and any(
                kw in " ".join(c).lower()
                for kw in ("content", "main", "article", "job", "posting", "description", "vacancy")
            ),
        )
        or soup.body
        or soup
    )

    text = main.get_text(separator="\n", strip=True)  # type: ignore[union-attr]
    words = text.split()
    return " ".join(words[:4000])


# ---------------------------------------------------------------------------
# Claude assessment
# ---------------------------------------------------------------------------

def _repair_json(raw: str) -> dict:
    """Attempt to repair truncated JSON by closing open strings and structures."""
    text = raw.strip()
    for suffix in ['"}', '"}}', '"}}}', '"}}}}']:
        try:
            return json.loads(text + suffix)
        except json.JSONDecodeError:
            continue
    last_comma = max(text.rfind('",\n'), text.rfind('",\r\n'))
    if last_comma > 0:
        truncated = text[:last_comma + 1] + '}'
        try:
            return json.loads(truncated)
        except json.JSONDecodeError:
            pass
    raise json.JSONDecodeError("Could not repair JSON", text, 0)


def assess_opportunity(client: anthropic.Anthropic, text: str, opp_type: str) -> tuple[dict, float]:
    """Call Claude and return (assessment_dict, estimated_cost). Raises on JSON failure."""
    type_str = opp_type if opp_type else "Unknown — infer from content (Consultancy, Full-time, or Roster)"
    user_prompt = _USER_PROMPT_TEMPLATE.format(extracted_text=text, type=type_str)

    message = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=3000,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
    )

    cost = message.usage.input_tokens * 0.000003 + message.usage.output_tokens * 0.000015
    log.info(
        "Tokens — input: %d | output: %d | est. cost: $%.4f",
        message.usage.input_tokens, message.usage.output_tokens, cost,
    )

    raw = message.content[0].text.strip()

    # Strip accidental markdown fences if the model adds them despite instructions
    if raw.startswith("```"):
        lines = raw.split("\n")
        raw = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

    try:
        return json.loads(raw), cost
    except json.JSONDecodeError:
        log.warning("Initial JSON parse failed — attempting repair.")
        try:
            return _repair_json(raw), cost
        except json.JSONDecodeError:
            log.error("Raw Claude response that failed JSON parsing:\n%s", raw)
            raise


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def calculate_days_left(deadline_str: str | None) -> int | None:
    """Return integer days from today to deadline_str (YYYY-MM-DD), or None."""
    if not deadline_str:
        return None
    try:
        return (date.fromisoformat(deadline_str) - date.today()).days
    except ValueError:
        log.warning("Could not parse deadline date: %s", deadline_str)
        return None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    profile = load_profile()
    if not validate_profile(profile):
        print(
            "OpRadar: profile.yaml is not configured. Please fill in your profile "
            "before running. See profile_example.yaml for reference."
        )
        sys.exit(0)
    global SYSTEM_PROMPT, _USER_PROMPT_TEMPLATE
    SYSTEM_PROMPT = build_system_prompt(profile)
    _USER_PROMPT_TEMPLATE = build_user_prompt_template(profile)

    api_key, notion_token, database_id = load_env()
    notion = Client(auth=notion_token)
    claude = anthropic.Anthropic(api_key=api_key)

    log.info("=" * 60)
    log.info("Opportunity Assessor — run started")

    pages = fetch_pending_opportunities(notion, database_id)

    if not pages:
        log.info("No pending opportunities found.")
        print("\nNo pending opportunities found.")
        return

    log.info("Found %d pending opportunity(ies).", len(pages))

    assessed = 0
    failed = 0
    skipped = 0
    total_cost = 0.0

    for page in pages:
        page_id = page["id"]
        props = page["properties"]

        # --- Read input fields ---
        title_blocks = props.get("Name", {}).get("title", [])
        current_name = title_blocks[0]["plain_text"] if title_blocks else ""
        name = current_name or page_id  # page_id used only for log lines

        url_prop = props.get("URL", {})
        rich_text_blocks = url_prop.get("rich_text", [])
        if rich_text_blocks:
            url = rich_text_blocks[0]["text"]["content"].strip() or None
        else:
            url = url_prop.get("url")  # fallback if field type is ever changed to url

        type_prop = props.get("Type", {}).get("select")
        opp_type = type_prop["name"] if type_prop else ""

        deadline_prop = props.get("Deadline", {}).get("date")
        deadline_str = deadline_prop["start"] if deadline_prop else None

        log.info("--- %s", name)

        # --- Skip rows with no URL ---
        if not url:
            log.warning("No URL found — skipping '%s'.", name)
            skipped += 1
            continue

        # --- Full processing cycle — never crash on a single row ---
        try:
            # 1. Fetch and extract page text
            try:
                html = fetch_url(url)
            except BlockedDomainError as e:
                log.warning("Blocked domain for '%s': %s", name, e)
                _set_fetch_failed(notion, page_id, name, note=str(e))
                failed += 1
                continue
            except requests.RequestException as e:
                log.error("Fetch error for '%s': %s", name, e)
                _set_fetch_failed(notion, page_id, name)
                failed += 1
                continue

            if html is None:
                log.error("PDF extraction returned no text for '%s'.", name)
                _set_fetch_failed(notion, page_id, name)
                failed += 1
                continue

            text = extract_text(html)
            if not text.strip():
                log.error("No extractable text from URL for '%s'.", name)
                _set_fetch_failed(notion, page_id, name)
                failed += 1
                continue

            log.info("Extracted %d words.", len(text.split()))

            # 2. Assess with Claude
            try:
                assessment, cost = assess_opportunity(claude, text, opp_type)
                total_cost += cost
            except json.JSONDecodeError:
                log.error("JSON parse failed for '%s' — marking Fetch Failed.", name)
                _set_fetch_failed(notion, page_id, name)
                failed += 1
                continue

            log.info(
                "Score: %s | Recommendation: %s",
                assessment.get("overall_score"),
                assessment.get("recommendation"),
            )

            # 3. Write results to Notion
            try:
                write_to_notion(notion, page_id, assessment, deadline_str, type_was_blank=not opp_type, current_name=current_name)
                log.info("Written to Notion: '%s'.", name)
                assessed += 1
            except (APIResponseError, Exception) as e:
                log.error("Notion write failed for page %s ('%s'): %s", page_id, name, e)
                failed += 1

        except Exception:
            log.exception("Unexpected error processing '%s'.", name)
            failed += 1

    summary = (
        f"\nRun complete: {assessed} assessed, {failed} failed, {skipped} skipped"
        f" | Total est. cost: ${total_cost:.4f}"
    )
    log.info(summary)
    print(summary)


def test_api() -> None:
    """Smoke-test the Claude API key and print response + token usage."""
    api_key, _, _ = load_env()
    client = anthropic.Anthropic(api_key=api_key)
    message = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=16,
        messages=[{"role": "user", "content": "Reply with the word OK and nothing else"}],
    )
    print("Response :", message.content[0].text)
    print("Model    :", message.model)
    print("Tokens   : input=%d  output=%d" % (message.usage.input_tokens, message.usage.output_tokens))


if __name__ == "__main__":
    import sys as _sys
    if len(_sys.argv) > 1 and _sys.argv[1] == "--test-api":
        test_api()
    else:
        main()
