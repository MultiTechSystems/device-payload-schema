package org.lora.schema;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;

/**
 * CR-2026-194 (PS-436, PS-437, PS-438, PS-440, PS-270): a one-field tlv tag may use the keys
 * {@code "[*]"} (any value) and {@code "[!n]"} (any value but n), ranked below every exact key;
 * a tlv has no {@code default} case. {@code findTLVCaseKey} already ranked the keys (by
 * {@code matchCompositeCaseKey}'s specificity); this is the load check, the spec prototype's
 * {@code check_one_field_keys}, in its words.
 */
final class OneFieldKeys {
    private OneFieldKeys() {}

    static final String DEFAULT_ERROR =
            "A tlv has no `default` case; its catch-all is the key \"[*]\" (PS-436, PS-438)";

    private record Parsed(String key, String element, int rank) {}

    /** The prototype's one_element_key: "*" or "!n" and its PS-270 rank, else null. */
    static Parsed parse(Object key) {
        if (!(key instanceof String s)) return null;
        String text = s.strip();
        if (!text.startsWith("[") || !text.endsWith("]") || text.contains(",")) return null;
        String inner = text.substring(1, text.length() - 1).strip();
        if (inner.equals("*")) return new Parsed(s, "*", 2);
        if (inner.startsWith("!")) {
            String n = inner.substring(1).strip();
            try {
                long value = n.startsWith("0x") || n.startsWith("0X")
                        ? Long.parseLong(n.substring(2), 16) : Long.parseLong(n);
                return new Parsed(s, "!" + value, 1);
            } catch (NumberFormatException e) {
                return null;
            }
        }
        return null;
    }

    /** The first problem with a tlv's case keys, in declaration order, else null. */
    static String error(Map<?, ?> tlv, Map<?, ?> cases) {
        if (cases.containsKey("default")) return DEFAULT_ERROR;
        if (tlv.get("tag_key") instanceof List<?> tagKey && tagKey.size() > 1
                && tlv.get("tag_fields") != null) {
            return null;    // a composite tag's keys are PS-436's [e1, e2, ...] form
        }
        List<Parsed> keys = new ArrayList<>();
        for (Object key : cases.keySet()) {
            Parsed parsed = parse(key);
            if (parsed != null) keys.add(parsed);
        }
        for (int i = 0; i < keys.size(); i++) {
            for (int j = i + 1; j < keys.size(); j++) {
                Parsed a = keys.get(i), b = keys.get(j);
                if (a.element().equals(b.element())) {
                    return "TLV keys '" + a.key() + "' and '" + b.key()
                            + "' are the same key (PS-437)";
                }
                if (a.rank() == b.rank()) {
                    return "TLV keys '" + a.key() + "' and '" + b.key()
                            + "' have the same rank and can match the same tag (PS-440)";
                }
            }
        }
        return null;
    }
}
