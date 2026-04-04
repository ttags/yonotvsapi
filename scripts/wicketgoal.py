import os
import json
import requests
import subprocess
import re
from pathlib import Path
from urllib.parse import urlparse, parse_qs, unquote

# ─────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────

# Source APIs (the three feeds the match-details page reads from)
SOURCE_APIS = [
    {"url": "https://ipl-api.pages.dev/api/match.json",         "type": "IPL"},
    {"url": "https://ipl-api.pages.dev/api/psl.json",           "type": "PSL"},
    {"url": "https://wicketgoal-api.pages.dev/api/football.json","type": "Football"},
]

# Player wrapper base URLs (same ones used in the match-details HTML)
PLAYER_BASE   = "https://wicket-goal.pages.dev/player/?id="
WRAPPER_BASE  = "https://cric-hd.pages.dev/page/?src="

# How many stream links to generate per match
STREAM_LINK_COUNT = 6

# Where to write output files  (change to your repo root if needed)
ROOT = Path(__file__).resolve().parent

# Optional GitHub Actions summary file
SUMMARY_FILE = os.environ.get("GITHUB_STEP_SUMMARY")


# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────

def log(msg):
    print(f"[sync] {msg}")

def write_summary(text):
    if SUMMARY_FILE:
        with open(SUMMARY_FILE, "a") as f:
            f.write(text + "\n")

def fetch_json(url):
    r = requests.get(url, timeout=20)
    r.raise_for_status()
    try:
        return r.json()
    except requests.exceptions.JSONDecodeError:
        log(f"❌ Failed to parse JSON from {url}")
        log(f"First 200 chars: {r.text[:200]}")
        raise

def create_slug(name: str) -> str:
    """Mirror the JS createSlug() used by the match-details page."""
    if not name:
        return ""
    name = name.lower()
    name = re.sub(r"[^a-z0-9\s]", "", name)
    name = re.sub(r"\s+", "", name)
    return name

def get_initials(name: str) -> str:
    """Mirror the JS getInitials() used to build stream IDs."""
    return "".join(word[0].upper() for word in name.split() if word)

def build_stream_links(team1: str, team2: str) -> list:
    """
    Build the same 6 stream links the match-details JS generates.
    Link 2 is always the fixed affiliate link (kept as-is from the original).
    """
    initials = get_initials(team1)
    links = []

    for i in range(1, STREAM_LINK_COUNT + 1):
        if i == 2:
            # Link 2 is a fixed third-party redirect in the original code
            url = "https://fixesconsessionconsession.com/jngpnhi8?key=5bd41ec34384a413e2daecfa48267270"
        else:
            player_url = f"{PLAYER_BASE}{initials}{i}"
            url = f"{WRAPPER_BASE}{player_url}"

        links.append({
            "name": f"Link {i}",
            "url": url
        })

    return links

def make_match_id(team1: str, match_type: str) -> str:
    """
    Stable, filesystem-safe ID for a match.
    Pattern:  <type>_<team1_slug>
    e.g.  ipl_mumbaiindians  /  psl_islamabadunited
    """
    return f"{match_type.lower()}_{create_slug(team1)}"


# ─────────────────────────────────────────────
# PROCESSING
# ─────────────────────────────────────────────

def process_match(raw: dict, match_type: str) -> dict:
    """
    Turn one raw match object from the source API into
    a clean, self-contained JSON file (like the yonotvs pattern).
    """
    team1  = raw.get("team1", "")
    team2  = raw.get("team2", "")
    venue  = raw.get("venue", "")
    league = raw.get("league", "")
    start  = raw.get("start_time", "")
    duration = raw.get("duration", 4)  # hours, default 4

    slug = create_slug(team1)

    return {
        "match_id":    make_match_id(team1, match_type),
        "type":        match_type,
        "league":      league,
        "team1":       team1,
        "team2":       team2,
        "slug":        slug,           # Used in ?match= URL param
        "venue":       venue,
        "start_time":  start,
        "duration_hours": duration,
        "match_url":   f"https://match-details.wicketgoal-tv.com/?match={slug}",
        "stream_links": build_stream_links(team1, team2),
    }

