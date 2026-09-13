"""Tests for rungs.miniyaml: the strict YAML subset used for front matter and
rungs.yaml. See DESIGN.md sections 3-4 for the documented subset."""

import unittest

import helpers  # noqa: F401  (inserts src/ onto sys.path)

from rungs import miniyaml as Y


class RoundTripTests(unittest.TestCase):
    def test_nested_mapping(self):
        text = "a: 1\nb:\n  c: x\n  d:\n    e: 2\n"
        self.assertEqual(Y.loads(text), {"a": 1, "b": {"c": "x", "d": {"e": 2}}})

    def test_flow_list_of_scalars(self):
        self.assertEqual(Y.loads("tags: [a, b, c]\n"), {"tags": ["a", "b", "c"]})

    def test_flow_list_empty(self):
        self.assertEqual(Y.loads("tags: []\n"), {"tags": []})

    def test_flow_list_mixed_types(self):
        self.assertEqual(Y.loads("x: [a, 1, true, null, 'q']\n"),
                          {"x": ["a", 1, True, None, "q"]})

    def test_block_list_of_scalars(self):
        text = "tags:\n  - a\n  - b\n"
        self.assertEqual(Y.loads(text), {"tags": ["a", "b"]})

    def test_single_quoted_escape(self):
        self.assertEqual(Y.loads("k: 'it''s here'\n"), {"k": "it's here"})

    def test_double_quoted_escape(self):
        self.assertEqual(Y.loads('k: "a\\nb\\tc\\\\d\\"e"\n'), {"k": 'a\nb\tc\\d"e'})

    def test_null_variants(self):
        self.assertEqual(Y.loads("a: null\nb: ~\nc:\n"), {"a": None, "b": None, "c": None})

    def test_booleans(self):
        self.assertEqual(Y.loads("a: true\nb: false\n"), {"a": True, "b": False})

    def test_ints(self):
        self.assertEqual(Y.loads("a: 0\nb: 42\n"), {"a": 0, "b": 42})

    def test_negative_ints(self):
        self.assertEqual(Y.loads("a: -1\nb: -42\n"), {"a": -1, "b": -42})

    def test_quoted_int_like_stays_string(self):
        self.assertEqual(Y.loads("a: '42'\nb: \"007\"\n"), {"a": "42", "b": "007"})

    def test_dump_load_round_trip(self):
        doc = {"a": 1, "b": {"c": "x", "d": {"e": 2}}, "list": ["x", "y"], "n": None}
        self.assertEqual(Y.loads(Y.dumps(doc)), doc)


class CommentTests(unittest.TestCase):
    def test_full_line_comment_is_ignored(self):
        text = "# a comment\na: 1\n# another\nb: 2\n"
        self.assertEqual(Y.loads(text), {"a": 1, "b": 2})

    def test_inline_comment_after_plain_scalar(self):
        self.assertEqual(Y.loads("a: value # trailing note\n"), {"a": "value"})

    def test_inline_comment_after_flow_list(self):
        self.assertEqual(Y.loads("a: [x, y] # trailing note\n"), {"a": ["x", "y"]})

    def test_comment_only_document_is_empty(self):
        self.assertEqual(Y.loads("# just a comment\n"), {})


