# Contributing

Create focused branches and conventional commits (`feat:`, `fix:`, `docs:`, `test:`,
`refactor:`, `chore:`). Before opening a pull request:

1. Add or update tests for behavior changes.
2. Run `pytest -q` and `python -m compileall -q src scripts`.
3. Update API, deployment, security, or evaluation documentation when contracts change.
4. Explain operational and migration impact in the pull request.

Keep domain logic behind interfaces rather than coupling it to FastAPI or a broker. Never commit
credentials, private corpora, generated indexes, or production prompts containing sensitive data.
By contributing, you agree that your contribution is licensed under the project license.
