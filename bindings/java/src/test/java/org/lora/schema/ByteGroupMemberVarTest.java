package org.lora.schema;

import org.junit.jupiter.api.Test;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;

/**
 * A member of a packed group with a {@code var:} and no name (reserved bits a later field
 * reads) binds the var and reports nothing. Java skipped any member without a name before
 * it reached the binding, so the reference read as absent. A named member's var is the
 * shared fixture byte-group-member-var.yaml.
 */
public class ByteGroupMemberVarTest {

    @Test
    public void anUnnamedMemberBindsItsVar() {
        Schema schema = Schema.fromYaml("name: t\nfields:\n"
                + "- byte_group: [{type: 'u8[4:7]', var: hi}, {name: lo, type: 'u8[0:3]'}]\n"
                + "- {name: c, type: number, ref: $hi}\n");
        Map<String, Object> out = schema.decode(new byte[] {0x12});
        assertEquals(2L, ((Number) out.get("lo")).longValue());
        assertEquals(1L, ((Number) out.get("c")).longValue());
        assertFalse(out.containsKey("hi"));
    }
}
