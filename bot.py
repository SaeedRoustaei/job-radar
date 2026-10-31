# -*- coding: utf-8 -*-
"""
Job Radar v6 (final) — networking jobs in Iraq, Azerbaijan & Armenia.

- Runs twice a day (09:00 & 18:00 Tehran) via GitHub Actions — no manual runs
- Scans LinkedIn (guest API), job.am, vakansiyam.az, vacanciesiniraq.com
  and 8 public Telegram job channels
- English ads only, >=60% skill match with resume.txt (title + ad text)
- Deduplicates across runs (seen.json)
- Each proposal has a "📝 Cover letter" button; when you tap it, the bot
  builds a tailored PDF cover letter and sends it here as a downloadable file

Secrets: BOT_TOKEN, CHAT_ID
Repo files: resume.txt
State files (auto-committed): seen.json, jobs.json, state.json
"""

import json
import hashlib
import html
import os
import re
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin, quote

import requests
from bs4 import BeautifulSoup
from fpdf import FPDF

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
CHAT_ID = os.environ.get("CHAT_ID", "")
CALLBACK_ONLY = os.environ.get("CALLBACK_ONLY") == "1"

SEEN_FILE = "seen.json"
JOBS_FILE = "jobs.json"
STATE_FILE = "state.json"
RESUME_TXT = "resume.txt"

STATE_DAYS = 45
REQUEST_TIMEOUT = 30
DELAY = 2

MATCH_THRESHOLD = 60   # show ads scoring >= this

SIGNATURE = (
    "Saeed Roustaei\n"
    "Email: saeedrs71@gmail.com\n"
    "Phone: +98 919 224 0209\n"
    "LinkedIn: https://www.linkedin.com/in/saeed-rouataei/"
)

KEYWORDS = [
    "network engineer", "network administrator", "network admin",
    "network specialist", "network technician", "networking",
    "noc engineer", "noc", "system engineer", "systems engineer",
    "infrastructure engineer", "routing", "switching", "cisco",
    "aci", "vxlan", "ospf", "bgp", "vlan", "stp", "ccna", "ccnp",
    "ccie", "sd-wan", "firewall", "şəbəkə", "ցանց", "شبكات", "شبکه",
]

SKILLS = [
    "cisco", "ccna", "ccnp", "ccie", "routing", "switching", "ospf",
    "bgp", "vlan", "stp", "vxlan", "aci", "sd-wan", "sdn", "mpls",
    "firewall", "fortigate", "palo alto", "checkpoint", "asa",
    "nexus", "catalyst", "wireless", "wlc", "vpn", "ipsec",
    "qos", "load balancing", "f5", "tcp/ip", "dns", "dhcp", "linux",
    "windows server", "wireshark", "monitoring", "zabbix", "nagios",
    "solarwinds", "python", "automation", "ansible", "voip", "sip",
    "azure", "aws", "vmware", "data center", "troubleshooting",
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

TG_CHANNELS = [
    {"slug": "jobs_inarmenia",   "country": "Armenia"},
    {"slug": "bestjobinarmenia", "country": "Armenia"},
    {"slug": "JobBroadcast",     "country": "Armenia"},
    {"slug": "jobsarm",          "country": "Armenia"},
    {"slug": "Rabota_Az",        "country": "Azerbaijan"},
    {"slug": "vakansiyam_az",    "country": "Azerbaijan"},
    {"slug": "awezajobs",        "country": "Iraq"},
    {"slug": "allkurdistanjobs", "country": "Iraq"},
]


# ================================================================ helpers
def clean(text):
    return re.sub(r"\s+", " ", (text or "")).strip()


def stable_link(url):
    return url.split("?")[0].split("#")[0]


def job_id(link):
    return hashlib.md5(link.encode()).hexdigest()[:10]


def is_english(text):
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return False
    latin = [c for c in letters if c.isascii()]
    return len(latin) / len(letters) > 0.6


def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1)


# ================================================================ resume & scoring
def load_resume():
    try:
        with open(RESUME_TXT, "r", encoding="utf-8") as fh:
            return fh.read()
    except FileNotFoundError:
        return ""


def resume_skills(resume_text):
    low = resume_text.lower()
    return {skill for skill in SKILLS if skill in low}


def score_job(job, my_skills):
    """0-100 match between the ad (title + text) and the resume's skills."""
    text = f"{job['title']} {job.get('snippet', '')} {job.get('company', '')}".lower()
    matched = [s for s in my_skills if s in text]
    score = 45                                   # title already keyword-matched
    score += min(50, 10 * len(matched))          # skills overlap
    return min(100, score), matched


