# `make gates` runs every gate that needs no docker: validator strictness, provenance
# (against $(BASE)), vendor cross-validation (needs node and the pinned oracle
# checkouts) and mutation floors. `gate-verdicts` adds the Go, Java and C# containers.
.PHONY: gates
gates: gate-validator-strict gate-provenance gate-crossval gate-mutation
