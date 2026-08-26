# LoRaWAN Payload Schema Build System
# Portable C code targeting Linux, Zephyr, FreeRTOS

# Platform selection
PLATFORM ?= linux
VARIANT ?= debug

# Build directory
BUILD_DIR = build-$(PLATFORM)-$(VARIANT)

# Compiler settings
ifeq ($(PLATFORM),linux)
    CC = gcc
    CFLAGS = -std=c11 -Wall -Wextra -Werror
    CFLAGS += -DPLATFORM_LINUX
    LDFLAGS = -lm
    SYS_SRC = sys_linux.c
endif

ifeq ($(PLATFORM),zephyr)
    # Zephyr uses its own build system (west/cmake)
    # This is for reference; actual build uses west build
    $(error Zephyr builds use: west build -b <board>)
endif

# Variant settings
ifeq ($(VARIANT),debug)
    CFLAGS += -g -O0 -DDEBUG
endif

ifeq ($(VARIANT),release)
    CFLAGS += -O2 -DNDEBUG
endif

ifeq ($(VARIANT),coverage)
    CFLAGS += -g -O0 --coverage -fprofile-arcs -ftest-coverage
    LDFLAGS += --coverage
endif

# Include paths
CFLAGS += -Iinclude
# Header dependency tracking. Without it a header-only change rebuilt nothing, and the
# whole schema interpreter is a header - so `make selftest` could pass against stale
# objects after any change to it, which is exactly the change most worth testing.
CFLAGS += -MMD -MP

# Source files - selftest framework only
SELFTEST_SRCS = src/selftests.c src/selftest_codec.c src/selftest_protocol.c src/selftest_schema.c src/sys_linux.c
SELFTEST_OBJS = $(patsubst src/%.c,$(BUILD_DIR)/%.o,$(SELFTEST_SRCS))

# Test binary
TEST_BIN = $(BUILD_DIR)/bin/selftest

# Codec binaries
CODEC_TEST_BIN = $(BUILD_DIR)/bin/test_codec
BENCHMARK_BIN = $(BUILD_DIR)/bin/benchmark

