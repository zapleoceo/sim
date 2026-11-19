# Live session open retry boundary

A call may retry at most once, while still ringing, only when the adapter explicitly
marks its failure retry-safe. BrokerLiveVoice marks only TimeoutError and
ConnectionRefusedError from connect(), before any start send is attempted.
Unknown connect failures, HTTP/authorization failures, and every error after entering
send(start) fail closed. A send exception does not prove that start was not delivered.
Auth/quota/budget refusals are not retried, even if the server might later recover.

No routes, access controls, prompts, provider selection, or successful-session teardown
are changed. Failed startup sockets retain the existing close behavior. A socket close
is not proof the remote paid session stopped; that requires a Broker contract.
Cancellation cleanup and malformed ready fields remain outside this narrow patch.

Tradeoff: transient errors other than the two explicit pre-start types do not recover
automatically. Retrying after start requires a verified idempotency or no-session-created
contract, not error-string matching. This change prevents blind duplicate starts; it does
not fix current provider availability, latency, quota, or call audio quality.

Validation: fake regression cases added to test_calls.py for ambiguous send, lost ready,
malformed ready, auth/quota/budget, unknown/auth connect, transient connect, and caller gone.
Existing successful teardown and failed answer tests remain. Local tests/lint/build have
NOT run, per AGENTS.md. Required CI on the exact future commit must pass before merge.
Publication is a separate draft PR; no deployment or live provider test is needed for CI.