class ErrorTests(unittest.TestCase):
    def test_tab_indentation_rejected(self):
        with self.assertRaises(Y.YamlError):
            Y.loads("key:\n\tnested: 1\n")

    def test_pure_tab_indentation_rejected(self):
        with self.assertRaises(Y.YamlError):
            Y.loads("\tkey: 1\n")

    def test_block_scalar_pipe_rejected(self):
        with self.assertRaises(Y.YamlError):
            Y.loads("key: |\n  text\n")

    def test_block_scalar_fold_rejected(self):
        with self.assertRaises(Y.YamlError):
            Y.loads("key: >\n  text\n")

    def test_flow_mapping_rejected(self):
        with self.assertRaises(Y.YamlError):
            Y.loads("key: {a: 1}\n")

    def test_list_of_mappings_rejected(self):
        with self.assertRaises(Y.YamlError):
            Y.loads("key:\n  - a: 1\n")

    def test_anchor_rejected(self):
        with self.assertRaises(Y.YamlError):
            Y.loads("key: &anchor value\n")

    def test_alias_rejected(self):
        with self.assertRaises(Y.YamlError):
            Y.loads("key: *anchor\n")

    def test_duplicate_key_rejected(self):
        with self.assertRaises(Y.YamlError):
            Y.loads("key: 1\nkey: 2\n")

    def test_bad_indentation_rejected(self):
        with self.assertRaises(Y.YamlError):
            Y.loads("key:\n    nested: 1\n  bad: 2\n")

    def test_unterminated_single_quote(self):
        with self.assertRaises(Y.YamlError):
            Y.loads("key: 'abc\n")

    def test_unterminated_double_quote(self):
        with self.assertRaises(Y.YamlError):
            Y.loads('key: "abc\n')

    def test_unterminated_flow_list(self):
        with self.assertRaises(Y.YamlError):
            Y.loads("key: [a, b\n")

    def test_non_mapping_document_rejected(self):
        with self.assertRaises(Y.YamlError):
            Y.loads("- a\n- b\n")


class DumpsQuotingTests(unittest.TestCase):
    def test_colon_is_quoted(self):
        self.assertEqual(Y.dumps({"k": "a:b"}), "k: 'a:b'\n")

    def test_hash_is_quoted(self):
        self.assertEqual(Y.dumps({"k": "a#b"}), "k: 'a#b'\n")

    def test_triple_dash_is_quoted(self):
        self.assertEqual(Y.dumps({"k": "a---b"}), "k: 'a---b'\n")

    def test_leading_dash_is_quoted(self):
        self.assertEqual(Y.dumps({"k": "-x"}), "k: '-x'\n")

    def test_reserved_words_are_quoted(self):
        for word in ("null", "~", "true", "false", "yes", "no", "on", "off", ""):
            with self.subTest(word=word):
                dumped = Y.dumps({"k": word})
                self.assertEqual(dumped, f"k: '{word}'\n")
                # round trips back to the same string, not a special value
                self.assertEqual(Y.loads(dumped), {"k": word})

    def test_int_like_string_is_quoted(self):
        dumped = Y.dumps({"k": "42"})
        self.assertEqual(dumped, "k: '42'\n")
        self.assertEqual(Y.loads(dumped), {"k": "42"})

    def test_plain_string_is_not_quoted(self):
        self.assertEqual(Y.dumps({"k": "plain"}), "k: plain\n")

    def test_real_int_is_not_quoted(self):
        self.assertEqual(Y.dumps({"k": 42}), "k: 42\n")

    def test_real_negative_int_is_not_quoted(self):
        self.assertEqual(Y.dumps({"k": -7}), "k: -7\n")

    def test_newline_in_scalar_raises(self):
        with self.assertRaises(Y.YamlError):
            Y.dumps({"k": "line1\nline2"})

    def test_quote_helper_escapes_single_quotes(self):
        self.assertEqual(Y.quote("it's"), "'it''s'")


class FlowListQuoteTests(unittest.TestCase):
    """The closing ']' of a flow list is found with quotes honoured, so a
    quoted item containing ' #' or ']' does not end the list early."""

    def test_double_quoted_non_first_flow_item_with_hash_space(self):
        result = Y.loads('key: [x, "a # foo"]\n')
        self.assertEqual(result, {"key": ["x", "a # foo"]})

    def test_empty_flow_mapping(self):
        self.assertEqual(Y.loads("agents: {}\npolicy: {}  # none\n"), {"agents": {}, "policy": {}})
        self.assertEqual(Y.loads(Y.dumps({"agents": {}})), {"agents": {}})
        with self.assertRaises(Y.YamlError):
            Y.loads("agents: {a: 1}\n")

    def test_quoted_bracket_and_trailing_comment(self):
        self.assertEqual(Y.loads("key: ['a]b', x]  # note\n"), {"key": ["a]b", "x"]})
        with self.assertRaises(Y.YamlError):
            Y.loads("key: [a, 'b\n")


if __name__ == "__main__":
    unittest.main()
