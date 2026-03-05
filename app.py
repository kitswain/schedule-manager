from __future__ import annotations

from pathlib import Path
import re
import random
import sqlite3
import math

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
RULES_FILE = DATA_DIR / "validation_rules.csv"
CONSIDERATIONS_FILE = DATA_DIR / "schedule_considerations.csv"
SAVED_SCHEDULES_FILE = DATA_DIR / "saved_schedules.csv"
DB_FILE = DATA_DIR / "schedule_manager.db"
PROGRAMS_FILE = DATA_DIR / "programs.csv"
PROGRAM_DIVISIONS_FILE = DATA_DIR / "program_divisions.csv"


def get_db_connection() -> sqlite3.Connection:
    return sqlite3.connect(DB_FILE)


def init_db() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = get_db_connection()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS saved_schedules (
                schedule_id INTEGER PRIMARY KEY AUTOINCREMENT,
                division TEXT NOT NULL,
                weekends INTEGER NOT NULL,
                created_on TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS saved_schedule_games (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                schedule_id INTEGER NOT NULL,
                division_code TEXT,
                week_no INTEGER,
                time_slot TEXT,
                home_team_name TEXT,
                away_team_name TEXT,
                FOREIGN KEY (schedule_id) REFERENCES saved_schedules(schedule_id)
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def save_schedule_to_db(division: str, weekends: int, games_df: pd.DataFrame) -> int:
    conn = get_db_connection()
    try:
        created_on = pd.Timestamp.now().isoformat(timespec="seconds")
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO saved_schedules (division, weekends, created_on) VALUES (?, ?, ?)",
            (division, int(weekends), created_on),
        )
        schedule_id = int(cur.lastrowid)

        if not games_df.empty:
            rows = []
            for _, r in games_df.iterrows():
                rows.append(
                    (
                        schedule_id,
                        str(r.get("division_code", "")),
                        int(r.get("week_no", 0)) if str(r.get("week_no", "")).strip() else None,
                        str(r.get("time_slot", "")),
                        str(r.get("home_team_name", "")),
                        str(r.get("away_team_name", "")),
                    )
                )
            cur.executemany(
                """
                INSERT INTO saved_schedule_games
                (schedule_id, division_code, week_no, time_slot, home_team_name, away_team_name)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
        conn.commit()
        return schedule_id
    finally:
        conn.close()


def load_saved_schedules_from_db() -> pd.DataFrame:
    conn = get_db_connection()
    try:
        q = """
            SELECT schedule_id, division, weekends, created_on
            FROM saved_schedules
            ORDER BY schedule_id DESC
        """
        return pd.read_sql_query(q, conn)
    finally:
        conn.close()


def load_saved_schedule_games_from_db(schedule_id: int) -> pd.DataFrame:
    conn = get_db_connection()
    try:
        q = """
            SELECT
                division_code,
                week_no,
                time_slot,
                home_team_name,
                away_team_name
            FROM saved_schedule_games
            WHERE schedule_id = ?
            ORDER BY week_no, time_slot
        """
        return pd.read_sql_query(q, conn, params=[int(schedule_id)])
    finally:
        conn.close()


def load_csv(name: str) -> pd.DataFrame:
    path = DATA_DIR / name
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path)


def apply_tooltip_position_css() -> None:
    # Keep native Streamlit help tooltip behavior, but bias placement to the right of the icon.
    st.markdown(
        """
        <style>
        div[role="tooltip"][data-popper-placement] {
            transform: translateX(14px) !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def ensure_program_models(divisions: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    if PROGRAMS_FILE.exists():
        programs = pd.read_csv(PROGRAMS_FILE)
    else:
        programs = pd.DataFrame(
            [
                {
                    "program_id": "CYB-2025-26",
                    "program_name": "2025-26 CYB Season",
                    "is_active": 1,
                }
            ]
        )
        programs.to_csv(PROGRAMS_FILE, index=False)

    for col, default in [("program_id", ""), ("program_name", ""), ("is_active", 1)]:
        if col not in programs.columns:
            programs[col] = default
    programs["is_active"] = pd.to_numeric(programs["is_active"], errors="coerce").fillna(0).astype(int)

    if PROGRAM_DIVISIONS_FILE.exists():
        program_divisions = pd.read_csv(PROGRAM_DIVISIONS_FILE)
    else:
        default_program = str(programs.iloc[0]["program_id"]) if not programs.empty else "CYB-2025-26"
        div_codes = (
            sorted(divisions["division_code"].dropna().astype(str).tolist())
            if not divisions.empty and "division_code" in divisions.columns
            else []
        )
        program_divisions = pd.DataFrame(
            [{"program_id": default_program, "division_code": d} for d in div_codes]
        )
        program_divisions.to_csv(PROGRAM_DIVISIONS_FILE, index=False)

    for col, default in [("program_id", ""), ("division_code", "")]:
        if col not in program_divisions.columns:
            program_divisions[col] = default

    return programs, program_divisions


def divisions_for_program(divisions: pd.DataFrame, program_divisions: pd.DataFrame, program_id: str) -> pd.DataFrame:
    if divisions.empty:
        return divisions
    if program_divisions.empty or not str(program_id).strip():
        return divisions
    scoped = set(
        program_divisions[
            program_divisions["program_id"].astype(str).str.strip() == str(program_id).strip()
        ]["division_code"].astype(str).tolist()
    )
    if not scoped:
        return divisions
    return divisions[divisions["division_code"].astype(str).isin(scoped)].copy().reset_index(drop=True)


def normalize_time(value: str) -> str:
    if pd.isna(value):
        return ""
    v = str(value).strip()
    if not v:
        return ""
    for fmt in ["%H:%M", "%I:%M %p", "%I:%M%p"]:
        try:
            return pd.to_datetime(v, format=fmt).strftime("%H:%M")
        except ValueError:
            continue
    try:
        return pd.to_datetime(v).strftime("%H:%M")
    except Exception:
        return v


def prep_game_days(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    expected = ["game_date", "day_of_week", "enabled", "notes"]
    for col in expected:
        if col not in out.columns:
            out[col] = ""
    out["game_date"] = pd.to_datetime(out["game_date"], errors="coerce").dt.date.astype("string")
    out["enabled"] = pd.to_numeric(out["enabled"], errors="coerce").fillna(0).astype(int)
    out["day_of_week"] = out["day_of_week"].fillna("").astype(str).str.slice(0, 3).str.title()
    return out[expected]


def prep_venue_slots(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["day_of_week"] = out["day_of_week"].fillna("").astype(str).str.slice(0, 3).str.title()
    out["start_time"] = out["start_time"].map(normalize_time)
    out["is_available"] = pd.to_numeric(out.get("is_available", 1), errors="coerce").fillna(0).astype(int)
    return out


def build_dated_slots(game_days: pd.DataFrame, venue_slots: pd.DataFrame) -> pd.DataFrame:
    enabled = game_days[game_days["enabled"] == 1].copy()
    if enabled.empty:
        return pd.DataFrame(columns=["game_date", "day_of_week", "venue_id", "start_time", "slot_key"])

    enabled["game_date"] = pd.to_datetime(enabled["game_date"], errors="coerce")
    enabled = enabled.dropna(subset=["game_date"])
    enabled["day_of_week"] = enabled["game_date"].dt.strftime("%a")
    enabled["game_date"] = enabled["game_date"].dt.date.astype("string")

    slots = venue_slots[venue_slots["is_available"] == 1].copy()
    merged = enabled.merge(slots, on="day_of_week", how="inner")
    merged = merged[["game_date", "day_of_week", "venue_id", "start_time"]].copy()
    merged["slot_key"] = merged["game_date"] + "|" + merged["start_time"] + "|" + merged["venue_id"]
    return merged.sort_values(["game_date", "start_time", "venue_id"]).reset_index(drop=True)


def empty_schedule_template() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "game_date": [""],
            "start_time": [""],
            "venue_id": [""],
            "division_code": [""],
            "home_team_id": [""],
            "away_team_id": [""],
            "notes": [""],
        }
    )


def default_rules() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"rule_key": "required_field", "enabled": 1, "severity": "error", "description": "Required schedule columns must be populated."},
            {"rule_key": "invalid_matchup", "enabled": 1, "severity": "error", "description": "Home and away team cannot be the same."},
            {"rule_key": "unknown_team", "enabled": 1, "severity": "error", "description": "Team IDs must exist in teams data."},
            {"rule_key": "cross_division_match", "enabled": 1, "severity": "error", "description": "Home and away teams must belong to same division."},
            {"rule_key": "division_mismatch", "enabled": 1, "severity": "warning", "description": "division_code should match selected teams."},
            {"rule_key": "slot_not_in_availability", "enabled": 1, "severity": "warning", "description": "Scheduled slot should exist in enabled game dates and venue slots."},
            {"rule_key": "slot_collision", "enabled": 1, "severity": "error", "description": "Only one game per venue/date/time."},
            {"rule_key": "team_overlap", "enabled": 1, "severity": "error", "description": "A team cannot play two games at same date/time."},
            {"rule_key": "coach_overlap", "enabled": 1, "severity": "error", "description": "A coach cannot have overlapping games."},
            {"rule_key": "non_primary_venue", "enabled": 1, "severity": "warning", "description": "Warn when division is off its primary venue."},
            {"rule_key": "avoid_slot_used", "enabled": 1, "severity": "warning", "description": "Warn when division uses an avoid-marked slot."},
        ]
    )


def load_or_default_rules() -> pd.DataFrame:
    if RULES_FILE.exists():
        rules = pd.read_csv(RULES_FILE)
    else:
        rules = default_rules()
    for col, default in [("rule_key", ""), ("enabled", 1), ("severity", "warning"), ("description", "")]:
        if col not in rules.columns:
            rules[col] = default
    rules["enabled"] = pd.to_numeric(rules["enabled"], errors="coerce").fillna(0).astype(int)
    rules["severity"] = rules["severity"].fillna("warning").astype(str).str.lower()
    return rules[["rule_key", "enabled", "severity", "description"]]


def save_rules(rules: pd.DataFrame) -> None:
    rules.to_csv(RULES_FILE, index=False)


def is_enabled(rules_map: dict[str, dict], rule_key: str) -> bool:
    return bool(rules_map.get(rule_key, {}).get("enabled", 0))


def rule_severity(rules_map: dict[str, dict], rule_key: str, fallback: str) -> str:
    sev = str(rules_map.get(rule_key, {}).get("severity", fallback)).lower()
    return sev if sev in {"error", "warning"} else fallback


def round_robin_pairs(team_ids: list[str]) -> list[list[tuple[str, str]]]:
    teams = list(team_ids)
    if len(teams) < 2:
        return []
    if len(teams) % 2 == 1:
        teams.append("BYE")

    rounds = []
    n = len(teams)
    rotation = teams[:]
    for _ in range(n - 1):
        pairs = []
        for i in range(n // 2):
            a = rotation[i]
            b = rotation[n - 1 - i]
            if a != "BYE" and b != "BYE":
                pairs.append((a, b))
        rounds.append(pairs)
        rotation = [rotation[0]] + [rotation[-1]] + rotation[1:-1]
    return rounds


def build_round_plan(teams: pd.DataFrame, dates: list[str]) -> list[dict]:
    plan = []
    for division_code, g in teams.groupby("division_code"):
        team_ids = sorted(g["team_id"].tolist())
        rounds = round_robin_pairs(team_ids)
        if not rounds:
            continue
        for week_ix, game_date in enumerate(dates):
            current_round = rounds[week_ix % len(rounds)]
            for match_ix, (a, b) in enumerate(current_round):
                if week_ix % 2 == 0:
                    home, away = a, b
                else:
                    home, away = b, a
                plan.append(
                    {
                        "week_ix": week_ix,
                        "game_date": game_date,
                        "division_code": division_code,
                        "home_team_id": home,
                        "away_team_id": away,
                        "match_ix": match_ix,
                    }
                )
    return plan


def choose_best_slot(
    game: dict,
    slots_for_date: pd.DataFrame,
    used_slots: set[str],
    team_time: set[tuple[str, str, str]],
    coach_time: set[tuple[str, str, str]],
    team_to_coaches: dict[str, list[str]],
    primary_venue: dict[str, str],
    avoid_slots: set[tuple[str, str, str, str]],
    preferred_slots: set[tuple[str, str, str, str]],
) -> tuple[str | None, str | None]:
    best = None
    best_score = 10**9

    for _, slot in slots_for_date.iterrows():
        venue_id = str(slot["venue_id"])
        start_time = str(slot["start_time"])
        game_date = str(game["game_date"])
        slot_key = f"{game_date}|{start_time}|{venue_id}"
        if slot_key in used_slots:
            continue

        score = 0
        division = str(game["division_code"])

        if primary_venue.get(division) and primary_venue[division] != venue_id:
            score += 30

        pref_key = (division, venue_id, "Sun", start_time)
        if pref_key in avoid_slots:
            score += 25
        if pref_key in preferred_slots:
            score -= 10

        for team_id in [game["home_team_id"], game["away_team_id"]]:
            if (team_id, game_date, start_time) in team_time:
                score += 1000
            for coach_id in team_to_coaches.get(team_id, []):
                if (coach_id, game_date, start_time) in coach_time:
                    score += 1000

        if score < best_score:
            best_score = score
            best = (venue_id, start_time)

    return best if best else (None, None)


def generate_schedule(
    teams: pd.DataFrame,
    dated_slots: pd.DataFrame,
    team_coaches: pd.DataFrame,
    division_venue_prefs: pd.DataFrame,
    division_slot_prefs: pd.DataFrame,
    max_weeks: int,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    if teams.empty or dated_slots.empty:
        return empty_schedule_template(), pd.DataFrame([{"reason": "Missing teams or dated slots."}])

    dates = sorted(dated_slots["game_date"].dropna().astype(str).unique().tolist())
    if max_weeks > 0:
        dates = dates[:max_weeks]

    if not dates:
        return empty_schedule_template(), pd.DataFrame([{"reason": "No enabled game dates found."}])

    plan = build_round_plan(teams, dates)
    if not plan:
        return empty_schedule_template(), pd.DataFrame([{"reason": "No matchups generated."}])

    team_to_coaches = {}
    if not team_coaches.empty:
        team_to_coaches = team_coaches.groupby("team_id")["coach_id"].apply(list).to_dict()

    primary_venue = {}
    if not division_venue_prefs.empty:
        p = division_venue_prefs[division_venue_prefs["is_primary"] == 1]
        primary_venue = p.set_index("division_code")["venue_id"].to_dict()

    slot_pref = division_slot_prefs.copy()
    if not slot_pref.empty:
        slot_pref["start_time"] = slot_pref["start_time"].map(normalize_time)
    avoid_slots = set()
    preferred_slots = set()
    if not slot_pref.empty and "preference_level" in slot_pref.columns:
        avoid_rows = slot_pref[slot_pref["preference_level"] == "avoid"]
        preferred_rows = slot_pref[slot_pref["preference_level"] == "preferred"]
        avoid_slots = set(
            (str(r["division_code"]), str(r["venue_id"]), str(r["day_of_week"]), str(r["start_time"]))
            for _, r in avoid_rows.iterrows()
        )
        preferred_slots = set(
            (str(r["division_code"]), str(r["venue_id"]), str(r["day_of_week"]), str(r["start_time"]))
            for _, r in preferred_rows.iterrows()
        )

    used_slots = set()
    team_time = set()
    coach_time = set()
    scheduled_rows = []
    unscheduled = []

    plan_df = pd.DataFrame(plan).sort_values(["week_ix", "division_code", "match_ix"]).reset_index(drop=True)

    for _, game in plan_df.iterrows():
        game_date = str(game["game_date"])
        day_slots = dated_slots[dated_slots["game_date"] == game_date]
        venue_id, start_time = choose_best_slot(
            game.to_dict(),
            day_slots,
            used_slots,
            team_time,
            coach_time,
            team_to_coaches,
            primary_venue,
            avoid_slots,
            preferred_slots,
        )

        if not venue_id:
            unscheduled.append(
                {
                    "division_code": game["division_code"],
                    "game_date": game_date,
                    "home_team_id": game["home_team_id"],
                    "away_team_id": game["away_team_id"],
                    "reason": "No viable slot found.",
                }
            )
            continue

        slot_key = f"{game_date}|{start_time}|{venue_id}"
        used_slots.add(slot_key)
        for team_id in [game["home_team_id"], game["away_team_id"]]:
            team_time.add((team_id, game_date, start_time))
            for coach_id in team_to_coaches.get(team_id, []):
                coach_time.add((coach_id, game_date, start_time))

        scheduled_rows.append(
            {
                "game_date": game_date,
                "start_time": start_time,
                "venue_id": venue_id,
                "division_code": game["division_code"],
                "home_team_id": game["home_team_id"],
                "away_team_id": game["away_team_id"],
                "notes": "auto-generated",
            }
        )

    schedule = pd.DataFrame(scheduled_rows)
    if schedule.empty:
        schedule = empty_schedule_template()
    else:
        schedule = schedule.sort_values(["game_date", "start_time", "venue_id"]).reset_index(drop=True)
    return schedule, pd.DataFrame(unscheduled)


def validate_schedule(
    schedule: pd.DataFrame,
    dated_slots: pd.DataFrame,
    teams: pd.DataFrame,
    team_coaches: pd.DataFrame,
    division_venue_prefs: pd.DataFrame,
    division_slot_prefs: pd.DataFrame,
    rules: pd.DataFrame,
) -> pd.DataFrame:
    findings: list[dict] = []

    rules_map = {str(r["rule_key"]): {"enabled": int(r["enabled"]), "severity": str(r["severity"]).lower()} for _, r in rules.iterrows()}

    def add(rule_key: str, row: int | str, message: str, fallback_severity: str) -> None:
        if not is_enabled(rules_map, rule_key):
            return
        findings.append(
            {
                "severity": rule_severity(rules_map, rule_key, fallback_severity),
                "type": rule_key,
                "row": row,
                "message": message,
            }
        )

    if schedule.empty:
        add("required_field", "-", "No schedule rows provided.", "error")
        out = pd.DataFrame(findings)
        return out if not out.empty else pd.DataFrame([{"severity": "ok", "type": "validation", "row": "-", "message": "No rules enabled."}])

    df = schedule.copy().reset_index(drop=True)
    for col in ["game_date", "start_time", "venue_id", "division_code", "home_team_id", "away_team_id"]:
        if col not in df.columns:
            df[col] = ""

    df["row"] = df.index + 1
    df["start_time"] = df["start_time"].map(normalize_time)
    df["game_date"] = pd.to_datetime(df["game_date"], errors="coerce").dt.date.astype("string")

    required = ["game_date", "start_time", "venue_id", "home_team_id", "away_team_id"]
    for _, row in df.iterrows():
        for col in required:
            if not str(row[col]).strip() or str(row[col]) == "<NA>":
                add("required_field", int(row["row"]), f"Missing required field: {col}.", "error")

    team_to_div = teams.set_index("team_id")["division_code"].to_dict() if not teams.empty else {}
    valid_team_ids = set(team_to_div.keys())

    for _, row in df.iterrows():
        h = str(row["home_team_id"]).strip()
        a = str(row["away_team_id"]).strip()
        if h and a and h == a:
            add("invalid_matchup", int(row["row"]), "Home and away teams are the same.", "error")

        for side, team_id in [("home", h), ("away", a)]:
            if team_id and team_id not in valid_team_ids:
                add("unknown_team", int(row["row"]), f"Unknown {side}_team_id: {team_id}.", "error")

        if h in valid_team_ids and a in valid_team_ids:
            h_div = team_to_div[h]
            a_div = team_to_div[a]
            if h_div != a_div:
                add("cross_division_match", int(row["row"]), f"Teams are in different divisions ({h_div} vs {a_div}).", "error")
            if str(row["division_code"]).strip() and str(row["division_code"]).strip() != h_div:
                add("division_mismatch", int(row["row"]), f"division_code does not match team division ({h_div}).", "warning")

    slot_set = set(dated_slots["slot_key"].tolist()) if not dated_slots.empty else set()
    df["slot_key"] = df["game_date"] + "|" + df["start_time"] + "|" + df["venue_id"].astype(str)
    for _, row in df.iterrows():
        if slot_set and row["slot_key"] not in slot_set:
            add("slot_not_in_availability", int(row["row"]), "Slot is not in enabled game days + venue availability.", "warning")

    dup_slots = df[df.duplicated(subset=["game_date", "start_time", "venue_id"], keep=False)]
    for _, row in dup_slots.iterrows():
        add("slot_collision", int(row["row"]), "Another game is scheduled in the same venue/date/time.", "error")

    team_time_rows = []
    for _, row in df.iterrows():
        for team_col in ["home_team_id", "away_team_id"]:
            team_id = str(row[team_col]).strip()
            if team_id:
                team_time_rows.append({"row": int(row["row"]), "team_id": team_id, "game_date": row["game_date"], "start_time": row["start_time"]})
    team_time = pd.DataFrame(team_time_rows)
    if not team_time.empty:
        dups = team_time[team_time.duplicated(subset=["team_id", "game_date", "start_time"], keep=False)]
        for _, row in dups.iterrows():
            add("team_overlap", int(row["row"]), f"Team {row['team_id']} has overlapping games.", "error")

    team_to_coach = {}
    if not team_coaches.empty:
        team_to_coach = team_coaches.groupby("team_id")["coach_id"].apply(list).to_dict()
        coach_time_rows = []
        for _, row in df.iterrows():
            for team_col in ["home_team_id", "away_team_id"]:
                team_id = str(row[team_col]).strip()
                for coach_id in team_to_coach.get(team_id, []):
                    coach_time_rows.append({"row": int(row["row"]), "coach_id": coach_id, "game_date": row["game_date"], "start_time": row["start_time"]})
        coach_time = pd.DataFrame(coach_time_rows)
        if not coach_time.empty:
            dups = coach_time[coach_time.duplicated(subset=["coach_id", "game_date", "start_time"], keep=False)]
            for _, row in dups.iterrows():
                add("coach_overlap", int(row["row"]), f"Coach {row['coach_id']} has overlapping games.", "error")

    if not teams.empty and not division_venue_prefs.empty:
        primary = division_venue_prefs[division_venue_prefs["is_primary"] == 1]
        primary_map = primary.set_index("division_code")["venue_id"].to_dict()
        for _, row in df.iterrows():
            h = str(row["home_team_id"]).strip()
            if h in team_to_div:
                div = team_to_div[h]
                expected_venue = primary_map.get(div)
                if expected_venue and str(row["venue_id"]).strip() != expected_venue:
                    add("non_primary_venue", int(row["row"]), f"{div} usually plays at {expected_venue}.", "warning")

    if not teams.empty and not division_slot_prefs.empty:
        slot_pref = division_slot_prefs.copy()
        slot_pref["start_time"] = slot_pref["start_time"].map(normalize_time)
        pref_map = slot_pref.set_index(["division_code", "venue_id", "day_of_week", "start_time"])["preference_level"].to_dict()
        for _, row in df.iterrows():
            h = str(row["home_team_id"]).strip()
            if h in team_to_div:
                div = team_to_div[h]
                game_date = pd.to_datetime(row["game_date"], errors="coerce")
                if pd.notna(game_date):
                    dow = game_date.strftime("%a")
                    key = (div, str(row["venue_id"]).strip(), dow, str(row["start_time"]).strip())
                    if pref_map.get(key) == "avoid":
                        add("avoid_slot_used", int(row["row"]), f"{div} is marked as avoid for this slot.", "warning")

    if not findings:
        findings.append({"severity": "ok", "type": "validation", "row": "-", "message": "No conflicts found for enabled rules."})

    out = pd.DataFrame(findings).drop_duplicates()
    severity_order = {"error": 0, "warning": 1, "ok": 2}
    out["_severity_order"] = out["severity"].map(severity_order).fillna(9)
    return out.sort_values(["_severity_order", "row", "type"]).drop(columns=["_severity_order"]).reset_index(drop=True)


def build_friendly_export(schedule: pd.DataFrame, teams: pd.DataFrame, venues: pd.DataFrame) -> pd.DataFrame:
    if schedule.empty:
        return pd.DataFrame()

    df = schedule.copy()
    df["game_date"] = pd.to_datetime(df["game_date"], errors="coerce")
    df["start_time"] = df["start_time"].map(normalize_time)

    date_order = sorted(df["game_date"].dropna().dt.date.astype(str).unique().tolist())
    round_map = {d: i + 1 for i, d in enumerate(date_order)}

    team_name = {}
    if not teams.empty and "team_display_name" in teams.columns:
        team_name = teams.set_index("team_id")["team_display_name"].to_dict()

    venue_name = {}
    if not venues.empty:
        venue_name = venues.set_index("venue_id")["venue_name"].to_dict()

    def split_location_field(venue_id: str) -> tuple[str, str]:
        name = venue_name.get(venue_id, venue_id)
        if " - " in name:
            location, field = name.split(" - ", 1)
            return location, field
        return name, ""

    rows = []
    for _, r in df.iterrows():
        date_str = r["game_date"].date().isoformat() if pd.notna(r["game_date"]) else ""
        location, field = split_location_field(str(r["venue_id"]))
        rows.append(
            {
                "RoundNo": round_map.get(date_str, ""),
                "MatchDate": date_str,
                "StartTime": r["start_time"],
                "Division": r.get("division_code", ""),
                "Location": location,
                "Field": field,
                "HomeTeam": team_name.get(r.get("home_team_id", ""), r.get("home_team_id", "")),
                "AwayTeam": team_name.get(r.get("away_team_id", ""), r.get("away_team_id", "")),
                "HomeTeamId": r.get("home_team_id", ""),
                "AwayTeamId": r.get("away_team_id", ""),
                "VenueId": r.get("venue_id", ""),
                "Notes": r.get("notes", ""),
            }
        )

    out = pd.DataFrame(rows)
    return out.sort_values(["MatchDate", "StartTime", "VenueId"]).reset_index(drop=True)


def get_division_teams(teams: pd.DataFrame, division_code: str) -> pd.DataFrame:
    if teams.empty:
        return pd.DataFrame(columns=["team_number", "team_id", "team_display_name", "coach_last_name"])
    out = teams[teams["division_code"] == division_code].copy()
    if out.empty:
        return pd.DataFrame(columns=["team_number", "team_id", "team_display_name", "coach_last_name"])

    if "draft_slot" in out.columns:
        out["team_number"] = pd.to_numeric(out["draft_slot"], errors="coerce")
        out = out.sort_values(["team_number", "team_id"]).reset_index(drop=True)
        fallback_numbers = pd.Series(out.index + 1, index=out.index)
        out["team_number"] = out["team_number"].fillna(fallback_numbers).astype(int)
    else:
        out = out.sort_values(["team_id"]).reset_index(drop=True)
        out["team_number"] = out.index + 1

    if "team_display_name" not in out.columns:
        out["team_display_name"] = out["team_id"]
    if "coach_last_name" not in out.columns:
        out["coach_last_name"] = ""

    return out[["team_number", "team_id", "team_display_name", "coach_last_name"]]


def init_division_grid(week_count: int, time_slots: list[str]) -> pd.DataFrame:
    cols = ["time_slot"] + [f"W{k}" for k in range(1, week_count + 1)]
    rows = []
    for t in time_slots:
        row = {c: "" for c in cols}
        row["time_slot"] = t
        rows.append(row)
    return pd.DataFrame(rows, columns=cols)


def round_robin_number_pairs(team_numbers: list[int]) -> list[list[tuple[int, int]]]:
    teams = list(team_numbers)
    if len(teams) < 2:
        return []

    if len(teams) % 2 == 1:
        teams.append(-1)  # BYE sentinel

    rounds: list[list[tuple[int, int]]] = []
    n = len(teams)
    rotation = teams[:]
    for _ in range(n - 1):
        pairs: list[tuple[int, int]] = []
        for i in range(n // 2):
            a = rotation[i]
            b = rotation[n - 1 - i]
            if a != -1 and b != -1:
                pairs.append((a, b))
        rounds.append(pairs)
        rotation = [rotation[0]] + [rotation[-1]] + rotation[1:-1]
    return rounds


def parse_csv_list(value: str) -> list[str]:
    if value is None:
        return []
    return [x.strip() for x in str(value).split(",") if x.strip()]


def consideration_applies_to_builder(row: pd.Series) -> bool:
    applies_to = str(row.get("applies_to", "")).strip().lower()
    weekdays = [d.title()[:3] for d in parse_csv_list(str(row.get("weekdays", "")))]
    specific_dates = parse_csv_list(str(row.get("specific_dates", "")))
    if not applies_to or applies_to == "all games":
        return True
    if applies_to == "specific weekdays":
        return "Sun" in weekdays
    if applies_to == "specific dates and weekdays":
        if specific_dates:
            # Builder has no concrete date mapping, so weekday is the best proxy.
            return "Sun" in weekdays
        # If no concrete date was entered, treat as global so rule still applies.
        return True
    if applies_to == "specific dates":
        # If no dates were entered, treat as global to avoid silently dropping the rule.
        return len(specific_dates) == 0
    # Unknown setting: keep rule active rather than silently ignoring it.
    return True


def build_team_constraints_for_division(selected_division: str, division_team_df: pd.DataFrame) -> dict[int, list[dict]]:
    constraints: dict[int, list[dict]] = {}
    rules_df = load_or_default_considerations()
    if rules_df.empty or division_team_df.empty:
        return constraints

    team_id_to_number = {
        str(r["team_id"]): int(r["team_number"])
        for _, r in division_team_df.iterrows()
        if str(r.get("team_id", "")).strip()
    }

    rules = rules_df.copy()
    for col, default in [("status", "Active"), ("league_code", ""), ("league_scope", ""), ("team_id", ""), ("time_rule", ""), ("time_value", "")]:
        if col not in rules.columns:
            rules[col] = default

    rules = rules[rules["status"].fillna("").astype(str).str.lower() == "active"]
    if rules.empty:
        return constraints

    def rule_in_league(row: pd.Series) -> bool:
        if str(row.get("league_code", "")).strip() == selected_division:
            return True
        scope = parse_csv_list(str(row.get("league_scope", "")))
        return selected_division in scope

    rules = rules[rules.apply(rule_in_league, axis=1)]
    if rules.empty:
        return constraints

    for _, row in rules.iterrows():
        if not consideration_applies_to_builder(row):
            continue
        team_id = str(row.get("team_id", "")).strip()
        if not team_id or team_id not in team_id_to_number:
            continue
        team_num = team_id_to_number[team_id]
        constraints.setdefault(team_num, []).append(
            {
                "time_rule": str(row.get("time_rule", "")).strip(),
                "time_value": str(row.get("time_value", "")).strip(),
            }
        )
    return constraints


def team_slot_constraint_penalty(
    team_num: int,
    slot: str,
    slot_index: int,
    max_slot_index: int,
    team_constraints: dict[int, list[dict]],
) -> float:
    rules = team_constraints.get(team_num, [])
    if not rules:
        return 0.0

    penalty = 0.0
    slot_norm = normalize_time(slot)
    for r in rules:
        rule = str(r.get("time_rule", "")).strip().lower()
        value = str(r.get("time_value", "")).strip()

        if rule == "prefer later games":
            # Strong weight so this meaningfully shifts generated slots later.
            penalty += float(max_slot_index - slot_index) * 180.0
        elif rule == "prefer earlier games":
            penalty += float(slot_index) * 180.0
        elif rule == "no games before time":
            t = normalize_time(value)
            if t and slot_norm < t:
                penalty += 700.0
        elif rule == "no games after time":
            t = normalize_time(value)
            if t and slot_norm > t:
                penalty += 700.0
        elif rule == "prefer specific slot(s)":
            preferred = [normalize_time(v) for v in parse_csv_list(value)]
            preferred = [p for p in preferred if p]
            if preferred:
                penalty += -40.0 if slot_norm in preferred else 180.0
        elif rule == "avoid specific slot(s)":
            avoided = [normalize_time(v) for v in parse_csv_list(value)]
            avoided = [a for a in avoided if a]
            if slot_norm in avoided:
                penalty += 260.0
        elif rule == "between time window":
            if "-" in value:
                left, right = value.split("-", 1)
                t1 = normalize_time(left.strip())
                t2 = normalize_time(right.strip())
                if t1 and t2 and not (t1 <= slot_norm <= t2):
                    penalty += 500.0

    return penalty


def violates_hard_time_constraint(
    team_num: int,
    slot: str,
    team_constraints: dict[int, list[dict]],
) -> bool:
    rules = team_constraints.get(team_num, [])
    if not rules:
        return False

    slot_norm = normalize_time(slot)
    for r in rules:
        rule = str(r.get("time_rule", "")).strip().lower()
        value = str(r.get("time_value", "")).strip()
        if rule == "no games before time":
            t = normalize_time(value)
            if t and slot_norm < t:
                return True
        elif rule == "no games after time":
            t = normalize_time(value)
            if t and slot_norm > t:
                return True
    return False


def hard_blocked_slot_count(
    team_num: int,
    time_slots: list[str],
    team_constraints: dict[int, list[dict]],
) -> int:
    return sum(1 for slot in time_slots if violates_hard_time_constraint(team_num, slot, team_constraints))


def generate_random_division_grid(
    week_count: int,
    time_slots: list[str],
    team_count: int,
    seed: int,
    team_constraints: dict[int, list[dict]] | None = None,
    max_games_per_team_per_week: int = 1,
) -> tuple[pd.DataFrame, str]:
    grid = init_division_grid(week_count, time_slots)
    if team_count < 2:
        return grid, "Need at least 2 teams."

    rng = random.Random(seed)
    team_numbers = list(range(1, team_count + 1))
    rng.shuffle(team_numbers)
    rounds = round_robin_number_pairs(team_numbers)
    if not rounds:
        return grid, "Could not generate rounds."

    all_pairs = sorted({tuple(sorted((a, b))) for rnd in rounds for a, b in rnd})
    if not all_pairs:
        return grid, "Could not build matchup pairs."

    max_games_per_team_per_week = max(1, int(max_games_per_team_per_week))
    games_per_week = min(len(time_slots), (team_count * max_games_per_team_per_week) // 2)
    if games_per_week <= 0:
        return grid, "No games available to schedule."

    slot_to_row = {slot: i for i, slot in enumerate(time_slots)}
    slot_to_index = {slot: i for i, slot in enumerate(time_slots)}
    max_slot_index = max(0, len(time_slots) - 1)
    team_slot_counts = {t: {slot: 0 for slot in time_slots} for t in range(1, team_count + 1)}
    slot_usage = {slot: 0 for slot in time_slots}
    home_counts = {t: 0 for t in range(1, team_count + 1)}
    away_counts = {t: 0 for t in range(1, team_count + 1)}
    team_constraints = team_constraints or {}

    warning = ""
    pair_counts = {p: 0 for p in all_pairs}
    last_week_pairs: set[tuple[int, int]] = set()

    total_games_all_weeks = games_per_week * week_count
    total_appearances = 2 * total_games_all_weeks
    base_games, extra_teams_count = divmod(total_appearances, team_count)
    team_targets = {t: base_games for t in range(1, team_count + 1)}
    extra_teams = rng.sample(list(team_targets.keys()), extra_teams_count) if extra_teams_count > 0 else []
    for t in extra_teams:
        team_targets[t] += 1
    remaining_games = team_targets.copy()

    # Minimum repeat limit needed globally; keeps opponents faced balanced and avoids 3rd meetings when not needed.
    min_pair_repeat_cap = max(1, math.ceil(total_games_all_weeks / len(all_pairs)))
    pair_repeat_cap = min_pair_repeat_cap
    if pair_repeat_cap < 2 and total_games_all_weeks > len(all_pairs):
        pair_repeat_cap = 2

    week_doubleheader_counts = {t: 0 for t in range(1, team_count + 1)}

    for week_ix in range(week_count):
        week_col = f"W{week_ix + 1}"
        team_week_games = {t: 0 for t in range(1, team_count + 1)}
        week_pairs: list[tuple[int, int]] = []

        appearances_needed = 2 * games_per_week
        week_caps = {t: 0 for t in range(1, team_count + 1)}
        for _ in range(appearances_needed):
            best_team = None
            best_team_score = float("inf")
            for t in range(1, team_count + 1):
                if remaining_games[t] <= week_caps[t]:
                    continue
                if week_caps[t] >= max_games_per_team_per_week:
                    continue
                score = 0.0
                score += week_caps[t] * 100.0
                score += week_doubleheader_counts[t] * 40.0
                score += -remaining_games[t] * 15.0
                score += rng.random() * 0.01
                if score < best_team_score:
                    best_team_score = score
                    best_team = t
            if best_team is None:
                break
            week_caps[best_team] += 1

        while len(week_pairs) < games_per_week:
            best_pair: tuple[int, int] | None = None
            best_score = float("inf")

            for pair in all_pairs:
                a, b = pair
                if pair_counts[pair] >= pair_repeat_cap:
                    continue
                if team_week_games[a] >= week_caps[a]:
                    continue
                if team_week_games[b] >= week_caps[b]:
                    continue
                if pair in week_pairs:
                    continue

                score = 0.0
                score += pair_counts[pair] * 220.0
                if pair in last_week_pairs:
                    score += 220.0
                score += abs(remaining_games[a] - remaining_games[b]) * 12.0
                score += -(remaining_games[a] + remaining_games[b]) * 120.0
                score += (team_week_games[a] + team_week_games[b]) * 90.0
                score += rng.random() * 0.01

                if score < best_score:
                    best_score = score
                    best_pair = pair

            if best_pair is None:
                break

            a, b = best_pair
            week_pairs.append(best_pair)
            team_week_games[a] += 1
            team_week_games[b] += 1
            remaining_games[a] -= 1
            remaining_games[b] -= 1
            pair_counts[best_pair] += 1

        if len(week_pairs) < games_per_week:
            warning = "Could not fully fill all weekly slots with current team/week constraints."

        for t, c in team_week_games.items():
            if c >= 2:
                week_doubleheader_counts[t] += 1

        matchups = week_pairs[:]
        # Schedule constrained teams first so they don't get stranded with only blocked slots remaining.
        matchups.sort(
            key=lambda p: (
                hard_blocked_slot_count(p[0], time_slots, team_constraints)
                + hard_blocked_slot_count(p[1], time_slots, team_constraints)
            ),
            reverse=True,
        )
        available_slots = time_slots[:]
        rng.shuffle(available_slots)
        scheduled_pairs: list[tuple[int, int]] = []

        for team_a, team_b in matchups:
            if not available_slots:
                break

            best = None
            best_score = float("inf")
            for slot in available_slots:
                if violates_hard_time_constraint(team_a, slot, team_constraints):
                    continue
                if violates_hard_time_constraint(team_b, slot, team_constraints):
                    continue

                slot_balance_score = team_slot_counts[team_a][slot] + team_slot_counts[team_b][slot]
                slot_usage_score = slot_usage[slot]
                constraint_score = (
                    team_slot_constraint_penalty(team_a, slot, slot_to_index[slot], max_slot_index, team_constraints)
                    + team_slot_constraint_penalty(team_b, slot, slot_to_index[slot], max_slot_index, team_constraints)
                )

                hdiff_a = abs((home_counts[team_a] + 1) - away_counts[team_a])
                hdiff_b = abs(home_counts[team_b] - (away_counts[team_b] + 1))
                home_away_score_forward = hdiff_a + hdiff_b

                hdiff_a_sw = abs((home_counts[team_b] + 1) - away_counts[team_b])
                hdiff_b_sw = abs(home_counts[team_a] - (away_counts[team_a] + 1))
                home_away_score_swapped = hdiff_a_sw + hdiff_b_sw
                orientation_swapped = home_away_score_swapped < home_away_score_forward
                home_away_score = min(home_away_score_forward, home_away_score_swapped)

                score = (100 * slot_balance_score) + (10 * slot_usage_score) + home_away_score + constraint_score
                score += rng.random() * 0.01

                if score < best_score:
                    best_score = score
                    best = (slot, orientation_swapped)

            if not best:
                # Roll back allocation so the teams can still be scheduled in future weeks.
                pair = tuple(sorted((team_a, team_b)))
                remaining_games[team_a] += 1
                remaining_games[team_b] += 1
                pair_counts[pair] = max(0, pair_counts[pair] - 1)
                continue

            chosen_slot, swap = best
            available_slots.remove(chosen_slot)
            home, away = (team_b, team_a) if swap else (team_a, team_b)

            row_ix = slot_to_row[chosen_slot]
            grid.at[row_ix, week_col] = f"{home} vs {away}"

            team_slot_counts[team_a][chosen_slot] += 1
            team_slot_counts[team_b][chosen_slot] += 1
            slot_usage[chosen_slot] += 1
            home_counts[home] += 1
            away_counts[away] += 1
            scheduled_pairs.append(tuple(sorted((team_a, team_b))))

        last_week_pairs = set(scheduled_pairs)

    if any(v != 0 for v in remaining_games.values()):
        warning = "Generated schedule could not fully satisfy equalized total-game targets."

    return grid, warning


def parse_matchup_cell(value: str) -> tuple[int, int] | None:
    v = str(value).strip()
    if not v:
        return None
    m = re.match(r"^\s*(\d+)\s*(?:vs|v|-)\s*(\d+)\s*$", v, flags=re.IGNORECASE)
    if not m:
        return None
    return int(m.group(1)), int(m.group(2))


def analyze_division_grid(
    grid: pd.DataFrame,
    valid_team_numbers: set[int],
    max_games_per_team_per_week: int = 1,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    findings = []
    game_counts: dict[int, dict[str, int]] = {n: {"home": 0, "away": 0, "total": 0} for n in valid_team_numbers}
    max_games_per_team_per_week = max(1, int(max_games_per_team_per_week))

    week_cols = [c for c in grid.columns if c.startswith("W")]
    for week_col in week_cols:
        teams_seen_count: dict[int, int] = {}
        for _, row in grid.iterrows():
            slot = str(row.get("time_slot", ""))
            val = str(row.get(week_col, "")).strip()
            if not val:
                continue

            parsed = parse_matchup_cell(val)
            if not parsed:
                findings.append(
                    {
                        "severity": "warning",
                        "week": week_col,
                        "time_slot": slot,
                        "message": "Invalid format. Use `H vs A` (example: `3 vs 4`).",
                    }
                )
                continue

            home, away = parsed
            if home == away:
                findings.append(
                    {
                        "severity": "error",
                        "week": week_col,
                        "time_slot": slot,
                        "message": f"Team {home} cannot play itself.",
                    }
                )
                continue

            bad = [n for n in [home, away] if n not in valid_team_numbers]
            if bad:
                findings.append(
                    {
                        "severity": "error",
                        "week": week_col,
                        "time_slot": slot,
                        "message": f"Unknown team number(s): {bad}.",
                    }
                )
                continue

            for t in [home, away]:
                teams_seen_count[t] = teams_seen_count.get(t, 0) + 1
                if teams_seen_count[t] > max_games_per_team_per_week:
                    findings.append(
                        {
                            "severity": "error",
                            "week": week_col,
                            "time_slot": slot,
                            "message": (
                                f"Team {t} exceeds max games/week in {week_col} "
                                f"({teams_seen_count[t]} > {max_games_per_team_per_week})."
                            ),
                        }
                    )

            game_counts[home]["home"] += 1
            game_counts[home]["total"] += 1
            game_counts[away]["away"] += 1
            game_counts[away]["total"] += 1

    findings_df = pd.DataFrame(findings) if findings else pd.DataFrame(columns=["severity", "week", "time_slot", "message"])
    balance_rows = [
        {"team_number": n, "home_games": c["home"], "away_games": c["away"], "total_games": c["total"], "home_away_diff": c["home"] - c["away"]}
        for n, c in sorted(game_counts.items())
    ]
    balance_df = pd.DataFrame(balance_rows)
    return findings_df, balance_df


def build_time_slot_team_counts(grid: pd.DataFrame, valid_team_numbers: set[int]) -> pd.DataFrame:
    week_cols = [c for c in grid.columns if c.startswith("W")]
    team_numbers = sorted(valid_team_numbers)
    rows = []

    for _, row in grid.iterrows():
        time_slot = str(row.get("time_slot", "")).strip()
        counts = {n: 0 for n in team_numbers}
        for week_col in week_cols:
            val = str(row.get(week_col, "")).strip()
            parsed = parse_matchup_cell(val)
            if not parsed:
                continue
            home, away = parsed
            if home in counts:
                counts[home] += 1
            if away in counts:
                counts[away] += 1

        out_row = {"Time": time_slot}
        for n in team_numbers:
            out_row[str(n)] = counts[n]
        rows.append(out_row)

    if not rows:
        cols = ["Time"] + [str(n) for n in team_numbers]
        return pd.DataFrame(columns=cols)

    out = pd.DataFrame(rows)
    return out


def extract_valid_games(grid: pd.DataFrame, valid_team_numbers: set[int]) -> pd.DataFrame:
    rows = []
    week_cols = [c for c in grid.columns if c.startswith("W")]
    for week_col in week_cols:
        week_no = int(week_col[1:])
        for _, row in grid.iterrows():
            time_slot = str(row.get("time_slot", "")).strip()
            val = str(row.get(week_col, "")).strip()
            parsed = parse_matchup_cell(val)
            if not parsed:
                continue
            home, away = parsed
            if home == away:
                continue
            if home not in valid_team_numbers or away not in valid_team_numbers:
                continue
            rows.append({"week_no": week_no, "time_slot": time_slot, "home": home, "away": away})
    if not rows:
        return pd.DataFrame(columns=["week_no", "time_slot", "home", "away"])
    return pd.DataFrame(rows)


def build_opponents_faced_matrix(valid_team_numbers: set[int], games_df: pd.DataFrame) -> pd.DataFrame:
    teams = sorted(valid_team_numbers)
    matrix = {t: {o: 0 for o in teams} for t in teams}
    for _, g in games_df.iterrows():
        h = int(g["home"])
        a = int(g["away"])
        matrix[h][a] += 1
        matrix[a][h] += 1

    rows = []
    for t in teams:
        row = {"Team": str(t)}
        for o in teams:
            row[str(o)] = "" if t == o else matrix[t][o]
        rows.append(row)
    return pd.DataFrame(rows)


def build_games_per_week_matrix(valid_team_numbers: set[int], games_df: pd.DataFrame, week_cols: list[str]) -> pd.DataFrame:
    teams = sorted(valid_team_numbers)
    week_nos = [int(c[1:]) for c in week_cols]
    counts = {t: {w: 0 for w in week_nos} for t in teams}
    for _, g in games_df.iterrows():
        w = int(g["week_no"])
        h = int(g["home"])
        a = int(g["away"])
        if h in counts and w in counts[h]:
            counts[h][w] += 1
        if a in counts and w in counts[a]:
            counts[a][w] += 1

    rows = []
    for t in teams:
        row = {"Team": str(t)}
        for w in week_nos:
            row[f"W{w}"] = counts[t][w]
        rows.append(row)
    return pd.DataFrame(rows)


def division_grid_to_games(grid: pd.DataFrame, division_code: str, team_lookup: dict[int, dict[str, str]]) -> pd.DataFrame:
    rows = []
    week_cols = [c for c in grid.columns if c.startswith("W")]
    for week_col in week_cols:
        week_no = int(week_col[1:])
        for _, row in grid.iterrows():
            slot = str(row.get("time_slot", ""))
            val = str(row.get(week_col, "")).strip()
            parsed = parse_matchup_cell(val)
            if not parsed:
                continue
            home_num, away_num = parsed
            home_ref = team_lookup.get(home_num, {})
            away_ref = team_lookup.get(away_num, {})
            rows.append(
                {
                    "division_code": division_code,
                    "week_no": week_no,
                    "time_slot": slot,
                    "home_team_name": home_ref.get("team_display_name", f"Team {home_num}"),
                    "away_team_name": away_ref.get("team_display_name", f"Team {away_num}"),
                }
            )
    if not rows:
        return pd.DataFrame(
            columns=[
                "division_code",
                "week_no",
                "time_slot",
                "home_team_name",
                "away_team_name",
            ]
        )
    out = pd.DataFrame(rows)
    return out.sort_values(["week_no", "time_slot"]).reset_index(drop=True)


def build_schedule_volume_options(team_count: int, weekends: int, slots_per_week: int) -> pd.DataFrame:
    if team_count <= 0 or weekends <= 0 or slots_per_week <= 0:
        return pd.DataFrame(
            columns=[
                "option",
                "max_games_per_team_per_week",
                "total_games",
                "games_per_week",
                "team_game_distribution",
                "weekly_pattern",
            ]
        )

    def dist_text(total_games: int) -> str:
        appearances = 2 * total_games
        base = appearances // team_count
        extra = appearances % team_count
        if extra == 0:
            return f"{team_count} teams with {base} games"
        return f"{team_count - extra} teams with {base} games; {extra} teams with {base + 1} games"

    options = []
    for max_games_per_team_week, label in [
        (1, "Fewer games per team with a bye each week"),
        (2, "More games per team with a double-header each week"),
    ]:
        games_per_week = min(slots_per_week, (team_count * max_games_per_team_week) // 2)
        total_games = games_per_week * weekends
        options.append(
            {
                "option": label,
                "max_games_per_team_per_week": max_games_per_team_week,
                "total_games": total_games,
                "games_per_week": games_per_week,
                "team_game_distribution": dist_text(total_games),
                "weekly_pattern": "One team with a bye" if max_games_per_team_week == 1 else "One team with a double-header",
            }
        )

    # Remove duplicates when slot capacity makes options equivalent.
    out = pd.DataFrame(options).drop_duplicates(subset=["total_games", "games_per_week"]).reset_index(drop=True)
    return out


def render_division_builder(divisions: pd.DataFrame, teams: pd.DataFrame, venue_slots: pd.DataFrame) -> None:
    st.subheader("Schedule Builder")
    st.caption("Follow these simple steps to create your schedule in a matter of minutes!")

    st.markdown(
        "1. Select a division and number of weekends.\n"
        "2. Generate random matchups or fill cells manually using `Home vs Away` team numbers (example: `3 vs 4`).\n"
        "3. Review validations to ensure schedule balance.\n"
        "4. Save your Schedule."
    )

    division_options = sorted(divisions["division_code"].tolist()) if not divisions.empty else []
    if not division_options:
        st.warning("No divisions found in data/divisions.csv.")
        return

    default_division_index = division_options.index("B3") if "B3" in division_options else 0
    dcol, tcol, _ = st.columns([1, 1, 3])
    with dcol:
        selected_division = st.selectbox("Division", options=division_options, index=default_division_index, key="division_builder_division")
    division_team_df = get_division_teams(teams, selected_division) if selected_division else pd.DataFrame()
    team_count = int(len(division_team_df))
    with tcol:
        st.text_input(
            "Number of teams",
            value=str(team_count),
            disabled=True,
            help="This value is based on the Division configuration.",
        )

    wcol, _ = st.columns([1, 4])
    with wcol:
        weekends = st.number_input("Number of weekends", min_value=1, max_value=30, value=10, step=1, key="division_builder_weekends")

    parsed_slots: list[str] = []
    if (
        not venue_slots.empty
        and "start_time" in venue_slots.columns
        and "assigned_division_code" in venue_slots.columns
    ):
        scoped = venue_slots.copy()
        scoped["assigned_division_code"] = scoped["assigned_division_code"].fillna("").astype(str).str.strip()
        scoped["start_time"] = scoped["start_time"].map(normalize_time)
        scoped["is_available"] = pd.to_numeric(scoped["is_available"], errors="coerce").fillna(0).astype(int)
        scoped = scoped[
            (scoped["assigned_division_code"] == str(selected_division))
            & (scoped["is_available"] == 1)
        ]
        parsed_slots = sorted([t for t in scoped["start_time"].dropna().astype(str).tolist() if t], key=normalize_time)
        parsed_slots = list(dict.fromkeys(parsed_slots))

    if not parsed_slots:
        st.warning(
            f"No active timeslots assigned to {selected_division} in Venues. "
            "Assign timeslot rules in the Venues tab to build this schedule."
        )
        parsed_slots = ["08:30", "09:30", "10:45", "11:45", "13:00", "14:15", "15:15", "16:30"]

    inherited_display = ", ".join(parsed_slots)
    # Match Number of weekends width by default, but expand when slot text gets long.
    inherited_col_ratio = 1 if len(inherited_display) <= 48 else 2
    icl, _ = st.columns([inherited_col_ratio, 5 - inherited_col_ratio])
    with icl:
        st.text_input(
            "Time Slots",
            value=inherited_display,
            disabled=True,
            help="This value is based on the Venue configuration.",
        )

    st.write("Select Scheduling Options")
    options_df = build_schedule_volume_options(
        team_count=team_count,
        weekends=int(weekends),
        slots_per_week=len(parsed_slots),
    )
    if not options_df.empty:
        options_view = options_df[["option", "total_games", "games_per_week", "team_game_distribution", "weekly_pattern"]].copy()
        event = st.dataframe(
            options_view,
            use_container_width=True,
            hide_index=True,
            height=130,
            selection_mode="single-row",
            on_select="rerun",
            key=f"scheduling_options_table_{selected_division}",
        )
        selected_rows = event.selection.rows if event and event.selection else []
        selected_option_idx = selected_rows[0] if selected_rows else 0
        max_games_per_team_per_week = int(options_df.iloc[selected_option_idx]["max_games_per_team_per_week"])
    else:
        st.warning("No valid scheduling options available for current settings.")
        max_games_per_team_per_week = 1

    grid_state_key = f"division_grid_{selected_division}"
    signature = (selected_division, int(weekends), tuple(parsed_slots))
    sig_key = f"{grid_state_key}_sig"
    generated_key = f"{grid_state_key}_generated"
    team_constraints = build_team_constraints_for_division(selected_division, division_team_df)

    if sig_key not in st.session_state or st.session_state[sig_key] != signature:
        st.session_state[grid_state_key] = init_division_grid(int(weekends), parsed_slots)
        st.session_state[sig_key] = signature
        st.session_state[generated_key] = False

    def apply_generated_grid(seed_value: int) -> None:
        generated_grid, warning = generate_random_division_grid(
            week_count=int(weekends),
            time_slots=parsed_slots,
            team_count=int(team_count),
            seed=seed_value,
            team_constraints=team_constraints,
            max_games_per_team_per_week=int(max_games_per_team_per_week),
        )
        st.session_state[grid_state_key] = generated_grid
        st.session_state[sig_key] = signature
        st.session_state[generated_key] = True
        if warning:
            st.warning(warning)
        else:
            st.success("Generated schedule grid. You can now edit any matchup cell.")

    if team_constraints:
        rows = []
        for team_num in sorted(team_constraints.keys()):
            for rule in team_constraints[team_num]:
                rule_name = str(rule.get("time_rule", "")).strip()
                rule_val = str(rule.get("time_value", "")).strip()
                if rule_val and rule_val.lower() != "nan":
                    rule_text = f"{rule_name}: {rule_val}"
                else:
                    rule_text = rule_name
                rows.append({"Team #": team_num, "Constraint": rule_text})
        constraints_df = pd.DataFrame(rows)
        st.info("Constraints in effect for this division:")
        st.dataframe(constraints_df, use_container_width=True, hide_index=True, height=140)
    else:
        st.caption("No active team constraints found for this division.")

    g1, gap, g2, _ = st.columns([1.8, 0.08, 1.0, 5.0])
    with g1:
        if st.button("Generate random matchups"):
            apply_generated_grid(random.randint(0, 999999))
    with g2:
        if st.session_state.get(generated_key, False):
            if st.button("🔀 Shuffle"):
                new_seed = random.randint(0, 999999)
                apply_generated_grid(new_seed)

    grid_df = st.session_state[grid_state_key]
    grid_df = st.data_editor(grid_df, num_rows="fixed", use_container_width=True, key=f"{grid_state_key}_editor")
    st.session_state[grid_state_key] = grid_df

    valid_team_numbers = set(range(1, int(team_count) + 1))
    findings, balance = analyze_division_grid(
        grid_df,
        valid_team_numbers,
        max_games_per_team_per_week=int(max_games_per_team_per_week),
    )
    total_games = int((grid_df[[c for c in grid_df.columns if c.startswith("W")]] != "").sum().sum())

    c1, c2 = st.columns(2)
    with c1:
        st.metric("Filled Matchups", total_games)
    with c2:
        err_count = int((findings["severity"] == "error").sum()) if not findings.empty else 0
        st.metric("Grid Errors", err_count)

    st.write("Grid Validation")
    if findings.empty:
        if total_games == 0:
            st.info("No matchups entered yet. Add matchups or click Generate random matchups.")
        else:
            st.success("No grid validation issues found.")
    else:
        st.dataframe(findings, use_container_width=True, height=200)

    time_slot_counts = build_time_slot_team_counts(grid_df, valid_team_numbers)
    left, right = st.columns(2)
    with left:
        st.write("Games By Time Slot")
        if time_slot_counts.empty:
            st.info("No time-slot counts yet.")
        else:
            st.dataframe(time_slot_counts, use_container_width=True, height=220, hide_index=True)
    with right:
        st.write("Home/Away Balance")
        st.dataframe(balance, use_container_width=True, height=220, hide_index=True)

    valid_games = extract_valid_games(grid_df, valid_team_numbers)
    week_cols = [c for c in grid_df.columns if c.startswith("W")]
    opponents_matrix = build_opponents_faced_matrix(valid_team_numbers, valid_games)
    games_per_week = build_games_per_week_matrix(valid_team_numbers, valid_games, week_cols)

    o1, o2 = st.columns(2)
    with o1:
        st.write("Opponents Faced")
        st.dataframe(opponents_matrix, use_container_width=True, height=220, hide_index=True)
    with o2:
        st.write("Games Per Week")
        st.dataframe(games_per_week, use_container_width=True, height=220, hide_index=True)

    team_lookup = {}
    if not division_team_df.empty:
        team_lookup = {
            int(r["team_number"]): {"team_id": str(r["team_id"]), "team_display_name": str(r["team_display_name"])}
            for _, r in division_team_df.iterrows()
        }
    division_games = division_grid_to_games(grid_df, selected_division, team_lookup)
    st.write("Division Games Export Preview")
    st.dataframe(division_games, use_container_width=True, height=220)

    if st.button("Save Schedule"):
        grid_path = DATA_DIR / f"division_grid_{selected_division}.csv"
        games_path = DATA_DIR / f"division_games_{selected_division}.csv"
        grid_df.to_csv(grid_path, index=False)
        division_games.to_csv(games_path, index=False)
        schedule_id = save_schedule_to_db(selected_division, int(weekends), division_games)
        st.success(f"Saved {grid_path.name} and {games_path.name}. Schedule ID: {schedule_id}")


def render_workbench(
    divisions: pd.DataFrame,
    teams: pd.DataFrame,
    venues: pd.DataFrame,
    venue_slots: pd.DataFrame,
    team_coaches: pd.DataFrame,
    division_venue_prefs: pd.DataFrame,
    division_slot_prefs: pd.DataFrame,
) -> None:
    st.subheader("General Scheduler Workbench")

    st.subheader("1) Game Dates")
    uploaded_days = st.file_uploader("Game day input CSV", type=["csv"], key="days_upload")
    if uploaded_days is not None:
        game_days = pd.read_csv(uploaded_days)
    elif (DATA_DIR / "game_day_inputs.csv").exists():
        game_days = pd.read_csv(DATA_DIR / "game_day_inputs.csv")
    else:
        example_path = DATA_DIR / "game_day_inputs.example.csv"
        game_days = pd.read_csv(example_path) if example_path.exists() else pd.DataFrame(columns=["game_date", "day_of_week", "enabled", "notes"])

    game_days = prep_game_days(game_days)
    game_days = st.data_editor(game_days, num_rows="dynamic", use_container_width=True, key="game_days_editor")
    if st.button("Save game_day_inputs.csv"):
        game_days.to_csv(DATA_DIR / "game_day_inputs.csv", index=False)
        st.success("Saved data/game_day_inputs.csv")

    dated_slots = build_dated_slots(game_days, venue_slots)
    st.subheader("2) Dated Available Slots")
    st.dataframe(dated_slots, use_container_width=True, height=220)

    st.subheader("3) Generate Draft Schedule")
    enabled_dates = sorted(game_days[game_days["enabled"] == 1]["game_date"].dropna().astype(str).tolist())
    max_weeks_default = len(enabled_dates) if enabled_dates else 1
    weeks_to_schedule = st.number_input("Weeks to schedule", min_value=1, max_value=max(1, max_weeks_default), value=max_weeks_default, step=1)

    if st.button("Auto-generate draft schedule"):
        draft, unscheduled = generate_schedule(
            teams,
            dated_slots,
            team_coaches,
            division_venue_prefs,
            division_slot_prefs,
            max_weeks=int(weeks_to_schedule),
        )
        st.session_state.schedule_df = draft
        st.session_state.unscheduled_df = unscheduled
        st.success("Draft schedule generated.")

    if "unscheduled_df" in st.session_state and not st.session_state.unscheduled_df.empty:
        st.warning(f"Unscheduled games: {len(st.session_state.unscheduled_df)}")
        st.dataframe(st.session_state.unscheduled_df, use_container_width=True, height=140)

    st.subheader("4) Build or Import Schedule")
    schedule_upload = st.file_uploader("Schedule CSV", type=["csv"], key="schedule_upload")

    if schedule_upload is not None:
        schedule_df = pd.read_csv(schedule_upload)
    elif "schedule_df" in st.session_state:
        schedule_df = st.session_state.schedule_df
    elif (DATA_DIR / "schedule.csv").exists():
        schedule_df = pd.read_csv(DATA_DIR / "schedule.csv")
    else:
        schedule_df = empty_schedule_template()

    team_ids = sorted(teams["team_id"].tolist()) if not teams.empty else []
    division_ids = sorted(divisions["division_code"].tolist()) if not divisions.empty else []
    venue_ids = sorted(venues["venue_id"].tolist()) if not venues.empty else []

    edited_schedule = st.data_editor(
        schedule_df,
        num_rows="dynamic",
        use_container_width=True,
        key="schedule_editor",
        column_config={
            "division_code": st.column_config.SelectboxColumn("division_code", options=division_ids),
            "venue_id": st.column_config.SelectboxColumn("venue_id", options=venue_ids),
            "home_team_id": st.column_config.SelectboxColumn("home_team_id", options=team_ids),
            "away_team_id": st.column_config.SelectboxColumn("away_team_id", options=team_ids),
        },
    )
    st.session_state.schedule_df = edited_schedule

    c1, c2 = st.columns(2)
    with c1:
        if st.button("Save schedule to data/schedule.csv"):
            edited_schedule.to_csv(DATA_DIR / "schedule.csv", index=False)
            st.success("Saved data/schedule.csv")
    with c2:
        st.download_button("Download raw schedule CSV", data=edited_schedule.to_csv(index=False), file_name="schedule.csv", mime="text/csv")

    st.subheader("5) Validation Rules")
    rules = load_or_default_rules()
    rules = st.data_editor(
        rules,
        num_rows="fixed",
        use_container_width=True,
        key="rules_editor",
        column_config={
            "enabled": st.column_config.CheckboxColumn("enabled"),
            "severity": st.column_config.SelectboxColumn("severity", options=["error", "warning"]),
        },
    )
    if st.button("Save validation rules"):
        save_rules(rules)
        st.success("Saved data/validation_rules.csv")

    st.subheader("6) Validation Report")
    findings = validate_schedule(
        edited_schedule,
        dated_slots,
        teams,
        team_coaches,
        division_venue_prefs,
        division_slot_prefs,
        rules,
    )
    err_count = int((findings["severity"] == "error").sum()) if not findings.empty else 0
    warn_count = int((findings["severity"] == "warning").sum()) if not findings.empty else 0
    ok_count = int((findings["severity"] == "ok").sum()) if not findings.empty else 0
    m1, m2, m3 = st.columns(3)
    m1.metric("Errors", err_count)
    m2.metric("Warnings", warn_count)
    m3.metric("OK", ok_count)
    st.dataframe(findings, use_container_width=True, height=300)
    st.download_button("Download validation report", data=findings.to_csv(index=False), file_name="validation_report.csv", mime="text/csv")

    st.subheader("7) Friendly Export")
    friendly = build_friendly_export(edited_schedule, teams, venues)
    st.dataframe(friendly, use_container_width=True, height=220)
    st.download_button(
        "Download friendly upload CSV",
        data=friendly.to_csv(index=False),
        file_name="schedule_friendly_export.csv",
        mime="text/csv",
    )


def render_venues_tab(venues: pd.DataFrame, venue_slots: pd.DataFrame, divisions: pd.DataFrame) -> None:
    st.subheader("Venues")
    st.caption("Manage venue names and time slot availability.")

    venue_df_source = venues.copy() if not venues.empty else pd.DataFrame(
        columns=["venue_id", "venue_code", "venue_name", "is_active", "notes"]
    )
    for col, default in [("venue_id", ""), ("venue_code", ""), ("venue_name", ""), ("is_active", 1), ("notes", "")]:
        if col not in venue_df_source.columns:
            venue_df_source[col] = default
    venue_df_source["is_active"] = pd.to_numeric(venue_df_source["is_active"], errors="coerce").fillna(0).astype(int)

    venue_df = venue_df_source[["venue_id", "venue_name", "is_active", "notes"]].copy()
    venue_df["is_active"] = venue_df["is_active"].map(lambda x: "Y" if int(x) == 1 else "N")
    venue_df_edited = st.data_editor(
        venue_df,
        num_rows="dynamic",
        use_container_width=True,
        key="venues_tab_editor",
        column_config={"is_active": st.column_config.SelectboxColumn("is_active", options=["Y", "N"])},
    )

    venue_code_map = {
        str(r["venue_id"]): str(r["venue_code"])
        for _, r in venue_df_source.iterrows()
        if str(r["venue_id"]).strip()
    }
    venues_to_save = venue_df_edited.copy()
    venues_to_save["is_active"] = venues_to_save["is_active"].fillna("N").astype(str).str.upper().map({"Y": 1, "N": 0}).fillna(0).astype(int)
    venues_to_save["venue_code"] = venues_to_save["venue_id"].astype(str).map(venue_code_map).fillna("")
    missing_code = venues_to_save["venue_code"].astype(str).str.strip() == ""
    venues_to_save.loc[missing_code, "venue_code"] = venues_to_save.loc[missing_code, "venue_id"].astype(str)
    venues_to_save = venues_to_save[["venue_id", "venue_code", "venue_name", "is_active", "notes"]]

    slot_df = venue_slots.copy() if not venue_slots.empty else pd.DataFrame(
        columns=["slot_id", "venue_id", "day_of_week", "start_time", "is_available", "is_preferred", "notes"]
    )
    if "day_of_week" not in slot_df.columns:
        slot_df["day_of_week"] = "Sun"
    if "start_time" not in slot_df.columns:
        slot_df["start_time"] = ""
    if "is_available" not in slot_df.columns:
        slot_df["is_available"] = 1
    if "slot_id" not in slot_df.columns:
        slot_df["slot_id"] = ""
    if "is_preferred" not in slot_df.columns:
        slot_df["is_preferred"] = 1
    if "notes" not in slot_df.columns:
        slot_df["notes"] = ""
    if "assigned_division_code" not in slot_df.columns:
        slot_df["assigned_division_code"] = ""

    ordered_days = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
    day_col, _ = st.columns([1, 4])
    with day_col:
        selected_days = st.multiselect(
            "Days",
            options=ordered_days,
            default=["Sun"],
            key="venues_tab_days",
        )

    slot_df["day_of_week"] = slot_df["day_of_week"].fillna("").astype(str).str.slice(0, 3).str.title()
    slot_df["start_time"] = slot_df["start_time"].map(normalize_time)
    slot_df["is_available"] = pd.to_numeric(slot_df["is_available"], errors="coerce").fillna(0).astype(int)
    slot_df["assigned_division_code"] = slot_df["assigned_division_code"].fillna("").astype(str)

    if not selected_days:
        st.info("Select at least one day.")
        return

    active_slot_rows = slot_df[slot_df["day_of_week"].isin(selected_days)].copy()
    default_time_slots = ["08:30", "09:30", "10:45", "11:45", "13:00", "14:15", "15:15", "16:30"]

    venue_lookup = {}
    if not venue_df_edited.empty and "venue_id" in venue_df_edited.columns:
        temp = venue_df_edited.copy()
        if "venue_name" not in temp.columns:
            temp["venue_name"] = temp["venue_id"]
        venue_lookup = {
            str(r["venue_id"]): str(r["venue_name"]) if str(r["venue_name"]).strip() else str(r["venue_id"])
            for _, r in temp.iterrows()
            if str(r["venue_id"]).strip()
        }
    venue_ids = sorted(venue_lookup.keys())
    division_options = sorted(divisions["division_code"].dropna().astype(str).tolist()) if not divisions.empty else []
    slot_state_options = ["Inactive", "Active (Unassigned)"] + division_options

    matrix_rows = []
    for day in selected_days:
        day_rows = active_slot_rows[active_slot_rows["day_of_week"] == day]
        time_slots = sorted(day_rows["start_time"].dropna().astype(str).unique().tolist(), key=lambda t: normalize_time(t))
        if not time_slots:
            time_slots = default_time_slots

        for t in time_slots:
            row = {"Day": day, "Time": t}
            for vid in venue_ids:
                vname = venue_lookup[vid]
                mask = (
                    (active_slot_rows["venue_id"].astype(str) == vid)
                    & (active_slot_rows["day_of_week"].astype(str) == day)
                    & (active_slot_rows["start_time"].astype(str) == t)
                )
                slot_value = "Inactive"
                if mask.any():
                    is_avail = int(active_slot_rows.loc[mask, "is_available"].iloc[0]) == 1
                    assigned_div = str(active_slot_rows.loc[mask, "assigned_division_code"].iloc[0]).strip()
                    if is_avail:
                        slot_value = assigned_div if assigned_div else "Active (Unassigned)"
                row[vname] = slot_value
            matrix_rows.append(row)

    matrix_df = pd.DataFrame(matrix_rows)
    st.write("Time Slot Availability")
    if matrix_df.empty:
        st.info("No venues available for the matrix. Add venues first.")
    else:
        column_config = {
            col: st.column_config.SelectboxColumn(col, options=slot_state_options)
            for col in matrix_df.columns
            if col not in {"Day", "Time"}
        }
        matrix_df_edited = st.data_editor(
            matrix_df,
            num_rows="fixed",
            use_container_width=True,
            hide_index=True,
            key="venues_availability_matrix",
            column_config=column_config,
        )

        v1, v2, v3 = st.columns(3)
        with v1:
            if st.button("Save venues and availability"):
                venues_to_save.to_csv(DATA_DIR / "venues.csv", index=False)

                updated_slots = slot_df.copy()
                if "slot_id" not in updated_slots.columns:
                    updated_slots["slot_id"] = ""

                name_to_id = {name: vid for vid, name in venue_lookup.items()}
                updated_slots["_norm_start"] = updated_slots["start_time"].map(normalize_time)

                for _, r in matrix_df_edited.iterrows():
                    day_str = str(r.get("Day", "")).strip().title()[:3]
                    time_str = normalize_time(r["Time"])
                    hhmm = time_str.replace(":", "")
                    for venue_name, selected in r.items():
                        if venue_name in {"Day", "Time"}:
                            continue
                        vid = name_to_id.get(str(venue_name))
                        if not vid:
                            continue
                        mask = (
                            (updated_slots["venue_id"].astype(str) == vid)
                            & (updated_slots["day_of_week"].astype(str) == day_str)
                            & (updated_slots["_norm_start"].astype(str) == time_str)
                        )
                        if mask.any():
                            selected_str = str(selected).strip()
                            if selected_str == "Inactive":
                                updated_slots.loc[mask, "is_available"] = 0
                                updated_slots.loc[mask, "assigned_division_code"] = ""
                            elif selected_str == "Active (Unassigned)":
                                updated_slots.loc[mask, "is_available"] = 1
                                updated_slots.loc[mask, "assigned_division_code"] = ""
                            else:
                                updated_slots.loc[mask, "is_available"] = 1
                                updated_slots.loc[mask, "assigned_division_code"] = selected_str
                        else:
                            selected_str = str(selected).strip()
                            if selected_str == "Inactive":
                                is_available = 0
                                assigned_div = ""
                            elif selected_str == "Active (Unassigned)":
                                is_available = 1
                                assigned_div = ""
                            else:
                                is_available = 1
                                assigned_div = selected_str
                            new_row = {
                                "slot_id": f"{vid}-{day_str.upper()}-{hhmm}",
                                "venue_id": vid,
                                "day_of_week": day_str,
                                "start_time": time_str,
                                "is_available": is_available,
                                "is_preferred": 1,
                                "notes": "",
                                "assigned_division_code": assigned_div,
                                "_norm_start": time_str,
                            }
                            updated_slots = pd.concat([updated_slots, pd.DataFrame([new_row])], ignore_index=True)

                updated_slots = updated_slots.drop(columns=["_norm_start"], errors="ignore")
                updated_slots.to_csv(DATA_DIR / "venue_slots.csv", index=False)
                st.success("Saved data/venues.csv and data/venue_slots.csv")
        with v2:
            st.download_button(
                "Download venues CSV",
                data=venues_to_save.to_csv(index=False),
                file_name="venues.csv",
                mime="text/csv",
            )
        with v3:
            st.download_button(
                "Download venue slots CSV",
                data=slot_df.drop(columns=["_norm_start"], errors="ignore").to_csv(index=False),
                file_name="venue_slots.csv",
                mime="text/csv",
            )


def render_teams_tab(
    teams: pd.DataFrame,
    divisions: pd.DataFrame,
) -> None:
    st.subheader("Divisions")
    st.caption("Manage division team mappings and team records used by the scheduler.")
    st.write("Division Team Mapping")

    division_options = sorted(divisions["division_code"].tolist()) if not divisions.empty else []
    if division_options:
        mapping_division = st.selectbox("Team Number Mapping Division", options=division_options, index=0, key="teams_mapping_division")
        mapping_df = get_division_teams(teams, mapping_division)
        st.write("Team Number Mapping")
        st.dataframe(
            mapping_df,
            use_container_width=True,
            hide_index=True,
            height=420,
            column_config={
                "team_number": st.column_config.NumberColumn("team_number", width="small"),
                "team_id": st.column_config.TextColumn("team_id", width="small"),
                "team_display_name": st.column_config.TextColumn("team_display_name", width="large"),
                "coach_last_name": st.column_config.TextColumn("coach_last_name", width="medium"),
            },
        )


def load_or_default_considerations() -> pd.DataFrame:
    if CONSIDERATIONS_FILE.exists():
        df = pd.read_csv(CONSIDERATIONS_FILE)
    else:
        df = pd.DataFrame(
            columns=[
                "rule_id",
                "rule_name",
                "league_code",
                "league_scope",
                "team_id",
                "team_name",
                "coach_name",
                "request_type",
                "applies_to",
                "specific_dates",
                "weekdays",
                "time_rule",
                "time_value",
                "priority",
                "status",
                "summary",
                "created_at",
            ]
        )
    return df


def blank_consideration_wizard_state(default_division: str = "") -> dict:
    return {
        "rule_name": "",
        "league_code": default_division,
        "league_scope": [default_division] if default_division else [],
        "team_id": "",
        "team_name": "",
        "coach_name": "",
        "request_type": "Coach Request",
        "applies_to": "All games",
        "specific_dates": "",
        "weekdays": [],
        "time_rule": "Prefer later games",
        "time_value": "",
        "priority": "High",
        "status": "Active",
    }


def build_consideration_summary(d: dict) -> str:
    league = d.get("league_code", "").strip() or ", ".join(d.get("league_scope", [])) or "All leagues"
    team_name = d.get("team_name", "").strip()
    coach = d.get("coach_name", "").strip() or "Unknown coach"
    applies_to = d.get("applies_to", "All games")
    scope_parts = [applies_to]
    if d.get("specific_dates", "").strip():
        scope_parts.append(f"Dates: {d['specific_dates'].strip()}")
    if d.get("weekdays"):
        scope_parts.append(f"Weekdays: {', '.join(d['weekdays'])}")
    scope_text = " | ".join(scope_parts)
    time_rule = d.get("time_rule", "").strip()
    time_value = d.get("time_value", "").strip()
    time_text = f"{time_rule}{(': ' + time_value) if time_value else ''}"
    subject = f"{team_name} / {coach}" if team_name else coach
    return f"{subject} ({league}) | {scope_text} | {time_text}"


def render_schedule_considerations_tab(divisions: pd.DataFrame, teams: pd.DataFrame) -> None:
    st.subheader("Constraints")
    st.caption("Track coach requests and special scheduling constraints to consider during schedule creation.")
    division_codes = sorted(divisions["division_code"].dropna().astype(str).tolist()) if not divisions.empty else []
    st.write("Create Constraint")
    with st.form("create_constraint_form"):
        top1, top2, top3 = st.columns(3)
        with top1:
            request_type = st.selectbox("Request Type", options=["Coach Request", "Venue Constraint"])
        with top2:
            league_code = st.selectbox("League", options=division_codes if division_codes else [""])
        with top3:
            league_teams = teams[teams["division_code"] == league_code].copy() if (not teams.empty and league_code) else pd.DataFrame()
            team_options = league_teams["team_id"].astype(str).tolist() if not league_teams.empty else []
            team_label_map = {}
            for _, tr in league_teams.iterrows():
                tid = str(tr.get("team_id", ""))
                display_name = str(tr.get("team_display_name", tid))
                team_label_map[tid] = f"{display_name} ({tid})"
            team_id = st.selectbox("Team", options=team_options if team_options else [""], format_func=lambda x: team_label_map.get(x, x))

        coach_name = ""
        team_name = ""
        if team_options and team_id:
            selected_team_row = league_teams[league_teams["team_id"].astype(str) == team_id]
            if not selected_team_row.empty:
                team_name = str(selected_team_row.iloc[0].get("team_display_name", team_id))
                coach_name = str(selected_team_row.iloc[0].get("coach_last_name", "")).strip()

        apply_options = ["All games", "Specific dates", "Specific weekdays", "Specific dates and weekdays"]
        time_rule_options = [
            "Prefer later games",
            "Prefer earlier games",
            "No games before time",
            "No games after time",
            "Prefer specific slot(s)",
            "Avoid specific slot(s)",
            "Between time window",
            "Custom",
        ]
        row1, row2, row3 = st.columns(3)
        with row1:
            applies_to = st.selectbox("Applies to", options=apply_options)
        with row2:
            time_rule = st.selectbox("Time rule", options=time_rule_options)
        with row3:
            time_value = st.text_input("Time value (optional)", help="Examples: 15:15, 14:15-16:30, 08:30,09:30")

        specific_dates = ""
        weekdays: list[str] = []
        if applies_to in {"Specific dates", "Specific dates and weekdays"}:
            specific_dates = st.text_input("Specific date(s) (comma-separated YYYY-MM-DD)")
        if applies_to in {"Specific weekdays", "Specific dates and weekdays"}:
            weekday_options = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
            weekdays = st.multiselect("Weekday(s)", options=weekday_options, default=["Sun"])

        rule_name = st.text_input("Rule name")

        submitted = st.form_submit_button("Save Constraint")

    if submitted:
        current = load_or_default_considerations()
        next_id = 1 if current.empty else int(pd.to_numeric(current["rule_id"], errors="coerce").fillna(0).max()) + 1
        row = {
            "rule_id": next_id,
            "rule_name": rule_name.strip() or f"Rule {next_id}",
            "league_code": str(league_code).strip(),
            "league_scope": str(league_code).strip(),
            "team_id": str(team_id).strip(),
            "team_name": team_name.strip(),
            "coach_name": coach_name.strip(),
            "request_type": request_type,
            "applies_to": applies_to,
            "specific_dates": specific_dates.strip(),
            "weekdays": ",".join(weekdays),
            "time_rule": time_rule,
            "time_value": time_value.strip(),
            "priority": "High",
            "status": "Active",
            "summary": build_consideration_summary(
                {
                    "league_code": str(league_code).strip(),
                    "team_name": team_name.strip(),
                    "coach_name": coach_name.strip(),
                    "applies_to": applies_to,
                    "specific_dates": specific_dates.strip(),
                    "weekdays": weekdays,
                    "time_rule": time_rule,
                    "time_value": time_value.strip(),
                }
            ),
            "created_at": pd.Timestamp.now().isoformat(timespec="seconds"),
        }
        updated = pd.concat([current, pd.DataFrame([row])], ignore_index=True)
        updated.to_csv(CONSIDERATIONS_FILE, index=False)
        st.success("Saved constraint to data/schedule_considerations.csv")

    st.divider()
    st.write("Saved Rules")
    rules_df = load_or_default_considerations()
    edited_rules = st.data_editor(rules_df, num_rows="dynamic", use_container_width=True, key="considerations_table_editor")
    c1, c2 = st.columns(2)
    with c1:
        if st.button("Save Rules Table"):
            edited_rules.to_csv(CONSIDERATIONS_FILE, index=False)
            st.success("Saved data/schedule_considerations.csv")
    with c2:
        st.download_button(
            "Download Rules CSV",
            data=edited_rules.to_csv(index=False),
            file_name="schedule_considerations.csv",
            mime="text/csv",
        )


def render_saved_schedules_tab(divisions: pd.DataFrame) -> None:
    st.subheader("Saved Schedules")
    st.caption("Schedules saved from Schedule Builder.")

    table = load_saved_schedules_from_db()
    if table.empty:
        st.info("No saved schedules yet. Click 'Save Schedule' in Schedule Builder.")
    else:
        shown = table[["schedule_id", "division", "weekends", "created_on"]].copy()

        event = st.dataframe(
            shown,
            use_container_width=True,
            hide_index=True,
            selection_mode="single-row",
            on_select="rerun",
            key="saved_schedules_table",
        )

        selected_schedule_id = None
        selected_rows = event.selection.rows if event and event.selection else []
        if selected_rows:
            row_idx = selected_rows[0]
            if row_idx is not None and 0 <= row_idx < len(shown):
                selected_schedule_id = int(shown.iloc[row_idx]["schedule_id"])

        if selected_schedule_id is not None:
            st.write(f"Schedule Games (Schedule ID: {selected_schedule_id})")
            games = load_saved_schedule_games_from_db(selected_schedule_id)
            if games.empty:
                st.info("No saved games found for this schedule.")
            else:
                gf1, gf2 = st.columns(2)
                with gf1:
                    week_options = sorted(pd.to_numeric(games["week_no"], errors="coerce").dropna().astype(int).unique().tolist())
                    selected_weeks = st.multiselect("Week Number", options=week_options, default=[])
                with gf2:
                    team_options = sorted(
                        set(games["home_team_name"].dropna().astype(str).tolist())
                        | set(games["away_team_name"].dropna().astype(str).tolist())
                    )
                    selected_team = st.selectbox("Team", options=["All Teams"] + team_options, index=0)

                filtered_games = games.copy()
                if selected_weeks:
                    filtered_games = filtered_games[filtered_games["week_no"].isin(selected_weeks)]
                if selected_team != "All Teams":
                    filtered_games = filtered_games[
                        (filtered_games["home_team_name"].astype(str) == selected_team)
                        | (filtered_games["away_team_name"].astype(str) == selected_team)
                    ]

                st.dataframe(
                    filtered_games[["division_code", "week_no", "time_slot", "home_team_name", "away_team_name"]],
                    use_container_width=True,
                    hide_index=True,
                    height=260,
                )
        else:
            st.caption("Select a saved schedule row to view its games.")


def main() -> None:
    st.set_page_config(page_title="League Scheduler Workbench", layout="wide")
    apply_tooltip_position_css()
    st.title("Schedule Manager")
    st.caption("Add all your league data and auto-generate this season's schedule")
    init_db()

    divisions = load_csv("divisions.csv")
    teams = load_csv("teams.csv")
    venues = load_csv("venues.csv")
    venue_slots = prep_venue_slots(load_csv("venue_slots.csv"))
    team_coaches = load_csv("team_coaches.csv")
    division_venue_prefs = load_csv("division_venue_preferences.csv")
    division_slot_prefs = load_csv("division_slot_preferences.csv")
    programs, program_divisions = ensure_program_models(divisions)

    program_options = (
        programs[programs["is_active"] == 1]["program_id"].astype(str).tolist()
        if not programs.empty and "program_id" in programs.columns
        else []
    )
    if not program_options and not programs.empty:
        program_options = programs["program_id"].astype(str).tolist()
    if not program_options:
        program_options = ["CYB-2025-26"]

    program_name_map = (
        {
            str(r["program_id"]): str(r["program_name"])
            for _, r in programs.iterrows()
            if str(r.get("program_id", "")).strip()
        }
        if not programs.empty
        else {"CYB-2025-26": "2025-26 CYB Season"}
    )
    selected_program_id = st.selectbox(
        "Season",
        options=program_options,
        index=0,
        format_func=lambda x: program_name_map.get(str(x), str(x)),
        key="selected_program_id",
    )

    scoped_divisions = divisions_for_program(divisions, program_divisions, selected_program_id)

    tab0, tab1, tab2, tab3, tab4 = st.tabs(
        ["Divisions", "Venues", "Constraints", "Schedule Builder", "Saved Schedules"]
    )
    with tab0:
        render_teams_tab(teams, divisions)
    with tab1:
        render_venues_tab(venues, venue_slots, scoped_divisions)
    with tab2:
        render_schedule_considerations_tab(scoped_divisions, teams)
    with tab3:
        render_division_builder(scoped_divisions, teams, venue_slots)
    with tab4:
        render_saved_schedules_tab(scoped_divisions)


if __name__ == "__main__":
    main()
