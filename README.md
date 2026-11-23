# League Scheduler Workbench (MVP)

## Run

```bash
cd /Users/kitswain/Documents/CYB
python3 -m pip install --user -r requirements.txt
python3 -m streamlit run app.py
```

Open: [http://localhost:8501](http://localhost:8501)

## End-to-end workflow

### Tab 1: Division Builder (recommended first step)

1. Select one division (example: `B3`).
2. Set number of weekends (example: `10`).
3. Set number of teams and optional generator seed.
4. Enter time slots.
5. Click **Generate random matchups** (then edit cells as needed).
6. Fill/adjust the grid with team numbers (`3 vs 4`).
7. Review grid validation and home/away balance.
8. Download division-level CSV outputs.

### Tab 2: General Workbench

1. Enter or upload game dates (`game_day_inputs.csv` style).
2. Review generated dated available slots.
3. Click **Auto-generate draft schedule**.
4. Adjust schedule manually if needed.
5. Edit validation rules in-app (enable/disable, severity).
6. Review validation report.
7. Download:
- raw schedule CSV
- validation report CSV
- friendly upload CSV (`schedule_friendly_export.csv`)

### Dated Scheduler

Use the Dated Scheduler tab for season dates and dated availability. Season calendars use
`data/game_day_inputs_<program_id>.csv` with a `week_no` column; dates with the same week number
are treated as one round. Optional one-off availability is stored in
`data/special_venue_slots_<program_id>.csv` and overrides the recurring gym slots on those dates.

## Data files

- Core model: `data/divisions.csv`, `data/teams.csv`, `data/coaches.csv`, `data/venues.csv`
- Availability/preferences: `data/venue_slots.csv`, `data/division_venue_preferences.csv`, `data/division_slot_preferences.csv`
- Rule config: `data/validation_rules.csv`
