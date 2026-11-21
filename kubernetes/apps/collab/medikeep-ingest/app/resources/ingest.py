#!/usr/bin/env python3
"""Deterministic Google-Sheet -> Medikeep importer.

Reads two Google Sheets (read-only service account) and writes to Medikeep via
its REST API, authenticating as the patient's own account (single-patient model
=> patient_id 1). Idempotent and read-back-verified. Dry-run by default.

Vitals are additive. Dose periods are RECONCILED against the sheet: a period
the importer created (notes tagged `source:<DOSE_SOURCE>`) is added, updated
(dose amount / end date / status), or deleted to match what the sheet now
derives. Rows the importer did not create are never touched. Deletes are
guarded: none when the sheet yields no dose rows, and none (job exits non-zero)
when more than MAX_DOSE_DELETES would go in one run.

The importer is generic: a "readings" sheet maps to Medikeep vitals, and a
"dose" sheet maps to Medikeep treatment periods. ALL subject-specific values
(the treatment name/type, the sheet IDs) are supplied at runtime via env from a
restricted secret and are never committed here (data classification: Restricted).

v1 of an iteration loop: run with DRY_RUN=true against the live sheets, inspect
the planned writes, refine the parse, then set DRY_RUN=false and un-suspend.
"""
from __future__ import annotations

import json
import logging
import os
import re
import sys
from dataclasses import dataclass
from datetime import date, datetime

import gspread
import requests
from dateutil import parser as dtparse
from google.oauth2.service_account import Credentials

log = logging.getLogger("medikeep-ingest")

SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]


# --------------------------------------------------------------------------- #
# config (all subject-specific values come from the environment / secret)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Config:
    sa_key: dict
    medikeep_url: str
    medikeep_user: str
    medikeep_pass: str
    readings_sheet_id: str
    dose_sheet_id: str
    treatment_name: str
    treatment_type: str
    vitals_source: str   # provenance tag written to vitals.import_source
    dose_source: str     # provenance tag written into treatment notes
    patient_id: int
    weight_source_url: str  # base URL of the read-only weight view; "" => off
    device_default: str     # default device_used for home readings; "" => unset
    max_dose_deletes: int   # safety cap on stale dose periods deleted per run
    dry_run: bool

    @staticmethod
    def from_env() -> "Config":
        def req(k: str) -> str:
            v = os.environ.get(k)
            if not v:
                sys.exit(f"missing required env: {k}")
            return v

        return Config(
            sa_key=json.loads(req("GOOGLE_SA_KEY")),
            medikeep_url=req("MEDIKEEP_URL").rstrip("/"),
            medikeep_user=req("MEDIKEEP_USER"),
            medikeep_pass=req("MEDIKEEP_PASS"),
            readings_sheet_id=req("READINGS_SHEET_ID"),
            dose_sheet_id=req("DOSE_SHEET_ID"),
            treatment_name=req("TREATMENT_NAME"),
            treatment_type=os.environ.get("TREATMENT_TYPE", ""),
            vitals_source=os.environ.get("VITALS_SOURCE", "sheet-import"),
            dose_source=os.environ.get("DOSE_SOURCE", "sheet-import"),
            patient_id=int(os.environ.get("PATIENT_ID", "1")),
            weight_source_url=os.environ.get("WEIGHT_SOURCE_URL", "").rstrip("/"),
            device_default=os.environ.get("DEVICE_DEFAULT", "").strip(),
            max_dose_deletes=int(os.environ.get("MAX_DOSE_DELETES", "2")),
            # Default TRUE: a misconfigured job must not write medical rows.
            dry_run=os.environ.get("DRY_RUN", "true").lower() != "false",
        )


# --------------------------------------------------------------------------- #
# parsed record types
# --------------------------------------------------------------------------- #
@dataclass
class Reading:
    recorded_date: datetime
    systolic: int | None
    diastolic: int | None
    heart_rate: int | None
    notes: str

    def key(self) -> str:
        # tz-naive — Medikeep returns recorded_date with a 'Z'/offset, the sheet
        # is naive; normalize both sides so idempotency matches (home-ops ingest).
        return self.recorded_date.replace(tzinfo=None).isoformat()


@dataclass
class DosePeriod:
    start_date: date
    end_date: date | None  # None => open/ongoing period
    amount: int
    status: str  # "active" (open) | "completed" (closed)


