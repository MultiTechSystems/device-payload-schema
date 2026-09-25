# CI gates 2 and 4. Included from the Makefile (`-include mk/*.mk`).
#
#   gate-provenance         every device schema added or modified since $(BASE)
#                           (REF...HEAD) must carry a vector with an independent
#                           `source:`, and every vector the change adds must declare one.
#                           A vector whose expected values changed under an unchanged
#                           payload and source is a warning. See tools/provenance-gate.py.
#                           GATE_PROVENANCE_ARGS=--include-worktree also checks
#                           uncommitted work; `--all` prints corpus-wide status.
#   gate-provenance-all     corpus-wide provenance status; never fails.
#   gate-validator-strict   tools/validate_schema.py over every device schema and every
#                           example: a duplicate mapping key or a key outside the
#                           language vocabulary (tools/schema_vocabulary.py) is an error.

PROVENANCE_PYTHON = $(if $(PYTHON),$(PYTHON),.venv/bin/python)
BASE ?= origin/master
GATE_PROVENANCE_ARGS ?=

.PHONY: gate-provenance gate-provenance-all gate-validator-strict

gate-provenance:
	$(PROVENANCE_PYTHON) tools/provenance-gate.py --base $(BASE) $(GATE_PROVENANCE_ARGS)

gate-provenance-all:
	$(PROVENANCE_PYTHON) tools/provenance-gate.py --all

# The three examples skipped by validate-examples need preprocessing (library
# resolution / renames) before they are schemas at all; their *_resolved forms are
# validated instead.
gate-validator-strict:
	@status=0; n=0; \
	for schema in $$(find schemas/devices -name '*.yaml' | sort) examples/*.yaml; do \
		case "$$schema" in \
			*_with_lib.yaml|*_rename.yaml|examples/lib_sensors_test.yaml) continue;; \
		esac; \
		n=$$((n + 1)); \
		out=$$($(PROVENANCE_PYTHON) tools/validate_schema.py "$$schema" 2>&1) || { \
			echo "FAILED: $$schema"; echo "$$out" | sed 's/^/    /'; status=1; }; \
	done; \
	if [ $$status -eq 0 ]; then \
		echo "gate-validator-strict: $$n schemas, no duplicate or unknown keys, all vectors pass."; \
	fi; \
	exit $$status
