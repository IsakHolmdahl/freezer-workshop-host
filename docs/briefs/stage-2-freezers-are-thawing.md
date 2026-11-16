# Stage 2: the freezers are thawing

*Hand this to a team when its store turns red on the map.*

## What happened

Software 2.0.0 lets service technicians maintain freezers remotely. A freezer does
not decide by itself: it forwards every maintenance request to your store's cloud
service, which approves or rejects it (see "Maintenance requests" in
`docs/store-api.md`).

Since your rollout finished, somebody who is not a technician has been sending
requests, and your service approves them. Freezers have been set to +50 °C. The same
thing has happened to real freezers in real stores.

## What you must achieve

- Your service rejects every request that should not be carried out.
- Your service keeps approving the requests of genuine technicians. A store that
  rejects everything cannot be serviced, and that counts against you.
- The fix is in your repository, with tests, made by your factory. As before, you do
  not edit product code yourselves.

## What you should know

- The attacker does not try one thing. New kinds of request arrive a few minutes
  apart, each using a different weakness. Fixing the first one you see is not the end.
  Ask for a review of how requests are approved, not for one bug fix.
- Not running your service is not a defence. Unanswered requests expire, and the
  genuine technicians are then locked out as well.
- Your factory may not do what you ask this time. Read what the agents answer and
  what they escalate. If the factory is missing something, adding it is your job:
  that is what a factory engineer does. `docs/templates-and-skills.md` in the factory
  repository explains how roles and skills are built and published.

## Done when

The map shows all four waves of your store as repelled (green), no freezer is
thawing, and no genuine request is being rejected.
