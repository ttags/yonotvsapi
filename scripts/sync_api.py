import json
import os
import re
import subprocess
import sys
from urllib.request import urlopen

BASE_API = "https://yonotv-api.pages.dev"
API_FILE = "api.json"

WORKDIR = os.getcwd()


def fetch_json(url):
    with urlopen(url) as r:
        return json.loads(r.read().decode("utf-8"))


def write_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def git(cmd):
    subprocess.run(cmd, check=True)


def main():
    print("▶ Fetching api.json")
    api = fetch_json(f"{BASE_API}/api.json")

    api_str = json.dumps(api)
    api_str = api_str.replace("newsecrettips", "yonotvs")
    api = json.loads(api_str)

    write_json(API_FILE, api)

    valid_ids = set()

    print("▶ Processing match JSONs")

    for item in api:
        match_link = item.get("match_link", "")
        match_id = re.search(r"id=([a-zA-Z0-9_-]+)", match_link)

        if not match_id:
            continue

        match_id = match_id.group(1)
        valid_ids.add(match_id)

        print(f"  → {match_id}.json")

        match_json = fetch_json(f"{BASE_API}/{match_id}.json")

        raw = json.dumps(match_json)

        # Required replacements
        raw = raw.replace("telecast_links", "info_sources")
        raw = raw.replace(
            "https://yonotv.pages.dev/page.html?src",
            "https://ytvs-frame.pages.dev/frame?ref"
        )

        match_json = json.loads(raw)
        write_json(f"{match_id}.json", match_json)

    print("▶ Removing stale JSON files")

    for file in os.listdir(WORKDIR):
        if not file.endswith(".json"):
            continue
        if file == "api.json":
            continue

        name = file.replace(".json", "")
        if name not in valid_ids:
            print(f"  ✖ Removing stale file: {file}")
            os.remove(file)

    print("▶ Checking for changes")
    status = subprocess.check_output(["git", "status", "--porcelain"]).decode().strip()

    if not status:
        print("✔ No changes detected. Skipping commit.")
        return

    print("▶ Committing changes")
    git(["git", "add", "."])
    git(["git", "commit", "-m", "sync(api): refresh match jsons"])
    git(["git", "push"])

    print("✔ Sync complete")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("✖ Sync failed:", e)
        sys.exit(1)
