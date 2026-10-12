#!/usr/bin/env python3
"""Rebuild the featured-projects block in README.md from my pinned repositories.

Run it with a GitHub token in the environment:

    GITHUB_TOKEN=$(gh auth token) python scripts/update_pinned.py

Pinned repositories are only exposed through the GraphQL API, so this needs a
token even though everything it reads is public. If nothing is pinned it falls
back to my most starred repositories, so the section is never empty.
"""

import argparse
import html
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

API = "https://api.github.com/graphql"
START = "<!-- PINNED:START -->"
END = "<!-- PINNED:END -->"

REPO_FIELDS = """
  name
  description
  url
  homepageUrl
  stargazerCount
  forkCount
  pushedAt
  primaryLanguage { name color }
  repositoryTopics(first: 6) { nodes { topic { name } } }
"""

PINNED_QUERY = """
query($login: String!, $limit: Int!) {
  user(login: $login) {
    pinnedItems(first: $limit, types: REPOSITORY) {
      nodes { ... on Repository { %s } }
    }
  }
}
""" % REPO_FIELDS

POPULAR_QUERY = """
query($login: String!, $limit: Int!) {
  user(login: $login) {
    repositories(
      first: $limit
      privacy: PUBLIC
      isFork: false
      ownerAffiliations: OWNER
      orderBy: { field: STARGAZERS, direction: DESC }
    ) {
      nodes { %s }
    }
  }
}
""" % REPO_FIELDS

# simple-icons slugs that shields.io actually serves; anything missing here
# falls back to GitHub's own language colour with no icon.
LOGOS = {
    "c": "c",
    "c#": "dotnet",
    "c++": "cplusplus",
    "css": "css",
    "dart": "dart",
    "dockerfile": "docker",
    "go": "go",
    "html": "html5",
    "java": "openjdk",
    "javascript": "javascript",
    "jupyter notebook": "jupyter",
    "kotlin": "kotlin",
    "mdx": "mdx",
    "php": "php",
    "plpgsql": "postgresql",
    "python": "python",
    "ruby": "ruby",
    "rust": "rust",
    "scss": "sass",
    "shell": "gnubash",
    "svelte": "svelte",
    "swift": "swift",
    "tex": "latex",
    "typescript": "typescript",
    "vue": "vuedotjs",
}

# Brand colours read better than GitHub's language dots on a badge.
BRANDS = {
    "c#": "512BD4",
    "c++": "00599C",
    "css": "1572B6",
    "dockerfile": "2496ED",
    "go": "00ADD8",
    "html": "E34F26",
    "java": "ED8B00",
    "javascript": "F7DF1E",
    "jupyter notebook": "DA5B0B",
    "php": "777BB4",
    "plpgsql": "4169E1",
    "python": "3776AB",
    "ruby": "CC342D",
    "rust": "000000",
    "scss": "CC6699",
    "shell": "4EAA25",
    "svelte": "FF3E00",
    "swift": "F05138",
    "tex": "008080",
    "typescript": "3178C6",
    "vue": "4FC08D",
}

DARK_TEXT = {"javascript", "c", "kotlin"}
TOPIC_COLOR = "30363D"


def graphql(query, variables, token):
    request = urllib.request.Request(
        API,
        data=json.dumps({"query": query, "variables": variables}).encode(),
        headers={
            "Authorization": f"bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "profile-readme-updater",
        },
    )

    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.load(response)

    if "errors" in payload:
        raise SystemExit("GraphQL error: " + json.dumps(payload["errors"], indent=2))

    return payload["data"]


def fetch_repos(login, token, limit):
    data = graphql(PINNED_QUERY, {"login": login, "limit": limit}, token)
    repos = (data.get("user") or {}).get("pinnedItems", {}).get("nodes") or []

    if repos:
        return repos, "pinned"

    data = graphql(POPULAR_QUERY, {"login": login, "limit": limit}, token)
    return (data.get("user") or {}).get("repositories", {}).get("nodes") or [], "most starred"


def badge(label, color, logo=None, logo_color="white"):
    text = urllib.parse.quote(label.replace("-", "--").replace("_", "__"), safe="")
    url = f"https://img.shields.io/badge/{text}-{color}?style=flat-square"
    if logo:
        url += f"&logo={logo}&logoColor={logo_color}"
    return f'<img alt="{html.escape(label)}" src="{url}" />'