def make_cover_letter(job, matched):
    title = job["title"]
    company = job.get("company") or "your organization"
    skills = ", ".join(sorted(matched)[:6]) if matched else \
        "network design, routing & switching, and troubleshooting"
    return (
        f"Cover Letter\n"
        f"{'=' * 60}\n\n"
        f"Dear Hiring Team,\n\n"
        f"I am writing to apply for the position of {title} at {company}. "
        f"With hands-on experience in {skills}, I believe my background aligns "
        f"well with the requirements of this role.\n\n"
        f"In my current work I design, implement and troubleshoot enterprise "
        f"networks, and I am comfortable working with international teams and "
        f"standards-based documentation. I would welcome the opportunity to "
        f"discuss how I can contribute to your team.\n\n"
        f"Thank you for your time and consideration.\n\n"
        f"{SIGNATURE}"
    )


def make_cover_pdf(text):
    """Build a simple one-page A4 PDF (English text) as bytes."""
    pdf = FPDF(orientation="P", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 10, "Cover Letter", ln=1)
    pdf.set_draw_color(200, 200, 200)
    pdf.line(10, 22, 200, 22)
    pdf.ln(6)
    pdf.set_font("Helvetica", size=11)
    safe = text.encode("latin-1", "replace").decode("latin-1")
    pdf.multi_cell(0, 6, safe)
    return bytes(pdf.output())


