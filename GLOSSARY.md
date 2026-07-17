# RWI Glossary

This glossary defines the language used throughout the RWI platform.

Every entity, process and concept should have one clear definition.

---

# Core Entities

## Airport

A physical airport.

An airport contains one or more runways and is the highest operational entity in RWI.

Examples

- Aspen (ASE)
- JFK
- Manchester

---

## Runway

A physical runway belonging to one airport.

A runway has one or two runway ends.

Examples

- 15/33
- 04L/22R

---

## Runway End

One end of a runway.

A runway end may contain:

- EMAS
- RESA
- Localizer
- Lighting
- Obstacles

Examples

Runway 15

Runway 33

---

## EMAS Bed

One physical EMAS installation protecting one runway end.

One runway may contain two EMAS Beds.

Status examples

- Existing
- Planned
- Under Construction
- Removed

---

## Project

A planned or ongoing activity affecting airport infrastructure.

Examples

- EMAS installation
- Runway extension
- Airport modernization
- Safety improvement

A project may contain multiple events.

---

## Document

A published document.

Examples

- Airport Layout Plan
- Environmental Assessment
- FAA Report
- County Resolution
- Master Plan
- Capital Improvement Plan

Documents never change.

A new revision creates a new document.

---

## Source

The publisher or origin of information.

Examples

- FAA
- Airport Authority
- County
- ICAO
- Runway Safe

---

# Knowledge Model

## Observation

Information extracted from a document.

An observation is NOT yet considered verified.

Example

"Page 19 contains the text EMAS."

---

## Fact

Verified information supported by one or more sources.

Example

"Aspen Airport plans two EMAS Beds."

Every Fact must have at least one Source.

---

## Event

Something that happened at a specific point in time.

Examples

- ALP approved
- Funding granted
- Procurement published
- Construction started
- Project completed

---

## Intelligence

Knowledge generated from verified facts.

Intelligence may contain

- Score
- Recommendation
- Risk
- Opportunity
- Confidence

Intelligence never replaces facts.

---

## Research Note

Internal analyst notes.

Research Notes are hypotheses, ideas or observations.

They are never treated as verified facts.

---

# Verification

## Verification

The process of converting an Observation into a Fact.

Verification requires one or more trusted sources.

---

# Scoring

## RWI Score

A calculated value representing the likelihood or maturity of a project.

Scores are generated from rules.

Scores are never entered manually.

Every score must be explainable.

---

# Timeline

## Timeline

A chronological view of Events for one Airport, Project or Runway.

History is never deleted.

---

# Golden Rule

Documents create Observations.

Observations become Facts.

Facts create Intelligence.