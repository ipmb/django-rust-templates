//! `{% csrf_token %}`.

use crate::parser::desc::NodeDesc;
use crate::parser::error::Error;
use crate::parser::Parser;
use crate::token::Token;

pub fn compile(_parser: &mut Parser<'_>, _token: &Token) -> Result<NodeDesc, Error> {
    // Django reads no arguments at all, so `{% csrf_token junk %}` is accepted
    // rather than rejected.
    Ok(NodeDesc::new("CsrfTokenNode"))
}

#[cfg(test)]
mod tests {
    use crate::parser::tags::support::compile;

    #[test]
    fn csrf_token_reads_no_arguments() {
        // `csrf_token` never calls `parser.compile_filter` and reads no bits, so
        // an argument is ignored rather than rejected.
        assert_eq!(compile("{% csrf_token %}").class_name, "CsrfTokenNode");
        assert_eq!(compile("{% csrf_token junk %}").class_name, "CsrfTokenNode");
    }
}
