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
    assert gbfr_hash_hex("PL2900") == "74DD4C79"
    assert gbfr_hash_hex("WEP_PL2900_06") == "CDB13688"
    character_by_id = {item["id"]: item for item in characters["items"]}
    assert character_by_id["PL2900"]["name"] == "Fediel"
    assert character_by_id["PL2800"]["name"] is None
    assert preset["character"]["id"] == "PL2900"
    assert preset["sigils"][-2]["primary"]["id"] == "GEEN_178_90"
    assert preset["sigils"][-1]["primary"]["id"] == "GEEN_178_93"
    assert all("GEEN_177" not in item["primary"]["id"] for item in preset["sigils"])
    sigil_rows_by_gbid = {}
    for item in sigils["items"]:
        sigil_rows_by_gbid.setdefault(item["gbid"], []).append(item)
    for gbid in ("GEEN_178_90", "GEEN_178_93"):
        assert any(item["player_requirement"] == "PL2900" for item in sigil_rows_by_gbid[gbid])
    assert len(preset["sigils"]) == 12
    assert [item["slot"] for item in preset["sigils"]] == list(range(1, 13))
    assert all(item["level"] == 15 for item in preset["sigils"])
    assert all(item["primary"]["id"].startswith("GEEN_") for item in preset["sigils"])
    assert all(item["secondary"]["id"].startswith("SKILL_") for item in preset["sigils"])
    weapon_ids = {item["id"] for item in weapons["items"]}
    for character_id in ("PL2100", "PL2200", "PL2300"):
        assert f"WEP_{character_id}_05" not in weapon_ids
        assert f"WEP_{character_id}_07" in weapon_ids

    structure = (ROOT / "docs/SAVE_STRUCTURE.md").read_text(encoding="utf-8")
    for required in ("1403[0..11]", "2702", "2706", "120000000", "1701", "1702", "1402", "2802", "2803"):
        assert required in structure

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
