package schema

import (
	"fmt"
	"strconv"
	"strings"
)

// CR-2026-194 (PS-436, PS-437, PS-438, PS-440, PS-270): a one-field tlv tag may use the keys
// "[*]" (any value) and "[!n]" (any value but n), ranked below every exact key; a tlv has no
// `default` case. findTLVCaseKey already ranked them (matchCompositeCaseKey's specificity);
// this is the load check, the spec prototype's check_one_field_keys.

const tlvDefaultError = "A tlv has no `default` case; its catch-all is the key \"[*]\" (PS-436, PS-438)"

// oneElementTLVKey is the prototype's one_element_key: a one-field tag's "[*]" or "[!n]" key
// as its element ("*" or "!n" with n parsed) and its PS-270 rank, else ok false.
func oneElementTLVKey(key string) (element string, rank int, ok bool) {
	text := strings.TrimSpace(key)
	if !strings.HasPrefix(text, "[") || !strings.HasSuffix(text, "]") || strings.Contains(text, ",") {
		return "", 0, false
	}
	inner := strings.TrimSpace(text[1 : len(text)-1])
	if inner == "*" {
		return "*", 2, true
	}
	if strings.HasPrefix(inner, "!") {
		n, err := strconv.ParseInt(strings.TrimSpace(inner[1:]), 0, 64)
		if err != nil {
			return "", 0, false
		}
		return "!" + strconv.FormatInt(n, 10), 1, true
	}
	return "", 0, false
}

// tlvOneFieldKeyError is tlv_one_field_key_errors: the first problem with a tlv's keys, in
// declaration order.
func tlvOneFieldKeyError(tlv map[string]any, order keyOrder) error {
	cases := asStringMap(tlv["cases"])
	if cases == nil {
		return nil
	}
	if _, has := cases["default"]; has {
		return fmt.Errorf("%s", tlvDefaultError)
	}
	if tk, ok := tlv["tag_key"].([]any); ok && len(tk) > 1 && tlv["tag_fields"] != nil {
		return nil // a composite tag's keys are PS-436's [e1, e2, ...] form
	}
	type parsed struct {
		key, element string
		rank         int
	}
	var keys []parsed
	for _, key := range order.keys(tlv["cases"]) {
		if element, rank, ok := oneElementTLVKey(key); ok {
			keys = append(keys, parsed{key, element, rank})
		}
	}
	for i, a := range keys {
		for _, b := range keys[i+1:] {
			if a.element == b.element {
				return fmt.Errorf("TLV keys '%s' and '%s' are the same key (PS-437)", a.key, b.key)
			}
			if a.rank == b.rank {
				return fmt.Errorf("TLV keys '%s' and '%s' have the same rank and can match the same tag (PS-440)", a.key, b.key)
			}
		}
	}
	return nil
}

// checkOneFieldTLVKeys walks the document, test vectors and definitions aside (a definition
// is checked where it is spliced), and reports the first tlv whose keys break CR-2026-194.
func checkOneFieldTLVKeys(raw map[string]any, order keyOrder) error {
	var walk func(node any) error
	walk = func(node any) error {
		if list, ok := node.([]any); ok {
			for _, item := range list {
				if err := walk(item); err != nil {
					return err
				}
			}
			return nil
		}
		m := asStringMap(node)
		if m == nil {
			return nil
		}
		if tlv := asStringMap(m["tlv"]); tlv != nil {
			if err := tlvOneFieldKeyError(tlv, order); err != nil {
				return err
			}
		}
		for _, key := range order.keys(node) {
			if key == "test_vectors" || key == "definitions" {
				continue
			}
			if err := walk(m[key]); err != nil {
				return err
			}
		}
		return nil
	}
	return walk(raw)
}
