// Copyright (c) 2024-2026 Multitech Systems, Inc.
// SPDX-License-Identifier: MIT

package org.lora.schema;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.TreeMap;
import java.util.stream.Stream;
import org.junit.jupiter.api.Test;
import org.yaml.snakeyaml.Yaml;

/**
 * Encoding, measured against the decode corpus - the Java side of
 * {@code tests/test_encode_round_trip.py} and {@code go/schema/corpus_encode_test.go}.
 *
 * <p>Every corpus vector tested decoding and nothing tested encoding, because until now
 * this binding had no encoder to test: {@code Varint.encode} and an exception type were
 * all of it. {@code encode(decode(payload)) == payload} is the assertion the corpus gives
 * away for free, and it is what found the gaps worth fixing in the reference interpreter.
 *
 * <p>It cannot hold everywhere. A {@code skip} field's bytes are not recoverable from
 * output that omits them, a rounding stage discards precision, and a {@code lookup}
 * {@code default} label stands for every value the table does not list (PS-269). So the
 * floors are ratchets, not a target of 1191.
 */
class CorpusEncodeRoundTripTest {

    /**
     * The number of corpus vectors that re-encode to their exact payload. Raise it as
     * encoding improves; never lower it without saying why.
     *
     * <p>Unlike Go, this needs no separate ordered API: {@code decode} returns a
     * {@link LinkedHashMap}, so the order the TLV channels were read in survives into the
     * map the encoder reads back, which is what lets a multi-channel payload round-trip
     * rather than come back rearranged.
     */
    // CR-2026-024 packed a bare run of bit ranges the way byte_group was already
    // packed, so the three LoRaWAN header schemas round-trip: `plain fixed` rises from
    // 55 to 58 and the total to 1145.
    // CR-2026-027 gave this encoder the `default:` key beside a match's `cases`, so
    // match-default-fields.yaml round-trips: `match` rises from 43 to 44 and the
    // total to 1146.
    // CR-2026-028 gave this encoder the word-ordered u32le16/s32le16 case it never
    // had, so the flagged members of that type round-trip: `flagged` rises from 121
    // to 135 and the total to 1160.
    // CR-2026-030 resolved a `name_from` template on encode, so name-from.yaml
    // round-trips: `plain fixed` rises from 58 to 59 and the total to 1161.
    // CR-2026-031's name_from var-mismatch fixture round-trips here too, so
    // `plain fixed` rises from 59 to 61 and the total to 1163.
    private static final int ENCODE_FLOOR_TOTAL = 1166;

    /**
     * Per-shape floors, so a regression in a layout that works cannot hide behind the mass
     * of one that does not.
     */
    private static final Map<String, Integer> ENCODE_FLOOR_BY_SHAPE = Map.of(
            "tlv", 900,
            "flagged", 135,
            "plain fixed", 64,
            "match", 44,
            "byte_group", 17,
            "repeat", 6);

    /** The construct that dominates a schema's layout. */
    private static String schemaShape(String raw) {
        for (String key : new String[]{"tlv:", "match:", "repeat", "flagged:", "byte_group:"}) {
            if (raw.contains(key)) {
                return key.endsWith(":") ? key.substring(0, key.length() - 1) : key;
            }
        }
        return "plain fixed";
    }

