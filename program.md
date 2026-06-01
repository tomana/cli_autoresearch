# Agent brief

> Replace this file with your own. The driver reads its path from
> the positional `program` arg and tells the agent to read it +
> follow whatever it says, each iteration.

You're being driven in a loop. On every iteration you'll receive
the same prompt pointing back at this file. Use the file to decide
what one iteration should do.

A common shape:

1. **Look around** — read any state files in the cwd, scan for
   progress, see what's been done already.
2. **Do ONE small thing** — typically a few minutes of work, not
   the whole task.
3. **Record what you did** — leave a trail (a log line, a new file,
   an updated state file) so the next iteration knows where to
   continue from.
4. **Exit.** The driver loops you back.

Pick a stop condition: "when the goal state file says done", "when
the queue is empty", "after N iterations" — whatever fits. Write
that into this file.

One pattern worth considering: have the agent keep a `NOTES.md` it
**reads at the start** + **appends one line to at the end** of each
iter (what was tried, what happened, would-you-repeat). Cheap
institutional memory across iters with zero driver involvement —
the agent self-documents.

Anything else (file layout, scoring, naming conventions, agent
identity tags, schemas) is your project's call — `cli_autoresearch`
doesn't impose anything.