# Protocol buffer sources (if any)
PROTO_SRCS = $(wildcard proto/*.proto)
PROTO_C = $(patsubst proto/%.proto,src/%.pb.c,$(PROTO_SRCS))

# C++ compiler
CXX = g++
CXXFLAGS = -std=c++17 -Wall -Wextra -O3 -Iinclude

.PHONY: all clean test test-c selftest validate-devices ci docs-index docs-index-check score-check validate-examples hypothesis coverage proto help codec benchmark bench-c check-floors check-floors-python generate-codec pytest pytest-cov coverage-html coverage-all validate fuzz fuzz-quick fuzz-hypothesis fuzz-go fuzz-c test-go test-java test-dotnet test-languages

all: $(TEST_BIN)

# Create build directories
$(BUILD_DIR):
	mkdir -p $(BUILD_DIR)/bin

# Compile C sources
$(BUILD_DIR)/%.o: src/%.c | $(BUILD_DIR)
	$(CC) $(CFLAGS) -c $< -o $@

# Pull in the generated .d files so a header edit rebuilds what included it.
-include $(SELFTEST_OBJS:.o=.d)

# Build selftests.o with SELFTEST_MAIN for standalone executable
$(BUILD_DIR)/selftests.o: src/selftests.c | $(BUILD_DIR)
	$(CC) $(CFLAGS) -DSELFTEST_MAIN -c $< -o $@

# Link selftest binary
$(TEST_BIN): $(SELFTEST_OBJS) | $(BUILD_DIR)
	mkdir -p $(BUILD_DIR)/bin
	$(CC) $(SELFTEST_OBJS) $(LDFLAGS) -o $@

# Run self-tests
selftest: $(TEST_BIN)
	$(TEST_BIN)

test: selftest pytest validate-devices
	@echo "All tests complete."


# Tests for the Go, Java and C# interpreters, run in containers so no local
# toolchain is needed. Caches live under .cache/ so repeat runs are quick.
# Use test-languages to check that every implementation still agrees, which
# matters most when changing shared decode behaviour (see AGENTS.md).
DOCKER ?= docker
DOCKER_RUN = $(DOCKER) run --rm -v "$(CURDIR)":/work
CACHE_DIR = $(CURDIR)/.cache

test-go:
	@mkdir -p $(CACHE_DIR)/go
	$(DOCKER_RUN) -w /work/go/schema \
		-v $(CACHE_DIR)/go:/tmp/gocache \
		-e GOFLAGS=-mod=mod -e GOCACHE=/tmp/gocache \
		golang:1.22 sh -c "go vet ./... && go test ./..."

test-java:
	@mkdir -p $(CACHE_DIR)/m2
	$(DOCKER_RUN) -w /work/bindings/java \
		-v $(CACHE_DIR)/m2:/root/.m2 \
		maven:3.9-eclipse-temurin-21 mvn -B test

test-dotnet:
	@mkdir -p $(CACHE_DIR)/nuget
	$(DOCKER_RUN) -w /work/dotnet \
		-v $(CACHE_DIR)/nuget:/root/.nuget \
		mcr.microsoft.com/dotnet/sdk:8.0 dotnet test --nologo

# Every corpus vector through both conformance paths - the interpreted schema and the
# generated TS013 codec - with a verdict apiece. The corpus runners cover the interpreted
# path in five languages; nothing covered the generated path over the corpus until this.
verdicts:
	@mkdir -p build
	$(PYTHON) tools/vector-verdicts.py --json build/verdicts.json

test-languages: test selftest test-go test-java test-dotnet
	@echo "Python, C, Go, Java and C# implementations all pass."

# Python virtual environment
VENV = .venv
PYTHON = $(VENV)/bin/python
PIP = $(VENV)/bin/pip
PYTEST = $(VENV)/bin/pytest

$(VENV)/bin/activate:
	python3 -m venv $(VENV)
	$(PIP) install --quiet pytest pytest-cov pyyaml

# Python tests
pytest: $(VENV)/bin/activate
	@echo "Running Python tests..."
	PYTHONPATH=tools $(PYTEST) tests/ -v

# Mirrors the `Validate every device schema` job in .github/workflows/quality.yml.
# `make test` ran the five interpreters and the Python suite but never the validator,
# so three conformance fixtures using `round`, the named `op` form and `$$ref` were
# pushed while that job was red - the local checks could not have caught it.
# --- CI parity -------------------------------------------------------------
#
# Every check the GitHub workflows run, so a local pass and a red build cannot
# disagree. Added after two jobs went red on pushes that `make test` had approved:
# the device-schema validation (quality.yml) and the repository index check, neither
# of which existed as a local target. Whenever a workflow gains a step, add it here.
# The C interpreter's own tests, and the corpus harness that measures it (CR-2026-032).
#
# src/test_interpreter.c, src/test_binary_schema.c and src/test_encoder.c were in no build
# target at all - the comment at the top of src/selftest_schema.c says so - so nothing ran
# them and nothing noticed when the interpreter drifted from them. They pass; they are
# built and run here.
#
# src/test_comprehensive.c is deliberately NOT here. It fails 22 of its 160 assertions
# against *stale expectations*, not defects: it asserts the pre-CR-2026-009 lookup and enum
# behaviour that PS-105/PS-269 deliberately changed, and its little-endian s24/u64 cases
# fail while the interpreter decodes both correctly when driven directly. Updating it is its
# own change; adding it here would just make `make test-c` red.
C_TESTS = test_interpreter test_binary_schema test_encoder

test-c: $(VENV)/bin/activate
	@mkdir -p $(BUILD_DIR)/bin
	@for t in $(C_TESTS); do \
		echo "  cc  src/$$t.c"; \
		$(CC) -std=c11 -Iinclude -o $(BUILD_DIR)/bin/$$t src/$$t.c -lm || exit 1; \
		$(BUILD_DIR)/bin/$$t > /dev/null || { echo "FAILED: $$t"; exit 1; }; \
	done
	@echo "C interpreter tests pass."
	$(PYTHON) tools/c-corpus-harness.py

ci: test validate-examples docs-index-check score-check hypothesis test-c test-go test-java test-dotnet
	@echo "All CI checks passed."

# quality.yml: the index is generated, so a stale one fails the build.
docs-index-check: $(VENV)/bin/activate
	$(PYTHON) tools/generate_docs_index.py --check

docs-index: $(VENV)/bin/activate
	$(PYTHON) tools/generate_docs_index.py

# quality.yml: the gate is "nothing got worse", plus one example held at SILVER.
score-check: $(VENV)/bin/activate
	$(PYTHON) tools/score_schema.py schemas/devices --all --baseline score-baseline.json --quiet
	$(PYTHON) tools/score_schema.py examples/canonical-modifier-order.yaml --min-tier SILVER --quiet

# fuzz.yml: the examples, skipping the three that need preprocessing first.
validate-examples: $(VENV)/bin/activate
	@status=0; \
	for schema in examples/*.yaml; do \
		case "$$schema" in \
			*_with_lib.yaml|*_rename.yaml|examples/lib_sensors_test.yaml) continue;; \
		esac; \
		PYTHONPATH=tools $(PYTHON) tools/validate_schema.py "$$schema" > /tmp/ve.txt 2>&1 || { \
			echo "FAILED: $$schema"; cat /tmp/ve.txt; status=1; }; \
	done; \
	if [ $$status -eq 0 ]; then echo "All examples valid."; fi; \
	exit $$status

# fuzz.yml: the property tests under the profile CI uses.
hypothesis: $(VENV)/bin/activate
	PYTHONPATH=tools $(PYTEST) tests/test_hypothesis.py -q --hypothesis-profile=ci

validate-devices: $(VENV)/bin/activate
	@status=0; \
	for schema in schemas/devices/*/*.yaml; do \
		if ! $(PYTHON) tools/validate_schema.py "$$schema" > /tmp/vd.txt 2>&1; then \
			status=1; \
		fi; \
		if grep -q "Schema: INVALID" /tmp/vd.txt; then \
			echo "INVALID: $$schema"; cat /tmp/vd.txt; status=1; \
		fi; \
	done; \
	if [ $$status -eq 0 ]; then echo "All device schemas valid."; fi; \
	exit $$status


# Python tests with coverage
pytest-cov: $(VENV)/bin/activate
	@echo "Running Python tests with coverage..."
	PYTHONPATH=tools $(PYTEST) tests/ -v --cov=tools --cov-report=term-missing --cov-report=html:coverage-html

# Generate HTML coverage report
coverage-html: pytest-cov
	@echo "Coverage report generated: coverage-html/index.html"
	@echo "Open with: xdg-open coverage-html/index.html"

# Combined C and Python coverage
coverage-all: coverage pytest-cov
	@echo "=== Coverage Summary ==="
	@echo "C coverage: see *.gcov files"
	@echo "Python coverage: see coverage-html/index.html"

# Generated codec test
$(CODEC_TEST_BIN): src/test_codec.c include/env_sensor_codec.h | $(BUILD_DIR)
	mkdir -p $(BUILD_DIR)/bin
	$(CC) $(CFLAGS) src/test_codec.c -o $@

codec: $(CODEC_TEST_BIN)
	$(CODEC_TEST_BIN)

# C++ Benchmark
$(BENCHMARK_BIN): src/benchmark.cpp include/env_sensor_codec.h | $(BUILD_DIR)
	mkdir -p $(BUILD_DIR)/bin
	$(CXX) $(CXXFLAGS) src/benchmark.cpp -o $@

benchmark: $(BENCHMARK_BIN)
	$(BENCHMARK_BIN)

# Benchmarks the runtime interpreter in include/schema_interpreter.h against the Python
# reference on one corpus schema. Distinct from `benchmark` above, which times a small
# interpreter defined inline in src/benchmark.cpp and never includes that header - the
# figures in docs/SPEC-IMPLEMENTATION-STATUS.md come from this target, not that one.
# Not in `ci`: it is a measurement, and its numbers depend on the machine.
# Prints every corpus floor beside the actual its own implementation reaches. Floors are
# ratchets living in seven places across four languages, and reading them by eye is how they
# go wrong - twice, most recently reporting `tlv` loose at "900 against an actual of 910",
# which was Python's floor next to Go's actual with both exactly at their own.
# `check-floors` covers all four and runs their toolchains in Docker; `check-floors-python`
# needs nothing and is the one worth running while editing.
check-floors: $(VENV)/bin/activate
	$(PYTHON) tools/check-floors.py

check-floors-python: $(VENV)/bin/activate
	$(PYTHON) tools/check-floors.py --python

bench-c: $(VENV)/bin/activate
	$(PYTHON) tools/benchmark-c-interpreter.py

# Generate C codec from schema (old single-file generator)
generate-codec:
	python3 tools/generate-c.py examples/env_sensor.yaml -o include/env_sensor_codec.h
	@echo "Generated: include/env_sensor_codec.h"

# Generate codec + tests from schema (new comprehensive generator)
generate: $(VENV)/bin/activate
	$(PYTHON) tools/generate_codec.py examples/env_sensor.yaml -o generated/
	@echo "Generated codec and tests in generated/"

# Run generated tests
test-generated: generate
	@echo "=== C Tests ==="
	gcc -Wall -Wextra -o generated/env_sensor_test generated/env_sensor_test.c -lm
	./generated/env_sensor_test
	@echo ""
	@echo "=== Python Tests ==="
	PYTHONPATH=tools $(PYTEST) generated/test_env_sensor.py -v

# Validate schema and run test vectors
validate: $(VENV)/bin/activate
	@for schema in examples/*.yaml; do \
		echo ""; \
		$(PYTHON) tools/validate_schema.py $$schema; \
	done

# Validate single schema
validate-schema: $(VENV)/bin/activate
	@if [ -z "$(SCHEMA)" ]; then \
		echo "Usage: make validate-schema SCHEMA=path/to/schema.yaml"; \
		exit 1; \
	fi
	$(PYTHON) tools/validate_schema.py $(SCHEMA) -v

# Fuzz testing (10 min per schema - CI/release)
fuzz: $(VENV)/bin/activate
	@echo "Fuzzing decoder (10 min per schema)..."
	@for schema in examples/*.yaml; do \
		$(PYTHON) tools/fuzz_decoder.py $$schema --duration 600; \
	done
	@echo ""
	@echo "Fuzzing schema parser (10 min)..."
	$(PYTHON) tools/fuzz_decoder.py --schema-fuzz --duration 600

# Quick fuzz (10 sec - per commit)
fuzz-quick: $(VENV)/bin/activate
	@echo "Quick fuzz test (10 sec)..."
	$(PYTHON) tools/fuzz_decoder.py examples/env_sensor.yaml --duration 10 --schema-fuzz

# Hypothesis property-based testing
fuzz-hypothesis: $(VENV)/bin/activate
	@echo "Running Hypothesis property-based tests..."
	$(PIP) install --quiet hypothesis
	PYTHONPATH=tools $(PYTEST) tests/test_hypothesis.py -v --hypothesis-show-statistics

# Go fuzz (requires Go 1.18+)
fuzz-go:
	@echo "Running Go fuzz tests (60 sec each)..."
	cd fuzz/go && go test -fuzz=FuzzDecode -fuzztime=60s
	cd fuzz/go && go test -fuzz=FuzzDecodeEncode -fuzztime=60s

# C fuzz with libFuzzer (requires clang)
fuzz-c: generate-codec
	@echo "Building and running C fuzzer..."
	mkdir -p fuzz/corpus
	echo -n -e '\x09\x29\x82\x0C\xE4\x00' > fuzz/corpus/normal
	echo -n -e '\x00\x00\x00\x00\x00\x00' > fuzz/corpus/zeros
	clang -g -O1 -fsanitize=fuzzer,address -Iinclude fuzz/fuzz_decoder.c -o fuzz/fuzz_decoder
	./fuzz/fuzz_decoder fuzz/corpus/ -max_len=256 -max_total_time=60 -print_final_stats=1

# Full fuzz suite (all methods)
fuzz-all: fuzz-quick fuzz-hypothesis
	@echo "Note: Run 'make fuzz-go' and 'make fuzz-c' separately (require Go/clang)"

# Generate coverage report
coverage: VARIANT=coverage
coverage: clean $(TEST_BIN)
	$(TEST_BIN)
	gcov -o $(BUILD_DIR) src/*.c
	@echo "Coverage files generated. Use lcov for HTML report."

# Generate protobuf C files (requires nanopb)
proto: $(PROTO_C)

src/%.pb.c: proto/%.proto
	nanopb_generator -I proto -D src $<

# Python simulation
simulation:
	python simulation/run_simulation.py

# Clean build artifacts
clean:
	# Matched on the variant suffix, not `build-*`: the tracked `build-system/`
	# directory is a build-* too, and `rm -rf build-*` deleted its source files.
	rm -rf build-*-debug build-*-release build-*-coverage
	rm -f src/*.pb.c src/*.pb.h
	rm -f *.gcov *.gcda *.gcno
	rm -rf coverage-html .coverage .pytest_cache
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete

# Clean including venv
distclean: clean
	rm -rf $(VENV)

# Help
help:
	@echo "LoRaWAN Payload Schema Build System"
	@echo ""
	@echo "Usage: make [target] [PLATFORM=<platform>] [VARIANT=<variant>]"
	@echo ""
	@echo "Platforms:"
	@echo "  linux     Linux/POSIX (default)"
	@echo "  zephyr    Zephyr RTOS (use west build instead)"
	@echo ""
	@echo "Variants:"
	@echo "  debug     Debug build with symbols (default)"
	@echo "  release   Optimized release build"
	@echo "  coverage  Debug with coverage instrumentation"
	@echo ""
	@echo "Targets:"
	@echo "  all           Build test binary (default)"
	@echo "  selftest      Build and run C self-tests"
	@echo "  test          Run all tests (C + Python)"
	@echo "  pytest        Run Python tests only"
	@echo "  pytest-cov    Run Python tests with coverage"
	@echo "  coverage-html Generate HTML coverage report"
	@echo "  coverage-all  Run all coverage (C + Python)"
	@echo "  coverage      Build and run C with coverage"
	@echo "  proto         Generate protobuf C files"
	@echo "  codec         Build and test generated codec"
	@echo "  benchmark     Run C++ benchmark"
	@echo "  generate-codec Generate C codec from schema"
	@echo "  generate      Generate codec + tests from schema"
	@echo "  test-generated Build and run generated tests"
	@echo "  validate      Validate all example schemas"
	@echo "  validate-schema SCHEMA=path Validate single schema"
	@echo "  fuzz          Full fuzz test (10 min/schema - release)"
	@echo "  fuzz-quick    Quick fuzz test (10 sec - per commit)"
	@echo "  fuzz-hypothesis Hypothesis property-based testing"
	@echo "  fuzz-go       Go fuzz tests (requires Go 1.18+)"
	@echo "  fuzz-c        C libFuzzer tests (requires clang)"
	@echo "  fuzz-all      Run all Python fuzz methods"
	@echo "  test-go       Go interpreter tests (in docker)"
	@echo "  test-java     Java interpreter tests (in docker)"
	@echo "  test-dotnet   C# interpreter tests (in docker)"
	@echo "  test-languages Every implementation: Python, C, Go, Java, C#"
	@echo "  clean         Remove build artifacts"
	@echo "  help          Show this help"
	@echo ""
	@echo "Examples:"
	@echo "  make                          # Build debug"
	@echo "  make selftest                 # Build and run C tests"
	@echo "  make pytest                   # Run Python tests"
	@echo "  make pytest-cov               # Python tests + coverage"
	@echo "  make VARIANT=release          # Release build"
	@echo "  make coverage-all             # Full coverage report"
