//! The Django template engine, reimplemented in Rust.
//!
//! This crate deliberately has no Python dependency. Everything that must
//! interact with the host language is expressed as a trait here and implemented
//! in the `template-py` binding crate, so that the engine's own semantics stay
//! testable with plain `cargo test`.

pub mod filters;
pub mod lexer;
pub mod parser;
pub mod pynum;
pub mod pyposix;
pub mod pyregex;
pub mod pyunicode;
pub mod text;
pub mod token;

/// Version of the engine, surfaced through the Python binding.
pub fn version() -> &'static str {
    env!("CARGO_PKG_VERSION")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn version_is_not_empty() {
        assert!(!version().is_empty());
    }

    #[test]
    fn version_is_semver_like() {
        // Cargo validates the full version, including prerelease/build identifiers.
        // Only the core MAJOR.MINOR.PATCH components must be numeric.
        let core = version().split(['-', '+']).next().unwrap();
        let parts: Vec<&str> = core.split('.').collect();
        assert_eq!(
            parts.len(),
            3,
            "expected MAJOR.MINOR.PATCH core, got {}",
            version()
        );
        for part in parts {
            assert!(
                part.parse::<u64>().is_ok(),
                "non-numeric version component in {}",
                version()
            );
        }
    }
}