# --------------------------------------------------------------------------- #
# sheet reads
# --------------------------------------------------------------------------- #
def open_client(sa_key: dict) -> gspread.Client:
    creds = Credentials.from_service_account_info(sa_key, scopes=SCOPES)
    return gspread.authorize(creds)


def _to_int(v: str) -> int | None:
    v = (v or "").strip()
    if v == "":
        return None
    try:
        return round(float(v))  # reading columns are Integer; sheet has decimals
    except ValueError:
        return None


def _parse_dt(raw: str, default_year: int) -> datetime | None:
    raw = (raw or "").strip()
    if not raw:
        return None
    # "... (05/23/25)" -> pull the parenthesised date
    m = re.search(r"\(([^)]+)\)", raw)
    if m:
        raw = m.group(1)
    try:
        return dtparse.parse(raw, default=datetime(default_year, 1, 1), fuzzy=True)
    except (ValueError, OverflowError):
        return None


def parse_readings(ws_values: list[list[str]], year: int) -> list[Reading]:
    """A readings year-tab. Column layout drifts across years; take the FIRST
    value triplet after the date (the aggregate column when a per-side split is
    present, the single reading otherwise) and keep the Notes column verbatim."""
    out: list[Reading] = []
    for row in ws_values:
        if not row or not row[0].strip():
            continue
        first = row[0].strip().lower()
        if first.startswith("date") or first in ("average", "graph"):
            continue
        dt = _parse_dt(row[0], year)
        if dt is None:
            log.warning("readings: unparseable date %r (year %s) — skipped", row[0], year)
            continue
        c1 = _to_int(row[1]) if len(row) > 1 else None
        c2 = _to_int(row[2]) if len(row) > 2 else None
        c3 = _to_int(row[3]) if len(row) > 3 else None
        if c1 is None and c2 is None:
            continue  # not a reading row
        notes = (row[-1] or "").strip() if len(row) > 3 else ""
        if notes and re.fullmatch(r"[\d.]+", notes):
            notes = ""  # last cell is a value, not a note
        out.append(Reading(dt, c1, c2, c3, notes))
    return out


def parse_dose_rows(ws_values: list[list[str]]) -> list[tuple[date, int]]:
    """Parse one dose year-tab -> [(date, mg)]. A row is valid iff Dose(ml)
    (column 1) has a value; dose mg = round(ml * 200) (the vial concentration
    from the sheet header). Rows without an ml value are future placeholders and
    are skipped. The Day column carries the full M/D/YYYY date."""
    out: list[tuple[date, int]] = []
    for row in ws_values:
        if not row or not row[0].strip():
            continue
        if row[0].strip().lower() == "day":
            continue
        try:
            d = dtparse.parse(row[0].strip(), fuzzy=False).date()
        except (ValueError, OverflowError):
            continue
        ml_raw = row[1].strip() if len(row) > 1 else ""
        if ml_raw == "":
            continue  # no Dose(ml) => future placeholder
        try:
            ml = float(ml_raw)
        except ValueError:
            continue
        out.append((d, round(ml * 200)))
    return out


def derive_dose_periods(rows: list[tuple[date, int]]) -> list[DosePeriod]:
    """[(date, mg)] from all year tabs (any order, possibly duplicated across
    tabs) -> dose-change periods. Dedup by date, sort, then collapse consecutive
    same-amount weeks; the last period stays open (end_date=None)."""
    by_date: dict[date, int] = {}
    for d, amt in rows:
        by_date[d] = amt  # dedup by date — identical cross-tab rows collapse
    periods: list[DosePeriod] = []
    for d, amt in sorted(by_date.items()):
        if periods and periods[-1].amount == amt and periods[-1].end_date is None:
            continue  # same open amount, extend implicitly
        if periods and periods[-1].end_date is None:
            periods[-1].end_date = d  # close previous at the change date
            periods[-1].status = "completed"
        periods.append(DosePeriod(start_date=d, end_date=None, amount=amt, status="active"))
    return periods


# --------------------------------------------------------------------------- #
# per-reading enrichment (bodyweight + device)
# --------------------------------------------------------------------------- #
# Notes that indicate a reading was taken in a clinic/office rather than on the
# home monitor; such readings get device_used left null (it wasn't the home
# device). Deliberately NOT matching a bare "Dr" — "late to Dr apt" is about an
# appointment, not where the reading was taken. Every device decision is a
# home/clinic classification only; the device *name* comes from cfg.device_default.
CLINIC_RE = re.compile(r"\b(office|clinic|hospital|internal medicine|walk[- ]?in|at dr)\b", re.I)


