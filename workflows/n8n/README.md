# n8n workflows

Empty for now, on purpose. Nothing is committed here until it has been
imported into a running n8n and seen to work: an untested workflow export is
the first thing a reviewer would import, and it would fail in front of them.

Docker is not installed on the development machine yet, so n8n has not run.
`compose.yaml` mounts this directory into the container at `/workflows`,
read-only, so exports can be imported from the n8n interface once it is up.

Planned, in order:

1. **Process a submission.** Receive a submission id, read
   `GET /submissions/{id}`, stop if the outcome is already settled, call
   `POST /submissions/{id}/process`, then route on the outcome: validated,
   needs review, duplicate, unsupported, or a technical failure. Retry a
   technical failure with increasing delays and a bounded attempt count.
2. **Announce review cases.** Read `GET /notifications?status=pending` and
   deliver each one, then `POST /notifications/{id}/sent` with the channel.
   The outbox already guarantees one notification per subject, so this
   workflow does not need to de-duplicate anything itself.
3. **Scheduled recovery.** Call `POST /jobs/recover` on a schedule and report
   what it found.

When exports are added they must have their credentials stripped. n8n stores
the API key as an HTTP Header credential inside its own volume; an export
should reference the credential by name, never carry its value.

The authoritative record of what happened to an invoice is PostgreSQL, not
n8n's execution history. The execution history is for debugging a workflow.
