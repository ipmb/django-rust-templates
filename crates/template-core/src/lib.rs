//! The Django template engine, reimplemented in Rust.
//!
//! This crate deliberately has no Python dependency. Everything that must
//! interact with the host language is expressed as a trait here and implemented
//! in the `template-py` binding crate, so that the engine's own semantics stay
//! testable with plain `cargo test`.

pub mod lexer;
pub mod parser;

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
        let parts: Vec<&str> = version().split('.').collect();
        assert_eq!(
            parts.len(),
            3,
            "expected MAJOR.MINOR.PATCH, got {}",
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