def language_badge(language):
    if not language:
        return ""

    name = language["name"]
    key = name.lower()
    color = BRANDS.get(key) or (language.get("color") or "").lstrip("#") or "555555"
    logo_color = "black" if key in DARK_TEXT else "white"
    return badge(name, color, LOGOS.get(key), logo_color)


def shorten(text, limit=132):
    text = " ".join(text.split())
    if len(text) <= limit:
        return text

    cut = text[:limit].rsplit(" ", 1)[0]
    return cut.rstrip(".,;:·-") + "…"


def card(repo):
    name = repo["name"]
    lines = [f'<h3><a href="{repo["url"]}">{html.escape(name)}</a></h3>']

    if repo.get("description"):
        lines.append(f'<p>{html.escape(shorten(repo["description"]))}</p>')

    badges = [language_badge(repo.get("primaryLanguage"))]
    topics = [node["topic"]["name"] for node in repo["repositoryTopics"]["nodes"]][:4]
    badges += [badge(topic, TOPIC_COLOR) for topic in topics]
    badges = [b for b in badges if b]
    if badges:
        lines.append("<p>" + "\n  ".join(badges) + "</p>")

    facts = []
    if repo["stargazerCount"]:
        facts.append(f'★ {repo["stargazerCount"]}')
    if repo["forkCount"]:
        facts.append(f'⑂ {repo["forkCount"]}')
    if repo.get("pushedAt"):
        pushed = datetime.strptime(repo["pushedAt"], "%Y-%m-%dT%H:%M:%SZ")
        facts.append(f'updated {pushed.strftime("%b %Y")}')
    if repo.get("homepageUrl"):
        facts.append(f'<a href="{repo["homepageUrl"]}">live demo</a>')
    if facts:
        lines.append("<p><sub>" + " · ".join(facts) + "</sub></p>")

    body = "\n  ".join(lines)
    return f'<td width="50%" valign="top">\n  {body}\n</td>'


def render(repos):
    if not repos:
        return "_No public repositories to show yet._"

    rows = []
    for index in range(0, len(repos), 2):
        pair = repos[index:index + 2]
        cells = [card(repo) for repo in pair]
        if len(cells) == 1:
            cells.append('<td width="50%"></td>')
        rows.append("<tr>\n" + "\n".join(cells) + "\n</tr>")

    return "<table>\n" + "\n".join(rows) + "\n</table>"


def splice(readme, block):
    pattern = re.compile(
        re.escape(START) + r".*?" + re.escape(END),
        re.DOTALL,
    )

    if not pattern.search(readme):
        raise SystemExit(f"Could not find the {START} / {END} markers in the README.")

    return pattern.sub(f"{START}\n\n{block}\n\n{END}", readme)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user", default=os.environ.get("GITHUB_REPOSITORY_OWNER", "sameermankotia"))
    parser.add_argument("--readme", default="README.md")
    parser.add_argument("--limit", type=int, default=6)
    parser.add_argument("--dry-run", action="store_true", help="print the block instead of writing it")
    args = parser.parse_args()

    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not token:
        raise SystemExit("Set GITHUB_TOKEN (pinned repositories are only in the GraphQL API).")

    try:
        repos, source = fetch_repos(args.user, token, args.limit)
    except urllib.error.HTTPError as error:
        raise SystemExit(f"GitHub returned {error.code}: {error.reason}")
    except urllib.error.URLError as error:
        raise SystemExit(f"Could not reach GitHub: {error.reason}")

    block = render(repos)
    print(f"Found {len(repos)} {source} repositories for {args.user}:")
    for repo in repos:
        missing = [
            label
            for label, value in (
                ("description", repo.get("description")),
                ("topics", repo["repositoryTopics"]["nodes"]),
            )
            if not value
        ]
        note = f"  (no {', '.join(missing)} set on GitHub)" if missing else ""
        print(f"  · {repo['name']}{note}")

    if args.dry_run:
        print("\n" + block)
        return

    with open(args.readme, encoding="utf-8") as handle:
        readme = handle.read()

    updated = splice(readme, block)
    if updated == readme:
        print("\nREADME already up to date.")
        return

    with open(args.readme, "w", encoding="utf-8") as handle:
        handle.write(updated)

    print(f"\nRewrote the featured-projects block in {args.readme}.")


if __name__ == "__main__":
    main()
