package org.lora.schema;

import org.junit.jupiter.api.Test;
import java.util.ArrayList;
import java.util.HexFormat;
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.*;

/**
 * CR-2026-085 (bytes after the last field, a cut-off tlv tag) and CR-2026-086 (the order
 * of a field's arithmetic, amended: valid_range before the lookup; encoding inverts it in
 * reverse).
 *
 * <p>The fixtures {@code _language-conformance/leftover-bytes.yaml},
 * {@code range-before-lookup.yaml} and {@code match-default-skip.yaml} hold every
 * implementation to the decodes. What a fixture cannot state - an error, a warning not
 * reported twice, an encoder's output - is here. Mirrors tests/test_cr_2026_085_086.py.
 */
public class CR2026085086Test {

    private static final String ONE = "name: probe\nfields:\n- {name: a, type: u8}\n";

    private static Map<String, Object> decode(String yaml, String hex) {
        return Schema.fromYaml(yaml).decode(HexFormat.of().parseHex(hex));
    }

    private static List<String> warnings(Map<String, Object> out) {
        List<String> found = new ArrayList<>();
        if (out.get("_warnings") instanceof List<?> list) {
            for (Object w : list) found.add(String.valueOf(w));
        }
        return found;
    }

    private static List<String> leftover(Map<String, Object> out) {
        return warnings(out).stream().filter(w -> w.contains("PS-472")).toList();
    }

    @Test
    public void anExactFrameWarnsOfNothing() {
        Map<String, Object> out = decode(ONE, "2a");
        assertEquals(Map.of("a", 42L), out);
    }

    @Test
    public void bytesAfterTheLastFieldAreReported() {
        // PS-472: decoded as it would be otherwise, and the leftover named.
        Map<String, Object> out = decode(ONE, "2a0102");
        assertEquals(42L, ((Number) out.get("a")).longValue());
        assertEquals(List.of("2 byte(s) after the last field left undecoded, from offset 1 (PS-472)"),
                leftover(out));
    }

    @Test
    public void theLeftoverIsReportedAfterPortSelection() {
        String yaml = "name: probe\nports:\n  2:\n    fields:\n    - {name: a, type: u16}\n";
        Map<String, Object> out = Schema.fromYaml(yaml).decodeWithPort(
                HexFormat.of().parseHex("002a09"), 2);
        assertEquals(List.of("1 byte(s) after the last field left undecoded, from offset 2 (PS-472)"),
                leftover(out));
    }

    @Test
    public void aPs302WarningIsTheLeftoverWarning() {
        // The unknown tag is not decoded, so PS-302's warning already says what was left.
        String yaml = "name: probe\nfields:\n- tlv:\n    tag_size: 1\n    cases:\n"
                + "      1: [{name: a, type: u8}]\n";
        List<String> found = warnings(decode(yaml, "012a09ff"));
        assertEquals(1, found.size(), found.toString());
        assertTrue(found.get(0).contains("left undecoded") && !found.get(0).contains("PS-472"),
                found.toString());
    }

    @Test
    public void aTagCutOffAtTheEndIsAnError() {
        // PS-477: the plain tag_size form stopped the loop and reported success.
        String plain = "name: probe\nfields:\n- tlv:\n    tag_size: 2\n    cases:\n"
                + "      1: [{name: a, type: u8}]\n";
        String composite = "name: probe\nfields:\n- tlv:\n    tag_fields:\n"
                + "    - {name: ch, type: u8}\n    - {name: ty, type: u8}\n"
                + "    tag_key: [ch, ty]\n    cases:\n      '[1, 2]': [{name: a, type: u8}]\n";
        String[][] cases = {{plain, "00012a00"}, {composite, "01022a01"}};
        for (String[] c : cases) {
            SchemaException e = assertThrows(SchemaException.class, () -> decode(c[0], c[1]), c[1]);
            assertEquals("tlv entry at offset 3: 1 byte(s) remain, fewer than its 2-byte tag (PS-477)",
                    e.getMessage());
        }
    }

    @Test
    public void aCutOffTagIdentifiesTheTlvInADeviceSchema() throws java.io.IOException {
        // This errored before too, but as a short read naming no tlv.
        Schema schema = Schema.fromYamlFile(java.nio.file.Path.of(
                "..", "..", "schemas", "devices", "milesight", "ws50x.yaml"));
        SchemaException e = assertThrows(SchemaException.class,
                () -> schema.decodeWithPort(HexFormat.of().parseHex("ff291111"), 85));
        assertEquals("tlv entry at offset 3: 1 byte(s) remain, fewer than its 2-byte tag (PS-477)",
                e.getMessage());
    }

