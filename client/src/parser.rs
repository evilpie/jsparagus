pub use crate::parser_generated::{Handler, NonterminalId, TerminalId, Token};
use crate::parser_runtime::ParserTables;

const ACCEPT: i64 = -0x7fff_ffff_ffff_ffff;
const ERROR: i64 = ACCEPT - 1;

#[derive(Clone, Copy)]
struct Action(i64);

impl Action {
    fn is_shift(self) -> bool {
        0 <= self.0
    }

    fn shift_state(self) -> usize {
        assert!(self.is_shift());
        self.0 as usize
    }

    fn is_reduce(self) -> bool {
        ACCEPT < self.0 && self.0 < 0
    }

    fn reduce_prod_index(self) -> usize {
        assert!(self.is_reduce());
        (-self.0 - 1) as usize
    }

    fn is_accept(self) -> bool {
        self.0 == ACCEPT
    }

    fn is_error(self) -> bool {
        self.0 == ERROR
    }
}

pub enum ParseError {
    SyntaxError,
    UnexpectedEnd
}

impl ParseError {
    pub fn message(&self) -> String {
        match *self {
            ParseError::SyntaxError => format!("syntax error, lol"),
            ParseError::UnexpectedEnd => format!("unexpected end of input"),
        }
    }
}

pub type Result<T> = std::result::Result<T, ParseError>;

pub type Node = *mut ();

pub struct Parser<'a, Out, Reduce>
where
    Out: Handler,
    Reduce: Fn(&Out, usize, &mut Vec<Node>) -> NonterminalId,
{
    tables: &'a ParserTables<'a>,
    state_stack: Vec<usize>,
    node_stack: Vec<Node>,
    reduce: Reduce,
    handler: &'a Out,
}

impl<'a, Out, Reduce> Parser<'a, Out, Reduce>
where
    Out: Handler,
    Reduce: Fn(&Out, usize, &mut Vec<Node>) -> NonterminalId,
{
    pub fn new(
        tables: &'a ParserTables<'a>,
        reduce: Reduce,
        handler: &'a Out,
        entry_state: usize,
    ) -> Parser<'a, Out, Reduce> {
        tables.check();
        assert!(entry_state < tables.state_count);

        Parser {
            tables,
            state_stack: vec![entry_state],
            node_stack: vec![],
            reduce,
            handler,
        }
    }

    fn state(&self) -> usize {
        *self.state_stack.last().unwrap()
    }

    fn action(&self, t: TerminalId) -> Action {
        let t = t as usize;
        debug_assert!(t < self.tables.action_width);
        Action(self.tables.action_table[
            self.state() * self.tables.action_width + t
        ])
    }

    fn reduce_all(&mut self, t: TerminalId) -> Action {
        let tables = self.tables;
        let mut action = self.action(t);
        while action.is_reduce() {
            let prod_index = action.reduce_prod_index();
            let nt = (self.reduce)(self.handler, prod_index, &mut self.node_stack);
            debug_assert!((nt as usize) < tables.goto_width);
            debug_assert!(self.state_stack.len() >= self.node_stack.len());
            self.state_stack.truncate(self.node_stack.len());
            let prev_state = *self.state_stack.last().unwrap();
            let state_after = tables.goto_table[prev_state * tables.goto_width + nt as usize];
            debug_assert!(state_after < tables.state_count);
            self.state_stack.push(state_after);
            action = self.action(t);
        }

        debug_assert_eq!(self.state_stack.len(), self.node_stack.len() + 1);
        action
    }

    pub fn write_token(&mut self, token: Token) -> Result<()> {
        // Loop for error-handling. The normal path through this code reaches
        // the `return` statement.
        loop {
            let t = token.get_id();
            let action = self.reduce_all(t);
            if action.is_shift() {
                self.node_stack.push(
                    Box::into_raw(Box::new(token)) as *mut _
                );
                self.state_stack.push(action.shift_state());
                return Ok(());
            } else {
                assert!(action.is_error());
                self.try_error_handling(t)?;
            }
        }
    }

    pub fn close(&mut self) -> Result<Node> {
        // Loop for error-handling.
        loop {
            let action = self.reduce_all(TerminalId::End);
            if action.is_accept() {
                assert_eq!(self.node_stack.len(), 1);
                return Ok(self.node_stack.pop().unwrap());
            } else {
                assert!(action.is_error());
                self.try_error_handling(TerminalId::End)?;
            }
        }
    }

    fn try_error_handling(&mut self, t: TerminalId) -> Result<()> {
        // Error recovery version of the code in write_terminal. Differences
        // between this and write_terminal are commented below.
        assert!(t != TerminalId::ErrorToken);

        let action = self.reduce_all(TerminalId::ErrorToken);
        if action.is_shift() {
            // Don't actually push an ErrorToken onto the stack here. Treat the
            // ErrorToken as having been consumed and move to the recovered
            // state.
            *self.state_stack.last_mut().unwrap() = action.shift_state();
            Ok(())
        } else {
            // On error, don't attempt error handling again.
            assert!(action.is_error());
            Err(
                if t == TerminalId::End {
                    ParseError::UnexpectedEnd
                } else {
                    ParseError::SyntaxError
                }
            )
        }
    }

    fn can_accept_terminal(&self, t: TerminalId) -> bool {
        // BUG: This is wrong. Because this parser may be LALR, if we see a
        // reduce action, we need to simulate the reduce before we know if t is
        // really acceptable.
        !self.action(t).is_error()
    }


    /// Return true if self.close() would succeed.
    fn can_close(&self) -> bool {
        // Easy case: no error, parsing just succeeds.
        if self.can_accept_terminal(TerminalId::End) {
            true
        } else {
            // Hard case: maybe error-handling would succeed?  BUG: Need
            // simulator to simulate reduce_all; for now just give up
            false
        }
    }
}
