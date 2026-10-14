package org.lora.schema;

import org.junit.jupiter.api.Test;

import java.util.Map;

import static org.junit.jupiter.api.Assertions.*;

/**
 * PS-398, PS-151: a range case key has numeric bounds, not necessarily integers, and a
 * malformed one is rejected at load. Mirrors tests/test_range_case_keys.py.
 */
public class RangeKeyTest {

    private static String schema(String key) {
        return "name: p\nfields:\n- name: v\n  type: u8\n- match:\n    field: $v\n    default: []\n    cases:\n"
                + "      '" + key + "':\n        - {name: hit, type: u8}\n";
    }

    @Test
    public void aMalformedRangeKeyIsRejectedAtLoad() {
        for (String key : new String[] {"a..b", "1..", "..2", "1...5", "1.5.5..9", "5..2", "9..1.5"}) {
            SchemaException e = assertThrows(SchemaException.class, () -> Schema.fromYaml(schema(key)), key);
            assertTrue(e.getMessage().contains("match.cases[" + key + "]: ")
                    && e.getMessage().contains("PS-398"), e.getMessage());
        }
    }

    @Test
    public void anIntegerDiscriminatorMeetsFractionalBounds() {
        Schema s = Schema.fromYaml(schema("2.5..7.5"));
        int[] values = {2, 3, 7, 8};
        boolean[] want = {false, true, true, false};
        for (int i = 0; i < values.length; i++) {
            Map<String, Object> out = s.decode(new byte[] {(byte) values[i], 9});
            assertEquals(want[i], out.containsKey("hit"), "value " + values[i]);
        }
    }

    @Test
    public void aHexadecimalBoundMatches() {
        Schema s = Schema.fromYaml(schema("0x10..0x1F"));
        assertTrue(s.decode(new byte[] {0x10, 5}).containsKey("hit"));
        assertFalse(s.decode(new byte[] {0x20, 5}).containsKey("hit"));
        assertNotNull(RangeKey.parse("-10.5..-0.5"));
    }
}
