# stapel-referrals — contract emission + drift gate.
#
# Emits the contract triad (schema.json + flows.json + errors.json),
# capabilities.json, llms.txt and README.md from a single-module
# {referrals + core} instance mounted at /referrals/api/v1/. The authoritative CI
# gate is tests/test_contract.py; these targets are the dev loop.
# Edit docs/readme.md, never README.md.
PYTHON ?= python3
LLMS_BUDGET ?= 6000

.PHONY: contract contract-check migration-lint

contract:
	$(PYTHON) -m stapel_referrals._codegen --out docs
	$(PYTHON) -m stapel_referrals._capabilities --out docs
	$(PYTHON) -m stapel_tools.llms_txt . --out docs --budget $(LLMS_BUDGET)
	$(PYTHON) -m stapel_tools.readme .

contract-check:
	@tmp=$$(mktemp -d); \
	$(PYTHON) -m stapel_referrals._codegen --out "$$tmp" || { rm -rf "$$tmp"; exit 1; }; \
	$(PYTHON) -m stapel_referrals._capabilities --out "$$tmp" || { rm -rf "$$tmp"; exit 1; }; \
	$(PYTHON) -m stapel_tools.llms_txt . --out "$$tmp" --budget $(LLMS_BUDGET) || { rm -rf "$$tmp"; exit 1; }; \
	rc=0; \
	for f in schema.json flows.json errors.json capabilities.json llms.txt; do \
		if ! diff -q "docs/$$f" "$$tmp/$$f" >/dev/null 2>&1; then \
			echo "DRIFT: docs/$$f is stale — run 'make contract' and commit it"; \
			diff "docs/$$f" "$$tmp/$$f" | head -20; rc=1; \
		fi; \
	done; \
	rm -rf "$$tmp"; \
	$(PYTHON) -m stapel_tools.readme . --check || rc=1; \
	if [ $$rc -eq 0 ]; then echo "contract-check: docs + README.md up to date"; fi; \
	exit $$rc

migration-lint:
	$(PYTHON) -m stapel_tools.migration_lint . --strict
