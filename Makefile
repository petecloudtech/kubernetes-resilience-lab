.PHONY: test manifests run build

test:
	python3 -m unittest discover -s tests -v

manifests:
	python3 tools/check_manifests.py

run:
	python3 -m app.service

build:
	docker build -t resilience-lab:local .
