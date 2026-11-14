# Release notes

One file per release, named `<version>.md` (for example `0.5.6.md`), holding
the same Markdown as the GitHub release. The app bundles these and shows them
in the "What's new" window after an update (`cognitrix/api/routes/whats_new.py`).

Every version-bump PR must add the new version's file: `tests/test_whats_new.py`
fails until it exists. A leading `# Cognitrix vX.Y.Z` title, a "Covers PRs ..."
line and a leading `## Highlights` heading are dropped in the window.
