PY ?= .venv/bin/python
LF ?= .venv/bin/learnfig

.PHONY: setup test examples serve check-all clean

setup:              ## create .venv and install learnfig with dev extras
	uv venv .venv --python 3.10 || python3 -m venv .venv
	uv pip install --python .venv/bin/python -e ".[dev]" || .venv/bin/pip install -e ".[dev]"

test:               ## run the test suite
	$(PY) -m pytest -q

examples:           ## render every example into build/ with a gallery
	$(LF) examples -o build

check-all:          ## run the science checker on examples/ and topics/
	@for f in examples/*.yaml examples/*/*.yaml topics/*.yaml; do [ -f "$$f" ] && $(LF) check "$$f" >/dev/null && echo "PASS $$f" || echo "FAIL $$f"; done; true

serve:              ## serve build/ on http://localhost:8731
	$(PY) -m http.server 8731 --directory build

clean:
	rm -rf build .pytest_cache
	find . -name __pycache__ -type d -exec rm -rf {} +