def fetch_weights(url: str) -> list[tuple[date, float]]:
    """Bodyweight series (date asc) from the read-only PUMP weight view
    (api.v_weight: columns date, weight_lb) proxied by the health service at
    /api/pump/. Non-fatal by design: a fetch failure logs a warning and yields
    an empty series so the core BP import still runs — weight is enrichment, not
    a vital, and must never block the import or be fabricated."""
    if not url:
        return []
    try:
        r = requests.get(f"{url}/api/pump/v_weight?order=date.asc", timeout=30)
        r.raise_for_status()
        out: list[tuple[date, float]] = []
        for w in r.json():
            out.append((dtparse.parse(w["date"]).date(), float(w["weight_lb"])))
        out.sort(key=lambda t: t[0])
        log.info("weights: %d bodyweight points loaded", len(out))
        return out
    except (requests.RequestException, ValueError, KeyError, TypeError) as e:
        log.warning("weights: fetch failed (%s) — proceeding without weight enrichment", e)
        return []


def weight_as_of(weights: list[tuple[date, float]], d: date) -> float | None:
    """Latest bodyweight recorded on or before date d; None if none exists
    (e.g. the reading predates the weight series) — never fabricated."""
    best: float | None = None
    for wd, wlb in weights:  # ascending
        if wd <= d:
            best = wlb
        else:
            break
    return best


# --------------------------------------------------------------------------- #
# Medikeep API
# --------------------------------------------------------------------------- #
class Medikeep:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.s = requests.Session()
        self.s.headers["Accept"] = "application/json"

    def login(self) -> None:
        r = self.s.post(
            f"{self.cfg.medikeep_url}/api/v1/auth/login",
            data={"username": self.cfg.medikeep_user, "password": self.cfg.medikeep_pass},
            timeout=30,
        )
        r.raise_for_status()
        self.s.headers["Authorization"] = f"Bearer {r.json()['access_token']}"
        log.info("medikeep: authenticated")

    def _get_list(self, path: str) -> list[dict]:
        r = self.s.get(f"{self.cfg.medikeep_url}{path}", timeout=30)
        r.raise_for_status()
        data = r.json()
        return data if isinstance(data, list) else data.get("items", data.get("data", []))

    def existing_vitals_keys(self) -> set[str]:
        keys: set[str] = set()
        for v in self._get_list(f"/api/v1/vitals/?patient_id={self.cfg.patient_id}"):
            if v.get("import_source") != self.cfg.vitals_source:
                continue
            if v.get("recorded_date"):
                # strip tz so the key matches the naive sheet-derived key above
                keys.add(dtparse.parse(v["recorded_date"]).replace(tzinfo=None).isoformat())
        return keys

    def existing_treatments(self) -> list[dict]:
        return [
            t for t in self._get_list(f"/api/v1/treatments/?patient_id={self.cfg.patient_id}")
            if t.get("treatment_name") == self.cfg.treatment_name
        ]

    def post(self, path: str, body: dict) -> dict:
        r = self.s.post(f"{self.cfg.medikeep_url}{path}", json=body, timeout=30)
        r.raise_for_status()
        return r.json()

    def put(self, path: str, body: dict) -> dict:
        r = self.s.put(f"{self.cfg.medikeep_url}{path}", json=body, timeout=30)
        r.raise_for_status()
        return r.json()

    def delete(self, path: str) -> None:
        r = self.s.delete(f"{self.cfg.medikeep_url}{path}", timeout=30)
        r.raise_for_status()


