# Working in this repo

## This repo is public

`Project-Space-Eagle` is public on GitHub — it's distributed via a curl-install
command, so the code itself needs to be public. Internal developer material
does not.

**Never commit or push internal developer docs**: architecture write-ups,
specs, plans, roadmaps, audits, vision/strategy documents, status reports —
anything that exists to help build the codebase rather than to help someone
install or use it. These are gitignored (`Aethelark_Architecture.md`,
`Aethelark_Specifications.md`, `Aethelark_Vision.md`, `CURRENT_STATUS.md`,
`docs/`) — keep writing them locally as normal, just don't remove them from
`.gitignore` or `git add -f` them back in.

**Only genuinely user-facing docs get tracked**: `readme.md`,
`Aethelark_Google_Setup.md`, `packaging/README.md`, and anything else a
person installing or using the eagle — not building it — would need to read.
When in doubt, ask before adding a new `.md` file to the tracked tree.

## Commit messages

Do not add a `Co-Authored-By: Claude ...` trailer to commits in this repo.
