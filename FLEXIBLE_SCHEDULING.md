# Flexible Scheduling Input (No Fixed Season Rules)

The scheduler should not hard-code game calendar logic.
Instead, each run should receive explicit playable dates.

## Runtime input
Use `data/game_day_inputs.csv` (copy from example) with these fields:

- `game_date` (`YYYY-MM-DD`)
- `day_of_week` (informational/check)
- `enabled` (`1` use date, `0` skip date)
- `notes` (optional)

## How this keeps scheduling flexible
- Any season can start on any date.
- Holiday breaks and blackout weekends are handled by setting `enabled=0`.
- Number of game weeks is inferred from enabled dates, not a rule table.

## Current data model impact
- Keep venue/time templates in `data/venue_slots.csv`.
- Combine enabled `game_date` rows with venue time slots at runtime to create schedulable dated slots.
- No `season_rules` or `season_weeks` tables are required.