def build_index(all_matches: list) -> list:
    """
    Lightweight index written to api.json — mirrors the shape
    the homepage JS expects from api_data (list of match objects).
    """
    index = []
    for m in all_matches:
        index.append({
            "match_id":   m["match_id"],
            "type":       m["type"],
            "league":     m["league"],
            "team1":      m["team1"],
            "team2":      m["team2"],
            "slug":       m["slug"],
            "start_time": m["start_time"],
            "match_url":  m["match_url"],
        })
    return index


# ─────────────────────────────────────────────
# GIT
# ─────────────────────────────────────────────

def git_has_changes():
    result = subprocess.run(["git", "status", "--porcelain"],
                            capture_output=True, text=True)
    return bool(result.stdout.strip())

def git_commit_and_push(message):
    subprocess.run(["git", "config", "user.name",  "github-actions[bot]"], check=True)
    subprocess.run(["git", "config", "user.email",
                    "41898282+github-actions[bot]@users.noreply.github.com"], check=True)
    subprocess.run(["git", "add", "."],              check=True)
    subprocess.run(["git", "commit", "-m", message], check=True)
    subprocess.run(["git", "push"],                  check=True)

def remove_stale_jsons(valid_ids: set):
    """Delete any <match_id>.json files that no longer appear in the source APIs."""
    for file in ROOT.glob("*.json"):
        if file.name == "api.json":
            continue
        if file.stem not in valid_ids:
            log(f"Removing stale file: {file.name}")
            file.unlink(missing_ok=True)


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────

def main():
    write_summary("## 🔄 WicketGoal API Sync\n")

    all_matches = []
    valid_ids   = set()

    # ── 1. Fetch every source API ──────────────────────────────────────
    for api in SOURCE_APIS:
        try:
            log(f"Fetching {api['type']} → {api['url']}")
            raw_data = fetch_json(api["url"])

            matches_raw = raw_data.get("matches", [])
            log(f"  Found {len(matches_raw)} matches")

            for raw_match in matches_raw:
                try:
                    processed = process_match(raw_match, api["type"])
                    all_matches.append(processed)
                    valid_ids.add(processed["match_id"])
                except Exception as e:
                    log(f"  ⚠️  Skipping match due to error: {e}")
                    continue

        except requests.exceptions.HTTPError as e:
            log(f"⚠️  HTTP error fetching {api['url']}: {e} — skipping this API")
            continue
        except requests.exceptions.JSONDecodeError:
            log(f"⚠️  Invalid JSON from {api['url']} — skipping this API")
            continue
        except Exception as e:
            log(f"⚠️  Unexpected error for {api['url']}: {e} — skipping")
            continue

    if not all_matches:
        log("❌ No matches fetched from any API. Aborting.")
        write_summary("❌ No matches fetched. Sync aborted.")
        return

    # ── 2. Remove stale match files ────────────────────────────────────
    remove_stale_jsons(valid_ids)

    # ── 3. Write individual match JSON files ───────────────────────────
    for match in all_matches:
        match_id = match["match_id"]
        out_path = ROOT / f"{match_id}.json"
        out_path.write_text(json.dumps(match, indent=2), encoding="utf-8")
        log(f"  ✅ Written {match_id}.json")

    # ── 4. Write the master index (api.json) ───────────────────────────
    index = build_index(all_matches)
    api_path = ROOT / "api.json"
    api_path.write_text(json.dumps(index, indent=2), encoding="utf-8")
    log(f"✅ Written api.json ({len(index)} matches)")

    # ── 5. Git commit & push if anything changed ───────────────────────
    try:
        if git_has_changes():
            git_commit_and_push("chore: sync wicketgoal match feeds")
            write_summary("✅ Changes detected and pushed.")
            log("✅ Pushed to git.")
        else:
            write_summary("ℹ️ No changes detected. Repo already up to date.")
            log("ℹ️ No changes — nothing to push.")
    except subprocess.CalledProcessError as e:
        log(f"⚠️  Git push failed: {e} (maybe not in a git repo, or no remote configured)")
        write_summary("⚠️ Sync completed but git push failed.")


if __name__ == "__main__":
    main()
