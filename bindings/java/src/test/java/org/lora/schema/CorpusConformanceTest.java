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
    // CR-2026-014's `expected_warnings` added three fixtures for the `unknown`
    // parameter, which no device schema sets, and the floor had drifted 29 below the
    // full count as vectors were added without it being raised. It is the full count
    // again: 1222.
    // CR-2026-020 brought the five implementations onto the same `match`, CR-2026-021
    // the same repeat `max`, and CR-2026-022 the same byte_length span, so their
    // fixtures pass everywhere and the full count is 1237.
    // CR-2026-031 added the name_from var-mismatch fixture, whose two vectors decode
    // everywhere, so the full count is 1239.
    // CR-2026-071/073 added six vectors in three _language-conformance fixtures; with
    // modifiers and the lookup applied to computed fields and a failed guard's `else`
    // reported as declared, all decode, and with laq4's vendor vector the full count of
    // payload vectors is 2363.
    // CR-2026-085/086 added six: leftover-bytes.yaml's two and range-before-lookup.yaml's
    // four, and match-default-skip.yaml's skipped body now expects the PS-472 warning.
    // With the leftover reported and valid_range compared before the lookup, all decode;
    // the full count of payload vectors is 2371.
    // CR-2026-096 added nine vectors (the meta-* fixtures); with _meta compared exactly
    // where a vector carries expected_meta, all pass: 2549.
    // CR-2026-097 added meta-tlv-channels.yaml: 2550.
    private static final int CORPUS_FLOOR = 2550;   // dl-atm41g2's two vendor vectors (+2); +121: vendor-codec vectors that kill vobo's mutation survivors

    @Test
    void corpusVectorsDecodeAsExpected() throws IOException {
        Path root = Path.of("..", "..", "schemas", "devices");
        String rootOverride = System.getenv("CORPUS_ROOT"); // see CorpusReport
        if (rootOverride != null && !rootOverride.isEmpty()) root = Path.of(rootOverride);
        if (!Files.isDirectory(root)) {
            return; // corpus unavailable
        }
        int total = 0, passed = 0;
        Map<String, Integer> failures = new LinkedHashMap<>();
        CorpusReport report = new CorpusReport();

        List<Path> files;
        try (Stream<Path> walk = Files.walk(root)) {
            files = walk.filter(p -> p.toString().endsWith(".yaml")).sorted().toList();
        }

        for (Path file : files) {
            String rel = CorpusReport.relative(root.relativize(file).toString());
            if (!report.includes(rel)) continue;
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
                for (int index = 0; index < vectors.size(); index++) {
                    if (!(vectors.get(index) instanceof Map<?, ?> vector)) continue;
                    if (vector.get("payload") == null) {
                        report.add(rel, index, vector.get("name"), "skip", "no payload (encode vector)");
                    } else {
                        report.add(rel, index, vector.get("name"), "error", "parse: " + e.getMessage());
                        report.addMeta(rel, index, vector.get("name"), null);
                    }
                }
                continue;
            }
            for (int index = 0; index < vectors.size(); index++) {
                Object vectorRaw = vectors.get(index);
                total++;
                if (!(vectorRaw instanceof Map<?, ?> vector)) continue;
                Object vectorName = vector.get("name");
                // An encode vector carries the values to encode and no payload to
                // decode (PS-047); it is not a failed decode. tools/vector-verdicts.py
                // runs those on both conformance paths.
                if (vector.get("payload") == null) {
                    report.add(rel, index, vectorName, "skip", "no payload (encode vector)");
                    continue;
                }
                String payloadHex = String.valueOf(vector.get("payload")).replace(" ", "");
                // CR-2026-096: the interpreter output's `_meta`, for every vector with a
                // payload when CORPUS_META_REPORT asks, and compared exactly where the
                // vector carries `expected_meta`. Null where the interpret fails (PS-497).
                Object expectedMeta = vector.get("expected_meta");
                Object meta = null;
                String metaError = null;
                if (expectedMeta != null || report.wantsMeta()) {
                    try {
                        meta = schema.interpret(hexToBytes(payloadHex), inputContext(vector)).get("_meta");
                    } catch (RuntimeException e) {
                        metaError = e.getClass().getSimpleName() + ": " + e.getMessage();
                    }
                    report.addMeta(rel, index, vectorName, meta);
                }
                Object expectedRaw = vector.get("expected");
                if (!(expectedRaw instanceof Map<?, ?> expected)) {
                    report.add(rel, index, vectorName, "skip", "no expected block");
                    continue;
                }
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
                    String mismatch = warningsMismatch(vector, out);
                    if (mismatch == null && expectedMeta != null) {
                        mismatch = metaError != null
                                ? "_meta: interpret failed: " + metaError
                                : metaMismatch(expectedMeta, meta, "_meta");
                    }
                    for (Map.Entry<?, ?> entry : expected.entrySet()) {
                        if (mismatch != null) break;
                        String key = String.valueOf(entry.getKey());
                        if (entry.getValue() == null) {
                            // PS-043 (CR-2026-075): null asserts the key is absent.
                            if (out.containsKey(key)) {
                                mismatch = key + ": reported " + out.get(key) + ", expected absent";
                                break;
                            }
                            continue;
                        }
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
                        report.add(rel, index, vectorName, "pass", "");
                    } else {
                        failures.merge(file.getFileName() + ": " + mismatch, 1, Integer::sum);
                        report.add(rel, index, vectorName, "fail", mismatch);
                    }
                } catch (RuntimeException e) {
                    failures.merge(file.getFileName() + ": " + e.getClass().getSimpleName()
                            + ": " + e.getMessage(), 1, Integer::sum);
                    report.add(rel, index, vectorName, "error",
                            e.getClass().getSimpleName() + ": " + e.getMessage());
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
        report.write();
        if (report.restricted()) {
            System.out.printf("CORPUS_ONLY/CORPUS_ROOT is set: %d schema(s) selected, floor %d not applied%n",
                    report.restrictedCount(), CORPUS_FLOOR);
        } else if (passed < CORPUS_FLOOR) {
            throw new AssertionError("only " + passed + " corpus vectors pass, floor is " + CORPUS_FLOOR);
        }
    }

    /**
     * A vector's input context (PS-495): its {@code fPort} or {@code fport}, and its
     * {@code input_metadata}'s {@code recvTime} and {@code devEUI} - and that block's
     * {@code fPort} where the vector gives none, as the reference's interpret() reads it.
     */
    static InputContext inputContext(Map<?, ?> vector) {
        Object fport = vector.get("fPort");
        if (fport == null) fport = vector.get("fport");
        Map<?, ?> metadata = vector.get("input_metadata") instanceof Map<?, ?> m ? m : Map.of();
        if (fport == null && isInteger(metadata.get("fPort"))) fport = metadata.get("fPort");
        Object devEUI = metadata.get("devEUI");
        return new InputContext(fport instanceof Number n ? Integer.valueOf(n.intValue()) : null,
                metadata.get("recvTime"), devEUI == null ? null : String.valueOf(devEUI));
    }

    private static boolean isInteger(Object value) {
        return value instanceof Integer || value instanceof Long || value instanceof Short
                || value instanceof Byte || value instanceof java.math.BigInteger;
    }

    /**
     * How {@code _meta} differs from {@code expected_meta}, or null where it is exactly
     * that (validate_schema.meta_matches): the same keys at every level and no others,
     * numbers by value ({@code 5} equals {@code 5.0}), a boolean never a number, and lists
     * element by element in order.
     */
    static String metaMismatch(Object want, Object got, String path) {
        if (want instanceof Map<?, ?> wantMap) {
            if (!(got instanceof Map<?, ?> gotMap)) {
                return path + ": expected an object, got " + got;
            }
            List<String> missing = new ArrayList<>();
            List<String> extra = new ArrayList<>();
            Set<String> wantKeys = new LinkedHashSet<>();
            for (Object k : wantMap.keySet()) wantKeys.add(String.valueOf(k));
            Map<String, Object> gotByKey = new LinkedHashMap<>();
            for (Map.Entry<?, ?> e : gotMap.entrySet()) gotByKey.put(String.valueOf(e.getKey()), e.getValue());
            for (String k : wantKeys) if (!gotByKey.containsKey(k)) missing.add(k);
            for (String k : gotByKey.keySet()) if (!wantKeys.contains(k)) extra.add(k);
            if (!missing.isEmpty() || !extra.isEmpty()) {
                return path + ": missing " + missing + ", unexpected " + extra;
            }
            for (Map.Entry<?, ?> e : wantMap.entrySet()) {
                String key = String.valueOf(e.getKey());
                String detail = metaMismatch(e.getValue(), gotByKey.get(key), path + "." + key);
                if (detail != null) return detail;
            }
            return null;
        }
        if (want instanceof List<?> wantList) {
            if (!(got instanceof List<?> gotList) || gotList.size() != wantList.size()) {
                return path + ": expected " + want + ", got " + got;
            }
            for (int i = 0; i < wantList.size(); i++) {
                String detail = metaMismatch(wantList.get(i), gotList.get(i), path + "[" + i + "]");
                if (detail != null) return detail;
            }
            return null;
        }
        boolean ok;
        if (want instanceof Boolean || got instanceof Boolean) {
            ok = want != null && want.equals(got);
        } else if (want instanceof Number a && got instanceof Number b) {
            ok = isInteger(a) && isInteger(b)
                    ? new java.math.BigInteger(a.toString()).equals(new java.math.BigInteger(b.toString()))
                    : a.doubleValue() == b.doubleValue();
        } else {
            ok = Objects.equals(want, got);
        }
        return ok ? null : path + ": expected " + want + ", got " + got;
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
                if (entry.getValue() == null) {
                    if (gotMap.containsKey(entry.getKey())) return false;   // PS-043
                    continue;
                }
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
     * How the warnings a decode produced differ from what the vector expects, or null
     * where they agree or the vector asserts nothing (PS-305 to PS-308).
     *
     * <p>Absent and {@code []} mean different things: absent asserts nothing, which is
     * most of the corpus, while {@code []} asserts that no warning was reported - the
     * form that catches a schema edit beginning to discard data. Entries are matched as
     * substrings, because the specification fixes what a warning must contain and not its
     * wording (PS-306), and positionally against a complete list (PS-305), so an
     * unexpected warning fails just as a missing one does.
     */
    @SuppressWarnings("unchecked")
    private static String warningsMismatch(Map<?, ?> vector, Map<String, Object> out) {
        Object declared = vector.get("expected_warnings");
        if (!(declared instanceof List<?> want)) {
            return null;
        }
        Object reported = out.get("_warnings");
        List<String> got = reported instanceof List
                ? (List<String>) reported
                : List.of();
        if (got.size() != want.size()) {
            return "expected " + want.size() + " warning(s), got " + got.size() + ": " + got;
        }
        for (int i = 0; i < want.size(); i++) {
            // An entry is a string, or a list of strings all of which must appear in that
            // one warning (PS-306): the tag and the byte count are not contiguous in any
            // implementation's text.
            Object entry = want.get(i);
            List<?> fragments = entry instanceof List<?> parts ? parts : List.of(entry);
            for (Object fragment : fragments) {
                if (!got.get(i).contains(String.valueOf(fragment))) {
                    return "warning[" + i + "]: \"" + fragment + "\" not found in \""
                            + got.get(i) + "\"";
                }
            }
        }
        return null;
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
