"""Validate generated catalogs, presets, hash vectors, and publish hygiene."""

from __future__ import annotations

import json
import re
from pathlib import Path

from gbfr_hash import gbfr_hash_hex

ROOT = Path(__file__).resolve().parents[1]


def load(relative: str):
    return json.loads((ROOT / relative).read_text(encoding="utf-8"))


def main() -> int:
    characters = load("catalogs/characters.json")
    weapons = load("catalogs/weapons.json")
    sigils = load("catalogs/sigils-2.0.json")
    preset = load("presets/characters/fediel-celestial-dual-trait-2.0.json")
    assert characters["count"] == 29 == len(characters["items"])
    assert weapons["count"] == 174 == len(weapons["items"])
    assert all(item["database_match"] for item in weapons["items"])
    assert sigils["count"] == 84 == len(sigils["items"])
    assert {item["family"] for item in sigils["items"]} == set(range(173, 179)) | set(range(320, 328))
    assert gbfr_hash_hex("PL2800") == "646C3168"
    assert gbfr_hash_hex("WEP_PL2800_06") == "D5EB1DEE"
    assert preset["character"]["id"] == "PL2800"
    assert len(preset["sigils"]) == 12
    assert [item["slot"] for item in preset["sigils"]] == list(range(1, 13))
    assert all(item["level"] == 15 for item in preset["sigils"])
    assert all(item["primary"]["id"].startswith("GEEN_") for item in preset["sigils"])
    assert all(item["secondary"]["id"].startswith("SKILL_") for item in preset["sigils"])

    forbidden_extensions = {".dat", ".sav", ".db", ".sqlite", ".exe", ".dll", ".zip"}
    steam_id_pattern = re.compile(r"\b7656119\d{10}\b")
    absolute_path_pattern = re.compile(r"[A-Za-z]:\\" + "|/" + "Users/|/" + "home/")
    for path in ROOT.rglob("*"):
        if ".git" in path.parts or not path.is_file():
            continue
        assert path.suffix.lower() not in forbidden_extensions, f"forbidden file: {path}"
        if path.suffix.lower() in {".md", ".json", ".yaml", ".yml", ".py", ".txt"}:
            text = path.read_text(encoding="utf-8")
            assert not steam_id_pattern.search(text), f"SteamID-like value in {path}"
            assert not absolute_path_pattern.search(text), f"absolute path in {path}"
    print("ok: 29 characters, 174 weapons, 84 Relink 2.0 sigil rows, 12-slot Fediel preset")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
