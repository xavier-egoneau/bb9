.PHONY: check lint types test fmt

# Reproduit localement le pipeline CI (.github/workflows/ci.yml).
# Prérequis : ruff, mypy et pytest sur le PATH.
#   pip install ruff mypy pytest      (ou: uv tool install mypy ruff)

check: lint types test

lint:
	ruff check .

types:
	mypy bb9

test:
	pytest -q

fmt:
	ruff check . --fix
