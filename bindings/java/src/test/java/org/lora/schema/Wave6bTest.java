package org.lora.schema;

import org.junit.jupiter.api.Test;

import java.util.HexFormat;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.*;

/**
 * 0.5.2 wave 6b: field-level rules. CR-2026-059 (optional), CR-2026-062 (match on bytes
 * remaining), CR-2026-065 (sentinel, out_of_range: omit), CR-2026-067 (internal fields) and
 * CR-2026-056 (a length naming a field). The _language-conformance fixtures hold the
 * decodes; this holds what a vector cannot express - rejections, errors and encoding.
 * Mirrors tests/test_wave6b_field_rules.py.
 */
public class Wave6bTest {

    private static byte[] hex(String text) {
        return HexFormat.of().parseHex(text.replace(" ", ""));
    }

    private static Map<String, Object> data(Object... kv) {
        Map<String, Object> out = new LinkedHashMap<>();
        for (int i = 0; i < kv.length; i += 2) out.put((String) kv[i], kv[i + 1]);
        return out;
    }

    private static final String[][] REJECTED = {
        {"PS-404", "name: p\nfields:\n  - {name: a, type: u8, optional: true}\n  - {name: b, type: u8}\n"},
        {"PS-404", "name: p\nfields:\n  - {name: r, type: repeat, count: 1, fields: "
            + "[{name: a, type: u8, optional: true}, {name: b, type: u8}]}\n"},
        {"PS-404", "name: p\nfields:\n  - {name: k, type: u8}\n  - match: {field: $k, cases: "
            + "{1: [{name: a, type: u8, optional: true}, {name: b, type: u8}]}}\n"},
        {"PS-433", "name: p\nfields:\n  - {name: _meta, type: u8}\n"},
        {"PS-433", "name: p\nfields:\n  - {name: _quality, type: number, value: 1}\n"},
        {"PS-433", "name: p\nfields:\n  - {name: k, type: u8}\n  - match: {field: $k, cases: "
            + "{1: [{name: _warnings, type: u8}]}}\n"},
        {"PS-416", "name: p\nfields:\n  - {name: k, type: u8, var: k}\n"
            + "  - match: {field: $k, remaining: true, cases: {1: [{name: a, type: u8}]}}\n"},
        {"PS-416", "name: p\nfields:\n  - match: {cases: {1: [{name: a, type: u8}]}}\n"},
        {"PS-414", "name: p\nfields:\n  - match: {remaining: false, cases: {1: [{name: a, type: u8}]}}\n"},
        {"PS-427", "name: p\nfields:\n  - {name: t, type: u8, sentinel: 5}\n"},
        {"PS-427", "name: p\nfields:\n  - {name: t, type: u8, sentinel: [x]}\n"},
        {"PS-428", "name: p\nfields:\n  - {name: t, type: u8, valid_range: [0, 1], out_of_range: drop}\n"},
    };

    @Test
    public void schemaRulesAreRejectedAtLoad() {
        for (String[] c : REJECTED) {
            SchemaException e = assertThrows(SchemaException.class, () -> Schema.fromYaml(c[1]), c[1]);
            assertTrue(e.getMessage().contains(c[0]), c[0] + " not in: " + e.getMessage());
        }
    }

    // PS-402, PS-403, PS-405 ----------------------------------------------------------

    private static final String OPTIONAL = "name: p\nfields:\n  - {name: a, type: u8}\n"
        + "  - {name: fw, type: u16, optional: true}\n  - {name: ble, type: u16, optional: true}\n";

    @Test
    public void optionalFieldsArePresentWhereTheirBytesRemain() {
        Schema schema = Schema.fromYaml(OPTIONAL);
        assertEquals(Map.of("a", 1L), schema.decode(hex("01")));
        assertEquals(Map.of("a", 1L, "fw", 258L), schema.decode(hex("01 0102")));
        assertEquals(Map.of("a", 1L, "fw", 258L, "ble", 772L), schema.decode(hex("01 0102 0304")));
    }

    @Test
    public void someBytesButTooFewIsAnError() {
        SchemaException e = assertThrows(SchemaException.class,
            () -> Schema.fromYaml(OPTIONAL).decode(hex("0102")));
        assertTrue(e.getMessage().contains("PS-403"), e.getMessage());
    }

