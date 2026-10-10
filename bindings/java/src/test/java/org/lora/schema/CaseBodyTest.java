package org.lora.schema;

import org.junit.jupiter.api.Test;

import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * PS-347, PS-441: a {@code match} or {@code tlv} case body is a field list, {@code []} for
 * a case that reads nothing. A bare string such as {@code 5: skip} was skipped by the
 * parser, so a match had no case for that value and a tlv skipped the tag. Mirrors
 * tests/test_case_body_field_list.py.
 */
public class CaseBodyTest {

    private static final String WANT =
            "a case body is a field list; write [] for a case that reads nothing (PS-441)";

    private static void assertRejected(String src, String prefix) {
        SchemaException e = assertThrows(SchemaException.class, () -> Schema.fromYaml(src));
        assertTrue(e.getMessage().contains(prefix + ": " + WANT), e.getMessage());
    }

    @Test
    public void aMatchCaseBodyThatIsAStringIsRejected() {
        assertRejected("name: p\nfields:\n- match:\n    length: 1\n    cases:\n"
                + "      5: skip\n      6: [{name: a, type: u8}]\n", "match.cases[5]");
    }

    @Test
    public void aTlvCaseBodyThatIsAStringIsRejected() {
        assertRejected("name: p\nfields:\n- tlv:\n    tag_size: 1\n    cases:\n      1: skip\n",
                "tlv.cases[1]");
    }

    @Test
    public void aCaseBodyInsideANestedGroupIsChecked() {
        assertRejected("name: p\nfields:\n- name: g\n  type: object\n  fields:\n  - match:\n"
                + "      length: 1\n      cases:\n        5: skip\n", "match.cases[5]");
    }

    @Test
    public void anEmptyCaseBodyIsAccepted() {
        Schema s = Schema.fromYaml("name: p\nfields:\n- match:\n    length: 1\n    cases:\n"
                + "      5: []\n      6: [{name: a, type: u8}]\n");
        Map<String, Object> out = s.decode(new byte[] {5});
        assertEquals(Map.of(), out);
    }
}
