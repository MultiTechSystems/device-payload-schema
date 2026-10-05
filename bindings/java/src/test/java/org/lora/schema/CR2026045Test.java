package org.lora.schema;

import org.junit.jupiter.api.Test;
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.*;

/** CR-2026-045 (references, PS-345 to PS-349, PS-461, PS-462) and CR-2026-051 (literals, PS-357 to PS-361). */
public class CR2026045Test {
    @Test
    public void invalidReferencesAreRejected() {
        String[][] cases = {
            {"PS-345", "definitions:\n  d: [{name: v, type: u8}]\nfields:\n  - $ref: '#/definitions/d'\n"},
            {"PS-348", "definitions:\n  d: {fields: [{name: v, type: u8}]}\nfields:\n  - $ref: '#/definitions/missing'\n"},
            {"PS-462", "definitions:\n  d: {fields: [{name: v, type: u8}]}\nfields:\n  - $ref: 'lib.yaml#/definitions/d'\n"},
            {"PS-461", "definitions:\n  d: {fields: [{name: v, type: u8}]}\nfields:\n  - $ref: '#/d'\n"},
            {"PS-349", "definitions:\n  a: {fields: [{$ref: '#/definitions/b'}]}\n  b: {fields: [{$ref: '#/definitions/a'}]}\nfields:\n  - {name: x, type: u8}\n"},
        };
        for (String[] c : cases) {
            Exception e = assertThrows(SchemaException.class, () -> Schema.fromYaml("name: p\n" + c[1]), c[0]);
            assertTrue(e.getMessage().contains(c[0]), e.getMessage());
        }
    }

    @Test
    public void aReferenceResolvesInANestedList() {
        Map<String, Object> out = Schema.fromYaml("name: p\ndefinitions:\n  d: {fields: [{name: v, type: u8}]}\nfields:\n"
            + "  - {name: o, type: object, fields: [{$ref: '#/definitions/d'}]}\n"
            + "  - {name: r, type: repeat, count: 2, fields: [{$ref: '#/definitions/d'}]}\n").decode(new byte[]{1, 2, 3});
        assertEquals(1L, ((Number) ((Map<?, ?>) out.get("o")).get("v")).longValue());
        assertEquals(2, ((List<?>) out.get("r")).size());
    }

    @Test
    public void literals() {
        for (String field : new String[]{"{name: k, type: string, value: 3}", "{name: k, type: number, value: '3'}",
                "{name: k, type: number, value: 3, mult: 2}"}) {
            assertThrows(SchemaException.class, () -> Schema.fromYaml("name: p\nfields:\n  - " + field + "\n"), field);
        }
        Schema bare = Schema.fromYaml("name: p\nfields:\n  - {name: s, type: string, length: 2}\n");
        Exception e = assertThrows(SchemaException.class, () -> bare.decode(new byte[]{'a', 'b'}));
        assertTrue(e.getMessage().contains("PS-361"), e.getMessage());
        Schema schema = Schema.fromYaml("name: p\nfields:\n  - {name: cid, type: u8, value: 16}\n"
            + "  - {name: unit, type: string, value: ppm}\n  - {name: x, type: u8}\n");
        EncodeResult result = schema.encode(Map.of("cid", 153, "x", 5));
        assertArrayEquals(new byte[]{0x10, 0x05}, result.getPayload());
        assertTrue(result.getWarnings().isEmpty(), String.valueOf(result.getWarnings()));
    }
}
