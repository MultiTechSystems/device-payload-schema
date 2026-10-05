package org.lora.schema;

import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.*;

/**
 * CR-2026-038, -041, -042: a ports schema decoded with no FPort is an error (PS-459); a
 * port key is 1 to 255 (PS-018); a top-level fPort is a port, every ports key beside it
 * equals it, and it is never consulted when decoding (PS-335 to PS-337).
 */
public class CR2026038Test {
    private static final String PORTED =
        "name: p\nports:\n  1:\n    fields: [{name: a, type: u8}]\n  default:\n    fields: [{name: b, type: u8}]\n";

    @Test
    public void noFPortIsAnError() {
        Schema schema = Schema.fromYaml(PORTED);
        Exception e = assertThrows(SchemaException.class, () -> schema.decode(new byte[]{7}));
        assertTrue(e.getMessage().contains("no FPort was supplied"), e.getMessage());
        assertEquals(7L, ((Number) schema.decodeWithPort(new byte[]{7}, 9).get("b")).longValue());
    }

    @Test
    public void portDeclarationsAreChecked() {
        String[] invalid = {
            "name: p\nports:\n  0:\n    fields: [{name: a, type: u8}]\n",
            "name: p\nports:\n  256:\n    fields: [{name: a, type: u8}]\n",
            "name: p\nfPort: 0\nfields: [{name: a, type: u8}]\n",
            "name: p\nfPort: '225'\nfields: [{name: a, type: u8}]\n",
            "name: p\nfPort: 20\nports:\n  21:\n    fields: [{name: a, type: u8}]\n",
        };
        for (String yaml : invalid) {
            assertThrows(SchemaException.class, () -> Schema.fromYaml(yaml), yaml);
        }
        Schema schema = Schema.fromYaml("name: p\nfport: 225\nfields: [{name: a, type: u8}]\n");
        assertEquals(5L, ((Number) schema.decode(new byte[]{5}).get("a")).longValue());
    }
}
