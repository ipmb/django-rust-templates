//! `{% debug %}`.

use crate::parser::desc::NodeDesc;
use crate::parser::error::Error;
use crate::parser::Parser;
use crate::token::Token;

pub fn compile(_parser: &mut Parser<'_>, _token: &Token) -> Result<NodeDesc, Error> {
    Ok(NodeDesc::new("DebugNode"))
}

#[cfg(test)]
mod tests {
    use crate::parser::tags::support::compile;

    #[test]
    fn debug_reads_no_arguments() {
        // Same shape as `{% csrf_token %}`: `debug`'s body is
        // `context.get_debug()` with no compile-time argument handling.
        assert_eq!(compile("{% debug %}").class_name, "DebugNode");
        assert_eq!(compile("{% debug junk more %}").class_name, "DebugNode");
    }
}
