import os
import json
import requests
import subprocess
import re
from pathlib import Path
from urllib.parse import urlparse, parse_qs, unquote

BASE_API = "https://yonotv-api.pages.dev"
OWNER = "ttags"
REPO = "yonotvsapi"
ROOT = Path(__file__).resolve().parent.parent
SUMMARY_FILE = os.environ.get("GITHUB_STEP_SUMMARY")

# Global cache so we only fetch the original page once per script run
CHANNELS_CACHE = None

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

def get_channels_mapping():
    """Scrapes the Original's LINK page and parses the JS channels object."""
    global CHANNELS_CACHE
    if CHANNELS_CACHE is not None:
        return CHANNELS_CACHE
        
    try:
        log("Scraping Original's stream mapping...")
        r = requests.get("https://yonotv-now.pages.dev/LINK", timeout=15)
        r.raise_for_status()
        
        # Regex to find the JS object: const channels = { ... };
        match = re.search(r'const\s+channels\s*=\s*\{([\s\S]*?)\};', r.text)
        if not match:
            log("Warning: Could not find channels object.")
            CHANNELS_CACHE = {}
            return CHANNELS_CACHE
            
        channels_text = match.group(1)
        channels_map = {}
        
        # Regex to extract key-value pairs (e.g., IND12: "https://...")
        kv_pattern = re.compile(r'([a-zA-Z0-9_-]+)\s*:\s*["\'](.*?)["\']')
        for kv_match in kv_pattern.finditer(channels_text):
            key, val = kv_match.groups()
            channels_map[key] = val
            
        CHANNELS_CACHE = channels_map
        return CHANNELS_CACHE
    except Exception as e:
        log(f"Error scraping channels: {e}")
        CHANNELS_CACHE = {}
        return CHANNELS_CACHE

def process_smart_url(url):
    """The brain of the operation. Parses and reconstructs the URLs."""
    url = unquote(url) # Decode URL to prevent %3D, %3F issues
    
    # 1. Remove ADS completely
    if "ADS" in url.upper():
        return None 
        
    # 2. Check if it's an iframe wrapper (page.html)
    if "page.html" in url and "src=" in url:
        # Extract the source URL safely
        parsed = urlparse(url)
        qs = parse_qs(parsed.query)
        src_url = qs.get("src", [None])[0]
        
        if not src_url:
            src_url = url.split("src=")[-1] # Fallback extraction
            
        # 3. Check if the source URL is a nested Original ID (LINK?id=)
        if "yonotv-now.pages.dev/LINK" in src_url:
            src_parsed = urlparse(src_url)
            src_qs = parse_qs(src_parsed.query)
            link_id = src_qs.get("id", [None])[0]
            
            if link_id:
                channels = get_channels_mapping()
                raw_player_url = channels.get(link_id)
                
                if raw_player_url:
                    # Rip the .m3u8/.mpd link out of their player wrapper
                    extracted_stream = None
                    if "src=" in raw_player_url:
                        p_parsed = urlparse(raw_player_url)
                        p_qs = parse_qs(p_parsed.query)
                        extracted_stream = p_qs.get("src", [None])[0]
                        if not extracted_stream:
                            extracted_stream = raw_player_url.split("src=")[-1]
                    else:
                        extracted_stream = raw_player_url
                        
                    # Validate it's an actual raw stream
                    if extracted_stream and (".m3u8" in extracted_stream or ".mpd" in extracted_stream):
                        return f"https://ytvs-render.pages.dev/shaka?ref={extracted_stream}"
                        
            # If ID not found, or it wasn't a valid stream, flag for manual intervention
            return "#"

        # 3.5 Check if it's a direct player wrapper (like plyrr) containing the raw stream
        elif "plyrr" in src_url and "src=" in src_url:
            nested_parsed = urlparse(src_url)
            nested_qs = parse_qs(nested_parsed.query)
            extracted_stream = nested_qs.get("src", [None])[0]
            
            if not extracted_stream:
                extracted_stream = src_url.split("src=")[-1]
                
            if extracted_stream and (".m3u8" in extracted_stream or ".mpd" in extracted_stream):
                # DOUBLE WRAP: Frame -> Shaka -> Raw Stream
                return f"https://ytvs-frame.pages.dev/frame?ref=https://ytvs-render.pages.dev/shaka?ref={extracted_stream}"
        
        # 4. If it's a generic 3rd party URL inside page.html
        return f"https://ytvs-frame.pages.dev/frame?ref={src_url}"

    # If it's a completely normal URL not matching our rules, return as is
    return url

def replace_domain(obj):
    """Recursively replace newsecrettips -> yonotvs (Basic Info Only)"""
    if isinstance(obj, dict):
        return {k: replace_domain(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [replace_domain(v) for v in obj]
    if isinstance(obj, str):
        return obj.replace("newsecrettips", "yonotvs")
    return obj

def process_match_json(data):
    """Handles the structural changes for match files specifically."""
    data = replace_domain(data)
    
    # Safely rename telecast_links to info_sources
    if "telecast_links" in data:
        data["info_sources"] = data.pop("telecast_links")
        
    if "info_sources" in data and isinstance(data["info_sources"], list):
        new_links = []
        counter = 1
        
        for link_obj in data["info_sources"]:
            orig_url = link_obj.get("url", "")
            
            new_url = process_smart_url(orig_url)
            
            # If process_smart_url returns None (like for ADS), skip this link entirely
            if not new_url:
                continue
                
            new_links.append({
                "name": f"Link {counter}",
                "url": new_url
            })
            counter += 1
            
        data["info_sources"] = new_links
        
    return data

def extract_match_id(match_link):
    parsed = urlparse(match_link)
    qs = parse_qs(parsed.query)
    return qs.get("id", [None])[0]

def git_has_changes():
    result = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True)
    return bool(result.stdout.strip())

def git_commit_and_push(message):
    subprocess.run(["git", "config", "user.name", "github-actions[bot]"], check=True)
    subprocess.run(["git", "config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com"], check=True)
    subprocess.run(["git", "add", "."], check=True)
    subprocess.run(["git", "commit", "-m", message], check=True)
    subprocess.run(["git", "push"], check=True)

def remove_stale_jsons(valid_ids):
    for file in ROOT.glob("*.json"):
        if file.name == "api.json": continue
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
        
        ids = []
        for match in api_data:
            mid = extract_match_id(match.get("match_link", ""))
            if mid: ids.append(mid)
            
        remove_stale_jsons(ids)
        
        for mid in ids:
            log(f"Fetching {mid}.json")
            data = fetch_json(f"{BASE_API}/{mid}.json")
            
            # Use the new smart processor
            data = process_match_json(data)
            
            out = ROOT / f"{mid}.json"
            out.write_text(json.dumps(data, indent=2), encoding="utf-8")
            
        if git_has_changes():
            git_commit_and_push("chore: sync api.json and match feeds")
            write_summary("✅ Changes detected and pushed in a single commit.")
        else:
            write_summary("ℹ️ No changes detected. Repo already up to date.")
            
    except Exception as e:
        log(f"ERROR: {e}")
        write_summary("❌ Sync failed. Check logs for details.")
        raise

if __name__ == "__main__":
    main()
