package org.lora.schema;

import org.junit.jupiter.api.Test;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.*;

/**
 * CR-2026-133 (PS-550): an encoder given a repeat whose identity, unit or ipso.instance names
 * a sibling repeat rejects arrays of different lengths, naming both, as a decoder does
 * (PS-546). Mirrors tests/test_cr_2026_133_sibling_length_encode.py.
 */
public class CR2026133Test {
    private static final String SCHEMA = "name: t\nendian: big\nfields:\n"
            + "  - name: codes\n    type: repeat\n    count: 2\n"
            + "    element: {type: u8, lookup: {1: '1.8.1', 2: '2.8.1'}}\n"
            + "  - name: values\n    type: repeat\n    count: 2\n    index: i\n"
            + "    identity: $codes[i]\n    element: {type: u32}\n";

    private static Map<String, Object> data(String sibling, List<Object> values) {
        Map<String, Object> out = new LinkedHashMap<>();
        out.put(sibling, new ArrayList<>(Arrays.asList("1.8.1", "2.8.1")));
        out.put("values", values);
        return out;
    }

    private static void assertRejected(String yaml, String sibling, List<Object> values, String message) {
        EncodeResult encoded = Schema.fromYaml(yaml).encode(data(sibling, values));
        assertFalse(encoded.isSuccess());
        assertTrue(String.join("; ", encoded.getErrors()).contains(message), encoded.getErrors().toString());
    }

    @Test
    public void equalLengthsEncode() {
        EncodeResult encoded = Schema.fromYaml(SCHEMA).encode(data("codes", List.of(123, 456)));
        assertTrue(encoded.isSuccess(), encoded.toString());
        assertArrayEquals(new byte[] {1, 2, 0, 0, 0, 0x7b, 0, 0, 1, (byte) 0xc8}, encoded.getPayload());
    }

    @Test
    public void moreValuesThanCodesAreRejected() {
        assertRejected(SCHEMA, "codes", List.of(123, 456, 789),
                "Repeat 'values' is given 3 values and 'codes', which identifies them, 2 (PS-550)");
    }

    @Test
    public void fewerValuesThanCodesAreRejected() {
        assertRejected(SCHEMA, "codes", List.of(123),
                "Repeat 'values' is given 1 values and 'codes', which identifies them, 2 (PS-550)");
    }

    @Test
    public void aSiblingUnitIsHeldToIt() {
        String yaml = "name: t\nendian: big\nfields:\n"
                + "  - name: units\n    type: repeat\n    count: 2\n"
                + "    element: {type: u8, lookup: {1: '1.8.1', 2: '2.8.1'}}\n"
                + "  - name: values\n    type: repeat\n    count: 2\n    index: i\n"
                + "    element: {type: u32, unit: '$units[i]'}\n";
        assertRejected(yaml, "units", List.of(1),
                "Repeat 'values' is given 1 values and 'units', which identifies them, 2 (PS-550)");
    }
}
