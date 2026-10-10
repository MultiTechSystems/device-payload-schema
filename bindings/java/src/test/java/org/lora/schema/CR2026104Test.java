package org.lora.schema;

import org.junit.jupiter.api.Test;

import java.util.Map;

import static org.junit.jupiter.api.Assertions.*;

/**
 * CR-2026-104: a lookup label may be a boolean (PS-106), reported as a JSON boolean, and
 * a label matches only an input of its own type (PS-513). Every label was stored as text,
 * so a boolean or number label decoded as a string and {@code false} encoded through
 * {@code [true, false]} as index 0. lookup-boolean-labels.yaml and lookup-number-labels.yaml
 * hold the decode and round trip; this holds what a fixture cannot state.
 */
public class CR2026104Test {
    private static final String PROBE = "name: p\nfields:\n"
        + "  - {name: m, type: \"u8[4:7]\", lookup: {0: false, default: true}}\n"
        + "  - {name: q, type: \"u8[0:0]\", consume: 1, lookup: [true, false]}\n";

    @Test
    public void decodesBooleans() {
        Schema schema = Schema.fromYaml(PROBE);
        Map<String, Object> out = schema.decode(new byte[] {0x31});
        assertEquals(Boolean.TRUE, out.get("m"));
        assertEquals(Boolean.FALSE, out.get("q"));
        out = schema.decode(new byte[] {0x00});
        assertEquals(Boolean.FALSE, out.get("m"));
        assertEquals(Boolean.TRUE, out.get("q"));
    }

    @Test
    public void sequenceEncodesByIndex() {
        Schema schema = Schema.fromYaml("name: p\nfields:\n  - {name: g, type: u8, lookup: [true, false]}\n");
        assertArrayEquals(new byte[] {1}, schema.encode(Map.of("g", false)).getPayload());
        assertArrayEquals(new byte[] {0}, schema.encode(Map.of("g", true)).getPayload());
    }

    @Test
    public void bitRangesEncode() {
        EncodeResult back = Schema.fromYaml(PROBE).encode(Map.of("m", false, "q", false));
        assertTrue(back.isSuccess(), String.valueOf(back.getErrors()));
        assertArrayEquals(new byte[] {1}, back.getPayload());
    }

    @Test
    public void defaultOnlyBooleanIsRefused() {
        Schema schema = Schema.fromYaml("name: p\nfields:\n  - {name: f, type: u8, lookup: {0: false, default: true}}\n");
        assertArrayEquals(new byte[] {0}, schema.encode(Map.of("f", false)).getPayload());
        EncodeResult refused = schema.encode(Map.of("f", true));
        assertFalse(refused.isSuccess());
        assertTrue(String.valueOf(refused.getErrors()).contains("PS-513"), String.valueOf(refused.getErrors()));
    }

    @Test
    public void labelsMatchByType() {
        Schema numbers = Schema.fromYaml("name: p\nfields:\n  - {name: n, type: u8, lookup: [0, 1]}\n");
        assertFalse(numbers.encode(Map.of("n", true)).isSuccess(), "true is not 1");
        Schema booleans = Schema.fromYaml("name: p\nfields:\n  - {name: n, type: u8, lookup: {0: false, 1: true}}\n");
        assertFalse(booleans.encode(Map.of("n", "true")).isSuccess(), "\"true\" is not true");
        Schema multiplier = Schema.fromYaml("name: p\nfields:\n  - {name: n, type: u8, lookup: {1: 1, 2: 5}}\n");
        assertEquals(5, multiplier.decode(new byte[] {2}).get("n"));
        assertArrayEquals(new byte[] {2}, multiplier.encode(Map.of("n", 5)).getPayload());
        assertFalse(multiplier.encode(Map.of("n", "5")).isSuccess(), "\"5\" is not 5");
        assertFalse(Schema.sameLabel(true, 1));
        assertFalse(Schema.sameLabel(1.0, true));
        assertTrue(Schema.sameLabel(1, 1.0));
    }

    @Test
    public void templateRejectsBooleanLabels() {
        SchemaException error = assertThrows(SchemaException.class, () -> Schema.fromYaml(
            "name: p\nfields:\n  - {name: f, type: u8, lookup: {0: false, default: \"v${value}\"}}\n"));
        assertTrue(error.getMessage().contains("PS-407"), error.getMessage());
    }
}
