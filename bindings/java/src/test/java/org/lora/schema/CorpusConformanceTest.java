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
    // The full corpus, so any failure is a regression rather than a known gap. The
    // three LoRaWAN frame vectors that used to fail here needed the sequential
    // bitfield form `u8:3`, which CR-2026-006 withdrew in favour of the bracket form
    // `u8[5:7]` this binding already supported.
    // CR-2026-007 settled the floored convention and this binding now uses
    // Math.floorMod for `mod` as well as Math.floorDiv for `idiv`, so its two
    // operators agree and the floor is the full corpus.
    private static final int CORPUS_FLOOR = 1193;

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
        // Recurse into lists and maps (PS-044, PS-045) rather than comparing their
        // printed forms. Stringifying made a nested list of integers fail against an
        // identical one: this binding decodes a u8 to a Double, so ts007's package
        // list printed as {package_id=0.0} where the vector says 0, even though every
        // value in it compared equal numerically.
        if (want instanceof List<?> wantList && got instanceof List<?> gotList) {
            if (wantList.size() != gotList.size()) return false;
            for (int i = 0; i < wantList.size(); i++) {
                if (!valuesMatch(wantList.get(i), gotList.get(i))) return false;
            }
            return true;
        }
        if (want instanceof Map<?, ?> wantMap && got instanceof Map<?, ?> gotMap) {
            for (Map.Entry<?, ?> entry : wantMap.entrySet()) {
                if (!gotMap.containsKey(entry.getKey())) return false;
                if (!valuesMatch(entry.getValue(), gotMap.get(entry.getKey()))) return false;
            }
            return true;
        }

        Double a = asNumber(want);
        Double b = asNumber(got);
        if (a != null && b != null) {
            // PS-039: an integer expectation must match exactly. PS-040's 0.001 is for
            // floats, and it is absolute. This used to be a relative
            // max(0.001, |want| * 0.001) applied to everything, which on a GPS
            // timestamp is about 20 days of slack.
            if (wantsInteger(want)) {
                return a.doubleValue() == b.doubleValue();
            }
            return Math.abs(a - b) <= 0.001;
        }
        return String.valueOf(want).equals(String.valueOf(got));
    }

    /**
     * Whether the vector wrote its expected value as an integer, which is what
     * selects exact comparison. A decoded value arriving as a Double does not make
     * the expectation a float - this binding widens every integer on the way out.
     */
    private static boolean wantsInteger(Object want) {
        if (want instanceof Integer || want instanceof Long
                || want instanceof Short || want instanceof Byte
                || want instanceof java.math.BigInteger) {
            return true;
        }
        if (want instanceof String text) {
            String trimmed = text.trim().toLowerCase();
            if (trimmed.startsWith("0x")) return true;
            if (trimmed.isEmpty() || trimmed.contains(".") || trimmed.contains("e")) return false;
            try {
                Long.parseLong(trimmed);
                return true;
            } catch (NumberFormatException e) {
                return false;
            }
        }
        return false;
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
