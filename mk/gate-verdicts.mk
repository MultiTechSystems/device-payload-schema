# CI gate 3: five-implementation verdicts, vector by vector.
# Included from the Makefile (`-include mk/*.mk`). See tools/verdicts-gate.py.
#
# The Python interpreter and the generated TS013 codec (node) run on the host; Go, Java
# and C# run in the same containers as test-go / test-java / test-dotnet, with
# CORPUS_REPORT set so each corpus runner also writes a per-vector report under
# build/verdicts/. Then:
#
#   gate-verdicts           whole corpus: a vector Python passes and another
#                           implementation does not must be listed, with a reason, in
#                           tools/verdicts-baseline.json (a ratchet both ways); and every
#                           vector of a schema changed since $(BASE) must pass in all five.
#   gate-verdicts-changed   only the schemas changed since $(BASE), run with CORPUS_ONLY;
#                           each of their vectors must pass in all five.
#   gate-verdicts-baseline  rewrite the baseline from a full run, keeping existing
#                           reasons. Deliberate only; say why in the commit message.
#
# GATE_VERDICTS_ARGS passes extra options, e.g. --impls python,go,ts013.

VERDICTS_PYTHON = $(if $(PYTHON),$(PYTHON),.venv/bin/python)
BASE ?= origin/master
GATE_VERDICTS_ARGS ?=

.PHONY: gate-verdicts gate-verdicts-changed gate-verdicts-baseline

gate-verdicts:
	@mkdir -p .cache/go .cache/m2 .cache/nuget build/verdicts
	DOCKER="$(or $(DOCKER),docker)" $(VERDICTS_PYTHON) tools/verdicts-gate.py --run \
		--base $(BASE) $(GATE_VERDICTS_ARGS)

gate-verdicts-changed:
	@mkdir -p .cache/go .cache/m2 .cache/nuget build/verdicts
	DOCKER="$(or $(DOCKER),docker)" $(VERDICTS_PYTHON) tools/verdicts-gate.py --run \
		--base $(BASE) --only-changed $(GATE_VERDICTS_ARGS)

gate-verdicts-baseline:
	@mkdir -p .cache/go .cache/m2 .cache/nuget build/verdicts
	DOCKER="$(or $(DOCKER),docker)" $(VERDICTS_PYTHON) tools/verdicts-gate.py --run \
		--write-baseline $(GATE_VERDICTS_ARGS)
