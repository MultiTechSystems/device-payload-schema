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
 * CR-2026-179 (0.5.7, PS-470, PS-600): a {@code semantic} is a string, a TTN normalized path,
 * and {@code _meta} carries it. The cases are shared with the reference, Go and C# in
 * tests/cr-2026-179-cases.yaml; the {@code normalized} ones are the generated codec's and the
 * reference's alone.
 */
public class CR2026179Test {

    @SuppressWarnings("unchecked")
    private static List<Map<String, Object>> cases() throws IOException {
        Path file = Path.of("..", "..", "tests", "cr-2026-179-cases.yaml");
        Map<String, Object> doc = new Yaml().load(Files.readString(file));
        return (List<Map<String, Object>>) doc.get("cases");
    }

    @TestFactory
    List<DynamicTest> everySharedCase() throws IOException {
        List<DynamicTest> tests = new ArrayList<>();
        for (Map<String, Object> c : cases()) {
            if (!c.containsKey("error") && !c.containsKey("meta")) continue;
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
                for (Map.Entry<?, ?> want : ((Map<?, ?>) c.get("meta")).entrySet()) {
                    Map<?, ?> entry = (Map<?, ?>) fields.get(want.getKey());
                    if (want.getValue() == null) {
                        assertFalse(entry.containsKey("semantic"), String.valueOf(want.getKey()));
                    } else {
                        assertEquals(want.getValue(), entry.get("semantic"));
                    }
                }
            }));
        }
        assertTrue(tests.size() >= 5);
        return tests;
    }
}
