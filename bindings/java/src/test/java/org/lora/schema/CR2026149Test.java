package org.lora.schema;

import org.junit.jupiter.api.DynamicTest;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.TestFactory;
import org.yaml.snakeyaml.Yaml;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.HexFormat;
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.*;

/**
 * CR-2026-149 (PS-558, PS-559): bf16 is the upper half of a binary32; an encoder rounds once
 * from binary64, a tie to even, and rejects a finite overflow, naming the field. The cases
 * are shared with the reference, the generated codec, Go and C# in tests/cr-2026-149-cases.yaml.
 */
public class CR2026149Test {

    @SuppressWarnings("unchecked")
    private static List<Map<String, Object>> cases() throws IOException {
        Path file = Path.of("..", "..", "tests", "cr-2026-149-cases.yaml");
        Map<String, Object> doc = new Yaml().load(Files.readString(file));
        return (List<Map<String, Object>>) doc.get("cases");
    }

    @TestFactory
    List<DynamicTest> everySharedCase() throws IOException {
        List<DynamicTest> tests = new ArrayList<>();
        for (Map<String, Object> c : cases()) {
            tests.add(DynamicTest.dynamicTest(String.valueOf(c.get("name")), () -> {
                Schema schema = Schema.fromYaml(String.valueOf(c.get("schema")));
                Map<String, Object> input = new Yaml().load(String.valueOf(c.get("input")));
                EncodeResult result = schema.encode(input);
                if (c.containsKey("error")) {
                    String want = String.valueOf(c.get("error"));
                    // The text only: whether a rejected input returns a payload is CR-2026-134's
                    // rule, held by its own test, as the reference's test here does.
                    assertTrue(result.getErrors().stream().anyMatch(e -> e.contains(want)),
                            "want '" + want + "', got " + result.getErrors());
                } else {
                    assertTrue(result.isSuccess(), result.getErrors().toString());
                    assertEquals(String.valueOf(c.get("payload")),
                            HexFormat.of().formatHex(result.getPayload()));
                }
            }));
        }
        return tests;
    }

    /** NaN and the infinities cannot be written in a JSON input, so the shared cases omit them. */
    @Test
    public void specialValuesAreTheirWords() throws IOException {
        Schema schema = Schema.fromYaml("name: t\nfields:\n  - {name: x, type: bf16}\n");
        assertEquals("7f80", HexFormat.of().formatHex(
                schema.encode(Map.of("x", Double.POSITIVE_INFINITY)).getPayload()));
        assertEquals("ff80", HexFormat.of().formatHex(
                schema.encode(Map.of("x", Double.NEGATIVE_INFINITY)).getPayload()));
        assertEquals("7fc0", HexFormat.of().formatHex(
                schema.encode(Map.of("x", Double.NaN)).getPayload()));
        for (String word : new String[] {"7f80", "ff80", "7fc1"}) {
            assertFalse(schema.decode(HexFormat.of().parseHex(word)).containsKey("x"), word);
        }
    }
}
