#!/usr/bin/env python3
"""
sync-blog.py
============
Fetches posts from https://github.com/dryophoenix/dryoblog and writes
them into the Hugo site's content/blog/ directory, then rebuilds the site.

Repo layout expected:
  monYR/          e.g.  jan25/  feb25/  mar25/
    1.md
    2.md
    ...

Hugo output:
  content/blog/
    _index.md            (preserved — edit freely)
    jan25/
      _index.md          (auto-generated month title)
      1.md
      2.md
    feb25/
      ...

Usage
-----
  python3 sync-blog.py [--dry-run]

Environment variables
---------------------
  HUGO_DIR          Absolute path to Hugo site root   (default: parent of this script)
  BLOG_REPO         Git URL of the blog repo          (default: dryophoenix/dryoblog)
  CLONE_DIR         Where to clone the repo           (default: /tmp/dryoblog)
"""

import os
import re
import sys
import shutil
import logging
import subprocess
from pathlib import Path
from datetime import date

# ── Configuration ────────────────────────────────────────────────────────────

BLOG_REPO  = os.environ.get(
    "BLOG_REPO",
    "https://github.com/dryophoenix/dryoblog.git"
)
HUGO_DIR   = Path(os.environ.get("HUGO_DIR", Path(__file__).resolve().parent.parent))
CLONE_DIR  = Path(os.environ.get("CLONE_DIR", "/tmp/dryoblog"))
CONTENT    = HUGO_DIR / "content" / "blog"
DRY_RUN    = "--dry-run" in sys.argv

# ── Logging ──────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("sync-blog")

# ── Month helpers ─────────────────────────────────────────────────────────────

MONTH_MAP = {
    "jan": (1,  "January"),   "feb": (2,  "February"), "mar": (3,  "March"),
    "apr": (4,  "April"),     "may": (5,  "May"),       "jun": (6,  "June"),
    "jul": (7,  "July"),      "aug": (8,  "August"),    "sep": (9,  "September"),
    "oct": (10, "October"),   "nov": (11, "November"),  "dec": (12, "December"),
}

MON_YR_RE = re.compile(r"^([a-z]{3})(\d{2})$")


def parse_folder(name: str):
    """'jan25' → (2025, 1, 'January', date(2025,1,1))"""
    m = MON_YR_RE.match(name.lower())
    if not m:
        return None
    mon_key, yr_suffix = m.group(1), m.group(2)
    if mon_key not in MONTH_MAP:
        return None
    mon_num, mon_label = MONTH_MAP[mon_key]
    year = 2000 + int(yr_suffix)
    return year, mon_num, mon_label, date(year, mon_num, 1)


def extract_title(content: str, post_num: int) -> str:
    """Return the first # heading, or 'Entry N' as a fallback."""
    m = re.search(r"^#\s+(.+)$", content, re.MULTILINE)
    return m.group(1).strip() if m else f"Entry {post_num}"


def inject_more_divider(full_content: str) -> str:
    """
    Add Hugo's <!--more--> summary divider after the first paragraph of the
    post body (i.e. after the front matter ends), if one is not already present.

    When <!--more--> is present Hugo uses the content before it as .Summary
    *with HTML preserved* — without it, Hugo strips all formatting and returns
    a plain-text blob.
    """
    if "<!--more-->" in full_content:
        return full_content

    # Locate end of TOML (+++) or YAML (---) front matter
    if full_content.startswith("+++"):
        try:
            fm_end = full_content.index("+++", 3) + 3
        except ValueError:
            fm_end = 0
    elif full_content.startswith("---"):
        try:
            fm_end = full_content.index("---", 3) + 3
        except ValueError:
            fm_end = 0
    else:
        fm_end = 0

    header = full_content[:fm_end]
    body   = full_content[fm_end:]

    # Split at the first blank line separating paragraphs
    parts = re.split(r"\n\n+", body.strip(), maxsplit=1)
    if len(parts) >= 2:
        body = "\n\n" + parts[0] + "\n\n<!--more-->\n\n" + parts[1]
    # If there's only one paragraph there's nothing useful to truncate

    return header + body


def has_front_matter(content: str) -> bool:
    return content.lstrip().startswith(("+++", "---"))


def make_front_matter(title: str, dt: date, weight: int) -> str:
    safe_title = title.replace('"', '\\"')
    return (
        f'+++\n'
        f'title   = "{safe_title}"\n'
        f'date    = "{dt.isoformat()}"\n'
        f'weight  = {weight}\n'
        f'draft   = false\n'
        f'+++\n\n'
    )