# ================================================================ sources
def linkedin_job_details(link):
    """Fetch the full description of one LinkedIn job via the guest API."""
    m = re.search(r"(\d{6,})", link.split("?")[0])
    if not m:
        return ""
    url = f"https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{m.group(1)}"
    try:
        resp = SESSION.get(url, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
    except Exception:
        return ""
    soup = BeautifulSoup(resp.text, "html.parser")
    desc = soup.select_one("div.show-more-less-html__markup")
    if not desc:
        return ""
    return clean(desc.get_text(" ", strip=True))[:3000]


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
        jobs.append({
            "title": clean(title_el.get_text()),
            "link": stable_link(urljoin(url, a.get("href", ""))),
            "company": clean(company_el.get_text()) if company_el else "",
            "location": clean(loc_el.get_text()) if loc_el else location,
            "source": "LinkedIn",
            "snippet": "",
        })
    print(f"[+] LinkedIn {location} / '{keyword}': {len(jobs)}")
    return jobs


def generic_board(url, source, country, link_contains):
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
        if not title or len(title) < 3 or len(title) > 120:
            continue
        if not KEYWORD_RE.search(title):
            continue
        link = stable_link(urljoin(url, href))
        if link in seen:
            continue
        seen.add(link)
        jobs.append({"title": title, "link": link, "company": "",
                     "location": country, "source": source, "snippet": ""})
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
        jobs.append({"title": title, "link": link, "company": "",
                     "location": "Iraq", "source": "VacanciesInIraq", "snippet": ""})
    print(f"[+] vacanciesiniraq '{query}': {len(jobs)}")
    return jobs


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
        if len(text) < 10 or not KEYWORD_RE.search(text):
            continue
        date_el = msg.select_one("a.tgme_widget_message_date")
        if not date_el:
            continue
        link = stable_link(urljoin(url, date_el.get("href", "")))
        if link.rstrip("/") == url:
            continue
        first_line = text.split(". ")[0][:90]
        jobs.append({
            "title": first_line or "Telegram job post",
            "link": link, "company": "",
            "location": channel["country"],
            "source": f"TG @{channel['slug']}",
            "snippet": text[:1500],
        })
    print(f"[+] TG @{channel['slug']}: {len(jobs)}")
    return jobs


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


# ================================================================ telegram
def tg_api(method, payload):
    resp = requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/{method}",
        json=payload, timeout=REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()


def send_message(text, reply_markup=None):
    payload = {
        "chat_id": CHAT_ID, "text": text,
        "parse_mode": "HTML", "disable_web_page_preview": True,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    return tg_api("sendMessage", payload)


def send_document(filename, content, caption=""):
    resp = requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendDocument",
        data={"chat_id": CHAT_ID, "caption": caption},
        files={"document": (filename, content, "application/pdf")},
        timeout=REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()


def edit_message(msg_id, text):
    try:
        tg_api("editMessageText", {
            "chat_id": CHAT_ID, "message_id": msg_id, "text": text,
            "parse_mode": "HTML", "disable_web_page_preview": True,
        })
    except Exception as exc:
        print(f"[!] editMessage failed: {exc}")


def process_callbacks(seen, jobs_store, state):
    """Handle 'cover letter' / dismiss button presses since last run."""
    offset = state.get("offset", 0)
    try:
        updates = tg_api("getUpdates", {
            "offset": offset, "timeout": 0,
            "allowed_updates": ["callback_query"],
        }).get("result", [])
    except Exception as exc:
        print(f"[!] getUpdates failed: {exc}")
        return

    handled = 0
    for upd in updates:
        offset = max(offset, upd["update_id"] + 1)
        cb = upd.get("callback_query")
        if not cb:
            continue
        data = cb.get("data", "")
        cb_id = cb["id"]
        jid = data.split(":", 1)[-1]
        job = jobs_store.get(jid)
        try:
            if job and data.startswith("cover:"):
                matched = job.get("matched", [])
                cover = make_cover_letter(job, matched)
                pdf = make_cover_pdf(cover)
                send_document(
                    "Cover_Letter.pdf", pdf,
                    caption=f"📝 Cover letter — {job['title']}",
                )
                edit_message(job["msg_id"],
                             f"📄 Cover letter sent above ✅\n\n"
                             f"<b>{html.escape(job['title'])}</b>\n"
                             f"🔗 {job['link']}")
                tg_api("answerCallbackQuery", {
                    "callback_query_id": cb_id, "text": "Cover letter ready!",
                })
                handled += 1
            elif job and data.startswith("skip:"):
                edit_message(job["msg_id"],
                             f"❌ Dismissed\n\n{html.escape(job['title'])}\n"
                             f"🔗 {job['link']}")
                tg_api("answerCallbackQuery", {
                    "callback_query_id": cb_id, "text": "Dismissed",
                })
                handled += 1
            else:
                tg_api("answerCallbackQuery", {
                    "callback_query_id": cb_id, "text": "Expired",
                })
        except Exception as exc:
            print(f"[!] callback handling failed: {exc}")
    state["offset"] = offset
    print(f"[*] callbacks handled: {handled}")


# ================================================================ deliver
def propose(job, matched, score, jobs_store):
    jid = job_id(job["link"])
    text = (
        f"🎯 <b>{html.escape(job['title'])}</b> — match {score}٪\n"
        f"🏢 {html.escape(job.get('company') or '—')}"
        f" | 📍 {html.escape(job.get('location') or '—')}"
        f" | 🌐 {html.escape(job['source'])}\n"
        f"🧩 {html.escape(', '.join(sorted(matched)[:8]) or '—')}\n"
        f"📝 {html.escape(job.get('snippet', '')[:350])}\n"
        f"🔗 {job['link']}"
    )
    keyboard = {"inline_keyboard": [[
        {"text": "📝 Cover letter", "callback_data": f"cover:{jid}"},
        {"text": "❌ Dismiss", "callback_data": f"skip:{jid}"},
    ]]}
    sent = send_message(text, keyboard)
    job["matched"] = matched
    job["score"] = score
    job["msg_id"] = sent["result"]["message_id"]
    jobs_store[jid] = job


# ================================================================ main
def main():
    if not BOT_TOKEN or not CHAT_ID:
        raise SystemExit("BOT_TOKEN / CHAT_ID secrets are missing")

    seen = load_json(SEEN_FILE, {})
    jobs_store = load_json(JOBS_FILE, {})
    state = load_json(STATE_FILE, {})

    # 1) handle button presses since the last run
    process_callbacks(seen, jobs_store, state)

    if CALLBACK_ONLY:
        # between scans: only process button presses, exit fast
        save_json(JOBS_FILE, jobs_store)
        save_json(STATE_FILE, state)
        return

    # 2) full scan
    resume = load_resume()
    if not resume.strip():
        print("[!] resume.txt is empty — add your English resume to the repo")
    my_skills = resume_skills(resume)
    print(f"[*] resume skills found: {len(my_skills)}")

    now_iso = datetime.now(timezone.utc).isoformat()
    proposed = 0
    for job in collect_all():
        if job["link"] in seen:
            continue
        if not is_english(f"{job['title']} {job.get('snippet', '')}"):
            continue

        # LinkedIn ads have no snippet from search — fetch the full text once
        if job["source"] == "LinkedIn" and not job.get("snippet"):
            desc = linkedin_job_details(job["link"])
            if desc:
                job["snippet"] = desc
                time.sleep(1)

        score, matched = score_job(job, my_skills)
        seen[job["link"]] = now_iso
        if score < MATCH_THRESHOLD:
            continue
        try:
            propose(job, matched, score, jobs_store)
            proposed += 1
            time.sleep(0.5)
        except Exception as exc:
            print(f"[!] propose failed: {exc}")

    # 3) prune state older than STATE_DAYS
    cutoff = datetime.now(timezone.utc) - timedelta(days=STATE_DAYS)
    seen = {k: v for k, v in seen.items()
            if isinstance(v, str) and v > cutoff.isoformat()}
    jobs_store = {k: v for k, v in jobs_store.items()
                  if seen.get(v.get("link"))}

    save_json(SEEN_FILE, seen)
    save_json(JOBS_FILE, jobs_store)
    save_json(STATE_FILE, state)
    print(f"[*] proposals sent: {proposed}")


if __name__ == "__main__":
    main()