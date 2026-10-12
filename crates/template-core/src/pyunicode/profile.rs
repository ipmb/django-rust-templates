//! Select CPython's Unicode tables once, before any parser regex is compiled.
//! Standalone Rust callers use Unicode 16; the Python module selects its interpreter's release.

use std::sync::OnceLock;

static VERSION: OnceLock<String> = OnceLock::new();

pub fn configure(version: &str) -> Result<(), &'static str> {
    if !matches!(version, "15.0.0" | "15.1.0" | "16.0.0") {
        return Err("unsupported CPython Unicode version");
    }
    if let Some(current) = VERSION.get() {
        return if current == version {
            Ok(())
        } else {
            Err("CPython Unicode version already configured differently")
        };
    }
    VERSION
        .set(version.to_owned())
        .map_err(|_| "Unicode configuration raced")
}

pub fn version() -> &'static str {
    VERSION.get().map_or("16.0.0", String::as_str)
}

pub fn older() -> bool {
    version() != "16.0.0"
}

pub fn word_in_class() -> String {
    let text = match version() {
        "15.0.0" => include_str!("word_15_0.txt"),
        "15.1.0" => include_str!("word_15_1.txt"),
        _ => return crate::pyregex::PYTHON_WORD_IN_CLASS.to_owned(),
    };
    let mut class = String::new();
    for line in text.lines() {
        let (lo, hi) = line.split_once(' ').expect("generated Unicode range");
        class.push_str(&format!(r"\x{{{lo}}}-\x{{{hi}}}"));
    }
    class
}

pub fn suppress_upper(ch: char) -> bool {
    older() && matches!(ch as u32, 411 | 612 | 7306 | 42957 | 42971 | 68976..=68997)
}

pub fn excludes_digit(code: u32) -> bool {
    older()
        && matches!(code, 68928..=68937 | 71376..=71395 | 72688..=72697
        | 90416..=90425 | 93552..=93561 | 118000..=118009 | 124401..=124410)
}
