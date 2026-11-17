# Stage 3: an energy and doors screen

## Why

The store manager wants to know which freezers cost the most to run and whether
doors are left open. The store API has the numbers: `GET /energy` gives energy use,
door openings and average temperature per freezer and hour (see "Energy" in
`docs/store-api.md`).

## What to build

A new screen in your cloud service, reachable from the existing dashboard:

1. Energy use per freezer for the last 24 hours, so the freezers can be compared.
2. Door openings per freezer for the same period.
3. How energy use changes over the day for the whole store.
4. The freezer that stands out is easy to spot without reading every number.
5. The screen looks like it belongs to the product: the same colours, type, spacing,
   cards and tables as the existing dashboard. A visitor should not be able to tell
   that it was built later.

The data comes through your service, not straight from the browser to the store API,
and the new code has tests.

## Done when

An organiser has looked at the screen next to your dashboard and marked the stage as
done on the map.

## Then: do it again, cheaper

Improve the factory, then run this
same task again from a fresh branch and compare the two runs on the
**Run comparison** dashboard in Grafana: tokens per run, tokens by role, rework and
friction. The goal is the same screen, or a better one, for fewer tokens.
