# -*- coding: utf-8 -*-
"""
Job Radar v2 — networking jobs in Iraq, Azerbaijan and Armenia.
Sources: LinkedIn public jobs guest API + job.am + vacanciesiniraq.com
Runs on GitHub Actions twice a day (Tehran time 09:00 & 18:00).
"""

import json
import os
import re
import time
import html
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin, quote

import requests
from bs4 import BeautifulSoup

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
CHAT_ID = os.environ.get("CHAT_ID", "")

SEEN_FILE = "seen.json"
STATE_DAYS = 45
REQUEST_TIMEOUT = 30
DELAY_BETWEEN_REQUESTS = 2

KEYWORDS = [
    "network engineer", "network administrator", "network admin",
    "network specialist", "network technician", "networking",
    "noc engineer", "noc", "system engineer", "systems engineer",
    "infrastructure engineer", "routing", "switching", "cisco",
    "aci", "vxlan", "ospf", "bgp", "vlan", "stp", "ccna", "ccnp",
    "ccie", "sd-wan", "firewall", "şəbəkə",
]

# words that mean the ad is really a networking/IT job, not just any job
STRONG_HINTS = [
    "network", "şəbəkə", "noc", "cisco", "routing", "switching",
    "ospf", "bgp", "vlan", "vxlan", "infrastructure", "sysadmin",
    "system administrator", "systems administrator", "it ", "information technology",
]

KEYWORD_RE = re.compile(
    "|".join(r"\b" + re.escape(k) + r"\b" for k in KEYWORDS),
    re.IGNORECASE,
)

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "en",
})

LINKEDIN_LOCATIONS = ["Iraq", "Azerbaijan", "Armenia"]
LINKEDIN_KEYWORDS = ["network engineer", "network administrator", "NOC engineer"]


def clean(text):
    return re.sub(r"\s+", " ", (text or "")).strip()


def stable_link(url):
    """Strip tracking query params so dedup works across runs."""
    return url.split("?")[0].split("#")[0]


# ----------------------------------------------------------------------
# Source 1: LinkedIn public guest API (returns an HTML snippet, no login)
# ----------------------------------------------------------------------
def linkedin_search(keyword, location):
    url = (
        "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
        f"?keywords={quote(keyword)}&location={quote(location)}&start=0"
    )
    jobs = []
    try:
        resp = SESSION.get(url, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
    except Exception as exc:
        print(f"[!] LinkedIn {location} / '{keyword}': {exc}")
        return jobs

    soup = BeautifulSoup(resp.text, "html.parser")
    for card in soup.select("div.base-card"):
        a = card.select_one("a.base-card__full-link")
        title_el = card.select_one("h3.base-search-card__title")
        company_el = card.select_one("h4.base-search-card__subtitle")
        loc_el = card.select_one("span.job-search-card__location")
        if not a or not title_el:
            continue
        link = stable_link(urljoin(url, a.get("href", "")))
        title = clean(title_el.get_text())
        company = clean(company_el.get_text()) if company_el else ""
        loc = clean(loc_el.get_text()) if loc_el else location
        jobs.append({
            "title": title, "link": link, "company": company,
            "location": loc, "source": "LinkedIn",
        })

    print(f"[+] LinkedIn {location} / '{keyword}': {len(jobs)} ad(s)")
    return jobs


# ----------------------------------------------------------------------
# Source 2: job.am (Armenia) — WordPress-style search
# ----------------------------------------------------------------------
def job_am_search(query):
    url = f"https://job.am/en/jobs/?s={quote(query)}"
    jobs = []
    try:
        resp = SESSION.get(url, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
    except Exception as exc:
        print(f"[!] job.am '{query}': {exc}")
        return jobs

    soup = BeautifulSoup(resp.text, "html.parser")
    seen = set()
    for anchor in soup.find_all("a", href=True):
        href = anchor["href"]
        if "/en/job/" not in href:
            continue
        title = clean(anchor.get_text(" ", strip=True))
        if not title or len(title) < 3 or len(title) > 120:
            continue
        link = stable_link(urljoin(url, href))
        if link in seen:
            continue
        seen.add(link)
        if not KEYWORD_RE.search(title):
            continue
        jobs.append({
            "title": title, "link": link, "company": "",
            "location": "Armenia", "source": "job.am",
        })

    print(f"[+] job.am '{query}': {len(jobs)} ad(s)")
    return jobs


# ----------------------------------------------------------------------
# Source 3: vacanciesiniraq.com — WordPress search
# ----------------------------------------------------------------------
def vacancies_in_iraq(query):
    url = f"https://vacanciesiniraq.com/?s={quote(query)}"
    jobs = []
    try:
        resp = SESSION.get(url, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
    except Exception as exc:
        print(f"[!] vacanciesiniraq '{query}': {exc}")
        return jobs

    soup = BeautifulSoup(resp.text, "html.parser")
    seen = set()
    for anchor in soup.find_all("a", href=True):
        title = clean(anchor.get_text(" ", strip=True))
        if not title or len(title) < 4 or len(title) > 120:
            continue
        if not KEYWORD_RE.search(title):
            continue
        link = stable_link(urljoin(url, anchor["href"].strip()))
        if link in seen or link.rstrip("/") == "https://vacanciesiniraq.com":
            continue
        seen.add(link)
        jobs.append({
            "title": title, "link": link, "company": "",
            "location": "Iraq", "source": "VacanciesInIraq",
        })

    print(f"[+] vacanciesiniraq '{query}': {len(jobs)} ad(s)")
    return jobs


# ----------------------------------------------------------------------
def collect_all():
    jobs = {}
    for loc in LINKEDIN_LOCATIONS:
        for kw in LINKEDIN_KEYWORDS:
            for job in linkedin_search(kw, loc):
                jobs[job["link"]] = job
            time.sleep(DELAY_BETWEEN_REQUESTS)
    for q in ["network", "network administrator"]:
        for job in job_am_search(q):
            jobs[job["link"]] = job
        time.sleep(DELAY_BETWEEN_REQUESTS)
    for q in ["network engineer", "network administrator"]:
        for job in vacancies_in_iraq(q):
            jobs[job["link"]] = job
        time.sleep(DELAY_BETWEEN_REQUESTS)
    return list(jobs.values())


def is_relevant(job):
    text = f"{job['title']} {job.get('company', '')}".lower()
    if not KEYWORD_RE.search(job["title"]):
        return False
    return any(h in text for h in STRONG_HINTS)


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
    out = {}
    for link, ts in state.items():
        try:
            if datetime.fromisoformat(ts) > cutoff:
                out[link] = ts
        except ValueError:
            continue
    return out


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
        line = f"\n🔹 <b>{html.escape(job['title'])}</b>"
        if job.get("company"):
            line += f"\n🏢 {html.escape(job['company'])}"
        if job.get("location"):
            line += f"\n📍 {html.escape(job['location'])}"
        line += f"\n🌐 {html.escape(job['source'])}\n🔗 {job['link']}"
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

    all_jobs = collect_all()
    relevant = [j for j in all_jobs if is_relevant(j)]

    state = prune_state(load_state())
    new_jobs = [j for j in relevant if j["link"] not in state]
    now_iso = datetime.now(timezone.utc).isoformat()
    for job in new_jobs:
        state[job["link"]] = now_iso
    save_state(state)

    print(f"[*] total: {len(all_jobs)} | relevant: {len(relevant)} | new: {len(new_jobs)}")
    if new_jobs:
        deliver(new_jobs)
    else:
        print("nothing new to send")


if __name__ == "__main__":
    main()
