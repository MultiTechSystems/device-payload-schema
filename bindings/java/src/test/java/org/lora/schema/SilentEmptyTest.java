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
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.*;

/**
 * No silent empty encode (PS-442, PS-551): an encoder never returns success having
 * written nothing for a construct whose decode reads bytes - a match with no case chosen,
 * a tlv entry no case claimed - nor an empty payload where the input gives a value only
 * payload bytes carry. The cases are shared with the reference, the generated codec, Go
 * and C# in tests/silent-empty-cases.yaml.
 */
public class SilentEmptyTest {

    @SuppressWarnings("unchecked")
    private static List<Map<String, Object>> cases() throws IOException {
        Path file = Path.of("..", "..", "tests", "silent-empty-cases.yaml");
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
                    assertTrue(result.getErrors().stream().anyMatch(e -> e.contains(want)),
                            "want '" + want + "', got " + result.getErrors());
                    assertEquals(0, result.getPayload().length, "a rejected input writes no payload");
                } else {
                    assertTrue(result.isSuccess(), result.getErrors().toString());
                    assertEquals(String.valueOf(c.get("payload")),
                            HexFormat.of().formatHex(result.getPayload()));
                }
            }));
        }
        return tests;
    }

    // The last line, which no path reaches today: each construct now fails by name first.
    // Held directly, as the reference's TestTheEmptyPayloadGuard holds its own copy.

    private static final String SCHEMA = String.join("\n",
            "name: t",
            "fields:",
            "  - {name: battery, type: u8}",
            "  - match:",
            "      remaining: true",
            "      cases:",
            "        2:",
            "          - {name: _w, type: u16}",
            "          - {name: distance, type: number, ref: $_w}",
            "      default:",
            "        - {name: distance, type: string, value: none}",
            "        - {name: kind, type: number, value: 4}",
            "");

    private static List<Field> fields() {
        return Schema.fromYaml(SCHEMA).getFields();
    }

    private static Map<String, Object> data(Object... kv) {
        Map<String, Object> m = new LinkedHashMap<>();
        for (int i = 0; i < kv.length; i += 2) m.put((String) kv[i], kv[i + 1]);
        return m;
    }

    @Test
    void declaredSourcesTellReadsFromConstants() {
        Map<String, Encoder.Source> sources = Encoder.declaredSources(fields());
        assertTrue(sources.get("battery").reads);
        assertTrue(sources.get("battery").literals.isEmpty());
        assertTrue(sources.get("distance").reads);
        assertEquals(List.of("none"), sources.get("distance").literals);
        assertFalse(sources.get("kind").reads);
        assertEquals(1, sources.get("kind").literals.size());
    }

    @Test
    void aValueOnlyBytesCarryIsNamed() {
        assertEquals("Encoding wrote no bytes, though the input gives 'battery', which the "
                + "schema reads from the payload (PS-442)",
                Encoder.emptyPayloadError(fields(), data("battery", 3)));
    }

    @Test
    void aValueAConstantGivesIsNot() {
        assertNull(Encoder.emptyPayloadError(fields(), data("distance", "none", "kind", 4)));
    }

    @Test
    void anotherValueOfANameBytesAlsoCarryIsNamed() {
        assertTrue(Encoder.emptyPayloadError(fields(), data("distance", 12)).contains("'distance'"));
    }

    @Test
    void valuesWrittenInNoBytesAreNot() {
        assertNull(Encoder.emptyPayloadError(fields(),
                data("battery", List.of(), "distance", "", "_w", 5, "other", 1, "x", null)));
    }
}
