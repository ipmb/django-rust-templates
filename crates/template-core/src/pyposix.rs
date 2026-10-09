//! `posixpath`, the three functions `construct_relative_path` needs.
//!
//! A hand port rather than `std::path`: these are Python's semantics, and they
//! differ on cases the suite actually hits. `Path::components` resolves `..`
//! with lexically-different rules and has no `//` case at all;
//! `Path::file_name` has no `dirname("a") == ""` case at all.
//!
//! The tables in `mod tests` are the plan's evidence, measured on the pinned
//! interpreter (3.14.4). They are a subset of it: the same port, transliterated
//! to Python, was diffed against `posixpath` on 221,845 generated inputs and
//! 800,000 `normpath(join(a, b))` pairs with no mismatch
//! (`.review/m7-measurements/m7_measure_pyposix.py`). A transliteration is not
//! this code, so that sweep cannot see a byte-versus-char slicing defect — which
//! is why `dirname` slices at `rfind('/') + 1`, a position that is always a
//! char boundary.

/// `posixpath.join(a, b)`.
///
/// Not `Path::join`: Python's rule is "an absolute `b`, or an `a` ending in a
/// slash, or an inserted separator" — and it does **not** normalise, so
/// `join("a", "./b")` is `"a/./b"`. Every caller here passes the result to
/// [`normpath`].
pub fn join(a: &str, b: &str) -> String {
    if b.starts_with('/') {
        return b.to_string();
    }
    if a.is_empty() || a.ends_with('/') {
        return format!("{a}{b}");
    }
    format!("{a}/{b}")
}

/// `posixpath.dirname(p)` — Python 3.14's `rfind` form, **not** `split()[0]`.
///
/// The two disagree on a bare filename: `dirname("a")` is `""`, not `"."`. That
/// case is reachable — `construct_relative_path` calls this on
/// `current_template_name.lstrip("/")`, which for a top-level template *is* a
/// bare filename.
///
/// The `"/".repeat(head.len())` comparison is a run of *slashes*, not one slash
/// or two: `dirname("//a")` is `"//"` and `dirname("///a")` is `"///"`, because
/// POSIX allows an implementation-defined meaning for exactly two leading
/// slashes and Python preserves them.
pub fn dirname(p: &str) -> String {
    let i = p.rfind('/').map_or(0, |n| n + 1);
    let head = &p[..i];
    if !head.is_empty() && head != "/".repeat(head.len()) {
        head.trim_end_matches('/').to_string()
    } else {
        head.to_string()
    }
}

/// `posixpath.normpath(path)`.
///
/// Two rules make this more than a `..`-collapser, and both are load-bearing
/// for template names: a leading `..` in a *relative* path is kept (there is
/// nothing above it to pop), and the count of leading slashes is preserved —
/// 0 for relative, 2 for exactly `//x`, else 1, so `///x` is 1 and not 3. That
/// count is what keeps `//a` distinct from `/a`.
pub fn normpath(path: &str) -> String {
    if path.is_empty() {
        return ".".to_string();
    }
    // Python counts the leading slashes: 0 when relative, 2 for exactly `//x`,
    // else 1 — so `///x` is 1, not 3, and not 2. The count is what keeps `//a`
    // distinct from `/a`, and the `else` arm is pinned by the table's
    // `///a//b` row.
    let initial_slashes = if !path.starts_with('/') {
        0
    } else if path.starts_with("//") && !path.starts_with("///") {
        2
    } else {
        1
    };
    let mut new_comps: Vec<&str> = Vec::new();
    for comp in path.split('/') {
        if comp.is_empty() || comp == "." {
            continue;
        }
        if comp != ".."
            || (initial_slashes == 0 && new_comps.is_empty())
            || new_comps.last() == Some(&"..")
        {
            new_comps.push(comp);
        } else if !new_comps.is_empty() {
            new_comps.pop();
        }
    }
    let mut out = new_comps.join("/");
    if initial_slashes > 0 {
        out = format!("{}{}", "/".repeat(initial_slashes), out);
    }
    if out.is_empty() {
        ".".to_string()
    } else {
        out
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn normpath_matches_python_on_the_measured_table() {
        // Measured against `posixpath.normpath` on the pinned interpreter
        // (3.14.4, `posixpath.py` under mise's install); the table is the
        // plan's evidence, not a guess.
        //
        // `("///a//b", "/a/b")` is here for one clause and it is a review
        // finding: without it nothing exercises `initial_slashes`'s `else 1`
        // arm from a path that *starts* with `///`, and the
        // `!path.starts_with("///")` conjunct in the `== 2` arm is dead code as
        // far as this table can tell. Deleting that conjunct changes this row
        // to `"//a/b"` and changes nothing else.
        for (input, want) in [
            ("", "."),
            (".", "."),
            ("..", ".."),
            ("a/..", "."),
            ("a/../..", ".."),
            ("./a", "a"),
            ("a//b", "a/b"),
            ("//a//b", "//a/b"),
            ("///a//b", "/a/b"),
            ("/a/../b", "/b"),
            ("a/./b", "a/b"),
            ("../a", "../a"),
            ("./../a", "../a"),
        ] {
            assert_eq!(normpath(input), want, "normpath({input:?})");
        }
    }

    #[test]
    fn join_normpath_and_dirname_match_python_on_the_measured_table() {
        for (a, b, want) in [
            ("", "", "."),
            ("a", "b", "a/b"),
            ("a", "", "a"),
            ("", "b", "b"),
            ("a/b", "../c", "a/c"),
            ("a", "./b", "a/b"),
            ("sub", "../x", "x"),
            ("a/b/c", "../../x", "a/x"),
            ("", "./b", "b"),
            ("", "../b", "../b"),
            (".", "b", "b"),
            ("a", ".", "a"),
            ("a/..", "b", "b"),
            ("//a", "b", "//a/b"),
            ("/a", "b", "/a/b"),
        ] {
            assert_eq!(normpath(&join(a, b)), want, "normpath(join({a:?}, {b:?}))");
        }
        // `("//a", "//")` and `("///a", "///")` are the second half of the same
        // review finding. `("/a", "/")` already reaches the `"/".repeat(n)`
        // guard, but only at n == 1, where `head != "/"` and
        // `head != "/".repeat(head.len())` are the same test; the n > 1 rows
        // are what separate the length-sized repeat from a hard-coded `"//"`,
        // which `("//a", "//")` alone would let through.
        for (p, want) in [
            ("", ""),
            ("a", ""),
            ("a/b", "a"),
            ("a/b/", "a/b"),
            ("/a", "/"),
            ("//a", "//"),
            ("///a", "///"),
            ("sub/index.html", "sub"),
            ("/sub/index.html", "/sub"),
        ] {
            assert_eq!(dirname(p), want, "dirname({p:?})");
        }
    }
}
