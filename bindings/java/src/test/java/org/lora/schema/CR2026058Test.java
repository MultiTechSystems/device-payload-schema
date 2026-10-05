package org.lora.schema;

import org.junit.jupiter.api.Test;
import java.util.HexFormat;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.*;

/**
 * CR-2026-058: what the specification described and never required. The fixtures in
 * _language-conformance cover what a vector can express; these cover the rejections,
 * errors and absent keys a vector cannot.
 */
public class CR2026058Test {

    private static Map<String, Object> decode(String fields, String hex) {
        return Schema.fromYaml("name: p\nfields:\n" + fields).decode(HexFormat.of().parseHex(hex));
    }

    @Test
    public void invalidConstructsAreRejectedAtLoad() {
        String[] cases = {
            "- {name: v, type: u8, transform: [{round: 1}]}\n",
            "- {name: v, type: u8, transform: [{op: floor}]}\n",
            "- {name: v, type: u8, transform: [{op: round, ties: up}]}\n",
            "- {name: v, type: u8, transform: [{sub: 3}]}\n",
            "- {name: v, type: bytes, length: 2, format: base64, separator: ':'}\n",
            "- {name: v, type: bytes, length: 2, format: 'hex:lower'}\n",
            "- byte_group: {size: 1, fields: [{name: a, type: 'u8[0:3]'}, {name: b, type: 'u8[2:5]'}]}\n",
            "- {name: k, type: u8, var: k}\n- match: {field: $k, length: 1, cases: {1: [{name: a, type: u8}]}}\n",
            "- match: {cases: {1: [{name: a, type: u8}]}}\n",
        };
        for (String fields : cases) {
            assertThrows(SchemaException.class, () -> Schema.fromYaml("name: p\nfields:\n" + fields), fields);
        }
    }

    @Test
    public void roundTiesEvenAndAway() {
        for (String[] c : new String[][]{{"even", "78.12", "-2"}, {"away", "78.13", "-3"}}) {
            Map<String, Object> out = decode(
                "- {name: h, type: u8, transform: [{mult: 100}, {div: 256}, {op: round, decimals: 2, ties: " + c[0] + "}]}\n"
                + "- {name: n, type: s8, transform: [{div: 2}, {op: round, ties: " + c[0] + "}]}\n", "C8FB");
            assertEquals(Double.parseDouble(c[1]), ((Number) out.get("h")).doubleValue(), 1e-12, c[0]);
            assertEquals(Double.parseDouble(c[2]), ((Number) out.get("n")).doubleValue(), 1e-12, c[0]);
        }
    }

    @Test
    public void exceedingMaxIsAnError() {
        String[][] cases = {{"count: $n", "4 byte(s) at offset 1"}, {"until: end", "2 byte(s) at offset 3"},
                            {"byte_length: 4", "2 byte(s) at offset 3"}};
        for (String[] c : cases) {
            Exception e = assertThrows(SchemaException.class, () -> decode(
                "- {name: n, type: u8, var: n}\n- {name: r, type: repeat, max: 2, " + c[0]
                + ", fields: [{name: v, type: u8}]}\n", "040A141E28"), c[0]);
            assertTrue(e.getMessage().contains("max of 2") && e.getMessage().contains(c[1]), e.getMessage());
        }
    }

    @Test
    public void aFailedGuardWithoutElseOmitsTheField() {
        Map<String, Object> out = decode("- {name: raw, type: u8}\n"
            + "- {name: scaled, type: number, ref: $raw, mult: 2, guard: {when: [{field: $raw, lt: 100}]}}\n"
            + "- {name: after, type: u8}\n", "C805");
        assertFalse(out.containsKey("scaled"), String.valueOf(out.get("scaled")));
        assertEquals(5L, ((Number) out.get("after")).longValue());
    }

    @Test
    public void quotedListCaseKeysMatchAnyElement() {
        Map<String, Object> out = decode("- {name: k, type: u8, var: k}\n"
            + "- match: {field: $k, cases: {'[1, 2, 3]': [{name: a, type: u8}]}}\n", "0207");
        assertEquals(7L, ((Number) out.get("a")).longValue());
    }
}
