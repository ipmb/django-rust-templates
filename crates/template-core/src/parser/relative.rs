//! `construct_relative_path` — `django.template.loader_tags` (`loader_tags.py:246`).
//!
//! Read by `{% extends %}` and `{% include %}`: it turns a `./`- or `../`-prefixed
//! template name into a full one against the current template's own name. The
//! three messages interpolate with `%s`, not `repr`, so this is a pure string
//! port and needs no host round-trip.
//!
//! **The origin is `Option<&str>` and `None` is a real case, not a missing
//! argument** (A3): it stands for `Origin(UNKNOWN_SOURCE).template_name`, which
//! is `None` for every `Template` Django builds without a loader, and Django's
//! own first branch is `if current_template_name is None`. Only a *relative*
//! name reaches it — the early return above means an absolute or dynamic name
//! is passed through without consulting the origin at all.

use crate::parser::error::Error;
use crate::pyposix;

pub fn construct_relative_path(
    current_template_name: Option<&str>,
    relative_name: &str,
    allow_recursion: bool,
) -> Result<String, Error> {
    // Python's `str.strip("'\"")` removes any leading/trailing quote, in any mix.
    let new_name = relative_name.trim_matches(['\'', '"']);
    if !(new_name.starts_with("./") || new_name.starts_with("../")) {
        return Ok(relative_name.to_string());
    }
    let Some(current) = current_template_name else {
        // `relative_name` unquoted here, unlike the two below: Django writes
        // `f"The relative path {relative_name} ..."` (`loader_tags.py:264-265`),
        // an f-string rather than the `'%s'` form the others use.
        return Err(Error::syntax(format!(
            "The relative path {relative_name} cannot be evaluated due to an \
             unknown template origin."
        )));
    };
    let stripped = current.trim_start_matches('/');
    let new_name = pyposix::normpath(&pyposix::join(&pyposix::dirname(stripped), new_name));
    if new_name.starts_with("../") {
        // The origin as given, not `stripped`: Django interpolates
        // `current_template_name` (`loader_tags.py:276-277`).
        return Err(Error::syntax(format!(
            "The relative path '{relative_name}' points outside the file hierarchy \
             that template '{current}' is in."
        )));
    }
    if !allow_recursion && stripped == new_name {
        // `current`, **not** `new_name`: the second `%s` in Django's message is
        // `current_template_name` (`loader_tags.py:281-283`), and the two differ
        // whenever the origin had a leading slash — measured on the pin,
        // `construct_relative_path("/index.html", "'./index.html'", False)`
        // says "template name '/index.html'" while the new name is
        // `index.html`. That the two coincide for every slash-free origin is
        // what makes this the branch a same-shape test misses.
        return Err(Error::syntax(format!(
            "The relative path '{relative_name}' was translated to template name \
             '{current}', the same template in which the tag appears."
        )));
    }
    // `relative_name[0] == relative_name[-1]` on the *raw* input, so a quoted
    // name is re-quoted with double quotes and an unquoted one is not quoted at
    // all. Compare bytes rather than chars: the first must already be one of the
    // two quote characters (ASCII) for this to be true, so the last byte being
    // equal cannot be a UTF-8 continuation byte.
    let bytes = relative_name.as_bytes();
    let has_quotes = matches!(bytes.first(), Some(b'"' | b'\'')) && bytes.first() == bytes.last();
    Ok(if has_quotes {
        format!("\"{new_name}\"")
    } else {
        new_name
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::parser::error::ErrorKind;

    #[test]
    fn a_non_relative_name_is_returned_unchanged() {
        for name in [
            "'base.html'",
            "base.html",
            "'sub/base.html'",
            "'/abs/x.html'",
        ] {
            assert_eq!(
                construct_relative_path(Some("index.html"), name, false).unwrap(),
                name,
                "{name}"
            );
        }
    }

    #[test]
    fn a_relative_name_resolves_against_the_origin_and_requotes() {
        assert_eq!(
            construct_relative_path(Some("sub/index.html"), "'../base.html'", false).unwrap(),
            "\"base.html\""
        );
        assert_eq!(
            construct_relative_path(Some("index.html"), "'./base.html'", false).unwrap(),
            "\"base.html\""
        );
        assert_eq!(
            construct_relative_path(Some("index.html"), "./base.html", false).unwrap(),
            "base.html"
        );
    }

    /// `None` is Django's *unknown origin*, not "no origin object" (A3): the
    /// value it stands for is `Origin(UNKNOWN_SOURCE).template_name`, which is
    /// `None` on every `Template` Django builds without a loader. So the
    /// message is asserted, not just the kind — a `None` that fell through to
    /// "no check" would still be an `ErrorKind::Syntax` only by accident, and
    /// this is the branch that makes `None` mean something.
    #[test]
    fn the_three_failures_carry_djangos_exact_text() {
        let unknown = construct_relative_path(None, "'./base.html'", false).unwrap_err();
        assert!(matches!(unknown.kind, ErrorKind::Syntax(_)));
        // Verbatim (`loader_tags.py:264-265`); note it does **not** quote
        // `relative_name`, unlike the other two. The literal is long on purpose:
        // rustfmt does not wrap it, and a wrapped literal would hide the space
        // this message's line break sits on.
        assert_eq!(
            unknown.message(),
            "The relative path './base.html' cannot be evaluated due to an unknown template origin."
        );

        let outside =
            construct_relative_path(Some("index.html"), "'../../x.html'", false).unwrap_err();
        assert!(matches!(outside.kind, ErrorKind::Syntax(_)));
        assert_eq!(
            outside.message(),
            "The relative path ''../../x.html'' points outside the file hierarchy that \
             template 'index.html' is in."
        );

        let same = construct_relative_path(Some("index.html"), "'index.html'", false);
        assert!(
            same.is_ok(),
            "not relative: returns early, no recursion check"
        );

        let same =
            construct_relative_path(Some("index.html"), "'./index.html'", false).unwrap_err();
        assert!(matches!(same.kind, ErrorKind::Syntax(_)));
        assert_eq!(
            same.message(),
            "The relative path ''./index.html'' was translated to template name \
             'index.html', the same template in which the tag appears."
        );

        // The origin with a leading slash, which is the input that separates
        // `current` from `new_name` in that message: `stripped` is `index.html`
        // and so is `new_name`, but Django prints the *origin*. A test that only
        // used slash-free origins would pass with either. Measured on the pin.
        let same =
            construct_relative_path(Some("/index.html"), "'./index.html'", false).unwrap_err();
        assert_eq!(
            same.message(),
            "The relative path ''./index.html'' was translated to template name \
             '/index.html', the same template in which the tag appears."
        );

        assert_eq!(
            construct_relative_path(Some("index.html"), "'./index.html'", true).unwrap(),
            "\"index.html\"",
            "allow_recursion skips the third check"
        );
    }
}
