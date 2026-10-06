package org.lora.schema;

import org.junit.jupiter.api.Test;

import java.util.HexFormat;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.*;

/**
 * 0.5.2 wave 6a: the repeat iterator and reserved trailers. CR-2026-048 (reserve,
 * trailer), CR-2026-081 (tlv reserve), CR-2026-053 (index, count_as, present_if),
 * CR-2026-080 (count_as after present_if), CR-2026-055 (carry) and CR-2026-054's identity
 * and per-element annotations. The _language-conformance fixtures hold the decodes; this
 * holds what a vector cannot express - rejections, errors and encoding. Mirrors
 * tests/test_wave6a_repeat_iterator.py.
 */
public class Wave6aTest {

    private static byte[] hex(String text) {
        return HexFormat.of().parseHex(text.replace(" ", ""));
    }

    /** A repeat over u8 elements running to the end, with extra keys in flow YAML. */
    private static String repeat(String extra) {
        return "name: probe\nfields:\n"
            + "  - {name: r, type: repeat, until: end, fields: [{name: v, type: u8}]"
            + (extra.isEmpty() ? "" : ", " + extra) + "}\n";
    }

    private static final String[][] REJECTED = {
        {"PS-350", "name: p\nfields:\n  - {name: r, type: repeat, count: 1, reserve: 1, fields: [{name: v, type: u8}]}\n"},
        {"PS-350", repeat("reserve: -1")},
        {"PS-383", repeat("trailer: [{name: t, type: u8}]")},
        {"PS-384", repeat("reserve: 2, trailer: [{name: t, type: u8}]")},
        {"PS-384", repeat("reserve: 1, trailer: [{name: t, type: ascii, length: remaining}]")},
        {"PS-369", repeat("index: v")},
        {"PS-369", repeat("index: k, count_as: k")},
        {"PS-369", "name: p\nfields:\n  - {name: outer, type: repeat, count: 1, index: i, fields: "
            + "[{name: inner, type: repeat, count: 1, index: i, fields: [{name: v, type: u8}]}]}\n"},
        {"PS-386", repeat("present_if: {field: $v}")},
        {"PS-372", repeat("identity: $nope")},
        {"PS-381", "name: probe\nfields:\n  - {name: r, type: repeat, until: end, "
            + "fields: [{name: v, type: u8, carry: 0}]}\n"},
        {"PS-381", "name: p\nfields:\n  - {name: x, type: number, value: 1, carry: 0}\n"},
        {"PS-381", "name: probe\nfields:\n  - {name: r, type: repeat, until: end, fields: [{name: v, type: u8}, "
            + "{name: t, type: number, carry: zero, compute: {op: add, a: $t, b: $v}}]}\n"},
        {"PS-374", "name: probe\nfields:\n  - {name: r, type: repeat, until: end, fields: "
            + "[{name: code, type: u8}, {name: v, type: u8, unit: $code}]}\n"},
        {"PS-374", "name: probe\nfields:\n  - {name: r, type: repeat, until: end, index: i, fields: "
            + "[{name: v, type: u8, ipso: {object: 3303, instance: $i}}]}\n"},
        {"PS-373", "name: probe\nfields:\n  - {name: r, type: repeat, count: 2, fields: "
            + "[{name: v, type: u8, senml: {name: \"t_${nope}\"}}]}\n"},
        {"PS-471", "name: p\nfields:\n  - tlv: {tag_size: 1, reserve: -1, cases: {1: [{name: a, type: u8}]}}\n"},
    };

    @Test
    public void schemaRulesAreRejectedAtLoad() {
        for (String[] c : REJECTED) {
            SchemaException e = assertThrows(SchemaException.class, () -> Schema.fromYaml(c[1]), c[1]);
            assertTrue(e.getMessage().contains(c[0]), c[0] + " not in: " + e.getMessage());
        }
    }

    @Test
    public void aValidIdentitySchemaIsAccepted() {
        Schema schema = Schema.fromYaml("name: probe\nfields:\n"
            + "  - name: r\n    type: repeat\n    until: end\n    index: i\n    max: 4\n    identity: i\n"
            + "    fields:\n"
            + "      - {name: code, type: u8, lookup: {1: Cel, 2: \"%RH\"}}\n"
            + "      - {name: v, type: u8, unit: $code, ipso: {object: 3303, instance: $i}, senml: {name: \"t_${i}\"}}\n");
        Map<String, Object> out = schema.decode(hex("0102"));
        assertEquals(List.of(Map.of("code", "Cel", "v", 2L)), out.get("r"));
    }

    // PS-351, PS-471: too few bytes for the reserve ----------------------------------

    @Test
    public void tooFewBytesForARepeatReserveIsAnError() {
        Schema schema = Schema.fromYaml(repeat("reserve: 2"));
        Exception e = assertThrows(SchemaException.DecodeException.class, () -> schema.decode(hex("01")));
        assertTrue(e.getMessage().contains("PS-351"), e.getMessage());
    }

    @Test
    public void tooFewBytesForATrailerIsAnError() {
        Schema schema = Schema.fromYaml(repeat("reserve: 2, trailer: [{name: t, type: u16}]"));
        Exception e = assertThrows(SchemaException.DecodeException.class, () -> schema.decode(hex("01")));
        assertTrue(e.getMessage().contains("PS-351"), e.getMessage());
    }

    @Test
    public void tooFewBytesForATlvReserveIsAnError() {
        Schema schema = Schema.fromYaml("name: p\nfields:\n"
            + "  - tlv: {tag_size: 1, reserve: 2, cases: {1: [{name: a, type: u8}]}}\n");
        Exception e = assertThrows(SchemaException.DecodeException.class, () -> schema.decode(hex("01")));
        assertTrue(e.getMessage().contains("PS-471"), e.getMessage());
    }

