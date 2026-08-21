# SCAE — tareas de desarrollo local.
# Ejecuta `make` sin argumentos para ver la ayuda.

PY := ./.venv/bin/python
BACKEND := backend
FRONTEND := frontend

.DEFAULT_GOAL := help
.PHONY: help api web seed reset-demo test migrate stamp lint stop legacy

help:  ## Muestra esta ayuda
	@echo "SCAE — comandos disponibles:"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
	  | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

api:  ## Levanta la API (http://localhost:8000)
	cd $(BACKEND) && ../.venv/bin/python -m uvicorn app.main:app --reload --port 8000

web:  ## Levanta la interfaz web (http://localhost:5173)
	cd $(FRONTEND) && npm run dev

seed:  ## Crea los datos de demostración (no sobrescribe si ya existen)
	cd $(BACKEND) && ../.venv/bin/python -m app.seed

reset-demo:  ## BORRA la base y la vuelve a sembrar (pide confirmación)
	cd $(BACKEND) && ../.venv/bin/python -m app.seed --reset

test:  ## Ejecuta la suite de pruebas
	cd $(BACKEND) && ../.venv/bin/python -m pytest tests/ -q

migrate:  ## Aplica las migraciones pendientes
	cd $(BACKEND) && ../.venv/bin/python -m alembic upgrade head

stamp:  ## Marca la base actual como al día sin ejecutar migraciones
	cd $(BACKEND) && ../.venv/bin/python -m alembic stamp head

lint:  ## Comprueba los tipos del frontend
	cd $(FRONTEND) && npx tsc --noEmit

stop:  ## Detiene los servicios de SCAE
	-@kill $$(lsof -ti:8000) 2>/dev/null || true
	-@kill $$(lsof -ti:5173) 2>/dev/null || true
	@echo "Servicios detenidos."

legacy:  ## Levanta el prototipo v1 (Flask, puerto 5001)
	$(PY) app.py
