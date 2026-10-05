package org.lora.schema;

import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.*;

/** 0.5.2 wave 4: the rejections and the minifloat encoder; the fixtures hold the decodes. */
public class Wave4Test {
    @Test
    public void rejections() {
        String[] fields = {
            "{name: v, type: s16, encoding: bcd}",
            "{name: v, type: u16, encoding: zigzag}",
            "{name: v, type: u8, match_value: {a: 1}}",
            "{name: v, type: bitfield_string, length: 1, parts: [[0, 8, octal]]}",
            "byte_group: {size: 1, fields: [{name: a, type: 'u8[0:3]', endian: little}]}",
        };
        for (String field : fields) {
            assertThrows(SchemaException.class, () -> Schema.fromYaml("name: p\nfields:\n  - " + field + "\n"), field);
        }
    }

    @Test
    public void minifloatEncoder() {
        assertEquals(0xF800L, Wave4.encodeMinifloat(FieldType.UFLT16, 0.5));
        assertEquals(0x6831L, Wave4.encodeMinifloat(FieldType.UFLT16, 0.001));
        assertEquals(0xF400L, Wave4.encodeMinifloat(FieldType.SFLT16, -0.25));
        assertEquals(0x3F0000L, Wave4.encodeMinifloat(FieldType.SFLT24, 1.0));
        assertThrows(SchemaException.class, () -> Wave4.encodeMinifloat(FieldType.UFLT16, 1.0));
    }
}