    @Test
    public void aLengthCutOffLeavesTheEntryOverFromItsTag() {
        String yaml = "name: probe\nfields:\n- tlv:\n    tag_size: 1\n    length_size: 2\n"
                + "    cases:\n      1: [{name: a, type: u8}]\n";
        Map<String, Object> out = decode(yaml, "0100012a0100");
        assertEquals(42L, ((Number) out.get("a")).longValue());
        assertEquals(List.of("2 byte(s) after the last field left undecoded, from offset 4 (PS-472)"),
                leftover(out));
    }

    @Test
    public void anEncoderWritesNothingAfterTheLastField() {
        // PS-474: the leftover bytes were never data, so they are not re-encoded.
        Schema schema = Schema.fromYaml(ONE);
        Map<String, Object> out = schema.decode(HexFormat.of().parseHex("2a0102"));
        Map<String, Object> data = new java.util.LinkedHashMap<>(out);
        data.remove("_warnings");
        assertArrayEquals(new byte[] {0x2a}, schema.encode(data).getPayload());
    }

    private static final String GUARDED = "name: probe\nfields:\n- {name: x, type: u8}\n"
            + "- name: v\n  type: number\n  ref: $x\n"
            + "  guard: {when: [{field: $x, gt: 0}], else: 99}\n"
            + "  valid_range: [0, 10]\n  out_of_range: omit\n";

    @Test
    public void aGuardElseIsNotComparedWithTheRange() {
        // PS-443 as amended: a guard reporting its `else` ends the sequence, so 99 is
        // reported though the range stops at 10, and its quality is good.
        Map<String, Object> out = decode(GUARDED, "00");
        assertEquals(99, ((Number) out.get("v")).intValue());
        assertEquals("good", ((Map<?, ?>) out.get("_quality")).get("v"));
    }

    @Test
    public void aValueTheGuardLetsThroughIsCompared() {
        Map<String, Object> out = decode(GUARDED, "20");
        assertFalse(out.containsKey("v"), out.toString());
        assertEquals("out_of_range", ((Map<?, ?>) out.get("_quality")).get("v"));
    }

    @Test
    public void aLookedUpFieldIsComparedOnItsNumber() {
        // PS-475: compared after the lookup, a label was never a number and read "good",
        // and an omitted index the sequence lacks failed the decode (PS-105).
        String yaml = "name: probe\nfields:\n"
                + "- {name: f, type: u8, add: -1, valid_range: [0, 2], lookup: [zero, one, two, three]}\n"
                + "- {name: o, type: u8, add: -1, valid_range: [0, 2], out_of_range: omit,"
                + " lookup: [zero, one, two]}\n";
        Map<String, Object> out = decode(yaml, "0404");
        assertEquals("three", out.get("f"));
        assertFalse(out.containsKey("o"), out.toString());
        assertEquals(Map.of("f", "out_of_range", "o", "out_of_range"), out.get("_quality"));
        Map<String, Object> ok = decode(yaml, "0202");
        assertEquals(Map.of("f", "good", "o", "good"), ok.get("_quality"));
    }

    @Test
    public void encodeInvertsInReverseWhateverTheKeyOrder() {
        // PS-476: the lookup is reversed first, then add, then div, then mult.
        String yaml = "name: probe\nfields:\n"
                + "- {name: v, type: u8, add: 1, div: 2, mult: 4, lookup: {3: three}}\n";
        Schema schema = Schema.fromYaml(yaml);
        Map<String, Object> out = schema.decode(new byte[] {1});
        assertEquals("three", out.get("v"));
        assertArrayEquals(new byte[] {1}, schema.encode(Map.of("v", "three")).getPayload());
    }

    /** A tag component is as wide as its type: a u16 was read as one byte, written as two. */
    @Test
    void aTagComponentIsReadAtItsTypeWidth() {
        String yaml = "name: probe\nfields:\n- tlv:\n    tag_fields:\n    - {name: ch, type: u16}\n"
                + "    tag_key: [ch]\n    cases:\n      \"[258]\":\n      - {name: a, type: u8}\n";
        Schema schema = Schema.fromYaml(yaml);
        Map<String, Object> out = schema.decode(HexFormat.of().parseHex("01022a"));
        assertEquals(42L, ((Number) out.get("a")).longValue(), String.valueOf(out));
        assertArrayEquals(HexFormat.of().parseHex("01022a"),
                schema.encode(Map.of("a", 42)).getPayload());
    }
}
