package schema

import (
	"fmt"
	"sort"
	"strconv"
	"strings"
)

// CR-2026-146 (PS-169, amended for 0.5.5): a label two or more codes of a lookup or an
// enum share names no one code, so an encoder rejects it, naming the field and the codes,
// ascending, and writes none of them. Decoding is unaffected, and a code given as a number
// still encodes. Go wrote the smallest code, the reference and the others the first.
// Mirrors label_text and shared_label_error in tools/schema_interpreter.py.

// sharedLabelError is the PS-169 rejection.
type sharedLabelError struct{ text string }

func (e *sharedLabelError) Error() string { return e.text }

func newSharedLabelError(name string, label any, codes []int) error {
	sorted := append([]int(nil), codes...)
	sort.Ints(sorted)
	parts := make([]string, len(sorted))
	for i, c := range sorted {
		parts[i] = strconv.Itoa(c)
	}
	return &sharedLabelError{fmt.Sprintf(
		"Field '%s': the label %s is shared by codes [%s], so it cannot be encoded (PS-169)",
		name, labelText(label), strings.Join(parts, ", "))}
}

// labelText writes a label as the PS-169 and PS-067 messages do: text in single quotes,
// a boolean as JSON writes it, a number as JavaScript's String(number) does.
func labelText(label any) string {
	switch v := label.(type) {
	case string:
		return "'" + v + "'"
	case bool:
		if v {
			return "true"
		}
		return "false"
	}
	return givenText(label)
}

// lookupLabelCodes is every code a mapping lookup gives `value` as its label.
func lookupLabelCodes(field Field, value any) []int {
	var codes []int
	for k, v := range field.Lookup {
		if sameLabel(v, value) {
			codes = append(codes, k)
		}
	}
	sort.Ints(codes)
	return codes
}

// arrayLabelCodes is every index a sequence lookup gives `value` as its label.
func arrayLabelCodes(field Field, value any) []int {
	var codes []int
	for i, entry := range field.LookupArray {
		if sameLabel(entry, value) {
			codes = append(codes, i)
		}
	}
	return codes
}

// nibbleDecimalFitError is PS-168 for `udec`/`sdec` (PS-330): one byte holds a whole part
// in a nibble and tenths 0 to 9, so 0 to 15.9, or -8 to 7.9 signed. `tenths` is the value
// as encoded, in tenths. Both said "does not fit udec", in words of their own.
func nibbleDecimalFitError(tenths int, signed bool, name string, given any) error {
	low, high, lowText, highText := 0, 159, "0", "15.9"
	if signed {
		low, high, lowText, highText = -80, 79, "-8", "7.9"
	}
	if tenths >= low && tenths <= high {
		return nil
	}
	sign := ""
	if tenths < 0 {
		sign = "-"
	}
	abs := tenths
	if abs < 0 {
		abs = -abs
	}
	encoded := sign + strconv.Itoa(abs/10)
	if abs%10 != 0 {
		encoded += "." + strconv.Itoa(abs%10)
	}
	return fmt.Errorf("Field '%s': %s encodes as %s, outside %s to %s (PS-168)",
		name, givenText(given), encoded, lowText, highText)
}

// encodeEnumValue is an enum's name back to its code, or a code as given (PS-067). A name
// several codes share is rejected (PS-169), and so is one with no code, the `default`
// label among them (PS-068, PS-554). A code is rounded (PS-552) and must fit the base, the
// error naming the enum (PS-168): Go wrapped 300 into a u8 as 2c, silently.
func encodeEnumValue(field Field, value any, endian string) ([]byte, error) {
	var code float64
	switch v := value.(type) {
	case string:
		var codes []int
		for k, name := range field.Values {
			if name == v {
				codes = append(codes, k)
			}
		}
		if len(codes) == 0 {
			codes = lookupLabelCodes(field, v)
		}
		if len(codes) == 0 {
			return nil, fmt.Errorf("Field '%s': %s is not a name of the enum (PS-067)",
				field.Name, labelText(v))
		}
		if len(codes) > 1 {
			return nil, newSharedLabelError(field.Name, v, codes)
		}
		code = float64(codes[0])
	case bool:
		return nil, fmt.Errorf("Field '%s': %s is not a name or code of the enum (PS-067)",
			field.Name, labelText(v))
	default:
		num, ok := toFloat64(value)
		if !ok {
			return nil, fmt.Errorf("Field '%s': %v is not a name or code of the enum (PS-067)",
				field.Name, value)
		}
		code = roundToInteger(num)
	}
	// The base's width, as decodeField reads it. The field's length was used, which is 1
	// for an enum, so a u16 enum was written as one byte: 70000 as 70.
	size := enumBaseWidth(field.Base)
	signed := strings.HasPrefix(field.Base, "s")
	if err := integerFitError(code, size*8, signed, field.Name, value); err != nil {
		return nil, err
	}
	// encodeUint keeps the low `size` bytes, so a negative code is its two's complement.
	return encodeUint(uint64(int64(code)), size, endian), nil
}

// enumBaseWidth is the bytes an enum's base reads (PS-067); u8 where none is declared.
func enumBaseWidth(base string) int {
	switch base {
	case "u16", "s16":
		return 2
	case "u24", "s24":
		return 3
	case "u32", "s32":
		return 4
	}
	return 1
}

// sharedLabelCheck is the PS-169 rejection alone, for a path that recovers a member's
// number through rawForField, which ranks candidates and so takes any one code.
func sharedLabelCheck(field Field, value any) error {
	if _, err := reverseLookupLabel(field, value); err != nil {
		if _, shared := err.(*sharedLabelError); shared {
			return err
		}
	}
	return nil
}
