"""Daily timesheet from local activity evidence.

Three sources are merged on a one-minute grid for the requested day:

* ActivityWatch  - which window was in front, with away-from-keyboard time removed
* Claude Code    - session logs under ~/.claude/projects (folder and prompts name the client)
* git            - the user's commits in the configured repository roots

Each source claims minutes for a client ("site"). Minutes claimed by several clients are shared
between them; active minutes nobody could place inherit the nearest placed minute. The result is
allocated to billing slots and printed as a table, or emitted as JSON for the ClickUp push.
"""

__version__ = "2.0.0"
