package org.lora.schema;

import org.junit.jupiter.api.Test;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.*;

/**
 * CR-2026-117: a reference naming nothing declared is refused at load (PS-539), and a guard
 * condition on an absent value fails, so its {@code else} applies (PS-540). Mirrors
 * tests/test_cr_2026_117_references_and_guards.py; the message is the same in all four.
 */
public class CR2026117Test {
    static final String MESSAGE = "`$batery` (ref) names no field, var, tag component, index or "
            + "count_as the schema declares (PS-539)";

    @Test
    public void anUndeclaredReferenceIsRefusedAtLoad() {
        Exception e = assertThrows(SchemaException.class, () -> Schema.fromYaml(
                "name: t\nfields:\n  - {name: battery, type: u8}\n"
                + "  - {name: x, type: number, ref: $batery}\n"));
        assertEquals(MESSAGE, e.getMessage());
        for (String field : new String[] {
                "  - {name: x, type: u8, name_from: \"k_${batery}\"}\n",
                "  - match: {field: $batery, cases: {1: [{name: y, type: u8}]}}\n",
                "  - {name: x, type: number, ref: $battery, guard: {when: [{field: $batery, gt: 0}], else: 0}}\n",
                "  - {name: x, type: repeat, count: $batery, fields: [{name: y, type: u8}]}\n"}) {
            Exception f = assertThrows(SchemaException.class, () -> Schema.fromYaml(
                    "name: t\nfields:\n  - {name: battery, type: u8}\n" + field), field);
            assertTrue(f.getMessage().contains("PS-539"), f.getMessage());
        }
    }

    @Test
    public void whatCountsAsDeclared() {
        for (String doc : new String[] {
                "fields:\n  - {name: raw, type: u8, var: kind}\n  - {name: n, type: number, ref: $kind}\n"
                + "  - tlv: {tag_fields: [{name: rsn, type: u8}, {name: ch, type: u8}], tag_key: ch, "
                + "cases: {1: [{name: r, type: number, ref: $rsn}]}}\n",
                "fields:\n  - {name: e, type: repeat, count: 1, index: i, count_as: k, fields: [{name: v, type: number, ref: $i}]}\n"
                + "  - {name: after, type: number, ref: $k}\n",
                "fields:\n  - {name: v, type: repeat, count: 2, present_if: {field: $element, ne: 0}, element: {type: u8}}\n",
                "fields:\n  - {name: a, type: u8}\n  - {name: b, type: number, ref: $eui}\n"
                + "metadata: {include: [{name: eui, source: $device_eui}]}\n",
                "fields:\n  - {name: a, type: u8, lookup: {1: $nothing}}\n"}) {
            assertDoesNotThrow(() -> Schema.fromYaml("name: t\n" + doc), doc);
        }
    }

    @Test
    public void aGuardOnAnAbsentValueFails() {
        String base = "name: t\nfields:\n  - {name: a, type: u8}\n  - {name: d, type: u8}\n"
                + "  - {name: r, type: number, compute: {op: div, a: $a, b: $d}}\n";
        String[][] cases = {
                {"{when: [{field: $r, gte: 0}], else: -1}", "-1"},
                {"{when: [{field: $r, ne: 3}], else: -1}", "-1"},
                {"{when: [{field: $r, gte: 0}]}", null}};
        for (String[] c : cases) {
            Map<String, Object> out = Schema.fromYaml(
                    base + "  - {name: c, type: number, ref: $a, guard: " + c[0] + "}\n")
                    .decode(new byte[] {0x08, 0x00});
            assertFalse(out.containsKey("r"), out.toString());
            if (c[1] == null) {
                assertFalse(out.containsKey("c"), c[0] + ": " + out);
            } else {
                assertEquals(Double.parseDouble(c[1]), ((Number) out.get("c")).doubleValue(), c[0] + ": " + out);
            }
        }
    }
}
