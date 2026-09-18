# Market coverage capture plans

The coverage CLI reports a capture as complete only when `--capture-plan` names the event universe and tabs and the supplied payloads cover their exact Cartesian product once each. A complete capture is compared with a prior report only when both reports have the same deterministic scope ID.

```json
{
  "schema_version": 1,
  "sport": "NFL",
  "sportsbook": "FANDUEL",
  "event_ids": ["35610167"],
  "tabs": ["popular", "player-passing"],
  "discovery_artifact": "nfl-events-2026-09-12.json",
  "discovery_sha256": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
}
```

Each payload argument must include its tab as `TAB=PATH`. Event identity is read from `attachments.events` and `attachments.markets`; a payload without event identity fails closed. Missing, unexpected, or duplicate event-tab pairs also fail. The discovery artifact name and digest preserve the provenance of the event universe without committing raw sportsbook data.

The scope proves completeness only for the named event universe and tabs. It does not claim permanent coverage of every FanDuel market, future slate, or sportsbook.

Alligator
