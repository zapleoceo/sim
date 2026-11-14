# Persona editor cleanup

Long user-authored text is directly editable in a compact four-row textarea. The expanded editor edits the same complete form value. Closing it does not submit; use the persona or shared setting save button as before. Folding sections never discards values.

The assembled prompt is a read-only view with links to editable source fields. Technical agent rules and processing notes are folded under «Технические правила и обработка». Their full backend text remains available. No prompt, safety rule, access control or stored persona data changes.

Regression coverage in persona-page-layout.test.tsx checks direct and expanded editing, exact preservation of long text, folded fields and read-only technical rules. Verification runs only in GitHub Actions per AGENTS.md; no production writes or model calls are needed.
