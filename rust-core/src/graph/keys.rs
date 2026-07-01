//! Key compiler — renders structured identifiers and display labels from template patterns.
//!
//! This mirrors the Python `_compile_key_pattern` / `_compile_label_pattern` logic
//! so that dedup_key strings can be produced in Rust after key patterns are
//! resolved server-side from YAML templates.

use std::collections::HashMap;

/// Compiles a key pattern like `"{name}"` or `"{source}|{predicate}|{target}"`
/// into a reusable renderer. Placeholder names are extracted at construction time;
/// calling `render` substitutes values from a field map.
pub struct KeyCompiler {
    pattern: String,
    placeholders: Vec<String>,
}

impl KeyCompiler {
    pub fn new(pattern: &str) -> Self {
        let mut placeholders = Vec::new();
        let mut start = 0;
        while let Some(open) = pattern[start..].find('{') {
            let abs_open = start + open;
            if let Some(close) = pattern[abs_open..].find('}') {
                let abs_close = abs_open + close;
                let name = pattern[abs_open + 1..abs_close].to_string();
                if !name.is_empty() && !placeholders.contains(&name) {
                    placeholders.push(name);
                }
                start = abs_close + 1;
            } else {
                break;
            }
        }
        Self {
            pattern: pattern.to_string(),
            placeholders,
        }
    }

    /// Render the key pattern using values from the provided field map.
    /// Missing values are replaced with empty strings.
    /// After rendering, trailing '@' or '|' characters that precede empty values
    /// are stripped to avoid producing keys like 'A|cited|B@' when time is empty.
    pub fn render(&self, fields: &HashMap<String, String>) -> String {
        let mut result = self.pattern.clone();
        for ph in &self.placeholders {
            let value = fields.get(ph).cloned().unwrap_or_default();
            result = result.replace(&format!("{{{}}}", ph), &value);
        }
        // Strip trailing '@' or '|' left by empty suffix fields
        while result.ends_with('@') || result.ends_with('|') {
            result.pop();
        }
        result
    }

    /// Render a display label pattern, falling back gracefully on missing keys.
    /// If a placeholder is missing, falls back to "name" then "label" then "unknown".
    pub fn render_label(&self, fields: &HashMap<String, String>) -> String {
        let mut result = self.pattern.clone();
        for ph in &self.placeholders {
            let value = if let Some(v) = fields.get(ph) {
                v.clone()
            } else if let Some(v) = fields.get("name") {
                v.clone()
            } else if let Some(v) = fields.get("label") {
                v.clone()
            } else {
                "unknown".to_string()
            };
            result = result.replace(&format!("{{{}}}", ph), &value);
        }
        result
    }

    pub fn pattern(&self) -> &str {
        &self.pattern
    }

