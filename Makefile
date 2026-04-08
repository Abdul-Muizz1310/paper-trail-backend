.PHONY: run test

run:
	uv run uvicorn paper_trail.main:app --reload

test:
	uv run pytest