    @Test
    void corpusVectorsReEncodeToTheirPayload() throws IOException {
        Path root = Path.of("..", "..", "schemas", "devices");
        if (!Files.isDirectory(root)) {
            return; // corpus unavailable
        }

        List<Path> files;
        try (Stream<Path> walk = Files.walk(root)) {
            files = walk.filter(p -> p.toString().endsWith(".yaml")).sorted().toList();
        }

        Map<String, Map<String, Integer>> byShape = new TreeMap<>();
        Map<String, Integer> errorDetail = new LinkedHashMap<>();
        int decoded = 0;

        for (Path file : files) {
            String text = Files.readString(file);
            Map<String, Object> raw;
            try {
                raw = new Yaml().load(text);
            } catch (RuntimeException e) {
                continue;
            }
            if (raw == null || !(raw.get("test_vectors") instanceof List<?> vectors)) {
                continue;
            }
            Schema schema;
            try {
                schema = Schema.fromYaml(text);
            } catch (RuntimeException e) {
                continue;
            }
            String shape = schemaShape(text);
            Map<String, Integer> counts =
                    byShape.computeIfAbsent(shape, k -> new LinkedHashMap<>());

            for (Object vectorRaw : vectors) {
                if (!(vectorRaw instanceof Map<?, ?> vector)) continue;
                byte[] payload;
                try {
                    payload = hexToBytes(String.valueOf(vector.get("payload")).replace(" ", ""));
                } catch (RuntimeException e) {
                    continue;
                }
                Object fport = vector.get("fPort");
                if (fport == null) fport = vector.get("fport");

                Map<String, Object> data;
                try {
                    data = fport instanceof Number n
                            ? schema.decodeWithPort(payload, n.intValue())
                            : schema.decode(payload);
                } catch (RuntimeException e) {
                    continue;   // a decode gap is CorpusConformanceTest's business
                }
                decoded++;

                EncodeResult result;
                try {
                    result = fport instanceof Number n
                            ? schema.encodeWithPort(data, n.intValue())
                            : schema.encode(data);
                } catch (RuntimeException e) {
                    counts.merge("error", 1, Integer::sum);
                    errorDetail.merge(file.getFileName() + ": " + e.getClass().getSimpleName()
                            + ": " + e.getMessage(), 1, Integer::sum);
                    continue;
                }

                if (!result.isSuccess()) {
                    counts.merge("error", 1, Integer::sum);
                    errorDetail.merge(file.getFileName() + ": " + result.getErrors().get(0),
                            1, Integer::sum);
                } else if (java.util.Arrays.equals(result.getPayload(), payload)) {
                    counts.merge("round-trips", 1, Integer::sum);
                } else if (result.getPayload().length != payload.length) {
                    counts.merge("length differs", 1, Integer::sum);
                } else {
                    counts.merge("bytes differ", 1, Integer::sum);
                }
            }
        }

        int exact = 0;
        for (Map.Entry<String, Map<String, Integer>> entry : byShape.entrySet()) {
            Map<String, Integer> c = entry.getValue();
            exact += c.getOrDefault("round-trips", 0);
            System.out.printf("%-12s round-trips=%-5d length=%-4d bytes=%-4d error=%-4d%n",
                    entry.getKey(), c.getOrDefault("round-trips", 0),
                    c.getOrDefault("length differs", 0), c.getOrDefault("bytes differ", 0),
                    c.getOrDefault("error", 0));
        }
        System.out.printf("total round-trips: %d of %d vectors decoded%n", exact, decoded);
        int shown = 0;
        for (Map.Entry<String, Integer> entry : errorDetail.entrySet()) {
            if (shown++ >= 12) break;
            System.out.println("  " + entry.getValue() + "x " + entry.getKey());
        }

        List<String> problems = new ArrayList<>();
        if (exact < ENCODE_FLOOR_TOTAL) {
            problems.add("only " + exact + " corpus vectors re-encode exactly, floor is "
                    + ENCODE_FLOOR_TOTAL);
        }
        for (Map.Entry<String, Integer> entry : ENCODE_FLOOR_BY_SHAPE.entrySet()) {
            int got = byShape.getOrDefault(entry.getKey(), Map.of())
                    .getOrDefault("round-trips", 0);
            if (got < entry.getValue()) {
                problems.add(entry.getKey() + ": " + got + " re-encode exactly, floor is "
                        + entry.getValue());
            }
        }
        if (!problems.isEmpty()) {
            throw new AssertionError(String.join("; ", problems));
        }
    }

    private static byte[] hexToBytes(String hex) {
        if (hex.length() % 2 != 0) {
            throw new IllegalArgumentException("odd-length hex: " + hex);
        }
        byte[] out = new byte[hex.length() / 2];
        for (int i = 0; i < out.length; i++) {
            int hi = Character.digit(hex.charAt(i * 2), 16);
            int lo = Character.digit(hex.charAt(i * 2 + 1), 16);
            if (hi < 0 || lo < 0) {
                throw new IllegalArgumentException("not hex: " + hex);
            }
            out[i] = (byte) ((hi << 4) | lo);
        }
        return out;
    }
}