# --------------------------------------------------------------------------- #
# sync
# --------------------------------------------------------------------------- #
def sync_readings(mk: Medikeep, cfg: Config, readings: list[Reading],
                  weights: list[tuple[date, float]]) -> None:
    existing = mk.existing_vitals_keys()
    new = [r for r in readings if r.key() not in existing]
    log.info("readings: %d parsed, %d present, %d to add", len(readings), len(readings) - len(new), len(new))
    for r in new:
        # Enrichment: bodyweight as of the reading's date (null if none), and the
        # home device unless the note marks a clinic/office reading.
        is_clinic = bool(r.notes and CLINIC_RE.search(r.notes))
        weight = weight_as_of(weights, r.recorded_date.date())
        device = None if is_clinic else (cfg.device_default or None)
        body = {
            "patient_id": cfg.patient_id,
            "recorded_date": r.recorded_date.isoformat(),
            "systolic_bp": r.systolic,
            "diastolic_bp": r.diastolic,
            "heart_rate": r.heart_rate,
            "weight": weight,
            "device_used": device,
            "notes": r.notes or None,
            "import_source": cfg.vitals_source,
        }
        if cfg.dry_run:
            log.info("[dry-run] POST vitals %s (device=%s)",
                     {k: v for k, v in body.items() if v is not None and k != "device_used"},
                     "clinic/null" if device is None else "home")
            continue
        created = mk.post("/api/v1/vitals/", body)
        ok = (created.get("systolic_bp") == r.systolic and created.get("diastolic_bp") == r.diastolic
              and created.get("heart_rate") == r.heart_rate)
        if not ok:
            log.error("readings read-back MISMATCH for %s: sent %s/%s/%s got %s/%s/%s", r.key(),
                      r.systolic, r.diastolic, r.heart_rate,
                      created.get("systolic_bp"), created.get("diastolic_bp"), created.get("heart_rate"))
        else:
            log.info("readings: added %s", r.key())
        # Enrichment read-back is non-fatal (warn only) — weight/device are
        # metadata, not vitals, and a mismatch must not fail the reading.
        gw = created.get("weight")
        if weight is not None and (gw is None or abs(float(gw) - weight) > 0.05):
            log.warning("readings: weight read-back mismatch for %s: sent %s got %s", r.key(), weight, gw)
        if device is not None and created.get("device_used") != device:
            log.warning("readings: device read-back mismatch for %s", r.key())


def _norm_date(s: str | None) -> str | None:
    # normalize the API's date strings (may carry time/tz) to a plain date key
    try:
        return dtparse.parse(s).date().isoformat() if s else None
    except (ValueError, TypeError):
        return s


def dose_body(cfg: Config, p: DosePeriod) -> dict:
    return {
        "patient_id": cfg.patient_id,
        "treatment_name": cfg.treatment_name,
        "mode": "simple",
        "treatment_type": cfg.treatment_type or None,
        "dosage": f"{p.amount} mg/week",
        "frequency": "weekly",
        "start_date": p.start_date.isoformat(),
        "end_date": p.end_date.isoformat() if p.end_date else None,
        "status": p.status,
        "notes": f"source:{cfg.dose_source}",
    }


def owned_by_importer(t: dict, cfg: Config) -> bool:
    """A treatment row the importer created (and so may change or delete)."""
    return str(t.get("notes") or "").startswith(f"source:{cfg.dose_source}")


@dataclass
class DosePlan:
    add: list[dict]                    # bodies to POST
    update: list[tuple[int, dict]]     # (treatment id, body) to PUT
    delete: list[dict]                 # owned rows no longer in the sheet
    skipped: list[str]                 # human-readable notes (unowned collisions)


def plan_dose_sync(cfg: Config, periods: list[DosePeriod], existing: list[dict]) -> DosePlan:
    """Pure reconcile: sheet-derived periods vs existing treatment rows.

    Only importer-owned rows are ever updated or deleted. A derived period whose
    start date collides with a row the importer did NOT create is skipped (never
    duplicated, never overwritten) and reported."""
    owned: dict[str | None, dict] = {}
    duplicates: list[dict] = []  # extra importer rows sharing a start date
    for t in sorted((t for t in existing if owned_by_importer(t, cfg)), key=lambda t: t.get("id") or 0):
        key = _norm_date(t.get("start_date"))
        if key in owned:
            duplicates.append(t)
        else:
            owned[key] = t
    unowned = {_norm_date(t.get("start_date")) for t in existing if not owned_by_importer(t, cfg)}
    plan = DosePlan(add=[], update=[], delete=[], skipped=[])
    derived_starts = set()
    for p in periods:
        start = p.start_date.isoformat()
        derived_starts.add(start)
        body = dose_body(cfg, p)
        match = owned.get(start)
        if match is None:
            if start in unowned:
                plan.skipped.append(f"start={start}: a non-importer row already has this start date")
                continue
            plan.add.append(body)
            continue
        same = (
            str(match.get("dosage") or "") == body["dosage"]
            and _norm_date(match.get("end_date")) == body["end_date"]
            and str(match.get("status") or "").lower() == p.status
        )
        if not same:
            plan.update.append((match["id"], body))
    plan.delete = [t for s, t in sorted(owned.items(), key=lambda kv: kv[0] or "") if s not in derived_starts]
    plan.delete.extend(duplicates)
    return plan


