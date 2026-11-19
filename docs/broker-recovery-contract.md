# Broker recovery contract evidence

Read-only inspection of the server checkout `/var/www/aibroker` on 2026-11-16:
commit `df4460a5bbe3a3e7311861e024e1ca1b1c893156`. No tracked changes were reported.
This is source evidence, not a live model/API probe or proof of the running image.

* `src/aibroker/routes/jobs_control.py:100-180`: `GET /v1/jobs` requires exactly
  one `label.<key>=<value>` pair and scopes the lookup to the authenticated project.
  JobListItem exposes labels and idempotency_key. JobListResponse exposes jobs,
  pagination_complete and next_cursor. Thus `label.sim_request=<key>&limit=2`
  matches the implemented route; incomplete/ambiguous/missing results fail closed.
* `src/aibroker/services/job_list.py:96-114,155-190`: filters project and exact
  label, serializes both identity fields, computes next_cursor and `not more`.
  Unauthorized agent jobs are excluded, so an empty list cannot prove absence.
* `src/aibroker/routes/proxy.py:894-1041`: `_replay` loads an existing accepted
  job and calls `_require_agent_read_access`, which can reject access. The submit
  route also checks auth/capability/image access before looking up a known key.
  Therefore a 4xx is not a universal guarantee that this key never created a job.
  SIM must not rotate keys simply on HTTP 400/401/402/403/422.
* `src/aibroker/agent/runs.py:94-161`: job idempotency lookup precedes insertion;
  key races resolve to the winning job. Labels/key are persisted on DeepJobRow.
* `src/aibroker/services/job_queue.py:462-484`: retention deletes terminal job
  rows, including their keys. Reposting a pruned key is unsafe. SIM performs a
  read-only identity lookup instead and blocks when the retained receipt is absent.

## Real conversation replay

SIM `ConversationService.dialog_context` reads the current clock on each attempt.
`prompt.dynamic_context` includes minute-resolution UTC; persona_clock additionally
computes local date, minute, age and routine. Exact serialized-request hashing thus
changes on a normal 90-second recovery with no new message. Guard additionally uses
full ISO time and must not reuse an old approval without current trusted facts.

Generation now provides a separate logical identity built by the same prompt builder
with a canonical clock used ONLY for hashing. History, context, instructions,
persona/routine/timezone/birth date and dialog activity revision remain in the
identity; model/capability/JSON/options remain in the adapter hash. Relative delay
wording is excluded from identity. Original accepted messages are preserved for
recovery, while the guard is rebuilt with the real current time. New messages or
settings cause a fresh generation only after prior remote jobs settle. Repairs carry
the rejected draft and guard feedback in their own identity. Synthetic tests run the
real conversation handler across 91 seconds and a UTC date boundary in Berlin and
Vietnam, and distinguish clock-only recovery from new input/settings.

`CurrentContactProfile.read` also emits an observation timestamp, including for
`not_enabled`. It now returns an explicit timestamp-free identity text from the
same structured status/fields (and preserves photo bytes), rather than stripping
patterns from user-authored text. The actual prompt still uses the observation
timestamp. Real run_reply regression includes a profile request; unit coverage
checks that fields/status/photo changes and dates inside bio are not erased.

## Time-driven context boundaries

The reuse guarantee covers unchanged effective context with different computed clock
facts or profile observation time. It does not freeze the entire conversation snapshot.
ContextWindow can drop messages at its configured age boundary; persona_media_tool.busy
can change the media offer when gap_min expires. These intentional context changes may
start a new generator job after previously accepted requests settle. An uncertain outcome
still blocks submissions. Raw call incidents are audit-only; the real run_reply regression
checks that an audit-only incident does not change input or re-purchase a generation.
Only a recent answered call may contribute the sanitized fact that some speech may not
have played. Its expiry can still change the effective context, not the audit history.
Thus this increment does not promise zero additional generation cost for every
time-only restart. Guard always uses fresh time, including when generation is reused.


## Reply worker exhaustion with an unresolved Broker request

A local polling deadline is not a provider verdict or billing limit. While the
reply job has worker attempts left, the existing backoff retries the same queue
identity, reconciling its durable Broker intent before any new submission. A
known remote ID is polled; if the submission receipt is missing, recovery uses
read-only correlation. Poll errors and missing receipts are not a reason to
resubmit or switch models.

If the worker exhausts its existing attempt budget while a Broker intent remains
unresolved, the job stays failed with its last error and durable intent intact.
The worker trace records `broker_reconciliation_required`. This does not pause
the dialog, set its operator flag, change any existing manual pause or opt-out,
or send an unreviewed response. There is no automatic replay after exhaustion;
reconcile the original remote outcome rather than blindly requeueing it.

This is separate from a genuine conversational or guard handoff. The existing
configured reserve remains restricted to a confirmed terminal Broker `error`
with `model_unavailable` or `capacity_busy` and an available subscription route
listed for the same project. An ambiguous pending or timeout does not qualify.
