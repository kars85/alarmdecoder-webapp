---
name: alarmdecoder-webapp-abc
description: "Plan, implement and validate GitHub issue work in alarmdecoder-webapp using its A/B/C handoff and Empirica project."
---

# alarmdecoder-webapp /abc

Execute the A/B/C workflow for alarmdecoder-webapp. Read `docs/agent-workflow.md` and `.agent-workflow.json` from the assigned checkout before acting. Run the workflow helper's bootstrap/verify/session commands. Use only `kars85/alarmdecoder-webapp` and its configured Empirica UUID.

Resolve the requested issue in that repository. If none is supplied, inspect ready open issues and select only work covered by the user's request. Work one independently verifiable checklist item through PM-HANDOFF, DEV-EXECUTION and PM-VALIDATION. Claude plans/reviews and Codex implements when both runtimes are available. Read local CLI help to invoke them; never silently substitute an unavailable runtime or reviewer.

Use the profile's actual checks plus affected-area guidance. For --loop, continue ready items in the selected issue; for --loopall, continue authorized ready issues. Stop when the selected scope is accepted, no authorized ready work remains, or a concrete external decision blocks progress. Report unresolved dependencies without marking them done.

Keep issue acceptance, live system changes and merge/release authorization distinct. Record the exact state reached and link the issue/PR. Legacy Python 2.7 application: read README.md, ad2web/decoder.py and ad2web/ser2sock/ser2sock.py for runtime and device contracts. Keep the workflow helper/tests on Python 3; their success does not establish application compatibility. Coordinate ser2sock and TLS changes with the alarmdecoder library and ad-mqtt consumers; avoid a partial modernization.
