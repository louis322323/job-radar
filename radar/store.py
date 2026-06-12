"""SQLite persistence: which jobs have we already seen, page-watch hashes,
and scored results for the dashboard."""
import hashlib
import json
import sqlite3
import time


class Store:
    def __init__(self, path="data/radar.sqlite"):
        self.db = sqlite3.connect(path)
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS seen   (key TEXT PRIMARY KEY, first_seen REAL);
        CREATE TABLE IF NOT EXISTS pages  (source TEXT PRIMARY KEY, hash TEXT);
        CREATE TABLE IF NOT EXISTS scored (key TEXT PRIMARY KEY, ts REAL, json TEXT);
        """)

    @staticmethod
    def key(job):
        raw = (job.get("url") or "") + "|" + (job.get("title") or "")
        return hashlib.sha256(raw.encode()).hexdigest()[:24]

    def is_new(self, job):
        k = self.key(job)
        cur = self.db.execute("SELECT 1 FROM seen WHERE key=?", (k,))
        if cur.fetchone():
            return False
        self.db.execute("INSERT INTO seen VALUES (?,?)", (k, time.time()))
        self.db.commit()
        return True

    def get_page_hash(self, source):
        row = self.db.execute("SELECT hash FROM pages WHERE source=?", (source,)).fetchone()
        return row[0] if row else None

    def set_page_hash(self, source, digest):
        self.db.execute("INSERT OR REPLACE INTO pages VALUES (?,?)", (source, digest))
        self.db.commit()

    def save_scored(self, job):
        self.db.execute("INSERT OR REPLACE INTO scored VALUES (?,?,?)",
                        (self.key(job), time.time(), json.dumps(job, ensure_ascii=False)))
        self.db.commit()

    def recent_scored(self, days=30):
        cutoff = time.time() - days * 86400
        rows = self.db.execute(
            "SELECT json FROM scored WHERE ts>? ORDER BY ts DESC", (cutoff,))
        return [json.loads(r[0]) for r in rows]