    @Test
    public void aRaggedTailIsMeasuredAgainstTheRegion() {
        // PS-350: the ragged-tail rule applies to the payload less the reserved bytes.
        Schema schema = Schema.fromYaml("name: probe\nfields:\n"
            + "  - {name: r, type: repeat, until: end, reserve: 1, fields: [{name: v, type: u16}]}\n");
        assertEquals(List.of(Map.of("v", 1L)), schema.decode(hex("000102")).get("r"));
        Exception e = assertThrows(SchemaException.DecodeException.class, () -> schema.decode(hex("00010203")));
        assertTrue(e.getMessage().contains("ragged"), e.getMessage());
    }

    // PS-368: element names are scoped ------------------------------------------------

    @Test
    public void aReferenceFromOutsideToAnElementNameIsAnError() {
        Schema schema = Schema.fromYaml("name: p\nfields:\n"
            + "  - {name: r, type: repeat, count: 2, fields: [{name: v, type: u8}]}\n"
            + "  - {name: last, type: number, ref: $v}\n");
        Exception e = assertThrows(SchemaException.DecodeException.class, () -> schema.decode(hex("0102")));
        assertTrue(e.getMessage().contains("PS-368"), e.getMessage());
    }

    @Test
    public void aReferenceToAFieldNotYetDecodedIsAnError() {
        Schema schema = Schema.fromYaml("name: p\nfields:\n"
            + "  - {name: r, type: repeat, count: 1, fields: [{name: early, type: number, ref: $v}, {name: v, type: u8}]}\n");
        Exception e = assertThrows(SchemaException.DecodeException.class, () -> schema.decode(hex("05")));
        assertTrue(e.getMessage().contains("PS-368"), e.getMessage());
    }

    @Test
    public void anOtherwiseUnboundReferenceStillReadsAsZero() {
        Schema schema = Schema.fromYaml("name: p\nfields:\n"
            + "  - {name: v, type: u8}\n  - {name: w, type: number, ref: $nowhere}\n");
        assertEquals(0L, schema.decode(hex("05")).get("w"));
    }

    @Test
    public void countAsIsNotBoundInsideTheElements() {
        // PS-367: count_as exists only once the repeat completes.
        Schema schema = Schema.fromYaml("name: p\nfields:\n"
            + "  - {name: r, type: repeat, count: 1, count_as: n, fields: [{name: v, type: u8}, "
            + "{name: seen, type: number, ref: $n}]}\n");
        assertEquals(List.of(Map.of("v", 5L, "seen", 0L)), schema.decode(hex("05")).get("r"));
    }

    @Test
    public void neitherIndexNorCountAsIsReported() {
        // PS-370.
        Schema schema = Schema.fromYaml(repeat("index: i, count_as: n"));
        Map<String, Object> out = schema.decode(hex("0102"));
        assertEquals(Map.of("r", List.of(Map.of("v", 1L), Map.of("v", 2L))), out);
    }

    @Test
    public void carryFromAFieldDecodedBeforeTheRepeat() {
        // PS-378: carry may name a field decoded before the repeat.
        Schema schema = Schema.fromYaml("name: p\nfields:\n"
            + "  - {name: start, type: u8}\n"
            + "  - {name: r, type: repeat, count: 2, fields: [{name: step, type: u8}, "
            + "{name: level, type: number, carry: $start, compute: {op: add, a: $level, b: $step}}]}\n");
        assertEquals(List.of(Map.of("step", 1L, "level", 101L), Map.of("step", 5L, "level", 106L)),
            schema.decode(hex("640105")).get("r"));
    }

    // Encoding: PS-370, PS-385, PS-387 -----------------------------------------------

    @Test
    public void theTrailerIsWrittenAfterTheElements() {
        Schema schema = Schema.fromYaml(repeat("reserve: 1, trailer: [{name: base, type: u8}]"));
        Map<String, Object> data = new LinkedHashMap<>();
        data.put("r", List.of(Map.of("v", 1), Map.of("v", 2)));
        data.put("base", 9);
        EncodeResult result = schema.encode(data);
        assertArrayEquals(hex("010209"), result.getPayload(), String.valueOf(result.getErrors()));
        assertEquals(Map.of("base", 9L, "r", List.of(Map.of("v", 1L), Map.of("v", 2L))),
            schema.decode(hex("010209")));
    }

    @Test
    public void theIndexIsBoundWhileEncoding() {
        // PS-370: a name_from template resolves on encode as it did on decode.
        Schema schema = Schema.fromYaml("name: p\nfields:\n"
            + "  - {name: r, type: repeat, count: 2, index: i, fields: [{name: v, type: u8, name_from: \"ch_${i}\"}]}\n");
        Map<String, Object> decoded = schema.decode(hex("0708"));
        assertEquals(Map.of("r", List.of(Map.of("ch_0", 7L), Map.of("ch_1", 8L))), decoded);
        EncodeResult result = schema.encode(decoded);
        assertArrayEquals(hex("0708"), result.getPayload(), String.valueOf(result.getErrors()));
    }

    @Test
    public void presentIfOverByteReadingElementsCannotBeEncoded() {
        Schema schema = Schema.fromYaml(repeat("present_if: {field: $v, ne: 0}"));
        EncodeResult result = schema.encode(Map.of("r", List.of(Map.of("v", 1))));
        assertFalse(result.isSuccess());
        assertTrue(result.getErrors().stream().anyMatch(e -> e.contains("PS-387")), String.valueOf(result.getErrors()));
    }
}
