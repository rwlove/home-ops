#!/usr/bin/env python3
"""Deterministic Google-Sheet -> Medikeep importer.

Reads two Google Sheets (read-only service account) and writes to Medikeep via
its REST API, authenticating as the patient's own account (single-patient model
=> patient_id 1). Idempotent, additive, read-back-verified. Dry-run by default.

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
        return self.recorded_date.isoformat()


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
                keys.add(dtparse.parse(v["recorded_date"]).isoformat())
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


# --------------------------------------------------------------------------- #
# sync
# --------------------------------------------------------------------------- #
def sync_readings(mk: Medikeep, cfg: Config, readings: list[Reading]) -> None:
    existing = set() if cfg.dry_run else mk.existing_vitals_keys()
    new = [r for r in readings if r.key() not in existing]
    log.info("readings: %d parsed, %d present, %d to add", len(readings), len(readings) - len(new), len(new))
    for r in new:
        body = {
            "patient_id": cfg.patient_id,
            "recorded_date": r.recorded_date.isoformat(),
            "systolic_bp": r.systolic,
            "diastolic_bp": r.diastolic,
            "heart_rate": r.heart_rate,
            "notes": r.notes or None,
            "import_source": cfg.vitals_source,
        }
        if cfg.dry_run:
            log.info("[dry-run] POST vitals %s", {k: v for k, v in body.items() if v is not None})
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


def sync_dose(mk: Medikeep, cfg: Config, periods: list[DosePeriod]) -> None:
    existing = [] if cfg.dry_run else mk.existing_treatments()
    by_start = {t.get("start_date"): t for t in existing}
    log.info("dose: %d periods derived, %d existing rows", len(periods), len(existing))
    for p in periods:
        body = {
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
        match = by_start.get(p.start_date.isoformat())
        if match is None:
            if cfg.dry_run:
                log.info("[dry-run] POST treatment %s", {k: v for k, v in body.items() if v is not None})
                continue
            mk.post("/api/v1/treatments/", body)
            log.info("dose: added period start=%s status=%s", p.start_date, p.status)
        else:
            # period exists; only close it (set end_date/status) when it changes.
            if match.get("end_date") == body["end_date"] and match.get("status") == p.status:
                continue
            if cfg.dry_run:
                log.info("[dry-run] PUT treatment id=%s end_date=%s status=%s",
                         match.get("id"), body["end_date"], p.status)
                continue
            mk.put(f"/api/v1/treatments/{match['id']}", body)
            log.info("dose: closed period start=%s", p.start_date)


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

    mk = Medikeep(cfg)
    if not cfg.dry_run:
        mk.login()
    sync_readings(mk, cfg, readings)
    sync_dose(mk, cfg, periods)
    log.info("medikeep-ingest done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
