/* =====================================================================
 * Row-identity tests for content.js
 *
 * Run:  node test/keys.test.mjs       (from carnow-auto-claimer/)
 *
 * These cover the two defects that would have caused WRONG CLAIMS —
 * leads belonging to other salespeople getting tapped by the extension.
 * The regexes are lifted out of content.js at runtime rather than copied,
 * so this suite cannot silently drift from the implementation.
 * ===================================================================== */

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const here = dirname(fileURLToPath(import.meta.url));
const src = readFileSync(join(here, '..', 'content.js'), 'utf8');

const VOLATILE_PATTERNS = eval(src.match(/const VOLATILE_PATTERNS = (\[[\s\S]*?\]);/)[1]);
const ABS_TIME_RE = eval(src.match(/const ABS_TIME_RE = (\/.*?\/i);/)[1]);
const REL_TIME_RE = eval(src.match(/const REL_TIME_RE = (\/.*?\/i);/)[1]);

const norm = (s) => (s || '').replace(/\s+/g, ' ').trim();

const stripVolatile = (text) => {
  let out = text;
  for (const re of VOLATILE_PATTERNS) out = out.replace(re, ' ');
  return out.replace(/\s+/g, ' ').trim();
};

/** Mirrors rowKey()'s cell path: a cell holding a link keys on link text. */
const keyOf = (cells) => 'cells=' + cells
  .map((c) => stripVolatile(typeof c === 'object' ? (c.link || c.text) : c))
  .filter(Boolean).join('|').slice(0, 200);

/** Mirrors assigneeState() over a row's leaf texts. */
function assigneeState(leaves) {
  for (const text of leaves.map(norm)) {
    if (!text || text.length > 80) continue;
    let m = text.match(/^(.*?)\(([^()]{3,})\)$/);
    if (!m) m = text.match(/^(.*?)\([^()]*$/);
    if (!m) continue;
    return m[1].trim() ? 'assigned' : 'unassigned';
  }
  return 'unknown';
}

function ageMin(text) {
  const rel = norm(text).match(REL_TIME_RE);
  if (rel) {
    const n = +rel[1], u = rel[2].toLowerCase();
    const mult = u.startsWith('sec') ? 1e3 : u.startsWith('min') ? 6e4
               : (u.startsWith('hour') || u.startsWith('hr')) ? 36e5 : 864e5;
    return Math.round(n * mult / 60000);
  }
  const abs = norm(text).match(ABS_TIME_RE);
  if (!abs) return null;
  let h = +abs[4] % 12;
  if (abs[6].toLowerCase() === 'p') h += 12;
  const d = new Date(+abs[3], +abs[1] - 1, +abs[2], h, +abs[5]);
  return Math.round((Date.now() - d.getTime()) / 60000);
}

let failed = 0;
const check = (name, got, want) => {
  const ok = got === want;
  if (!ok) failed++;
  console.log(`${ok ? '  PASS' : '  FAIL'}  ${name}` +
    (ok ? '' : `\n        got:  ${JSON.stringify(got)}\n        want: ${JSON.stringify(want)}`));
};
const section = (t) => console.log('\n' + t);

/* --- Regression: a coworker claiming a row must not re-key it --------- */
section('A coworker claims a baselined lead');
const coworkerBefore = [
  { link: 'Robby Johnson', text: 'Robby Johnson (Nashville Toyota North)' },
  '2024 Toyota Tundra 1794 Stock # 2609961', '', '', 'Pre-Owned Sales SMS', '09/07/2026 06:42 pm'
];
const coworkerAfter = [
  { link: 'Robby Johnson', text: 'Robby Johnson Thiago H (Nashville Toyota North)' },
  '2024 Toyota Tundra 1794 Stock # 2609961', '', '', 'Pre-Owned Sales SMS', '09/07/2026 06:45 pm'
];
check('key survives the row gaining a rep', keyOf(coworkerBefore), keyOf(coworkerAfter));

/* --- Regression: refreshes must not re-key a row ---------------------- */
section('The list refreshes');
const ken = (stamp) => [
  { link: 'Ken Mick', text: 'Ken Mick Jacob Werries (Na...' },
  '2026 Toyota Camry SE Stock # 2611220', '', '', 'New Car Sales Desktop Browser', stamp
];
check('key survives a Last Update tick',
  keyOf(ken('09/09/2026 09:55 am')), keyOf(ken('09/11/2026 02:31 pm')));

const conv = (unread, stamp) => [
  `${unread} Benton_747335 Sergio F (Nashville Toyota North)`, 'Active', stamp, '', 'New Car Sales Mobile Phone'
];
check('key survives an unread-badge change',
  keyOf(conv('6 0', '09/10/2026 07:03 pm')), keyOf(conv('9 2', '09/10/2026 07:14 pm')));
check('key survives a relative-time change',
  keyOf(['Elizabethtown_217053 Jacob Werries', 'Closed 2d ago']),
  keyOf(['Elizabethtown_217053 Jacob Werries', 'Closed 3d ago']));

/* --- Keys must still discriminate ------------------------------------ */
section('Keys stay distinct and keep their identifiers');
check('different leads get different keys', keyOf(ken('09/09/2026 09:55 am')) !== keyOf(coworkerBefore), true);
check('lead id survives stripping', keyOf(conv('6 0', '09/10/2026 07:03 pm')).includes('Benton_747335'), true);
check('stock number survives stripping', keyOf(coworkerBefore).includes('2609961'), true);
check('vehicle year survives stripping', keyOf(coworkerBefore).includes('2024'), true);

/* --- Assignee gate --------------------------------------------------- */
section('Assignee gate');
check('unclaimed row', assigneeState(['Robby Johnson', '(Nashville Toyota North)']), 'unassigned');
check('claimed row', assigneeState(['Robby Johnson', 'Thiago H (Nashville Toyota North)']), 'assigned');
check('truncated claimed row (All Leads)', assigneeState(['Ashburn_199...', 'Johnson Fabiyi (Na...']), 'assigned');
check('truncated unclaimed row (All Leads)', assigneeState(['Robby Johnson', '(Nashville Toyota N...']), 'unassigned');
check('no assignee pattern', assigneeState(['Some Row', 'no parens here']), 'unknown');

/* --- Freshness gate -------------------------------------------------- */
section('Freshness gate');
const now = new Date();
const pad = (n) => String(n).padStart(2, '0');
const stamp = `${pad(now.getMonth() + 1)}/${pad(now.getDate())}/${now.getFullYear()} ` +
  `${pad(now.getHours() % 12 || 12)}:${pad(now.getMinutes())} ${now.getHours() < 12 ? 'am' : 'pm'}`;
check('a just-now timestamp reads fresh', ageMin(stamp) <= 1, true);
check('a dated backlog row reads stale', ageMin('09/09/2026 09:55 am') > 5, true);
check('"3 minutes ago" parses', ageMin('Updated 3 minutes ago'), 3);
check('"2 days ago" reads stale', ageMin('2 days ago') > 5, true);
check('no timestamp returns null', ageMin('no timestamp here'), null);

console.log(`\n${failed === 0 ? 'ALL PASS' : failed + ' FAILURE(S)'}\n`);
process.exit(failed ? 1 : 0);
