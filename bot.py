# -*- coding: utf-8 -*-
"""
Job Radar v4 — networking jobs in Iraq, Azerbaijan & Armenia.
Scan -> score against resume -> propose English-only ads (>=80% match,
with a direct email) -> you approve via inline buttons -> next run sends
the application email (resume + tailored cover letter) from your Gmail.

Secrets required:
  BOT_TOKEN, CHAT_ID, GMAIL_USER, GMAIL_APP_PASSWORD
Repo files:
  resume.txt (your English resume, plain text) — resume.pdf attached if present
State files (auto-committed): seen.json, pending.json, applied.json
"""

import json
import hashlib
import html
import os
import re
import smtplib
import time
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from urllib.parse import urljoin, quote

import requests
from bs4 import BeautifulSoup

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
CHAT_ID = os.environ.get("CHAT_ID", "")
GMAIL_USER = os.environ.get("GMAIL_USER", "")
GMAIL_PASS = os.environ.get("GMAIL_APP_PASSWORD", "")

SEEN_FILE = "seen.json"
PENDING_FILE = "pending.json"
APPLIED_FILE = "applied.json"
RESUME_TXT = "resume.txt"
RESUME_PDF = "resume.pdf"

STATE_DAYS = 45
REQUEST_TIMEOUT = 30
DELAY = 2

AUTO_THRESHOLD = 80   # >= : propose auto-email with approve buttons
VIEW_THRESHOLD = 60   # >= : just show with a manual Apply link

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

STRONG_HINTS = [
    "network", "şəbəkə", "noc", "cisco", "routing", "switching",
    "ospf", "bgp", "vlan", "vxlan", "infrastructure", "sysadmin",
    "system administrator", "systems administrator",
]

# skill vocabulary used for matching resume <-> job ad
SKILLS = [
    "cisco", "ccna", "ccnp", "ccie", "routing", "switching", "ospf",
    "bgp", "vlan", "stp", "vxlan", "aci", "sd-wan", "sdn", "mpls",
    "firewall", "fortigate", "palo alto", "checkpoint", "asa",
    "nexus", "catalyst", "wireless", "wlc", "vpn", "ipsec", "bgp peering",
    "qos", "load balancing", "f5", "tcp/ip", "dns", "dhcp", "linux",
    "windows server", "wireshark", "monitoring", "zabbix", "nagios",
    "solarwinds", "python", "automation", "ansible", "voip", "sip",
    "azure", "aws", "vmware", "data center", "troubleshooting",
]

KEYWORD_RE = re.compile(
    "|".join(r"\b" + re.escape(k) + r"\b" for k in KEYWORDS),
    re.IGNORECASE,
)
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.\-]+", re.IGNORECASE)

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
    {"slug": "jobs_inarmenia",   "name": "Jobs in Armenia",     "country": "Armenia"},
    {"slug": "bestjobinarmenia", "name": "Best Job in Armenia", "country": "Armenia"},
    {"slug": "JobBroadcast",     "name": "Job Broadcast",       "country": "Armenia"},
    {"slug": "jobsarm",          "name": "Работа в Армении",    "country": "Armenia"},
    {"slug": "Rabota_Az",        "name": "Vakansiyalar Baku",   "country": "Azerbaijan"},
    {"slug": "vakansiyam_az",    "name": "Vakansiyam.az",       "country": "Azerbaijan"},
    {"slug": "awezajobs",        "name": "Aweza Jobs",          "country": "Iraq"},
    {"slug": "allkurdistanjobs", "name": "All Kurdistan Jobs",  "country": "Iraq"},
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
    found = set()
    low = resume_text.lower()
    for skill in SKILLS:
        if skill in low:
            found.add(skill)
    return found


def score_job(job, my_skills):
    """0-100 match score between the ad and the resume's skills."""
    text = f"{job['title']} {job.get('snippet', '')} {job.get('company', '')}".lower()
    matched = [s for s in my_skills if s in text]
    score = 40                                   # title already keyword-matched
    score += min(50, 10 * len(matched))          # skills overlap
    if EMAIL_RE.search(job.get("snippet", "")):
        score += 10                              # direct email = auto-applyable
    return min(100, score), matched


