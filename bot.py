# -*- coding: utf-8 -*-
"""
Job Radar — scans job boards in Iraq, Azerbaijan and Armenia
for networking jobs and sends new ones to a private Telegram chat.
Designed to run on GitHub Actions twice a day (Tehran time 09:00 & 18:00).
"""

import json
import os
import re
import time
import html
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
CHAT_ID = os.environ.get("CHAT_ID", "")

SEEN_FILE = "seen.json"
STATE_DAYS = 45          # forget links older than this
REQUEST_TIMEOUT = 25
DELAY_BETWEEN_SITES = 2  # seconds — be polite to the sites

KEYWORDS = [
    "network engineer", "network administrator", "network admin",
    "network specialist", "network technician", "networking",
    "noc engineer", "noc", "system engineer", "systems engineer",
    "infrastructure engineer", "routing", "switching", "cisco",
    "aci", "vxlan", "ospf", "bgp", "vlan", "stp", "ccna", "ccnp",
    "ccie", "sd-wan", "firewall", "network security",
]

KEYWORD_RE = re.compile(
    "|".join(r"\b" + re.escape(k) + r"\b" for k in KEYWORDS),
    re.IGNORECASE,
)

# a link only counts as a job ad if its path contains one of these
JOB_LINK_HINTS = (
    "job", "vacanc", "vacancy", "opportunity", "career", "position", "apply",
)

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "en",
})

SITES = [
    # ---------- Iraq ----------
    {"name": "Bayt – Iraq",          "url": "https://www.bayt.com/en/iraq/jobs/network-engineer-jobs/"},
    {"name": "Tanqeeb – Iraq",       "url": "https://iraq.tanqeeb.com/s/jobs/network-engineer"},
    {"name": "Hawa Jobs – Iraq",     "url": "https://www.hawa.jobs/search?q=network"},
    {"name": "VacanciesInIraq",      "url": "https://vacanciesiniraq.com/?s=network"},
    {"name": "Jooble – Iraq",        "url": "https://iq.jooble.org/SearchResult?keywords=network%20engineer"},
    {"name": "Bayt – Azerbaijan",    "url": "https://www.bayt.com/en/azerbaijan/jobs/network-engineer-jobs/"},
    # ---------- Azerbaijan ----------
    {"name": "eJob – Azerbaijan",    "url": "https://ejob.az/en/jobs/?search=network"},
    {"name": "Joba – Azerbaijan",    "url": "https://joba.az/en/vacancies?search=network"},
    {"name": "Jooble – Azerbaijan",  "url": "https://az.jooble.org/SearchResult?keywords=network%20engineer"},
    {"name": "Bayt – Armenia",       "url": "https://www.bayt.com/en/armenia/jobs/network-engineer-jobs/"},
    # ---------- Armenia ----------
    {"name": "Job.am – Armenia",     "url": "https://job.am/en/jobs?query=network"},
    {"name": "Staff.am – Armenia",   "url": "https://staff.am/en/jobs?Jobs%5Bkeyword%5D=network"},
    {"name": "Worknet – Armenia",    "url": "https://worknet.am/en/?s=network"},
    {"name": "Jooble – Armenia",     "url": "https://am.jooble.org/SearchResult?keywords=network%20engineer"},
]


def clean(text):
    return re.sub(r"\s+", " ", (text or "")).strip()


def scrape_site(site):
    """Fetch one site and return jobs found via generic link extraction."""
    jobs = []
    try:
        resp = SESSION.get(site["url"], timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
    except Exception as exc:
        print(f"[!] {site['name']}: request failed -> {exc}")
        return jobs

    soup = BeautifulSoup(resp.text, "html.parser")
    seen_urls = set()
    base = site["url"]
    for anchor in soup.find_all("a", href=True):
        title = clean(anchor.get_text(" ", strip=True))
        if not title or len(title) < 4 or len(title) > 120:
            continue
        if not KEYWORD_RE.search(title):
            continue

        link = urljoin(base, anchor["href"].strip())
        parsed = urlparse(link)
        if parsed.scheme not in ("http", "https"):
            continue
        path = parsed.path.lower()
        if not any(h in path for h in JOB_LINK_HINTS):
            continue
        if link in seen_urls:
            continue
        seen_urls.add(link)
        jobs.append({"title": title, "link": link, "source": site["name"]})

    print(f"[+] {site['name']}: {len(jobs)} candidate job(s)")
    return jobs


def load_state():
    try:
        with open(SEEN_FILE, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(state):
    with open(SEEN_FILE, "w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False, indent=1)


def prune_state(state):
    cutoff = datetime.now(timezone.utc) - timedelta(days=STATE_DAYS)
    return {
        link: ts for link, ts in state.items()
        if datetime.fromisoformat(ts) > cutoff
    }


def send_telegram(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    resp = requests.post(url, json={
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()


def deliver(new_jobs):
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    lines = [
        f"📡 <b>Networking Jobs</b> — {today}\n"
        f"🆕 {len(new_jobs)} new ad(s)\n"
    ]
    chunks = []
    for job in new_jobs:
        line = (
            f"\n🔹 <b>{html.escape(job['title'])}</b>\n"
            f"🌐 {html.escape(job['source'])}\n"
            f"🔗 {job['link']}"
        )
        if sum(len(x) for x in lines) + len(line) > 3500:
            chunks.append("\n".join(lines))
            lines = []
        lines.append(line)
    chunks.append("\n".join(lines))

    for chunk in chunks:
        send_telegram(chunk)
        time.sleep(1)


def main():
    if not BOT_TOKEN or not CHAT_ID:
        raise SystemExit("BOT_TOKEN / CHAT_ID secrets are missing")

    all_jobs = []
    for site in SITES:
        all_jobs.extend(scrape_site(site))
        time.sleep(DELAY_BETWEEN_SITES)

    state = prune_state(load_state())
    new_jobs = [j for j in all_jobs if j["link"] not in state]
    now_iso = datetime.now(timezone.utc).isoformat()
    for job in new_jobs:
        state[job["link"]] = now_iso
    save_state(state)

    print(f"[*] total candidates: {len(all_jobs)} | new: {len(new_jobs)}")
    if new_jobs:
        deliver(new_jobs)
    else:
        print("nothing new to send")


if __name__ == "__main__":
    main()