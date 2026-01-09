import requests
import json
import os
from github import Github
from urllib.parse import urlparse, parse_qs

# Fetch token from GitHub Actions Environment
GITHUB_TOKEN = os.getenv('MY_GIT_TOKEN')
REPO_NAME = "ttags/yonotvsapi" 
BASE_API_URL = "https://yonotv-api.pages.dev"
SOURCE_JSON_URL = "https://yonotv-api.pages.dev/api.json"

def process_and_push():
    if not GITHUB_TOKEN:
        print("Error: MY_GIT_TOKEN not found.")
        return

    g = Github(GITHUB_TOKEN)
    repo = g.get_repo(REPO_NAME)
    
    print("Fetching source JSON...")
    response = requests.get(SOURCE_JSON_URL)
    if response.status_code != 200: return

    # Keyword replacement
    modified_text = response.text.replace("newsecrettips", "yonotvs")
    api_data = json.loads(modified_text)

    files_to_push = {"api.json": json.dumps(api_data, indent=2)}

    for match in api_data:
        match_url = match.get("match_link", "")
        parsed_url = urlparse(match_url)
        match_id = parse_qs(parsed_url.query).get('id', [None])[0]

        if match_id:
            target_name = f"{match_id}.json"
            sub_res = requests.get(f"{BASE_API_URL}/{target_name}")
            if sub_res.status_code == 200:
                files_to_push[target_name] = sub_res.text.replace("newsecrettips", "yonotvs")

    # Upload logic
    for file_path, content in files_to_push.items():
        try:
            contents = repo.get_contents(file_path)
            repo.update_file(contents.path, f"Sync {file_path}", content, contents.sha)
            print(f"Updated: {file_path}")
        except:
            repo.create_file(file_path, f"Initial {file_path}", content)
            print(f"Created: {file_path}")

if __name__ == "__main__":
    process_and_push()
