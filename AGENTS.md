# SIM project instructions

- Do not run local verification commands on a laptop or dot PC, including pytest, Karma, migration/concurrency smoke, lint, typecheck or build. Run them only in CI/CD on a published Git commit or pull request.
- Use the GitHub Actions workflow in `.github/workflows/ci.yml` as the verification gate. Do not describe a local build, lint command, YAML file or queued workflow as a passed test. Report the exact workflow run and job conclusions.
- Keep CI isolated: temporary PostgreSQL, fake or mocked transports, no production database, real connector accounts, channel sends, deployment credentials or production SSH.
- A change is ready for Git merge only after required CI jobs pass for the exact head SHA. Preserve branch protection and existing security boundaries. Production release is a separate decision and requires its own backup, migration and rollout gates.
