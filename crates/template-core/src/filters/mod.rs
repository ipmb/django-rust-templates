//! One module per filter (design spec §10.4).
//!
//! M3 writes the directory and the registry and adds **no filters**: §10.3's M4
//! row owns all 57 built-in filters and §10.4 assigns `FilterFlags` to M4, so
//! M3 proves the filter *pipeline* with filters the host supplies. The point of
//! creating this now is that M4 adds files rather than structure.

/// Filter name → compile-time metadata, in the shape M4 fills in.
///
/// Empty at M3 and asserted empty, so that a filter added at M3 — which would
/// mean implementing `is_safe` outside its milestone — fails a test.
pub fn filter_names() -> Vec<&'static str> {
    Vec::new()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn m3_owns_no_filters() {
        assert_eq!(
            filter_names(),
            Vec::<&'static str>::new(),
            "§10.3 gives all 57 built-in filters to M4; a name here means M3 \
             implemented one outside its milestone",
        );
    }
}
