#!/usr/bin/env python3
"""Blank non-UUID MusicBrainz IDs left in the library by as-is imports.

`import.quiet_fallback: asis` preserves whatever MUSICBRAINZ_* tags a source
file shipped with. Bootleg / Discogs-tagged rips arrive with Discogs IDs
(e.g. ``16327311-1``) in those fields. Music Assistant's OpenSubsonic sync
validates every MBID as a UUID and aborts the ENTIRE track-sync pass on the
first bad one, silently freezing MA's library. gonic passes the bad value
straight through from the file tag.

This scrub runs after each import. For every item it (a) blanks any mb_* field
whose value is present but is NOT a valid UUID and (b) lowercases any valid
UUID that carries uppercase hex — MA rejects non-lowercase MBIDs and aborts the
sync exactly the same way (e.g. `…-A3fb…`). Both the beets DB and the file's
tags (what gonic actually reads) are corrected. Already-lowercase UUIDs are
never touched, so this is safe to run across the whole library.

Idempotent. Non-fatal by contract: it must never fail the import job.
"""
import os
import re
import sqlite3
import sys

LIBRARY = os.environ.get("BEETS_LIBRARY", "/config/library.db")
MUSIC_ROOT = (sys.argv[1] if len(sys.argv) > 1 else "/media").encode()

UUID = re.compile(
    rb"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I
)

# beets `items` columns that hold MusicBrainz identifiers.
DB_FIELDS = [
    "mb_workid", "mb_trackid", "mb_albumid", "mb_artistid", "mb_artistids",
    "mb_albumartistid", "mb_albumartistids", "mb_releasetrackid",
    "mb_releasegroupid",
]
# Subset that maps to real on-disk tags (mediafile attributes).
FILE_FIELDS = [
    "mb_trackid", "mb_releasetrackid", "mb_albumid", "mb_artistid",
    "mb_albumartistid", "mb_releasegroupid", "mb_workid",
]


def fix(value):
    """Return the corrected MBID for a field, or None if no change is needed.

    ''            -> blank it (value present but not a valid UUID)
    lowercased    -> valid UUID that contained uppercase hex (MA rejects it)
    None          -> empty, or already a valid lowercase UUID; leave as-is
    """
    if not value:
        return None
    s = value.decode() if isinstance(value, (bytes, bytearray)) else str(value)
    st = s.strip()
    if not st:
        return None
    if UUID.match(st.encode()) is None:
        return ""
    low = st.lower()
    return low if low != s else None


def main() -> int:
    try:
        import mediafile
    except Exception as exc:  # pragma: no cover - import guard
        print(f"mbid-scrub: mediafile unavailable, skipping: {exc}")
        return 0

    db = sqlite3.connect(LIBRARY, timeout=60)
    db.execute("PRAGMA busy_timeout=60000")
    cols = {r[1] for r in db.execute("PRAGMA table_info(items)")}
    db_fields = [f for f in DB_FIELDS if f in cols]

    rows = db.execute(
        "SELECT id, path, %s FROM items" % ",".join(db_fields)
    ).fetchall()

    fixed_db = fixed_files = miss = noperm = err = 0
    for row in rows:
        item_id, path = row[0], row[1]
        # field -> corrected value ('' blanks a non-UUID, or a lowercased UUID)
        db_changes = {
            f: nv for f, nv in
            ((f, fix(v)) for f, v in zip(db_fields, row[2:]))
            if nv is not None
        }
        if not db_changes:
            continue

        # 1) beets DB — blank bad / lowercase upper so values aren't re-stamped.
        try:
            db.execute(
                "UPDATE items SET %s WHERE id=?"
                % ",".join("%s=?" % f for f in db_changes),
                list(db_changes.values()) + [item_id],
            )
            db.commit()
            fixed_db += 1
        except Exception as exc:
            print(f"mbid-scrub: DB update failed id={item_id}: {exc}")
            err += 1
            continue

        # 2) File tags — what gonic / Music Assistant actually read.
        ap = path if os.path.isabs(path) and os.path.exists(path) \
            else os.path.join(MUSIC_ROOT, path)
        if not os.path.exists(ap):
            miss += 1
            continue
        if not os.access(ap, os.W_OK):
            noperm += 1
            print(f"mbid-scrub: no write perm: {ap!r}")
            continue
        try:
            mf = mediafile.MediaFile(ap)
            changed = False
            for f in FILE_FIELDS:
                nv = fix(getattr(mf, f, None))
                if nv is not None:
                    setattr(mf, f, nv or None)  # '' -> None blanks the tag
                    changed = True
            if changed:
                mf.save()
                fixed_files += 1
        except Exception as exc:
            print(f"mbid-scrub: tag write failed {ap!r}: {exc}")
            err += 1

    print(
        "mbid-scrub: cleaned db=%d files=%d (missing=%d noperm=%d err=%d) "
        "over %d items"
        % (fixed_db, fixed_files, miss, noperm, err, len(rows))
    )
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # never fail the import job
        print(f"mbid-scrub: unexpected error, ignoring: {exc}")
        sys.exit(0)
