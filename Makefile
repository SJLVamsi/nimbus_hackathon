PY := .venv/bin/python
PIP := .venv/bin/pip

.PHONY: help install seed reset api mcp agent ask test demo clean

help:
	@echo "make install   install python deps into .venv"
	@echo "make seed      seed the demo clinic day"
	@echo "make api       run the web app on http://localhost:8080"
	@echo "make mcp       run the MCP server (stdio)"
	@echo "make ask Q=... ask the manager agent via MCP"
	@echo "make test      run the test suite"
	@echo "make demo      seed + run the web app"

install:
	python3 -m venv .venv
	$(PIP) install -q --upgrade pip
	$(PIP) install -q -r requirements.txt
	@echo "installed. copy .env.example to .env to enable live LLM calls."

seed:
	$(PY) -m handoff.seed

reset: seed

api:
	$(PY) -m uvicorn api.main:app --port 8080

mcp:
	$(PY) -m mcp_server.server

ask:
	$(PY) -m agent.demo_agent "$(Q)"

test:
	$(PY) -m unittest discover -s tests -v

demo: seed
	$(PY) -m uvicorn api.main:app --port 8080

clean:
	rm -f handoff.db handoff.db-wal handoff.db-shm
