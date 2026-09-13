# Intake: work from outside systems

An application's "request a change" button, a support queue, a monitoring
script or a person with a shell can all put work into a rungs store without
knowing anything about ticket files. They drop one small JSON file per request
into `.rungs/inbox/`, and a scheduler runs `rungs intake`.

That is the whole contract. **A producer never writes a ticket file.** The
tool validates the request, sanitises every string, refuses a source it does
not know, deduplicates, creates the ticket, and writes a result file the
producer can read back.

Anyone who would rather not write JSON at all can use `rungs request` instead:
the same fields as command-line options, through the same validation and the
same creation path. See *One command instead of a file* below.

## The file

Any filename ending in `.json`, written directly into `.rungs/inbox/` (not
into `inbox/done/`, which is the tool's). One JSON object per file:

```json
{
  "source": "helpdesk",
  "key": "change-request-8412",
  "title": "The Save button on a daily log does nothing on a phone",
  "goal": "A site engineer on a phone taps Save on a daily log and the page does nothing: no error, no saved record. It must save, or say why it cannot.",
  "kind": "fix",
  "skill": "medium",
  "priority": 4,
  "domain": "ui",
  "epic": "user-requests",
  "tags": ["daily-logs", "mobile"],
  "scope": ["resources/js/pages/DailyLogs/", "app/Contexts/FieldOperations/"],
  "acceptance": "- Saving a daily log from a phone stores the record and shows it in the list.\n- A failure shows the reason in plain words.",
  "context": {
    "Page": "/projects/312/daily-logs/new",
    "Route": "projects.daily-logs.create",
    "Page component": "DailyLogs/Create.vue",
    "Company id": "88",
    "Project id": "312",
    "Viewport width": "390",
    "Browser": "Safari on iOS 18"
  },
  "quoted": "i press save and nothing happens\nhad to type it all again in the office",
  "requested_by": "application user id 4471",
  "approved_by": "platform administrator id 12"
}
```

Only `source`, `key`, `title` and `goal` are required.

## Field contract

| Field | Type | Required | Rule |
|---|---|---|---|
| `source` | string `[a-z0-9-]{1,40}` | yes | Must exist under `intake.sources` in `rungs.yaml`, unless `intake.allow_unknown_sources: true`. |
| `key` | string ≤ 120 | yes | The producer's own identifier for the request. `origin` becomes `source:key` and is unique across the whole store. |
| `title` | string ≤ 200 | yes | One line. |
| `goal` | string ≤ 20 000 | yes | Becomes the Goal section. |
| `kind` | `design` `implement` `fix` `review` `check` `research` `doc` `chore` | no | Falls back to the source block, then `intake.default_kind`. |
| `skill` | `very-low` `low` `medium` `high` `xhigh` (aliases `1`–`5`, `vlow`, `very-high`) | no | Falls back likewise. |
| `priority` | integer, lower is more urgent | no | Falls back likewise. |
| `domain`, `epic` | string ≤ 80 | no | Falls back likewise. |
| `tags` | list of strings ≤ 40 each, at most 50 | no | **Merged** with the source block's tags, not replacing them. |
| `scope` | list of strings ≤ 300 each, at most 50 | no | Path patterns, relative to the project root. |
| `acceptance` | string ≤ 20 000 | no | Becomes the Acceptance criteria section. Also decides `triage` vs `open` — see below. |
| `context` | object of string → string, at most 50 pairs | no | Rendered as a bullet list under Goal. Keys are capped at 80 characters, values at 2 000. |
| `quoted` | string ≤ 20 000 | no | What a person actually typed. Rendered under a heading that says so. |
| `requested_by`, `approved_by` | string ≤ 200 | no | Rendered under Goal. |
| anything else | | | Ignored, and listed in the result as `ignored_fields`. |

Prefer ids to names in `context`, `requested_by` and `approved_by`: a ticket
file is committed to a repository and read by agents. An id is one lookup away
for whoever needs it and carries no personal data into the store.

## What the tool does to the text

Every string is sanitised, because a ticket store must never be corrupted by
what someone typed into a web form:

- control characters are removed;
- `\r\n` and `\r` become `\n`;
- any run of three or more dashes is broken (`---` becomes `- - -`), so no
  line of a request can ever look like a front matter boundary, even to
  another tool reading the file;
- one-line fields (title, key, domain, epic, tags, the context pairs) have
  their line breaks flattened to spaces;
- anything over a *length* cap is **truncated, not rejected** — an over-long
  title ends in `…`.

A cap on a *count* is a refusal rather than a trim, because a request past one
is malformed rather than merely long: at most 50 `tags`, 50 `scope` entries and
50 `context` pairs. A file in the inbox larger than **1 MiB** is `invalid` and
is never read — only its size is looked at.

Two more things a request may not do, whatever it sends:

- **Every `scope` entry must be a relative path inside the project.**
  `/etc/passwd` and `../elsewhere` are refused, and an entry that is merely
  untidy is normalised (`./src//a.py` becomes `src/a.py`).
- **The finished ticket is validated before it is written**, with the same
  check every other creating path uses, so nothing an outside system sends can
  put a ticket in the store that `rungs doctor` would call broken.

`rungs intake --dry-run` reports these refusals exactly as a real run would.

The quoted text is written under `### What the requester wrote` inside the
Goal section, each line prefixed with `> `, under one sentence saying it is
the requester's own words copied in as data and not instructions to the agent
working the ticket. Nothing in a ticket is ever executed.

## Outcomes

Each file gets exactly one of three outcomes:

| Outcome | When | Effect |
|---|---|---|
| `created` | the request is valid and new | one ticket, one `intake` event whose detail is the origin |
| `duplicate` | a ticket already carries this `origin` | nothing is created; the result names the existing ticket |
| `invalid` | the file is a symlink, is larger than 1 MiB, is not valid JSON, is not a JSON object, is missing a required field, has a wrong type, breaks a cap, has a scope outside the project, or names an unknown source | nothing is created; the result carries the error |

**An invalid request is an outcome, not a failure.** `rungs intake` exits 0
whenever the run itself worked, so a scheduler never sees a red run because
somebody posted rubbish. Exit 1 means the run could not happen at all (no
store, an unreadable `rungs.yaml`, a `--file` that does not exist).

### triage or open

A new ticket lands in `triage/` unless **both** are true:

- the source block in `rungs.yaml` says `ready: true`, and
- the request carries a non-empty `acceptance`.

Then it lands in `open/` and can be claimed immediately. `rungs triage` lists
what is waiting for a triager to finish; `rungs ready <id>` moves one on once
title, goal, kind, skill and acceptance criteria are filled in.

Mark a source `ready: true` only when you trust it to write a complete,
reviewed request. A "request a change" button used by any signed-in person is
not that; a queue an administrator has already approved may be.

## The result file

Every processed file is moved to `inbox/done/` (or deleted with `--delete`),
and `inbox/done/<name>.result.json` is written beside it:

```json
{
  "source": "helpdesk",
  "key": "change-request-8412",
  "outcome": "created",
  "ticket": "rg-3f9a1",
  "error": null,
  "ignored_fields": ["submitted_at"],
  "at": "2026-09-13T02:55:43",
  "file": "change-request-8412.json"
}
```

`outcome` is `created`, `duplicate` or `invalid`; `ticket` is the ticket id
for the first two and `null` for the third; `error` is filled in only for
`invalid`. `file` is the name the file was given in `inbox/done/` (the tool
adds a `-1`, `-2` … suffix if that name is taken), so a producer that wrote
several requests can match each result back to what it sent. `rungs intake
--json` prints the same records on stdout.

A producer that wants to know what happened polls `inbox/done/` for its own
`<name>.result.json`. A producer that does not care writes the file and walks
away.

## Configuring a source

In `rungs.yaml`:

```yaml
intake:
  default_skill: medium
  default_kind: fix
  default_priority: 15
  allow_unknown_sources: false
  sources:
    helpdesk:
      epic: user-requests
      domain: ui
      kind: fix
      skill: medium
      priority: 15
      tags: [user-request]
      ready: false
```

The defaults cascade **request → source block → `intake` defaults**, field by
field, except `tags`, which are merged. `allow_unknown_sources: true` accepts
any source name that matches `[a-z0-9-]{1,40}`; leave it `false` unless you
control every writer of the inbox.

## Arbite files

An exporter that already writes arbite-format `tic-xxxx.md` files can be
pointed straight at `.rungs/inbox` without changing the application. The front
matter is read leniently (PyYAML's folded quoted scalars and block lists
included), `source` is `arbite` and `key` is the arbite id, so `origin` is
`arbite:tic-xxxx`. The file is a duplicate if the store already holds that
origin **or** a ticket with that id — so a store converted with `rungs
import-arbite` never gets the same work twice.

The `arbite` source is built in (tag `arbite`, the `intake` defaults for
everything else), so no configuration is needed. Add a block only to change
what those tickets carry:

```yaml
    arbite:
      epic: user-requests
      domain: ui
      tags: [user-request]
      ready: false
```

Arbite files carry no acceptance criteria, so they always land in `triage/`
whatever `ready` says. The arbite `status` field is not honoured: the triager
decides when the work is ready.

## One command instead of a file

Writing JSON from a shell script is a chore, and a person filing one request
should not have to. `rungs request` takes the same fields as options:

```
rungs request --source helpdesk --key cr-8412 \
    --title 'Saving a daily log on a phone does nothing' \
    --goal 'A site engineer taps Save on a daily log and nothing happens: no error, no record. It must save, or say why it cannot.' \
    --kind fix --skill medium --priority 4 --domain ui \
    --tags daily-logs,mobile \
    --scope 'resources/js/pages/DailyLogs/' \
    --acceptance-file /tmp/acceptance.md \
    --context Page=/projects/312/daily-logs/new \
    --context 'Browser=Safari on iOS 18' \
    --quoted-file /tmp/what-they-typed.txt \
    --requested-by 'application user id 4471'
```

Every option maps one for one onto the field of the same name in the table
above, and **there is no second code path**: the command builds exactly the
request object the JSON contract defines and runs it through the same
validation, sanitising, defaults cascade and duplicate check. A ticket filed
this way is indistinguishable from one filed as a file.

| Option | Notes |
|---|---|
| `--source`, `--key`, `--title` | required |
| `--goal TEXT` or `--goal-file F` | required, one or the other |
| `--kind`, `--skill`, `--priority`, `--domain`, `--epic` | optional; the cascade still applies when they are left out |
| `--tags a,b` / `--scope a,b` | comma-separated. A path containing a comma is not supported |
| `--acceptance TEXT` / `--acceptance-file F` | optional |
| `--quoted TEXT` / `--quoted-file F` | optional. A file is usually easier: it keeps the line breaks and needs no shell quoting |
| `--context KEY=VALUE` | repeatable, once per fact. Everything after the first `=` is the value, so `--context 'Note=a = b'` works. A repeated key keeps the last value |
| `--requested-by`, `--approved-by` | optional |
| `--json` | print the result record instead of a line of text |
| `--defer` | file the request for the scheduler instead of creating the ticket now |

### Exit codes

| Code | Meaning |
|---|---|
| 0 | the ticket was created (or, with `--defer`, the request was filed) |
| 2 | the origin is already in the store; nothing was created, and the existing ticket id is printed |
| 1 | the request was refused; the reason is on stderr and nothing was created |

So a script can branch on the outcome without parsing anything:

```bash
if rungs request --source helpdesk --key "$id" --title "$title" --goal "$goal"; then
    echo "filed"
elif [ $? = 2 ]; then
    echo "already filed"
else
    echo "refused" >&2
fi
```

The result record is written to
`.rungs/inbox/done/<source>-<key>.json.result.json` exactly as it is for a
file, with `file` set to that synthetic name, so a producer reads the outcome
the same way whichever route it used. The source and key are reduced to
`[A-Za-z0-9_-]` for the file name (`cr/8412` becomes `cr-8412`), so nothing a
caller passes can write outside the inbox; the untouched `key` is still what
`origin` is built from.

### `--defer`

```
rungs request --source helpdesk --key cr-8412 --title '…' --goal '…' --defer
```

writes the request as `.rungs/inbox/<source>-<key>.json` and exits 0 without
creating anything. The next scheduled `rungs intake` validates it and writes
the result file. Use it when the caller must not wait for the work, or is not
allowed to write into the ticket folders — the inbox is the only folder it
needs. Note that **nothing is validated at this point**: an unknown source or
a missing field is reported by the intake run, in the result file, not by the
command.

## Running it

```
rungs intake                 # every file in the inbox, oldest first by mtime then name
rungs intake --file req.json # just that one
rungs intake --dry-run       # print the outcomes, change nothing
rungs intake --delete        # delete each processed file instead of keeping it
rungs intake --quiet         # print nothing on success
rungs intake --json          # the result records on stdout

rungs request --source S --key K --title T --goal G   # file one now
rungs request … --defer                               # file it for the scheduler
```

Scheduler line:

```
*/5 * * * * cd /path/to/project && rungs intake --quiet
```

The inbox is the only folder an outside system ever writes to. Give the
producing user write permission on `.rungs/inbox/` and `.rungs/inbox/done/`
and nothing else.

**Symlinks in the inbox are refused and never followed.** A link is recorded
as `invalid` with the error `symlinks are not accepted`, and the link itself —
not its target — is moved to `inbox/done/` or deleted. Otherwise a producer
that may only write into the inbox could make the tool read any file the user
running `rungs intake` can open.
