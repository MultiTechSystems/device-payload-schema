package org.lora.schema;

import org.junit.jupiter.api.Test;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * An unrecognised `type:` is a schema error, not a one-byte read.
 *
 * <p>{@code FieldType.fromString} returned U8 for anything it did not list. Nothing
 * reported it, so a typo, or a type this binding had not implemented, read a single byte,
 * reported success, and shifted every following field by the difference between the
 * declared width and one. No corpus vector could catch it: the corpus only contains
 * spellings Java already knew, which is exactly why the silent branch survived.
 *
 * <p>Measured before the fix, against the other implementations: Go gave
 * {@code unknown field type: totally_bogus}, C# threw {@code Unknown field type}, the
 * Python interpreter gave {@code Unknown type: totally_bogus}, and Java alone returned
 * {@code {v=1, w=2}} with no error.
 */
public class UnknownFieldTypeTest {

    private static byte[] bytes(int... vals) {
        byte[] b = new byte[vals.length];
        for (int i = 0; i < vals.length; i++) b[i] = (byte) vals[i];
        return b;
    }

    @Test
    public void anUnknownTypeIsRejectedRatherThanReadAsOneByte() {
        String src = "name: p\nendian: big\nfields:\n"
                   + "- {name: v, type: totally_bogus}\n"
                   + "- {name: w, type: u8}\n";
        SchemaException e = assertThrows(SchemaException.class, () -> Schema.fromYaml(src));
        assertTrue(e.getMessage().contains("totally_bogus"),
                "the error must name the spelling it could not resolve: " + e.getMessage());
    }

    @Test
    public void theEndianPrefixSpellingsAreRejectedRatherThanSilentlyMisread() {
        // These decoded as a single byte and looked like support. Byte order is declared
        // with `endian:`, which field-endian.yaml covers; the prefix is not a type.
        for (String spelling : new String[]{"le_u16", "be_u16", "le_f32", "u16le"}) {
            String src = "name: p\nendian: big\nfields:\n- {name: v, type: " + spelling + "}\n";
            assertThrows(SchemaException.class, () -> Schema.fromYaml(src),
                    "expected " + spelling + " to be refused");
        }
    }

    @Test
    public void aBitRangeStillParsesAndIsNotMistakenForAnUnknownType() {
        // The bracket form is matched before fromString, which cannot parse it. Getting
        // that order wrong would turn every bitfield in the corpus into a parse failure.
        String src = "name: p\nendian: big\nfields:\n"
                   + "- {name: hi, type: 'u8[4:7]'}\n"
                   + "- {name: lo, type: 'u8[0:3]', consume: 1}\n";
        Map<String, Object> out = Schema.fromYaml(src).decode(bytes(0xAB));
        assertEquals(0xA, ((Number) out.get("hi")).intValue());
        assertEquals(0xB, ((Number) out.get("lo")).intValue());
    }

    @Test
    public void aFieldCarryingAConstructInsteadOfATypeStillParses() {
        // A construct field declares no `type:` at all; an absent type stays U8 so the
        // decoder can dispatch on the construct. Throwing on that would refuse every
        // tlv, match and $ref schema in the corpus.
        String src = "name: p\nendian: big\n"
                   + "definitions:\n  g:\n    fields:\n    - {name: a, type: u8}\n"
                   + "fields:\n- $ref: '#/definitions/g'\n- {name: tail, type: u8}\n";
        Map<String, Object> out = Schema.fromYaml(src).decode(bytes(1, 2));
        assertEquals(1, ((Number) out.get("a")).intValue());
        assertEquals(2, ((Number) out.get("tail")).intValue());
    }
}
