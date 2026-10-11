package org.lora.schema;

import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.*;

/**
 * PS-539 (CR-2026-127) checks references in one decode order: within a mapping,
 * {@code tag_fields}, {@code trailer}, {@code fields} and {@code element} first, then the
 * remaining keys in source order. Each schema below differs from its pair only in the order
 * two keys are written, and that order alone decides whether the reference follows its
 * declaration. Each port is a decode of its own (CR-2026-130), so a name only another port
 * declares is never in scope. Mirrors tests/test_ps539_walk_order.py and
 * go/schema/ps539_walk_order_test.go.
 */
public class ReferenceWalkOrderTest {
    static final String ORDER = "is referenced before the schema declares it (PS-539, PS-122)";

    /** {schema body, message or null when it loads}. */
    static final String[][] CASES = {
            // A match's cases written before its discriminator declare the name it reads.
            {"fields:\n  - {name: k, type: u8}\n"
                    + "  - match: {cases: {1: [{name: x, type: u8}]}, default: skip, field: $x}\n", null},
            {"fields:\n  - {name: k, type: u8}\n"
                    + "  - match: {field: $x, default: skip, cases: {1: [{name: x, type: u8}]}}\n",
                    "`$x` (field) " + ORDER},
            // A match's default field list against its cases.
            {"fields:\n  - {name: k, type: u8}\n  - match: {field: $k, default: [{name: x, type: u8}], "
                    + "cases: {1: [{name: y, type: number, ref: $x}]}}\n", null},
            {"fields:\n  - {name: k, type: u8}\n  - match: {field: $k, default: [{name: y, type: number, ref: $x}], "
                    + "cases: {1: [{name: x, type: u8}]}}\n", "`$x` (ref) " + ORDER},
            // Each port is a decode of its own (CR-2026-130): a name only another port declares
            // is out of scope in either source order, and within a port the source order decides.
            {"ports:\n  9: {fields: [{name: x, type: u8}]}\n"
                    + "  10: {fields: [{name: y, type: number, ref: $x}]}\n", "`$x` (ref) " + ORDER},
            {"ports:\n  2: {fields: [{name: y, type: number, ref: $x}]}\n"
                    + "  1: {fields: [{name: x, type: u8}]}\n", "`$x` (ref) " + ORDER},
            {"ports:\n  9: {fields: [{name: x, type: u8}]}\n"
                    + "  10: {fields: [{name: x, type: u8}, {name: y, type: number, ref: $x}]}\n", null},
            {"ports:\n  10: {fields: [{name: x, type: u8}, {name: y, type: number, ref: $x}]}\n"
                    + "  9: {fields: [{name: z, type: u8}]}\n", null},
            {"ports:\n  10: {fields: [{name: y, type: number, ref: $x}, {name: x, type: u8}]}\n"
                    + "  9: {fields: [{name: z, type: u8}]}\n", "`$x` (ref) " + ORDER},
            {"metadata: {include: [{name: eui, source: $device_eui}]}\n"
                    + "ports:\n  1: {fields: [{name: a, type: number, ref: $eui}]}\n"
                    + "  2: {fields: [{name: b, type: number, ref: $eui}]}\n", null},
    };

    @Test
    public void referenceOrderFollowsTheSource() {
        for (String[] c : CASES) {
            String doc = "name: t\n" + c[0];
            if (c[1] == null) {
                assertDoesNotThrow(() -> Schema.fromYaml(doc), c[0]);
            } else {
                Exception e = assertThrows(SchemaException.class, () -> Schema.fromYaml(doc), c[0]);
                assertEquals(c[1], e.getMessage(), c[0]);
            }
        }
    }

    @Test
    public void referenceOrderFollowsTheSourceInJson() {
        // fromYaml reads JSON too; the key order is the document's.
        String ok = "{\"name\": \"t\", \"fields\": [{\"name\": \"k\", \"type\": \"u8\"}, {\"match\": "
                + "{\"cases\": {\"1\": [{\"name\": \"x\", \"type\": \"u8\"}]}, \"default\": \"skip\", \"field\": \"$x\"}}]}";
        String bad = "{\"name\": \"t\", \"fields\": [{\"name\": \"k\", \"type\": \"u8\"}, {\"match\": "
                + "{\"field\": \"$x\", \"default\": \"skip\", \"cases\": {\"1\": [{\"name\": \"x\", \"type\": \"u8\"}]}}}]}";
        assertDoesNotThrow(() -> Schema.fromYaml(ok));
        Exception e = assertThrows(SchemaException.class, () -> Schema.fromYaml(bad));
        assertEquals("`$x` (field) " + ORDER, e.getMessage());
    }
}
