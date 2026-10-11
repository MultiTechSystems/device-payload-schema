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
    // CR-2026-071/073's three fixtures round-trip, all six vectors under `plain fixed`
    // (88 -> 94), and laq4's vendor vector under `match` (122 -> 123): the total rises
    // from 1679 to 1686. Measured equal before and after the CR's decode changes - a
    // computed field reads no bytes, so its value never reached the encoder's output.
    // CR-2026-085/086's fixtures add four round-trips, all under `plain fixed`
    // (96 -> 100): leftover-bytes.yaml's exact frame and range-before-lookup.yaml's three
    // in-range vectors (whose field, first named `flagged`, was renamed after this
    // classifier filed it under that shape). The leftover vector re-encodes to its first byte only
    // (PS-474) and the omitted out-of-range one has no value to write, both by design.
    // +2 with plain fixed 100 -> 102 (CR-2026-096): the meta-* fixtures that round-trip.
    private static final int ENCODE_FLOOR_TOTAL = 2084;   // +4: mask-reference-binding (flagged); -26 deliberately: merging CR-2026-136 with CR-2026-134 - 26 tlv vectors of milesight ct10x/ct30x/gs301/ws515 round-tripped under CR-2026-136 by leaving a status peek's bits zero; the peek's lookup misses on an ordinary reading, so the decode omits it (PS-269), and PS-551 rejects the input that lacks it; +1: CR-2026-110 tlv-repeat-merge object_members_combine; +3: CR-2026-125 (tlv-repeat-merge number_keeps_collecting, tlv-repeat-collect-first-case, tlv-repeat-merge-shared-member); +2: CR-2026-128 (tlv-repeat-merge-shared-last, and tlv-repeat-object-collect now that each element finds its case); +3: CR-2026-134 (PS-551) derives an internal match discriminator (internal-discriminator.yaml, both vectors) and a nested flagged mask (flagged-group-field-list.yaml inner_groups); +3: byte-group-width (CR-2026-131); +4: ascii-latin1 (CR-2026-114); +4: lookup-reference-binding vectors added for the mutation gate; +6: CR-2026-109/132 lookup-reference-binding (its miss, plain-default, enum-default and unnamed-enum vectors and the tag-component vectors do not round-trip); +1: merging CR-2026-109 with CR-2026-134 - lookup-reference-binding an_unnamed_enum_code_is_omitted_but_bound reports no discriminator, now derived from its case (PS-551); +265: CR-2026-136 lays a frame out as the decoder reads it - a bit-range peek no longer writes its byte twice, a flagged block is one field list, and a nested list carries its pending tail to the field after it (tlv +264: vobo 150, dnt 75, milesight 39) - and CR-2026-135 encoder-rounding adds 1 (plain fixed); the same set as the reference; +6: CR-2026-121/123 fixtures, all repeat (five new vectors, and repeat-element-null reserved_value now writes its sentinel back, PS-544); +6: CR-2026-121/123 fixtures, all repeat (five new vectors, and repeat-element-null reserved_value now writes its sentinel back, PS-544); +2: CR-2026-124 guard-set empty in/not_in (plain fixed); +4: match-u32-inline-discriminator (the peek fixture's internal word has no value to write back); +4: names-mangling-alike; +2: CR-2026-127 guard-boolean-operand; +2: CR-2026-118 guard-set (plain fixed; the records and present_if fixtures are refused, PS-542, PS-387); +6: registry divergence fixtures, all plain fixed (tag-component-ref does not round-trip); +5: CR-2026-111 element and index_base fixtures (repeat; the two null vectors are refused, PS-529); +8: name-from-rendering (CR-2026-112); +2: boolean-literal (CR-2026-107); +13: the nine TS013-defect fixtures (tlv-tag-bit-range does not round-trip); +8: CR-2026-104 lookup label fixtures (plain fixed); +3: byte-group-bool; +3: tlv-case-field-list; +2: tlv-case-key-patterns; +6: trimmed vendor vectors (am30x, ws50x, em310-tilt) now round-trip

    /**
     * Per-shape floors, so a regression in a layout that works cannot hide behind the mass
     * of one that does not.
     */
    private static final Map<String, Integer> ENCODE_FLOOR_BY_SHAPE = Map.of(
            // 1283 -> 1281 deliberately (CR-2026-067, PS-434): two vobo vectors round-tripped only because their internal byte halves were all zero, which the encoder wrote for an internal field with no value. It now reports such a field instead.
            "tlv", 1543,   // -26: CR-2026-136 with CR-2026-134 (see the total); +1: CR-2026-110 tlv-repeat-* fixtures; +3: CR-2026-125 (tlv-repeat-merge number_keeps_collecting, tlv-repeat-collect-first-case, tlv-repeat-merge-shared-member); +2: CR-2026-128 (tlv-repeat-merge-shared-last, and tlv-repeat-object-collect now that each element finds its case); +264: CR-2026-136 frame layout (vobo 150, dnt 75, milesight 39); +3: tlv-case-field-list; +2: tlv-case-key-patterns; +6: trimmed vendor vectors (am30x, ws50x, em310-tilt) now round-trip
            "flagged", 198,   // +4: mask-reference-binding; +1: CR-2026-134 flagged-group-field-list inner_groups (nested group's mask derived)
            "plain fixed", 148,   // +4: ascii-latin1 (CR-2026-114); +1: CR-2026-135 encoder-rounding; +2: CR-2026-124 guard-set; +4: names-mangling-alike; +2: CR-2026-127 guard-boolean-operand; +2: CR-2026-118 guard-set; +6: registry divergence fixtures; +8: name-from-rendering (CR-2026-112); +8: CR-2026-104 lookup label fixtures; +9: TS013 fixtures
            "match", 142,   // +1: CR-2026-109 with CR-2026-134 (see the total); +5: lookup-reference-binding (CR-2026-109); +1: CR-2026-132 enum port; +4: match-u32-inline-discriminator; +2: CR-2026-134 internal-discriminator (discriminator derived)
            "byte_group", 28,   // +3: byte-group-width (CR-2026-131); +3: byte-group-bool; +2: wide-group
            // 6 -> 5 with plain fixed 66 -> 67: a bucket move. Composed library schemas
            // now carry only the definitions they reach, so one no longer contains an
            // unreferenced definition's `repeat` text for the classifier to find.
            "repeat", 25);   // +6: CR-2026-121/123 fixtures and repeat-element-null reserved_value (PS-544); +5: CR-2026-111 element and index_base fixtures; +2: TS013 fixtures

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
        // `schema::vector` for every vector that does not re-encode, written where
        // ENCODE_ROUND_TRIP_LIST names a file: diff failing sets, never totals.
        List<String> failing = new ArrayList<>();
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
                    failing.add(root.relativize(file) + "::" + vector.get("name"));
                    continue;
                }

                if (!result.isSuccess() || !java.util.Arrays.equals(result.getPayload(), payload)) {
                    failing.add(root.relativize(file) + "::" + vector.get("name"));
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
        String listPath = System.getenv("ENCODE_ROUND_TRIP_LIST");
        if (listPath != null && !listPath.isEmpty()) {
            java.util.Collections.sort(failing);
            Files.write(Path.of(listPath), failing);
        }
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
