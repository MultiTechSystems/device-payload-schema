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
 * CR-2026-196 (PS-635 to PS-637): a {@code type: number} field declaring {@code source}
 * reports the FPort, the payload length or the bytes remaining, on decode, encode and load.
 * The cases are shared with the reference, the generated codec, Go and C# in
 * tests/cr-2026-196-cases.yaml.
 */
public class CR2026196Test {

    @SuppressWarnings("unchecked")
    private static List<Map<String, Object>> section(String key) throws IOException {
        Path file = Path.of("..", "..", "tests", "cr-2026-196-cases.yaml");
        Map<String, Object> doc = new Yaml().load(Files.readString(file));
        return (List<Map<String, Object>>) doc.get(key);
    }

    private static void assertSameValue(Object want, Object got, String key) {
        if (want instanceof Number w && got instanceof Number g) {
            assertEquals(w.doubleValue(), g.doubleValue(), key);
        } else {
            assertEquals(String.valueOf(want), String.valueOf(got), key);
        }
    }

    @TestFactory
    List<DynamicTest> decodeCases() throws IOException {
        List<DynamicTest> tests = new ArrayList<>();
        for (Map<String, Object> c : section("decode")) {
            tests.add(DynamicTest.dynamicTest(String.valueOf(c.get("name")), () -> {
                Schema schema = Schema.fromYaml(String.valueOf(c.get("schema")));
                byte[] payload = HexFormat.of().parseHex(String.valueOf(c.get("payload")));
                Map<String, Object> got = new java.util.LinkedHashMap<>(
                        c.get("fport") instanceof Number n ? schema.decodeWithPort(payload, n.intValue())
                                : schema.decode(payload));
                Object warned = got.remove("_warnings");
                if (c.containsKey("warnings")) {
                    assertEquals(String.valueOf(c.get("warnings")),
                            String.valueOf(warned == null ? List.of() : warned));
                }
                Map<?, ?> want = (Map<?, ?>) c.get("expected");
                assertEquals(want.size(), got.size(), "got " + got);
                for (Map.Entry<?, ?> e : want.entrySet()) {
                    assertSameValue(e.getValue(), got.get(e.getKey()), String.valueOf(e.getKey()));
                }
            }));
        }
        return tests;
    }

    @TestFactory
    List<DynamicTest> encodeCases() throws IOException {
        List<DynamicTest> tests = new ArrayList<>();
        for (Map<String, Object> c : section("cases")) {
            tests.add(DynamicTest.dynamicTest(String.valueOf(c.get("name")), () -> {
                Schema schema = Schema.fromYaml(String.valueOf(c.get("schema")));
                Map<String, Object> input = new Yaml().load(String.valueOf(c.get("input")));
                EncodeResult result = c.get("fport") instanceof Number n
                        ? schema.encodeWithPort(input, n.intValue()) : schema.encode(input);
                if (c.containsKey("error")) {
                    String want = String.valueOf(c.get("error"));
                    assertTrue(result.getErrors().stream().anyMatch(e -> e.contains(want)),
                            "want '" + want + "', got " + result.getErrors());
                    assertEquals(0, result.getPayload().length);
                } else {
                    assertTrue(result.isSuccess(), result.getErrors().toString());
                    assertEquals(String.valueOf(c.get("payload")),
                            HexFormat.of().formatHex(result.getPayload()));
                }
            }));
        }
        return tests;
    }

    @TestFactory
    List<DynamicTest> loadCases() throws IOException {
        List<DynamicTest> tests = new ArrayList<>();
        for (Map<String, Object> c : section("load")) {
            tests.add(DynamicTest.dynamicTest(String.valueOf(c.get("name")), () -> {
                SchemaException e = assertThrows(SchemaException.class,
                        () -> Schema.fromYaml(String.valueOf(c.get("schema"))));
                assertTrue(e.getMessage().contains(String.valueOf(c.get("error"))),
                        "want '" + c.get("error") + "', got " + e.getMessage());
            }));
        }
        return tests;
    }

    /** PS-581: a schema without ports reports the caller's FPort. */
    @Test
    void aFlatSchemaReportsTheCallersFPort() {
        Schema schema = Schema.fromYaml("name: t\nfields:\n  - {name: a, type: u8}\n");
        assertEquals(3, schema.encodeWithPort(Map.of("a", 1), 3).getFPort());
    }
}
