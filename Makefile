.PHONY: setup seed dev-api dev-mcp dev-web test ci register-bot

VENV := .venv/bin

setup:
	uv venv .venv
	uv pip install --python $(VENV)/python -e "packages/quant-core[dev]"
	cd apps/web && npm install

seed:
	$(VENV)/python scripts/seed_golden_dataset.py

dev-api:
	PYTHONPATH=packages/quant-core/src:services/quant-api/src $(VENV)/uvicorn quant_api.main:app --host 127.0.0.1 --port 8100 --reload

dev-mcp:
	PYTHONPATH=packages/quant-core/src:services/quant-api/src:services/quant-mcp/src $(VENV)/python -m quant_mcp.server --transport streamable-http --port 8101

dev-web:
	cd apps/web && npm run dev -- -p 3100

test:
	$(VENV)/pytest -c packages/quant-core/pyproject.toml -v

ci: test
	cd apps/web && npm run build

register-bot:
	node scripts/register_agent_home.mjs
