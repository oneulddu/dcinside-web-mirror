.PHONY: install install-dev test run run-prod

PYTHON ?= python3
PIP ?= $(PYTHON) -m pip

install:
	$(PIP) install -r requirements.txt

install-dev:
	$(PIP) install -r requirements-dev.txt

test:
	$(PYTHON) -m pytest -q

run:
	MIRROR_ENV=development $(PYTHON) run.py

run-prod:
	gunicorn -c gunicorn.conf.py wsgi:app
