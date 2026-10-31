# -*- coding: utf-8 -*-
"""
Job Radar v3 — networking jobs in Iraq, Azerbaijan and Armenia.
Sources:
  - LinkedIn public guest API (all 3 countries, several keywords)
  - job.am, vacanciesiniraq.com, vakansiyam.az (IT section)
  - Public Telegram job channels via t.me/s/ preview (no login needed)
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
DELAY = 2  # seconds between requests

KEYWORDS = [
    "network engineer", "network administrator", "network admin",
    "network specialist", "network technician", "networking",
    "noc engineer", "noc", "system engineer", "systems engineer",
    "infrastructure engineer", "routing", "switching", "cisco",
    "aci", "vxlan", "ospf", "bgp", "vlan", "stp", "ccna", "ccnp",
    "ccie", "sd-wan", "firewall", "şəbəkə", "ցանց", "شبكات", "شبکه",
]

# for job-board titles: ad must also look like a real IT/networking job
STRONG_HINTS = [
    "network", "şəbəkə", "noc", "cisco", "routing", "switching",
    "ospf", "bgp", "vlan", "vxlan", "infrastructure", "sysadmin",
    "system administrator", "systems administrator",
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
LINKEDIN_KEYWORDS = [
    "network engineer", "network administrator", "NOC engineer",
]

# Public Telegram job channels (read via t.me/s preview page)
TG_CHANNELS = [
    {"slug": "jobs_inarmenia",     "name": "Jobs in Armenia",       "country": "Armenia"},
    {"slug": "bestjobinarmenia",   "name": "Best Job in Armenia",   "country": "Armenia"},
    {"slug": "JobBroadcast",       "name": "Job Broadcast",         "country": "Armenia"},
    {"slug": "jobsarm",            "name": "Работа в Армении",      "country": "Armenia"},
    {"slug": "Rabota_Az",          "name": "Vakansiyalar Baku",     "country": "Azerbaijan"},
    {"slug": "vakansiyam_az",      "name": "Vakansiyam.az",         "country": "Azerbaijan"},
    {"slug": "awezajobs",          "name": "Aweza Jobs",            "country": "Iraq"},
    {"slug": "allkurdistanjobs",   "name": "All Kurdistan Jobs",    "country": "Iraq"},
]


def clean(text):
    return re.sub(r"\s+", " ", (text or "")).strip()


def stable_link(url):
    return url.split("?")[0].split("#")[0]


# ---------------------------------------------------------------- LinkedIn
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
        jobs.append({
            "title": clean(title_el.get_text()),
            "link": link,
            "company": clean(company_el.get_text()) if company_el else "",
            "location": clean(loc_el.get_text()) if loc_el else location,
            "source": "LinkedIn",
        })
    print(f"[+] LinkedIn {location} / '{keyword}': {len(jobs)}")
    return jobs


# ---------------------------------------------------------------- job boards
def generic_board(url, source, country, link_contains, min_len=3):
    """Scrape anchors whose href contains link_contains, keyword-filter titles."""
    jobs = []
    try:
        resp = SESSION.get(url, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
    except Exception as exc:
        print(f"[!] {source}: {exc}")
        return jobs

    soup = BeautifulSoup(resp.text, "html.parser")
    seen = set()
    for anchor in soup.find_all("a", href=True):
        href = anchor["href"]
        if link_contains not in href:
            continue
        title = clean(anchor.get_text(" ", strip=True))
        if not title or len(title) < min_len or len(title) > 120:
            continue
        if not KEYWORD_RE.search(title):
            continue
        link = stable_link(urljoin(url, href))
        if link in seen:
            continue
        seen.add(link)
        jobs.append({
            "title": title, "link": link, "company": "",
            "location": country, "source": source,
        })
    print(f"[+] {source}: {len(jobs)}")
    return jobs


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
    print(f"[+] vacanciesiniraq '{query}': {len(jobs)}")
    return jobs


# ---------------------------------------------------------------- Telegram channels
def telegram_channel(channel):
    url = f"https://t.me/s/{channel['slug']}"
    jobs = []
    try:
        resp = SESSION.get(url, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
    except Exception as exc:
        print(f"[!] TG @{channel['slug']}: {exc}")
        return jobs

    soup = BeautifulSoup(resp.text, "html.parser")
    for msg in soup.select(".tgme_widget_message"):
        text_el = msg.select_one(".tgme_widget_message_text")
        if not text_el:
            continue
        text = clean(text_el.get_text(" ", strip=True))
        if len(text) < 10:
            continue
        if not KEYWORD_RE.search(text):
            continue
        date_el = msg.select_one("a.tgme_widget_message_date")
        if not date_el:
            continue
        link = stable_link(urljoin(url, date_el.get("href", "")))
        if "//" in link or link.rstrip("/") == url:
            continue
        # title = first ~90 chars of the post
        title = text[:90] + ("…" if len(text) > 90 else "")
        jobs.append({
            "title": title,
            "link": link,
            "company": "",
            "location": channel["country"],
            "source": f"TG @{channel['slug']}",
            "snippet": text[:350] + ("…" if len(text) > 350 else ""),
        })
    print(f"[+] TG @{channel['slug']}: {len(jobs)}")
    return jobs


# ---------------------------------------------------------------- pipeline
def collect_all():
    jobs = {}
    for loc in LINKEDIN_LOCATIONS:
        for kw in LINKEDIN_KEYWORDS:
            for job in linkedin_search(kw, loc):
                jobs[job["link"]] = job
            time.sleep(DELAY)
    for q in ["network", "network administrator"]:
        for job in generic_board(
            f"https://job.am/en/jobs/?s={quote(q)}",
            "job.am", "Armenia", "/en/job/",
        ):
            jobs[job["link"]] = job
        time.sleep(DELAY)
    for job in generic_board(
        "https://www.vakansiyam.az/en/vakansiyalar/it",
        "Vakansiyam.az", "Azerbaijan", "/en/vakansiyalar/",
    ):
        jobs[job["link"]] = job
    time.sleep(DELAY)
    for q in ["network engineer", "network administrator"]:
        for job in vacancies_in_iraq(q):
            jobs[job["link"]] = job
        time.sleep(DELAY)
    for channel in TG_CHANNELS:
        for job in telegram_channel(channel):
            jobs[job["link"]] = job
        time.sleep(DELAY)
    return list(jobs.values())


def is_relevant(job):
    # channel posts are already matched on full text; boards need the
    # second hint check on the short title only
    if job.get("snippet"):
        return True
    if not KEYWORD_RE.search(job["title"]):
        return False
    text = f"{job['title']} {job.get('company', '')}".lower()
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
        line += f"\n🌐 {html.escape(job['source'])}"
        if job.get("snippet"):
            line += f"\n📝 {html.escape(job['snippet'])}"
        line += f"\n🔗 {job['link']}"
        if sum(len(x) for x in lines) + len(line) > 3300:
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
