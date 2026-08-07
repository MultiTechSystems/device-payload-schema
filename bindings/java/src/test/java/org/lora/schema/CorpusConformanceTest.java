// Copyright (c) 2024-2026 Multitech Systems, Inc.
// SPDX-License-Identifier: MIT

package org.lora.schema;

import java.io.IOException;
import java.nio.file.*;
import java.util.*;
import java.util.stream.Stream;
import org.junit.jupiter.api.Test;
import org.yaml.snakeyaml.Yaml;

/**
 * Runs every test vector in the device corpus through this interpreter, the same
 * vectors the Python, Go and C# suites read.
 *
 * <p>Until this existed the Java suite had no tests at all, which is how this
 * implementation came to return an empty map for every TLV schema in the repository.
 * Constructs it does not support yet are expected to fail, so the pass count is
 * compared against a committed floor: raise the floor when a gap closes, and a drop
 * means something regressed.
 */
class CorpusConformanceTest {

    /** Vectors this interpreter is known to decode correctly. Raise as gaps close. */
    // This binding now decodes the whole corpus, so the floor is the full count and
    // any failure is a regression rather than a known gap.
    private static final int CORPUS_FLOOR = 1117;

    @Test
    void corpusVectorsDecodeAsExpected() throws IOException {
        Path root = Path.of("..", "..", "schemas", "devices");
        if (!Files.isDirectory(root)) {
            return; // corpus unavailable
        }
        int total = 0, passed = 0;
        Map<String, Integer> failures = new LinkedHashMap<>();

        List<Path> files;
        try (Stream<Path> walk = Files.walk(root)) {
            files = walk.filter(p -> p.toString().endsWith(".yaml")).sorted().toList();
        }

        for (Path file : files) {
            String text = Files.readString(file);
            Map<String, Object> raw;
            try {
                raw = new Yaml().load(text);
            } catch (RuntimeException e) {
                continue;
            }
            Object vectorsRaw = raw == null ? null : raw.get("test_vectors");
            if (!(vectorsRaw instanceof List<?> vectors) || vectors.isEmpty()) {
                continue;
            }
            Schema schema;
            try {
                schema = Schema.fromYaml(text);
            } catch (RuntimeException e) {
                total += vectors.size();
                failures.merge(file.getFileName() + ": parse: " + e.getMessage(), 1, Integer::sum);
                continue;
            }
            for (Object vectorRaw : vectors) {
                total++;
                if (!(vectorRaw instanceof Map<?, ?> vector)) continue;
                String payloadHex = String.valueOf(vector.get("payload")).replace(" ", "");
                Object expectedRaw = vector.get("expected");
                if (!(expectedRaw instanceof Map<?, ?> expected)) continue;
                try {
                    byte[] payload = hexToBytes(payloadHex);
                    // Both spellings occur in the corpus. Reading only `fPort` meant
                    // a port-based schema was decoded with no port at all, so every
                    // field of it was reported missing - a runner defect that looked
                    // like an interpreter gap.
                    Object fport = vector.get("fPort");
                    if (fport == null) fport = vector.get("fport");
                    Map<String, Object> out = fport instanceof Number n
                            ? schema.decodeWithPort(payload, n.intValue())
                            : schema.decode(payload);
                    String mismatch = null;
                    for (Map.Entry<?, ?> entry : expected.entrySet()) {
                        String key = String.valueOf(entry.getKey());
                        if (!out.containsKey(key)) {
                            mismatch = key + " missing";
                            break;
                        }
                        if (!valuesMatch(entry.getValue(), out.get(key))) {
                            mismatch = key + ": want " + entry.getValue() + ", got " + out.get(key);
                            break;
                        }
                    }
                    if (mismatch == null) {
                        passed++;
                    } else {
                        failures.merge(file.getFileName() + ": " + mismatch, 1, Integer::sum);
                    }
                } catch (RuntimeException e) {
                    failures.merge(file.getFileName() + ": " + e.getClass().getSimpleName()
                            + ": " + e.getMessage(), 1, Integer::sum);
                }
            }
        }

        System.out.printf("corpus vectors: %d total, %d passed, %d failed%n",
                total, passed, total - passed);
        int shown = 0;
        for (String detail : failures.keySet()) {
            if (shown++ >= 12) {
                System.out.printf("  ... and %d more distinct failures%n", failures.size() - 12);
                break;
            }
            System.out.println("  " + detail);
        }
        if (passed < CORPUS_FLOOR) {
            throw new AssertionError("only " + passed + " corpus vectors pass, floor is " + CORPUS_FLOOR);
        }
    }

    /**
     * The comparison the conformance tolerance defines: numeric within tolerance,
     * hex literals read as numbers, booleans as 0 and 1. Without this the runner
     * reported its own formatting differences as decode failures.
     */
    private static boolean valuesMatch(Object want, Object got) {
        Double a = asNumber(want);
        Double b = asNumber(got);
        if (a != null && b != null) {
            return Math.abs(a - b) <= Math.max(0.001, Math.abs(a) * 0.001);
        }
        return String.valueOf(want).equals(String.valueOf(got));
    }

    private static Double asNumber(Object value) {
        if (value instanceof Number number) return number.doubleValue();
        if (value instanceof Boolean flag) return flag ? 1.0 : 0.0;
        String text = String.valueOf(value);
        try {
            if (text.equalsIgnoreCase("true")) return 1.0;
            if (text.equalsIgnoreCase("false")) return 0.0;
            if (text.toLowerCase().startsWith("0x")) return (double) Long.parseLong(text.substring(2), 16);
            return Double.parseDouble(text);
        } catch (NumberFormatException e) {
            return null;
        }
    }

    private static byte[] hexToBytes(String text) {
        String cleaned = text.replaceAll("[^0-9a-fA-F]", "");
        byte[] out = new byte[cleaned.length() / 2];
        for (int i = 0; i < out.length; i++) {
            out[i] = (byte) Integer.parseInt(cleaned.substring(i * 2, i * 2 + 2), 16);
        }
        return out;
    }
}