    @Test
    public void anOptionalObjectIsPresentOrAbsentWhole() {
        Schema schema = Schema.fromYaml("name: p\nfields:\n  - {name: a, type: u8}\n"
            + "  - {name: gps, type: object, optional: true, fields: [{name: lat, type: s16}, {name: lon, type: s16}]}\n");
        assertEquals(Map.of("a", 1L), schema.decode(hex("01")));
        assertEquals(Map.of("a", 1L, "gps", Map.of("lat", 1L, "lon", 2L)), schema.decode(hex("0100010002")));
        SchemaException e = assertThrows(SchemaException.class, () -> schema.decode(hex("010001")));
        assertTrue(e.getMessage().contains("PS-403"), e.getMessage());
    }

    @Test
    public void anOptionalFieldSuppliedAfterAnOmittedOneIsRejected() {
        EncodeResult result = Schema.fromYaml(OPTIONAL).encode(data("a", 1L, "ble", 2L));
        assertFalse(result.isSuccess());
        assertTrue(result.getErrors().stream().anyMatch(e -> e.contains("PS-405")), result.getErrors().toString());
    }

    @Test
    public void omittedOptionalFieldsWriteNothing() {
        EncodeResult result = Schema.fromYaml(OPTIONAL).encode(data("a", 1L));
        assertTrue(result.isSuccess(), result.getErrors().toString());
        assertArrayEquals(hex("01"), result.getPayload());
        assertTrue(result.getWarnings().isEmpty(), result.getWarnings().toString());
    }

    // PS-414 ---------------------------------------------------------------------------

    @Test
    public void remainingExcludesAnEnclosingReserve() {
        Schema schema = Schema.fromYaml("name: p\nfields:\n"
            + "  - {name: r, type: repeat, until: end, reserve: 1, max: 1, fields: "
            + "[{match: {remaining: true, cases: {2: [{name: pair, type: u16}]}}}]}\n"
            + "  - {name: tail, type: u8}\n");
        assertEquals(Map.of("r", List.of(Map.of("pair", 258L)), "tail", 255L), schema.decode(hex("0102FF")));
    }

    @Test
    public void aRemainingMatchWritesNoDiscriminator() {
        Schema schema = Schema.fromYaml("name: p\nfields:\n  - {name: s, type: u8}\n"
            + "  - match: {remaining: true, cases: {2: [{name: short, type: u16}], \"4..255\": [{name: long, type: u32}]}}\n");
        assertArrayEquals(hex("01 01020304"), schema.encode(data("s", 1L, "long", 16909060L)).getPayload());
        assertArrayEquals(hex("01 0102"), schema.encode(data("s", 1L, "short", 258L)).getPayload());
    }

    // PS-427, PS-428 -------------------------------------------------------------------

    @Test
    public void sentinelComparesTheBitsBeforeAnEncoding() {
        Schema schema = Schema.fromYaml("name: p\nfields:\n  - {name: v, type: u8, encoding: bcd, sentinel: [255]}\n");
        assertEquals(Map.of(), schema.decode(hex("FF")));
        assertEquals(Map.of("v", 42L), schema.decode(hex("42")));
    }

    @Test
    public void sentinelComparesTheRawValueBeforeModifiers() {
        Schema schema = Schema.fromYaml("name: p\nfields:\n  - {name: t, type: s16, div: 10, sentinel: [-32768]}\n");
        assertEquals(Map.of(), schema.decode(hex("8000")));
        assertEquals(23.1, ((Number) schema.decode(hex("00E7")).get("t")).doubleValue(), 1e-9);
        // An encoder given no value writes the sentinel back, raw.
        assertArrayEquals(hex("8000"), schema.encode(Map.of()).getPayload());
    }

    @Test
    public void anOmittedValueIsNotBound() {
        Schema schema = Schema.fromYaml("name: p\nfields:\n  - {name: t, type: u8, sentinel: [255]}\n"
            + "  - {name: n, type: u8}\n  - {name: b, type: bytes, length: $t}\n");
        SchemaException e = assertThrows(SchemaException.class, () -> schema.decode(hex("FF 01")));
        assertTrue(e.getMessage().contains("PS-465"), e.getMessage());
    }

    @Test
    public void outOfRangeOmitDropsTheValue() {
        Schema schema = Schema.fromYaml("name: p\nfields:\n"
            + "  - {name: h, type: u8, valid_range: [0, 100], out_of_range: omit}\n"
            + "  - {name: f, type: u8, valid_range: [0, 100]}\n");
        assertEquals(Map.of("f", 200L), schema.decode(hex("C8 C8")));
        assertEquals(Map.of("h", 50L, "f", 50L), schema.decode(hex("32 32")));
    }

    // PS-434 ---------------------------------------------------------------------------

