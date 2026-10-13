package org.lora.schema;

import org.junit.jupiter.api.DynamicTest;
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
 * CR-2026-161 (0.5.6, PS-570, PS-571): {@code _meta} entries carry {@code reports}, and a
 * lookup's labels are of one type.
 * The cases are shared with the reference, Go and C# in tests/cr-2026-161-cases.yaml.
 */
public class CR2026161Test {

    @SuppressWarnings("unchecked")
    private static List<Map<String, Object>> cases() throws IOException {
        Path file = Path.of("..", "..", "tests", "cr-2026-161-cases.yaml");
        Map<String, Object> doc = new Yaml().load(Files.readString(file));
        return (List<Map<String, Object>>) doc.get("cases");
    }

    @TestFactory
    List<DynamicTest> everySharedCase() throws IOException {
        List<DynamicTest> tests = new ArrayList<>();
        for (Map<String, Object> c : cases()) {
            tests.add(DynamicTest.dynamicTest(String.valueOf(c.get("name")), () -> {
                String text = String.valueOf(c.get("schema"));
                if (c.containsKey("error")) {
                    String want = String.valueOf(c.get("error"));
                    SchemaException e = assertThrows(SchemaException.class, () -> Schema.fromYaml(text));
                    assertTrue(e.getMessage().contains(want), "want '" + want + "', got " + e.getMessage());
                    return;
                }
                Schema schema = Schema.fromYaml(text);
                Map<String, Object> out = schema.interpret(
                        HexFormat.of().parseHex(String.valueOf(c.get("payload"))),
                        new InputContext(null, null, null));
                Map<?, ?> fields = (Map<?, ?>) ((Map<?, ?>) out.get("_meta")).get("fields");
                Map<?, ?> entry = (Map<?, ?>) fields.get("f");
                assertEquals(String.valueOf(c.get("reports")), entry.get("reports"));
                if (c.containsKey("element_reports")) {
                    assertEquals(String.valueOf(c.get("element_reports")),
                            ((Map<?, ?>) entry.get("element")).get("reports"));
                }
            }));
        }
        return tests;
    }
}