def make_cover_letter(job, matched):
    title = job["title"]
    company = job.get("company") or "your organization"
    skills = ", ".join(sorted(matched)[:6]) if matched else \
        "network design, routing & switching, and troubleshooting"
    return (
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


# ================================================================ sources
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
        # first message line that looks like a job title
        first_line = text.split(". ")[0][:90]
        jobs.append({
            "title": first_line or "Telegram job post",
            "link": link, "company": "",
            "location": channel["country"],
            "source": f"TG @{channel['slug']}",
            "snippet": text[:1200],
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


def process_callbacks(pending, seen, applied):
    """Read approve/skip button presses since last run."""
    offset = pending.get("offset", 0)
    try:
        updates = tg_api("getUpdates", {
            "offset": offset, "timeout": 0,
            "allowed_updates": ["callback_query"],
        }).get("result", [])
    except Exception as exc:
        print(f"[!] getUpdates failed: {exc}")
        return

    for upd in updates:
        offset = max(offset, upd["update_id"] + 1)
        cb = upd.get("callback_query")
        if not cb:
            continue
        data = cb.get("data", "")
        cb_id = cb["id"]
        jid = data.split(":", 1)[-1]
        entry = pending.get("jobs", {}).get(jid)
        try:
            if entry and data.startswith("apply:"):
                ok = send_application(entry)
                note = "✅ Application email sent!" if ok else "❌ Email failed"
                if ok:
                    applied[entry["link"]] = {
                        "title": entry["title"],
                        "email": entry["email"],
                        "date": datetime.now(timezone.utc).isoformat(),
                    }
                    seen[entry["link"]] = datetime.now(timezone.utc).isoformat()
            elif entry and data.startswith("skip:"):
                seen[entry["link"]] = datetime.now(timezone.utc).isoformat()
                note = "❌ Skipped"
            else:
                note = "Expired"
            tg_api("answerCallbackQuery", {"callback_query_id": cb_id, "text": note})
            if entry:
                tg_api("editMessageText", {
                    "chat_id": CHAT_ID,
                    "message_id": entry["msg_id"],
                    "text": f"{note}\n\n{entry['title']}\n🔗 {entry['link']}",
                    "disable_web_page_preview": True,
                })
                pending["jobs"].pop(jid, None)
        except Exception as exc:
            print(f"[!] callback handling failed: {exc}")
    pending["offset"] = offset


# ================================================================ email
def send_application(entry):
    if os.environ.get("TEST_EMAIL"):
        entry = dict(entry)
        entry["email"] = os.environ["TEST_EMAIL"]
        print(f"[test] email redirected to {entry['email']}")
    if not GMAIL_USER or not GMAIL_PASS:
        print("[!] Gmail credentials missing")
        return False
    msg = EmailMessage()
    msg["From"] = GMAIL_USER
    msg["To"] = entry["email"]
    msg["Subject"] = f"Application: {entry['title']} — Saeed Roustaei"
    msg.set_content(entry["cover"])
    if os.path.exists(RESUME_PDF):
        with open(RESUME_PDF, "rb") as fh:
            msg.add_attachment(
                fh.read(), maintype="application",
                subtype="pdf", filename="Saeed_Roustaei_CV.pdf",
            )
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=REQUEST_TIMEOUT) as smtp:
            smtp.login(GMAIL_USER, GMAIL_PASS)
            smtp.send_message(msg)
        print(f"[+] application sent -> {entry['email']}")
        return True
    except Exception as exc:
        print(f"[!] send failed ({entry['email']}): {exc}")
        return False


# ================================================================ deliver
def propose(job, matched, score, cover, pending):
    email_match = EMAIL_RE.search(job.get("snippet", ""))
    email_addr = email_match.group(0) if email_match else ""
    jid = job_id(job["link"])
    text = (
        f"🎯 <b>{html.escape(job['title'])}</b> — match {score}٪\n"
        f"🏢 {html.escape(job.get('company') or '—')}"
        f" | 📍 {html.escape(job.get('location') or '—')}"
        f" | 🌐 {html.escape(job['source'])}\n"
        f"🧩 skills matched: {html.escape(', '.join(sorted(matched)[:8]) or '—')}\n"
        f"📝 {html.escape(job.get('snippet', '')[:400])}\n"
        f"🔗 {job['link']}\n"
        f"─────────────\n"
        f"<b>Cover letter (will be sent as-is):</b>\n"
        f"<i>{html.escape(cover[:1200])}</i>"
    )
    if email_addr:
        text += f"\n\n📧 will be sent to: {email_addr}"
        keyboard = {"inline_keyboard": [[
            {"text": "✅ Send", "callback_data": f"apply:{jid}"},
            {"text": "❌ Skip", "callback_data": f"skip:{jid}"},
        ]]}
    else:
        text += "\n\nℹ️ no direct email found — apply manually via the link"
        keyboard = {"inline_keyboard": [[
            {"text": "❌ Dismiss", "callback_data": f"skip:{jid}"},
        ]]}

    sent = send_message(text, keyboard)
    if email_addr:
        pending.setdefault("jobs", {})[jid] = {
            "title": job["title"], "link": job["link"],
            "email": email_addr, "cover": cover,
            "msg_id": sent["result"]["message_id"],
        }


def inform(job, matched, score):
    text = (
        f"👁 <b>{html.escape(job['title'])}</b> — match {score}٪\n"
        f"🏢 {html.escape(job.get('company') or '—')}"
        f" | 📍 {html.escape(job.get('location') or '—')}"
        f" | 🌐 {html.escape(job['source'])}\n"
        f"🧩 {html.escape(', '.join(sorted(matched)[:8]) or '—')}\n"
        f"🔗 {job['link']}"
    )
    send_message(text)


# ================================================================ main
def main():
    if not BOT_TOKEN or not CHAT_ID:
        raise SystemExit("BOT_TOKEN / CHAT_ID secrets are missing")

    seen = load_json(SEEN_FILE, {})
    pending = load_json(PENDING_FILE, {})
    applied = load_json(APPLIED_FILE, {})

    # 1) handle button presses from the previous run
    process_callbacks(pending, seen, applied)

    # 2) scan sources
    resume = load_resume()
    if not resume.strip():
        print("[!] resume.txt is empty — add your English resume to the repo")
    my_skills = resume_skills(resume)
    print(f"[*] resume skills found: {len(my_skills)}")

    now_iso = datetime.now(timezone.utc).isoformat()
    auto, views = 0, 0
    for job in collect_all():
        if job["link"] in seen or job["link"] in applied:
            continue
        if not is_english(f"{job['title']} {job.get('snippet', '')}"):
            continue
        score, matched = score_job(job, my_skills)
        if score < VIEW_THRESHOLD:
            continue
        if score >= AUTO_THRESHOLD and EMAIL_RE.search(job.get("snippet", "")):
            cover = make_cover_letter(job, matched)
            try:
                propose(job, matched, score, cover, pending)
                auto += 1
            except Exception as exc:
                print(f"[!] propose failed: {exc}")
            seen[job["link"]] = now_iso
        else:
            try:
                inform(job, matched, score)
                views += 1
            except Exception as exc:
                print(f"[!] inform failed: {exc}")
            seen[job["link"]] = now_iso
        time.sleep(0.5)

    # 3) prune old state
    cutoff = datetime.now(timezone.utc) - timedelta(days=STATE_DAYS)
    seen = {k: v for k, v in seen.items()
            if isinstance(v, str) and v > cutoff.isoformat()}

    save_json(SEEN_FILE, seen)
    save_json(PENDING_FILE, pending)
    save_json(APPLIED_FILE, applied)
    print(f"[*] auto-apply proposals: {auto} | manual suggestions: {views}")


if __name__ == "__main__":
    main()
