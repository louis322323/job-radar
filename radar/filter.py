"""Free keyword pre-filter. Kills obvious non-matches before any LLM cost.
Logic: hard_exclude hit anywhere -> drop. Otherwise needs >=1 include hit
(page_watch alerts always pass — they're 'go look manually' signals)."""
import logging

log = logging.getLogger("radar.filter")


class PreFilter:
    def __init__(self, cfg):
        self.include = [w.lower() for w in cfg["include"]]
        self.exclude = [w.lower() for w in cfg["hard_exclude"]]
        self.flags = {k: [w.lower() for w in v] for k, v in cfg.get("flags", {}).items()}

    def check(self, job):
        """Returns (passes: bool, reason: str). Adds job['flags'] for LLM context."""
        if job.get("page_watch"):
            return True, "page_watch"
        text = f"{job.get('title','')} {job.get('snippet','')} {job.get('org','')}".lower()
        for w in self.exclude:
            if w in text:
                return False, f"exclude:{w}"
        hits = [w for w in self.include if w in text]
        if not hits:
            return False, "no_include_match"
        job["flags"] = sorted({name for name, words in self.flags.items()
                               if any(w in text for w in words)})
        job["matched"] = hits[:5]
        return True, f"include:{','.join(hits[:3])}"
