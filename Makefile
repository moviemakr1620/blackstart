.PHONY: dev backend frontend test build ui

backend:
	cd backend && PYTHONPATH=. uvicorn app.main:app --reload --host 0.0.0.0 --port 8088

frontend:
	cd frontend && npm run dev

dev:
	@echo "Run 'make backend' and 'make frontend' in two terminals."
	@echo "UI: http://127.0.0.1:5173   API: http://127.0.0.1:8088"

test:
	cd backend && PYTHONPATH=. pytest -q

ui:
	cd frontend && npm run build
	rm -rf backend/static/*
	cp -R frontend/dist/. backend/static/

build:
	docker compose build
