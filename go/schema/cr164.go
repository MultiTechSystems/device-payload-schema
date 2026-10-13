package schema

import (
	"fmt"

	"gopkg.in/yaml.v3"
)

// CR-2026-164 (PS-575): a schema's YAML text holds no plain scalar that YAML 1.1 reads as a
// boolean and YAML 1.2 as a string. `y`, `Y`, `n` and `N` are not in the list: they are
// field names (an accelerometer's `y` axis).
var yaml11Booleans = map[string]bool{
	"yes": true, "Yes": true, "YES": true,
	"no": true, "No": true, "NO": true,
	"on": true, "On": true, "ON": true,
	"off": true, "Off": true, "OFF": true,
}

// checkYAMLBooleans parses the text into a node tree and refuses a plain scalar (style 0,
// so neither quoted nor a block scalar) whose text is a YAML 1.1 boolean, in the spec
// prototype's words. Text that is not well-formed YAML is left to the loader that follows.
func checkYAMLBooleans(data string) error {
	var root yaml.Node
	if err := yaml.Unmarshal([]byte(data), &root); err != nil {
		return nil
	}
	var walk func(n *yaml.Node) error
	walk = func(n *yaml.Node) error {
		if n == nil {
			return nil
		}
		if n.Kind == yaml.ScalarNode && n.Style == 0 && yaml11Booleans[n.Value] {
			return fmt.Errorf("Line %d: the unquoted value '%s' is a boolean in YAML 1.1 and a string in YAML 1.2; quote it (\"%s\") or write true or false (PS-575)", n.Line, n.Value, n.Value)
		}
		for _, c := range n.Content {
			if err := walk(c); err != nil {
				return err
			}
		}
		return nil
	}
	return walk(&root)
}