def sync_dose(mk: Medikeep, cfg: Config, periods: list[DosePeriod]) -> bool:
    """Apply the reconcile plan, then read back and verify. Returns False when the
    run needs attention (a delete guard tripped or the read-back disagrees)."""
    existing = mk.existing_treatments()
    plan = plan_dose_sync(cfg, periods, existing)
    log.info("dose: %d periods derived, %d existing rows (%d importer-owned); plan add=%d update=%d delete=%d",
             len(periods), len(existing), sum(owned_by_importer(t, cfg) for t in existing),
             len(plan.add), len(plan.update), len(plan.delete))
    for note in plan.skipped:
        log.warning("dose: skipped %s", note)

    ok = True
    deletes = plan.delete
    if deletes and not periods:
        log.error("dose: sheet yielded no dose rows; refusing to delete %d periods", len(deletes))
        deletes, ok = [], False
    elif len(deletes) > cfg.max_dose_deletes:
        log.error("dose: %d stale periods exceeds MAX_DOSE_DELETES=%d; deleting none (starts: %s)",
                  len(deletes), cfg.max_dose_deletes,
                  ", ".join(str(_norm_date(t.get("start_date"))) for t in deletes))
        deletes, ok = [], False

    if cfg.dry_run:
        for body in plan.add:
            log.info("[dry-run] POST treatment %s", {k: v for k, v in body.items() if v is not None})
        for tid, body in plan.update:
            log.info("[dry-run] PUT treatment id=%s dosage=%s end_date=%s status=%s",
                     tid, body["dosage"], body["end_date"], body["status"])
        for t in deletes:
            log.info("[dry-run] DELETE treatment id=%s start=%s", t.get("id"), _norm_date(t.get("start_date")))
        return ok

    for body in plan.add:
        mk.post("/api/v1/treatments/", body)
        log.info("dose: added period start=%s status=%s", body["start_date"], body["status"])
    for tid, body in plan.update:
        mk.put(f"/api/v1/treatments/{tid}", body)
        log.info("dose: updated period id=%s start=%s", tid, body["start_date"])
    for t in deletes:
        mk.delete(f"/api/v1/treatments/{t['id']}")
        log.info("dose: deleted stale period id=%s start=%s", t.get("id"), _norm_date(t.get("start_date")))

    # Read back: after a clean apply the plan must be empty (except any delete a
    # guard deliberately held back, and any skipped unowned collision).
    after = plan_dose_sync(cfg, periods, mk.existing_treatments())
    if after.add or after.update or len(after.delete) != len(plan.delete) - len(deletes):
        log.error("dose: read-back MISMATCH after apply: add=%d update=%d delete=%d",
                  len(after.add), len(after.update), len(after.delete))
        ok = False
    else:
        log.info("dose: read-back verified")
    return ok


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    cfg = Config.from_env()
    log.info("medikeep-ingest starting (dry_run=%s, patient_id=%s)", cfg.dry_run, cfg.patient_id)

    gc = open_client(cfg.sa_key)

    readings_doc = gc.open_by_key(cfg.readings_sheet_id)
    readings: list[Reading] = []
    for ws in readings_doc.worksheets():
        if not re.fullmatch(r"\d{4}", ws.title.strip()):
            continue  # only year tabs
        readings.extend(parse_readings(ws.get_all_values(), int(ws.title.strip())))
    # Dedup within the batch — a reading (e.g. an in-office visit) can be listed
    # in more than one year tab; keep the first occurrence by timestamp.
    seen: set[str] = set()
    deduped: list[Reading] = []
    for r in readings:
        if r.key() in seen:
            continue
        seen.add(r.key())
        deduped.append(r)
    readings = deduped

    dose_doc = gc.open_by_key(cfg.dose_sheet_id)
    dose_rows: list[tuple[date, int]] = []
    for ws in dose_doc.worksheets():
        if not re.fullmatch(r"\d{4}", ws.title.strip()):
            continue  # only year tabs
        dose_rows.extend(parse_dose_rows(ws.get_all_values()))
    periods = derive_dose_periods(dose_rows)

    weights = fetch_weights(cfg.weight_source_url)

    mk = Medikeep(cfg)
    # Log in even on a dry run: reads are safe, and a dry run that cannot see the
    # existing rows cannot show the real plan.
    mk.login()
    sync_readings(mk, cfg, readings, weights)
    dose_ok = sync_dose(mk, cfg, periods)
    log.info("medikeep-ingest done%s", "" if dose_ok else " (dose sync needs attention)")
    return 0 if dose_ok else 1


if __name__ == "__main__":
    sys.exit(main())