    pub fn placeholders(&self) -> &[String] {
        &self.placeholders
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_simple_key() {
        let compiler = KeyCompiler::new("{name}");
        let mut fields = HashMap::new();
        fields.insert("name".to_string(), "Alice".to_string());
        assert_eq!(compiler.render(&fields), "Alice");
    }

    #[test]
    fn test_composite_key() {
        let compiler = KeyCompiler::new("{source}|{predicate}|{target}");
        let mut fields = HashMap::new();
        fields.insert("source".to_string(), "Alice".to_string());
        fields.insert("predicate".to_string(), "works_at".to_string());
        fields.insert("target".to_string(), "Acme Corp".to_string());
        assert_eq!(compiler.render(&fields), "Alice|works_at|Acme Corp");
    }

    #[test]
    fn test_missing_field_renders_empty() {
        let compiler = KeyCompiler::new("{name}|{missing}");
        let mut fields = HashMap::new();
        fields.insert("name".to_string(), "Alice".to_string());
        assert_eq!(compiler.render(&fields), "Alice|");
    }

    #[test]
    fn test_no_placeholders() {
        let compiler = KeyCompiler::new("static_key");
        assert!(compiler.placeholders().is_empty());
        let fields = HashMap::new();
        assert_eq!(compiler.render(&fields), "static_key");
    }

    #[test]
    fn test_render_label_fallback() {
        let compiler = KeyCompiler::new("{name} ({entity_type})");
        let mut fields = HashMap::new();
        fields.insert("name".to_string(), "Alice".to_string());
        // entity_type is missing — should fall back to "name" field for the missing placeholder
        let result = compiler.render_label(&fields);
        assert_eq!(result, "Alice (Alice)");
    }

    #[test]
    fn test_render_label_with_all_fields() {
        let compiler = KeyCompiler::new("{name} ({entity_type})");
        let mut fields = HashMap::new();
        fields.insert("name".to_string(), "Alice".to_string());
        fields.insert("entity_type".to_string(), "PERSON".to_string());
        assert_eq!(compiler.render_label(&fields), "Alice (PERSON)");
    }

    #[test]
    fn test_duplicate_placeholders_deduplicated() {
        let compiler = KeyCompiler::new("{name}|{name}");
        assert_eq!(compiler.placeholders().len(), 1);
    }

    #[test]
    fn test_temporal_key() {
        let compiler = KeyCompiler::new("{source}|{predicate}|{target}@{time}");
        let mut fields = HashMap::new();
        fields.insert("source".to_string(), "A".to_string());
        fields.insert("predicate".to_string(), "cited".to_string());
        fields.insert("target".to_string(), "B".to_string());
        fields.insert("time".to_string(), "2024".to_string());
        assert_eq!(compiler.render(&fields), "A|cited|B@2024");
    }

    #[test]
    fn test_spatial_key() {
        let compiler = KeyCompiler::new("{source}|{predicate}|{target}@{location}");
        let mut fields = HashMap::new();
        fields.insert("source".to_string(), "A".to_string());
        fields.insert("predicate".to_string(), "located".to_string());
        fields.insert("target".to_string(), "B".to_string());
        fields.insert("location".to_string(), "NYC".to_string());
        assert_eq!(compiler.render(&fields), "A|located|B@NYC");
    }

    #[test]
    fn test_empty_pattern() {
        let compiler = KeyCompiler::new("");
        assert!(compiler.placeholders().is_empty());
        let fields = HashMap::new();
        assert_eq!(compiler.render(&fields), "");
    }

    #[test]
    fn test_python_parity_composite() {
        let pattern = "{source}|{predicate}|{target}@{time}";
        let compiler = KeyCompiler::new(pattern);
        let mut fields = HashMap::new();
        fields.insert("source".to_string(), "EntityA".to_string());
        fields.insert("predicate".to_string(), "overruled".to_string());
        fields.insert("target".to_string(), "EntityB".to_string());
        fields.insert("time".to_string(), "2023-01-15".to_string());
        assert_eq!(compiler.render(&fields), "EntityA|overruled|EntityB@2023-01-15");
    }

    #[test]
    fn test_missing_fields_stripped_trailing_separator() {
        let compiler = KeyCompiler::new("{source}|{predicate}|{target}");
        let mut fields = HashMap::new();
        fields.insert("source".to_string(), "A".to_string());
        fields.insert("predicate".to_string(), "cited".to_string());
        assert_eq!(compiler.render(&fields), "A|cited");
    }

    #[test]
    fn test_render_label_all_missing_falls_to_unknown() {
        let compiler = KeyCompiler::new("{title}");
        let fields = HashMap::new();
        assert_eq!(compiler.render_label(&fields), "unknown");
    }

    #[test]
    fn test_pattern_accessor() {
        let compiler = KeyCompiler::new("{source}|{predicate}|{target}");
        assert_eq!(compiler.pattern(), "{source}|{predicate}|{target}");
    }

    #[test]
    fn test_empty_time_no_trailing_at() {
        let compiler = KeyCompiler::new("{source}|{predicate}|{target}@{time}");
        let mut fields = HashMap::new();
        fields.insert("source".to_string(), "A".to_string());
        fields.insert("predicate".to_string(), "cited".to_string());
        fields.insert("target".to_string(), "B".to_string());
        // time is missing — should not produce trailing '@'
        let result = compiler.render(&fields);
        assert!(!result.ends_with("@"), "Key ends with '@': {}", result);
        assert_eq!(result, "A|cited|B");
    }

    #[test]
    fn test_empty_time_explicit_empty_no_trailing_at() {
        let compiler = KeyCompiler::new("{source}|{predicate}|{target}@{time}");
        let mut fields = HashMap::new();
        fields.insert("source".to_string(), "A".to_string());
        fields.insert("predicate".to_string(), "cited".to_string());
        fields.insert("target".to_string(), "B".to_string());
        fields.insert("time".to_string(), String::new());
        let result = compiler.render(&fields);
        assert!(!result.ends_with("@"), "Key ends with '@': {}", result);
        assert_eq!(result, "A|cited|B");
    }

    #[test]
    fn test_empty_location_no_trailing_at() {
        let compiler = KeyCompiler::new("{source}|{predicate}|{target}@{location}");
        let mut fields = HashMap::new();
        fields.insert("source".to_string(), "A".to_string());
        fields.insert("predicate".to_string(), "located".to_string());
        fields.insert("target".to_string(), "B".to_string());
        let result = compiler.render(&fields);
        assert!(!result.ends_with("@"), "Key ends with '@': {}", result);
        assert_eq!(result, "A|located|B");
    }

    #[test]
    fn test_same_edge_different_times_different_keys() {
        let compiler = KeyCompiler::new("{source}|{predicate}|{target}@{time}");
        let mut fields_2023 = HashMap::new();
        fields_2023.insert("source".to_string(), "A".to_string());
        fields_2023.insert("predicate".to_string(), "cited".to_string());
        fields_2023.insert("target".to_string(), "B".to_string());
        fields_2023.insert("time".to_string(), "2023".to_string());

        let mut fields_2024 = HashMap::new();
        fields_2024.insert("source".to_string(), "A".to_string());
        fields_2024.insert("predicate".to_string(), "cited".to_string());
        fields_2024.insert("target".to_string(), "B".to_string());
        fields_2024.insert("time".to_string(), "2024".to_string());

        assert_ne!(compiler.render(&fields_2023), compiler.render(&fields_2024));
    }
}