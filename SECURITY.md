# Security policy

Squire only talks to a model backend you configure (default `http://127.0.0.1:11434`). It sends
nothing anywhere else and collects no telemetry. Text you pipe through it goes to that backend, so
don't point it at a remote server you don't control.

**Reporting a vulnerability:** use GitHub's private "Report a vulnerability" advisory on this
repository. Please don't open a public issue. Expect an acknowledgement within 7 days.
