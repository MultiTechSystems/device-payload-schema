package org.lora.schema;

import org.junit.jupiter.api.Test;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.HexFormat;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * CR-2026-037: the type vocabulary is closed. An unknown or absent type is rejected when
 * the schema is loaded (PS-327, PS-328, PS-334); the PS-049 alias table is exhaustive
 * (PS-326) and case-sensitive (PS-333); udec and sdec are required (PS-329, PS-330).
 */
public class CR2026037Test {

    private static String one(String type) {
        return "name: p\nfields:\n- {name: v, type: '" + type + "'}\n";
    }

    @Test
    public void undefinedAndRecasedSpellingsAreRejectedNamingFieldAndType() {
        for (String spelling : new String[]{"float", "double", "UDec", "U8", "FLOAT32", "byte",
                "uint", "float16", "boolean", "switch", "Ctrl-Switch", "bits", "tlv",
                "version_string", "hex:upper"}) {
            SchemaException e = assertThrows(SchemaException.class,
                    () -> Schema.fromYaml(one(spelling)), spelling);
            assertTrue(e.getMessage().contains("'v'") && e.getMessage().contains(spelling),
                    "must name the field and the spelling: " + e.getMessage());
        }
    }

    @Test
    public void aFieldWithNoTypeAndNoConstructIsRejected() {
        SchemaException e = assertThrows(SchemaException.class,
                () -> Schema.fromYaml("name: p\nfields:\n- {name: v}\n"));
        assertTrue(e.getMessage().contains("declares no type"), e.getMessage());
    }

    @Test
    public void everyAliasDecodes() {
        Object[][] cases = {
            {"uint8", "07", 7.0}, {"uint16", "0102", 258.0}, {"uint32", "00000009", 9.0},
            {"uint64", "0000000000000009", 9.0}, {"i8", "FF", -1.0}, {"int8", "FE", -2.0},
            {"int16", "FFFC", -4.0}, {"int24", "FFFFFA", -6.0}, {"int32", "FFFFFFF8", -8.0},
            {"int64", "FFFFFFFFFFFFFFF6", -10.0}, {"udec", "25", 2.5}, {"sdec", "E5", -1.5},
        };
        for (Object[] c : cases) {
            Map<String, Object> out = Schema.fromYaml(one((String) c[0]))
                    .decode(HexFormat.of().parseHex((String) c[1]));
            assertEquals((double) c[2], ((Number) out.get("v")).doubleValue(), 1e-12,
                    (String) c[0]);
        }
    }

    @Test
    public void anIntegerComputedFieldReportsAnIntegerAndRejectsAFraction() {
        String src = "name: p\nfields:\n- {name: a, type: u8}\n"
                   + "- {name: half, type: integer, compute: {op: div, a: $a, b: 2}}\n";
        Schema schema = Schema.fromYaml(src);
        assertEquals(4L, schema.decode(new byte[]{8}).get("half"));
        assertThrows(SchemaException.class, () -> schema.decode(new byte[]{7}));
    }

    @Test
    public void theVocabularyFixtureRoundTrips() throws Exception {
        Path dir = Path.of("").toAbsolutePath();
        while (dir != null && !Files.isDirectory(dir.resolve("schemas/devices"))) {
            dir = dir.getParent();
        }
        Schema schema = Schema.fromYaml(Files.readString(
                dir.resolve("schemas/devices/_language-conformance/type-vocabulary.yaml")));
        String hex = "010203040506" + "0708090A" + "0000000000000001" + "FFFEFFFDFFFCFFFFFBFFFFFA"
                + "FFFFFFF9FFFFFFF8" + "FFFFFFFFFFFFFFF7FFFFFFFFFFFFFFF6FFFFFFFFFFFFFFF5"
                + "3C0025E5414243";
        byte[] payload = HexFormat.of().parseHex(hex);
        EncodeResult result = schema.encode(schema.decode(payload));
        assertTrue(result.isSuccess(), String.valueOf(result.getErrors()));
        assertEquals(hex, HexFormat.of().withUpperCase().formatHex(result.getPayload()));
    }
}
