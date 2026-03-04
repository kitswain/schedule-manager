CREATE TABLE team_coaches (
  team_id TEXT NOT NULL,
  coach_id TEXT NOT NULL,
  is_primary INTEGER NOT NULL DEFAULT 1,
  PRIMARY KEY (team_id, coach_id),
  FOREIGN KEY (team_id) REFERENCES teams(team_id),
  FOREIGN KEY (coach_id) REFERENCES coaches(coach_id)
);
