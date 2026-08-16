# Slice 6A — CGF reconciliation investigation

CGF has one Runway (`6/24`), legacy Installations 54 (FAA aggregate), 144
(2018 end 06, 322 ft), and 145 (2018 end 24, 435 ft), with no incidents or
signals. Dates remain distinct: 2018 claimed completion, 2020 publication,
and NASR effective 2026-08-06.

| Assertion | Claim | Evidence |
|---|---|---|
| 54 | FAA Tableau EMASMAX at CGF | aggregate, partial/reviewed |
| 101 | 2018 completed bed at end 06 | Cuyahoga County, direct strong |
| 102 | 2018 completed bed at end 24 | Cuyahoga County, direct strong |
| 198 | EMAS at 06/24 end 06 | FAA NASR, direct strong |
| 199 | EMAS at 06/24 end 24 | FAA NASR, direct strong |

End 06 is **ESTABLISHED**: assertions 101 and 198 are compatible direct
airport/runway-end evidence. End 24 is independently **ESTABLISHED** by 102
and 199. Explicitly different ends establish different systems; no conclusion
depends on airport/type/year. The result is two physical EMAS installations,
at ends 06 and 24.

Legacy 54 is aggregate-only and maps directly to zero systems. Legacy 144 is
supported by 101/198; 145 by 102/199. No row is conflicting, duplicate, or
unsupported. No preserved evidence establishes vendor, replacement, repair,
removal, or lifecycle; NASR presence is not lifecycle evidence. The `6/24` /
`06/24` formatting compatibility requires future review, not ingestion inference.

The minimum future persistence is `InstallationAssertionLink`: assertion,
physical target, SAME/DIFFERENT/UNRESOLVED outcome, reason, actor, timestamp,
review state and supersession. A future human may confirm these two pairs;
automation may only propose them. This does not restore the former workflow.

## Verdict

1. Two installations are established at CGF.
2. They belong to ends 06 and 24.
3. End 06: assertions 101/198; end 24: 102/199; 54 is aggregate-only.
4. No legacy Installation is a proven duplicate or unsupported.
5. Evidence is sufficient for a future human-reviewed, non-destructive
   reconciliation persistence slice; no reconciliation was performed here.
