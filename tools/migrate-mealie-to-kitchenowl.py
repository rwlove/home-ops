#!/usr/bin/env python3
"""
Migrate recipes from Mealie to KitchenOwl.

Auth: Mealie via long-lived token; KitchenOwl via username+password (logs in to
obtain a short-lived JWT for the session).

Idempotent: skips KitchenOwl recipes whose name already matches a Mealie recipe.

Env:
  MEALIE_URL              e.g. https://mealie.thesteamedcrab.com
  MEALIE_TOKEN            Mealie long-lived token
  KITCHENOWL_URL          e.g. https://kitchenowl.thesteamedcrab.com
  KITCHENOWL_USERNAME     KitchenOwl username
  KITCHENOWL_PASSWORD     KitchenOwl password (read from stdin if unset)
  KITCHENOWL_HOUSEHOLD    KitchenOwl household id (default: 1)

Flags:
  --apply                 Actually create recipes (default is dry-run)
  --skip-images           Don't download/upload recipe photos
  --limit N               Only process the first N recipes
"""

from __future__ import annotations

import argparse
import getpass
import html
import os
import re
import sys
from typing import Any

import requests


def clean(s: str | None) -> str:
    return html.unescape(s).strip() if s else ""


def parse_iso8601_duration_minutes(s: str | None) -> int:
    """Parse 'PT1H30M' or '1 hour 20 minutes' into minutes. 0 if unparsable."""
    if not s:
        return 0
    m = re.match(r"^PT(?:(\d+)H)?(?:(\d+)M)?", s)
    if m and (m.group(1) or m.group(2)):
        return int(m.group(1) or 0) * 60 + int(m.group(2) or 0)
    total = 0
    for h in re.findall(r"(\d+)\s*hour", s, re.I):
        total += int(h) * 60
    for mn in re.findall(r"(\d+)\s*minute", s, re.I):
        total += int(mn)
    return total


def mealie_image_url(base: str, recipe_id: str) -> str:
    return f"{base}/api/media/recipes/{recipe_id}/images/original.webp"


class Mealie:
    def __init__(self, base: str, token: str):
        self.base = base.rstrip("/")
        self.s = requests.Session()
        self.s.headers["Authorization"] = f"Bearer {token}"

    def list_recipes(self) -> list[dict]:
        out: list[dict] = []
        page = 1
        while True:
            r = self.s.get(f"{self.base}/api/recipes",
                           params={"page": page, "perPage": 100}, timeout=30)
            r.raise_for_status()
            d = r.json()
            out.extend(d.get("items", []))
            if page * d.get("per_page", 100) >= d.get("total", 0):
                break
            page += 1
        return out

    def get_recipe(self, slug: str) -> dict:
        r = self.s.get(f"{self.base}/api/recipes/{slug}", timeout=30)
        r.raise_for_status()
        return r.json()

    def download_image(self, recipe_id: str) -> bytes | None:
        url = mealie_image_url(self.base, recipe_id)
        r = self.s.get(url, timeout=60)
        if r.status_code == 200 and r.content:
            return r.content
        return None


class KitchenOwl:
    def __init__(self, base: str, username: str, password: str, household: int):
        self.base = base.rstrip("/")
        self.household = household
        self.s = requests.Session()
        r = self.s.post(f"{self.base}/api/auth",
                        json={"username": username, "password": password,
                              "device": "mealie-migration"},
                        timeout=30)
        r.raise_for_status()
        token = r.json().get("access_token")
        if not token:
            raise SystemExit(f"KitchenOwl login returned no access_token: {r.text}")
        self.s.headers["Authorization"] = f"Bearer {token}"

    def list_recipes(self) -> list[dict]:
        r = self.s.get(f"{self.base}/api/household/{self.household}/recipe",
                       timeout=30)
        r.raise_for_status()
        return r.json()

    def upload_image(self, image_bytes: bytes, filename: str) -> str | None:
        files = {"file": (filename, image_bytes, "image/webp")}
        r = self.s.post(f"{self.base}/api/upload", files=files, timeout=60)
        r.raise_for_status()
        return r.json().get("filename")

    def create_recipe(self, payload: dict) -> dict:
        r = self.s.post(f"{self.base}/api/household/{self.household}/recipe",
                        json=payload, timeout=30)
        r.raise_for_status()
        return r.json()


def build_description(m: dict) -> str:
    parts: list[str] = []
    if m.get("description"):
        parts.append(clean(m["description"]))
    instructions = m.get("recipeInstructions") or []
    if instructions:
        parts.append("## Instructions")
        for i, step in enumerate(instructions, 1):
            text = clean(step.get("text"))
            title = clean(step.get("title"))
            if title:
                parts.append(f"\n**{title}**")
            parts.append(f"{i}. {text}")
    notes = m.get("notes") or []
    if notes:
        parts.append("\n## Notes")
        for n in notes:
            t = clean(n.get("title"))
            txt = clean(n.get("text"))
            parts.append(f"- **{t}**: {txt}" if t else f"- {txt}")
    return "\n\n".join(parts).strip()


