.PHONY: install run run-prod

install:
	pip install -r requirements.txt

run:
	python index.py

run-prod:
	gunicorn -c gunicorn.conf.py wsgi:app