    @Test
    public void anInternalFieldWithNoValueAndNoInputIsAnEncodeError() {
        Schema schema = Schema.fromYaml("name: p\nfields:\n  - {name: _version, type: u8}\n  - {name: a, type: u8}\n");
        EncodeResult result = schema.encode(data("a", 1L));
        assertFalse(result.isSuccess());
        assertTrue(result.getErrors().stream().anyMatch(e -> e.contains("PS-434") && e.contains("_version")),
            result.getErrors().toString());
    }

    @Test
    public void anInternalFieldTakesItsValueFromTheInputWhereSupplied() {
        Schema schema = Schema.fromYaml("name: p\nfields:\n  - {name: _version, type: u8}\n  - {name: a, type: u8}\n");
        assertArrayEquals(hex("0301"), schema.encode(data("_version", 3L, "a", 1L)).getPayload());
    }

    @Test
    public void anInternalValueIsWrittenWhateverTheInputSays() {
        Schema schema = Schema.fromYaml("name: p\nfields:\n  - {name: _version, type: u8, value: 2}\n  - {name: a, type: u8}\n");
        assertArrayEquals(hex("0201"), schema.encode(data("_version", 9L, "a", 1L)).getPayload());
    }

    @Test
    public void anInternalBitRangeFollowsTheSameRule() {
        Schema schema = Schema.fromYaml("name: p\nfields:\n  - {name: _r, type: \"u8[0:3]\"}\n"
            + "  - {name: v, type: \"u8[4:7]\", consume: 1}\n");
        EncodeResult result = schema.encode(data("v", 1L));
        assertTrue(result.getErrors().stream().anyMatch(e -> e.contains("PS-434")), result.getErrors().toString());
        assertArrayEquals(hex("15"), schema.encode(data("_r", 5L, "v", 1L)).getPayload());
    }

    @Test
    public void anInternalByteGroupMemberFollowsTheSameRule() {
        Schema schema = Schema.fromYaml("name: p\nfields:\n  - byte_group: {size: 1, fields: "
            + "[{name: _r, type: \"u8[0:3]\"}, {name: v, type: \"u8[4:7]\"}]}\n");
        EncodeResult result = schema.encode(data("v", 1L));
        assertTrue(result.getErrors().stream().anyMatch(e -> e.contains("PS-434")), result.getErrors().toString());
        Schema declared = Schema.fromYaml("name: p\nfields:\n  - byte_group: {size: 1, fields: "
            + "[{name: _r, type: \"u8[0:3]\", value: 7}, {name: v, type: \"u8[4:7]\"}]}\n");
        assertArrayEquals(hex("17"), declared.encode(data("v", 1L)).getPayload());
    }

    @Test
    public void anInternalComputedFieldWritesNothing() {
        Schema schema = Schema.fromYaml("name: p\nfields:\n  - {name: a, type: u8}\n"
            + "  - {name: _double, type: number, ref: $a, mult: 2}\n");
        EncodeResult result = schema.encode(data("a", 4L));
        assertTrue(result.isSuccess(), result.getErrors().toString());
        assertArrayEquals(hex("04"), result.getPayload());
    }

    @Test
    public void anInternalMatchDiscriminatorIsNotInferred() {
        Schema schema = Schema.fromYaml("name: p\nfields:\n  - {name: _kind, type: u8}\n"
            + "  - match: {field: $_kind, cases: {1: [{name: t, type: u8}]}}\n");
        EncodeResult result = schema.encode(data("t", 5L));
        assertTrue(result.getErrors().stream().anyMatch(e -> e.contains("PS-434")), result.getErrors().toString());
    }

    // PS-464, PS-465 -------------------------------------------------------------------

    @Test
    public void aLengthMayNameAPrecedingField() {
        Schema schema = Schema.fromYaml("name: p\nfields:\n  - {name: n, type: u8}\n"
            + "  - {name: b, type: bytes, length: n}\n  - {name: after, type: u8}\n");
        assertEquals(Map.of("n", 2L, "b", "aabb", "after", 7L), schema.decode(hex("02 AABB 07")));
        assertArrayEquals(hex("02 AABB 07"), schema.encode(data("n", 2L, "b", "aabb", "after", 7L)).getPayload());
    }

    @Test
    public void anUnresolvedLengthNamesTheField() {
        Schema schema = Schema.fromYaml("name: p\nfields:\n  - {name: b, type: bytes, length: $n}\n");
        SchemaException e = assertThrows(SchemaException.class, () -> schema.decode(hex("01")));
        assertTrue(e.getMessage().contains("PS-465") && e.getMessage().contains("'n'"), e.getMessage());
    }
}
