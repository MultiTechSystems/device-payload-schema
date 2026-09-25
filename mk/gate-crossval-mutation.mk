# CI gate 1: vendor cross-validation and mutation-score ratchets.
# Included from the Makefile (`-include mk/*.mk`). Both gates fail on a regression
# against a committed baseline; the *-update targets rewrite that baseline and should
# only be run deliberately, with the reason stated in the commit message.
#
#   gate-crossval   needs node and the oracle checkouts at the commits pinned in
#                   tools/crossval-baseline.json. Override their locations with
#                   TTN_DEVICES_REPO=... DECENTLAB_DECODERS=... (environment or make).
#   gate-mutation   needs nothing beyond the venv. GATE_MUTATION_ARGS passes extra
#                   options, e.g. GATE_MUTATION_ARGS="--changed-since origin/master".

GATE_PYTHON ?= $(if $(PYTHON),$(PYTHON),.venv/bin/python)
GATE_CROSSVAL_ARGS ?=
GATE_MUTATION_ARGS ?=

export TTN_DEVICES_REPO
export DECENTLAB_DECODERS

.PHONY: gate-crossval gate-crossval-update gate-mutation gate-mutation-update

gate-crossval:
	$(GATE_PYTHON) tools/crossval-gate.py $(GATE_CROSSVAL_ARGS)

gate-crossval-update:
	$(GATE_PYTHON) tools/crossval-gate.py --update $(GATE_CROSSVAL_ARGS)

gate-mutation:
	$(GATE_PYTHON) tools/mutation-gate.py $(GATE_MUTATION_ARGS)

gate-mutation-update:
	$(GATE_PYTHON) tools/mutation-gate.py --update $(GATE_MUTATION_ARGS)
