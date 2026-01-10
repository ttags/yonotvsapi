import os
import json
import requests
import subprocess
from pathlib import Path
from urllib.parse import urlparse, parse_qs

BASE_API = "https://yonotv-api.pages.dev"
OWNER = "ttags"
REPO = "yonotvsapi"

ROOT = Path(__file__).resolve().parent.parent
SUMMARY_FILE = os.environ.get("GITHUB_STEP_SUMMARY")


def log(msg):
    print(f"[sync] {msg}")


def write_summary(text):
    if SUMMARY_FILE:
        with open(SUMMARY_FILE, "a") as f:
            f.write(text + "\n")


def fetch_json(url):
    r = requests.get(url, timeout=20)
    r.raise_for_status()
    return r.json()


def replace_domain(obj):
    """Recursively replace newsecrettips -> yonotvs"""
    if isinstance(obj, dict):
        return {k: replace_domain(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [replace_domain(v) for v in obj]
    if isinstance(obj, str):
        return obj.replace("newsecrettips", "yonotvs")
    return obj


def replace_match_specific(obj):
    """
    Extra replacements for xxx.json files:
    - Rename 'telecast_links' -> 'info_sources'
    - Replace frame URLs in link URLs
    - Standardize link names as Link 1, Link 2, ...
    """
    if isinstance(obj, dict):
        new = {}
        for k, v in obj.items():
            # Rename key
            if k == "telecast_links":
                k = "info_sources"
                # Standardize names if it's a list of links
                if isinstance(v, list):
                    v_new = []
                    for i, link in enumerate(v):
                        v_new.append({
                            "name": f"Link {i+1}",
                            "url": replace_match_specific(link.get("url", ""))
                        })
                    new[k] = v_new
                    continue  # Skip the default recursion
            new[k] = replace_match_specific(v)
        return new

    if isinstance(obj, list):
        return [replace_match_specific(v) for v in obj]

    if isinstance(obj, str):
        # Replace old frame URLs with new frame URLs
        if obj.startswith((
            "http://yonotv.pages.dev/page.html?src",
            "https://yonotv.pages.dev/page.html?src",
        )):
            return obj.replace(
                "yonotv.pages.dev/page.html?src",
                "ytvs-frame.pages.dev/frame?ref",
                1,
            )
        return obj

    return obj




def extract_match_id(match_link):
    parsed = urlparse(match_link)
    qs = parse_qs(parsed.query)
    return qs.get("id", [None])[0]


def git_has_changes():
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        capture_output=True,
        text=True,
    )
    return bool(result.stdout.strip())


def git_commit_and_push(message):
    subprocess.run(["git", "config", "user.name", "github-actions[bot]"], check=True)
    subprocess.run(
        ["git", "config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com"],
        check=True,
    )
    subprocess.run(["git", "add", "."], check=True)
    subprocess.run(["git", "commit", "-m", message], check=True)
    subprocess.run(["git", "push"], check=True)


def remove_stale_jsons(valid_ids):
    """
    Deletes json files not present in api.json anymore
    (excluding api.json itself)
    """
    for file in ROOT.glob("*.json"):
        if file.name == "api.json":
            continue
        if file.stem not in valid_ids:
            log(f"Removing stale file: {file.name}")
            file.unlink(missing_ok=True)


def main():
    write_summary("## 🔄 Yonotvs API Sync\n")

    try:
        log("Fetching api.json")
        api_data = fetch_json(f"{BASE_API}/api.json")
        api_data = replace_domain(api_data)

        api_path = ROOT / "api.json"
        api_path.write_text(json.dumps(api_data, indent=2), encoding="utf-8")
        log("Updated api.json")

        ids = []
        for match in api_data:
            mid = extract_match_id(match.get("match_link", ""))
            if mid:
                ids.append(mid)

        log(f"Found {len(ids)} match IDs: {', '.join(ids)}")
        write_summary(f"- Matches found: `{', '.join(ids)}`")

        # ---- remove stale ----
        remove_stale_jsons(ids)
        
        for mid in ids:
            log(f"Fetching {mid}.json")
            data = fetch_json(f"{BASE_API}/{mid}.json")

            data = replace_domain(data)
            data = replace_match_specific(data)

            out = ROOT / f"{mid}.json"
            out.write_text(
                json.dumps(data, indent=2),
                encoding="utf-8",
            )

        if git_has_changes():
            log("Changes detected, committing once")
            git_commit_and_push("chore: sync api.json and match feeds")
            write_summary("✅ Changes detected and pushed in a single commit.")
        else:
            log("No changes detected")
            write_summary("ℹ️ No changes detected. Repo already up to date.")

    except Exception as e:
        log(f"ERROR: {e}")
        write_summary("❌ Sync failed. Check logs for details.")
        raise  # makes GitHub Action fail (red)


if __name__ == "__main__":
    main()
