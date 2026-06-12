"""Output: writes dashboard/results.json (for the Netlify dashboard)
and optionally sends an email digest if SMTP env vars are set:

  SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS, DIGEST_TO
  (Gmail: smtp.gmail.com / 587 / app password)
"""
import json
import logging
import os
import smtplib
import time
from email.mime.text import MIMEText

log = logging.getLogger("radar.digest")
MIN_SCORE = int(os.environ.get("MIN_SCORE", "70"))


def write_results(store, path="dashboard/results.json"):
    jobs = store.recent_scored(days=45)
    payload = {"generated": time.strftime("%Y-%m-%d %H:%M"),
               "min_score": MIN_SCORE, "jobs": jobs}
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    log.info("wrote %d jobs to %s", len(jobs), path)


def email_digest(new_jobs):
    host = os.environ.get("SMTP_HOST")
    if not host or not new_jobs:
        return
    keep = [j for j in new_jobs
            if j.get("page_watch") or j.get("score") is None
            or j["score"] >= MIN_SCORE]
    if not keep:
        return
    lines = []
    for j in sorted(keep, key=lambda x: -(x.get("score") or 0)):
        s = j.get("score")
        lines.append(f"[{s if s is not None else '–'}] {j['title']} — {j.get('org','')}"
                     f"\n     {j.get('verdict','')}\n     {j['url']}\n")
    msg = MIMEText("\n".join(lines), "plain", "utf-8")
    msg["Subject"] = f"Job-radar: {len(keep)} nye ({time.strftime('%d/%m')})"
    msg["From"] = os.environ["SMTP_USER"]
    msg["To"] = os.environ["DIGEST_TO"]
    with smtplib.SMTP(host, int(os.environ.get("SMTP_PORT", "587"))) as s:
        s.starttls()
        s.login(os.environ["SMTP_USER"], os.environ["SMTP_PASS"])
        s.send_message(msg)
    log.info("emailed digest: %d jobs", len(keep))
