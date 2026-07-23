# tests/contract

Contract tests validating each connector's parsed output against the recorded
sample payloads in `tests/fixtures`, catching upstream schema drift (spec §36
"invalid payload" handling) before it reaches staging.