# ── Git helpers ───────────────────────────────────────────────────────────────

def git(*args, cwd=None):
    cmd = ["git"] + list(args)
    log.debug("$ %s", " ".join(cmd))
    result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"git {args[0]} failed:\n{result.stderr.strip()}")
    return result.stdout.strip()


def clone_or_pull():
    if CLONE_DIR.exists():
        log.info("Pulling latest from %s", BLOG_REPO)
        git("pull", "--ff-only", cwd=CLONE_DIR)
    else:
        log.info("Cloning %s → %s", BLOG_REPO, CLONE_DIR)
        git("clone", BLOG_REPO, str(CLONE_DIR))


# ── Sync logic ────────────────────────────────────────────────────────────────

def sync():
    clone_or_pull()

    # Discover valid monYR folders in the repo
    repo_folders = {
        f.name: f
        for f in CLONE_DIR.iterdir()
        if f.is_dir() and parse_folder(f.name)
    }

    if not repo_folders:
        log.warning("No monYR folders found in repo — nothing to sync.")
        return

    log.info("Found %d month folder(s): %s",
             len(repo_folders),
             ", ".join(sorted(repo_folders)))

    # Preserve root _index.md
    root_index = CONTENT / "_index.md"
    root_index_text = root_index.read_text() if root_index.exists() else None

    # Remove only month-pattern subdirs (never touches root _index.md or other files)
    for child in CONTENT.iterdir():
        if child.is_dir() and parse_folder(child.name):
            if not DRY_RUN:
                shutil.rmtree(child)
            log.debug("Removed stale dir: %s", child.name)

    CONTENT.mkdir(parents=True, exist_ok=True)

    # Restore root index
    if root_index_text and not root_index.exists():
        if not DRY_RUN:
            root_index.write_text(root_index_text)

    # Process each month folder
    for folder_name, src_folder in sorted(
        repo_folders.items(),
        key=lambda kv: parse_folder(kv[0])[3]   # sort by date
    ):
        yr, mon_num, mon_label, first_day = parse_folder(folder_name)
        dest_dir = CONTENT / folder_name

        log.info("Syncing %s (%s %d)…", folder_name, mon_label, yr)
        if not DRY_RUN:
            dest_dir.mkdir(exist_ok=True)

        # Auto-generate _index.md for this month
        month_index = (
            f'+++\n'
            f'title  = "{mon_label} {yr}"\n'
            f'date   = "{first_day.isoformat()}"\n'
            f'draft  = false\n'
            f'+++\n'
        )
        if not DRY_RUN:
            (dest_dir / "_index.md").write_text(month_index)

        # Collect post files, sorted numerically
        posts = sorted(
            [p for p in src_folder.iterdir() if p.suffix == ".md"],
            key=lambda p: int(p.stem) if p.stem.isdigit() else 9999,
        )

        for post_file in posts:
            weight = int(post_file.stem) if post_file.stem.isdigit() else 9999
            raw = post_file.read_text(encoding="utf-8")

            if has_front_matter(raw):
                # Inject weight if not present (preserves everything else)
                if "weight" not in raw.split("+++")[1] and "weight" not in raw.split("---")[0]:
                    fm_end = raw.index("+++", 3) if raw.startswith("+++") else raw.index("---", 3)
                    raw = raw[:fm_end] + f"weight  = {weight}\n" + raw[fm_end:]
                final = raw
            else:
                title     = extract_title(raw, weight)
                post_date = date(yr, mon_num, min(weight, 28))
                final     = make_front_matter(title, post_date, weight) + raw

            # Ensure the first paragraph is set as the HTML-preserving summary
            final = inject_more_divider(final)

            dest_path = dest_dir / post_file.name
            log.debug("  Writing %s", dest_path.relative_to(HUGO_DIR))
            if not DRY_RUN:
                dest_path.write_text(final, encoding="utf-8")

        log.info("  → %d post(s) written", len(posts))

    # Rebuild Hugo
    if not DRY_RUN:
        log.info("Running hugo rebuild…")
        result = subprocess.run(["hugo", "--buildFuture"], cwd=str(HUGO_DIR), capture_output=True, text=True)
        if result.returncode != 0:
            log.error("Hugo build failed:\n%s", result.stderr)
            sys.exit(1)
        log.info("Hugo rebuild complete.")
    else:
        log.info("[DRY RUN] Would rebuild Hugo now.")


if __name__ == "__main__":
    log.info("=== dryoblog sync starting ===")
    sync()
    log.info("=== done ===")