def build_items(m: dict) -> list[dict]:
    items: list[dict] = []
    seen: set[str] = set()
    for ing in m.get("recipeIngredient") or []:
        food = ing.get("food") or {}
        name = clean(food.get("name") if isinstance(food, dict) else None)
        if not name:
            name = clean(ing.get("display") or ing.get("note"))
        if not name:
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        desc_parts: list[str] = []
        qty = ing.get("quantity")
        if qty:
            unit = ing.get("unit") or {}
            unit_name = clean(unit.get("name") if isinstance(unit, dict) else "")
            desc_parts.append(f"{qty} {unit_name}".strip())
        note = clean(ing.get("note"))
        if note and note != name:
            desc_parts.append(note)
        items.append({
            "name": name[:100],
            "description": " — ".join(desc_parts)[:200],
            "optional": False,
        })
    return items


def build_tags(m: dict) -> list[str]:
    tags = []
    for t in (m.get("tags") or []):
        n = clean(t.get("name") if isinstance(t, dict) else str(t))
        if n:
            tags.append(n)
    for c in (m.get("recipeCategory") or []):
        n = clean(c.get("name") if isinstance(c, dict) else str(c))
        if n and n not in tags:
            tags.append(n)
    return tags


def transform(m: dict) -> dict:
    yields = int(m.get("recipeYieldQuantity") or m.get("recipeServings") or 0)
    cook = parse_iso8601_duration_minutes(m.get("cookTime") or m.get("performTime"))
    prep = parse_iso8601_duration_minutes(m.get("prepTime"))
    total = parse_iso8601_duration_minutes(m.get("totalTime")) or (cook + prep)
    payload: dict[str, Any] = {
        "name": clean(m["name"]),
        "description": build_description(m),
        "yields": max(yields, 0),
        "cook_time": cook,
        "prep_time": prep,
        "time": total,
        "items": build_items(m),
        "tags": build_tags(m),
    }
    if m.get("orgURL"):
        payload["source"] = m["orgURL"]
    return payload


def env(name: str, required: bool = True) -> str:
    v = os.environ.get(name, "")
    if not v and required:
        raise SystemExit(f"missing env: {name}")
    return v


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true",
                    help="Actually create recipes (default: dry-run)")
    ap.add_argument("--skip-images", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    mealie = Mealie(env("MEALIE_URL"), env("MEALIE_TOKEN"))

    pw = os.environ.get("KITCHENOWL_PASSWORD") or getpass.getpass(
        "KitchenOwl password: ")
    ko = KitchenOwl(env("KITCHENOWL_URL"), env("KITCHENOWL_USERNAME"),
                    pw, int(os.environ.get("KITCHENOWL_HOUSEHOLD") or "1"))

    existing = {clean(r["name"]).lower() for r in ko.list_recipes()}
    print(f"KitchenOwl already has {len(existing)} recipes in household "
          f"{ko.household}")

    mealie_summaries = mealie.list_recipes()
    print(f"Mealie has {len(mealie_summaries)} recipes")

    to_process = mealie_summaries[:args.limit] if args.limit else mealie_summaries

    created = skipped = failed = 0
    for s in to_process:
        name = clean(s["name"])
        slug = s["slug"]
        if name.lower() in existing:
            print(f"  SKIP  {name!r} (already exists)")
            skipped += 1
            continue

        try:
            m = mealie.get_recipe(slug)
            payload = transform(m)
        except Exception as e:
            print(f"  FAIL  {name!r}: fetch/transform: {e}")
            failed += 1
            continue

        photo_filename: str | None = None
        if not args.skip_images and m.get("image"):
            try:
                img = mealie.download_image(m["id"])
                if img and args.apply:
                    photo_filename = ko.upload_image(img, f"{slug}.webp")
            except Exception as e:
                print(f"        image: {e}")

        if photo_filename:
            payload["photo"] = photo_filename

        if not args.apply:
            print(f"  DRY   {name!r}: {len(payload['items'])} items, "
                  f"{len(payload['description'])} desc chars, "
                  f"image={'yes' if (m.get('image') and not args.skip_images) else 'no'}")
            continue

        try:
            ko.create_recipe(payload)
            print(f"  OK    {name!r}")
            created += 1
        except requests.HTTPError as e:
            body = e.response.text[:300] if e.response is not None else ""
            print(f"  FAIL  {name!r}: {e}\n        body: {body}")
            failed += 1

    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"\n[{mode}] processed={len(to_process)} created={created} "
          f"skipped={skipped} failed={failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
