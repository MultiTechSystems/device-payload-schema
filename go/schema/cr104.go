package schema

// CR-2026-104: boolean lookup labels (PS-106, PS-407, PS-513).

// lookupLabel is a lookup label as the schema wrote it, where it is one PS-106 allows:
// a string, a number or a boolean. Anything else is no label and is left out, as before.
func lookupLabel(v any) (any, bool) {
	switch v.(type) {
	case string, bool, int, int64, uint64, float64:
		return v, true
	}
	return nil, false
}

// sameLabel is PS-513: a boolean label matches only a boolean input, and a boolean input
// only a boolean label - `true` is not 1, and "true" is not `true`. A string matches only
// an equal string, and a number any number equal to it, whatever its Go type.
func sameLabel(label, value any) bool {
	lb, labelIsBool := label.(bool)
	vb, valueIsBool := value.(bool)
	if labelIsBool || valueIsBool {
		return labelIsBool && valueIsBool && lb == vb
	}
	ls, labelIsString := label.(string)
	vs, valueIsString := value.(string)
	if labelIsString || valueIsString {
		return labelIsString && valueIsString && ls == vs
	}
	ln, ok := toFloat64(label)
	vn, ok2 := toFloat64(value)
	return ok && ok2 && ln == vn
}
